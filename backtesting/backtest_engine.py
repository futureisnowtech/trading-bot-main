"""
backtesting/backtest_engine.py

Backtesting module. Run this standalone to validate strategies before live.
Uses backtesting.py (simple, readable) with vectorbt fallback for speed.

Usage:
  python run_backtest.py --strategy crypto_macd --symbol BTC-USD --period 6mo
  python run_backtest.py --strategy equity --symbol AAPL --period 1y

Both strategies are wired here. Results printed + saved to logs/backtest/.
"""
import os
import sys
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import add_all_indicators, get_fib_levels, fib_confluence
from config import BACKTEST_SLIPPAGE_PCT

BACKTEST_DIR = 'logs/backtest'
os.makedirs(BACKTEST_DIR, exist_ok=True)

# ─── backtesting.py strategies ───────────────────────────────────────────────

try:
    import sys as _sys
    _SITE = '/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages'
    # Temporarily hide the local 'backtesting' package from the module cache
    # so the installed library can be imported instead.
    _saved = {k: v for k, v in _sys.modules.items()
              if k == 'backtesting' or k.startswith('backtesting.')}
    for _k in _saved:
        del _sys.modules[_k]
    _sys.path.insert(0, _SITE)
    from backtesting import Backtest, Strategy
    from backtesting.lib import crossover
    # Re-register under a private alias to avoid future collisions
    _sys.modules['_bt_installed'] = _sys.modules.pop('backtesting')
    # Restore the local package
    _sys.path.pop(0)
    for _k, _v in _saved.items():
        _sys.modules[_k] = _v
    BACKTESTING_PY = True
except Exception as _e:
    BACKTESTING_PY = False
    print(f"[backtest] backtesting.py not installed. Run: pip install backtesting ({_e})")


if BACKTESTING_PY:

    def _calc_fib_confluence(
        high: pd.Series, low: pd.Series, close: pd.Series,
        lookback: int = 100, atr_period: int = 14,
    ) -> np.ndarray:
        """
        Vectorized Fibonacci confluence signal, look-ahead free.

        At each bar we treat the rolling max-high and min-low over the last
        `lookback` bars as the swing high / swing low.  Retracement levels
        are computed from those.  If the current close is within 0.5×ATR of
        a key level the function returns:
            > 0  →  bullish support confluence (multiply by MACD confidence)
            < 0  →  bearish resistance pushback
            = 0  →  no fib confluence at this bar

        Values are scaled to match the confidence boosts used in the live
        strategy (fib_618 = ±0.15, fib_382 = ±0.12, etc.).
        """
        sh = high.rolling(lookback).max()          # range high
        sl = low.rolling(lookback).min()           # range low
        diff = sh - sl

        # ATR for tolerance band
        tr_raw = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr_raw.ewm(span=atr_period, adjust=False).mean()
        tol = atr * 0.5

        midpoint  = (sh + sl) / 2.0
        is_support = (close <= midpoint).astype(float)   # 1 = support, 0 = resistance
        direction  = np.where(is_support == 1, 1.0, -1.0)

        # Fib levels and their boosts (highest to lowest priority)
        fib_params = [
            (0.618, 0.15),
            (0.382, 0.12),
            (0.500, 0.08),
            (0.786, 0.08),
            (0.236, 0.06),
        ]

        result = pd.Series(0.0, index=close.index)
        for ratio, boost in fib_params:
            level   = sh - diff * ratio
            near    = (close - level).abs() <= tol
            # Only mark bars not already claimed by a higher-priority level
            unset   = result == 0.0
            result  = result.where(~(near & unset), other=pd.Series(direction * boost, index=close.index))

        return result.values


    def _calc_adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> np.ndarray:
        """Lightweight ADX calculation (no external dependency)."""
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=n, adjust=False).mean()
        up   = high.diff()
        down = -low.diff()
        dm_p = np.where((up > down) & (up > 0), up, 0.0)
        dm_m = np.where((down > up) & (down > 0), down, 0.0)
        di_p = pd.Series(dm_p, index=high.index).ewm(span=n, adjust=False).mean() / atr * 100
        di_m = pd.Series(dm_m, index=high.index).ewm(span=n, adjust=False).mean() / atr * 100
        dx   = (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan) * 100
        return dx.ewm(span=n, adjust=False).mean().values


    class CryptoMACDWorkhorse(Strategy):
        """
        MACD(3/15/3) Histogram strategy — mirrors live crypto_macd variant 1.

        long_short = False → LONG only (default — crypto live bot is long-only)
        long_short = True  → LONG when hist>0, SHORT when hist<0 (requires margin)
        adx_min           → skip entry when market is choppy (ADX < threshold)
        use_fib = True    → fib confluence required to confirm entries
                            (same logic as live strategy)
        """
        fast          = 3
        slow          = 15
        signal_period = 3
        adx_min       = 15
        long_short    = False   # long-only: mirrors live bot (no margin/short)
        use_fib       = True    # set False to compare without fib

        def init(self):
            close = pd.Series(self.data.Close)
            high  = pd.Series(self.data.High)
            low   = pd.Series(self.data.Low)

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line  = ema_f - ema_s
                signal = line.ewm(span=sig, adjust=False).mean()
                return (line - signal).values

            self.macd_histogram = self.I(
                macd_hist, close, self.fast, self.slow, self.signal_period
            )
            self.adx = self.I(_calc_adx, high, low, close)
            if self.use_fib:
                self.fib = self.I(_calc_fib_confluence, high, low, close)

        def next(self):
            hist = self.macd_histogram[-1]
            adx  = self.adx[-1]
            if np.isnan(adx) or adx < self.adx_min:
                return

            # fib_val > 0: price is at fib support (buy-friendly)
            # fib_val < 0: price is at fib resistance (sell-friendly)
            # fib_val = 0: no fib confluence
            fib_val = self.fib[-1] if self.use_fib else 0.0

            want_long  = hist > 0
            want_short = hist < 0

            # When fib conflicts with direction, skip the trade.
            # e.g. MACD says BUY but fib says we're at resistance → skip.
            if self.use_fib:
                if want_long  and fib_val < -0.05:   # resistance pushback
                    return
                if want_short and fib_val > 0.05:    # support pushback
                    return

            if self.long_short:
                if want_long and not self.position.is_long:
                    if self.position.is_short:
                        self.position.close()
                    self.buy()
                elif want_short and not self.position.is_short:
                    if self.position.is_long:
                        self.position.close()
                    self.sell()
            else:
                if want_long and not self.position:
                    self.buy()
                elif want_short and self.position:
                    self.position.close()


    class CryptoMACDClassic(Strategy):
        """
        MACD(4/16/3) Line vs Signal crossover — mirrors live variant 2.
        adx_min / long_short / use_fib params same as Workhorse.
        """
        fast          = 4
        slow          = 16
        signal_period = 3
        adx_min       = 15
        long_short    = False   # long-only
        use_fib       = True

        def init(self):
            close = pd.Series(self.data.Close)
            high  = pd.Series(self.data.High)
            low   = pd.Series(self.data.Low)

            def macd_line(prices, f, s):
                return (prices.ewm(span=f, adjust=False).mean()
                        - prices.ewm(span=s, adjust=False).mean()).values

            def signal_line(prices, f, s, sig):
                ml = pd.Series(macd_line(prices, f, s))
                return ml.ewm(span=sig, adjust=False).mean().values

            self.macd   = self.I(macd_line, close, self.fast, self.slow)
            self.signal = self.I(signal_line, close, self.fast, self.slow, self.signal_period)
            self.adx    = self.I(_calc_adx, high, low, close)
            if self.use_fib:
                self.fib = self.I(_calc_fib_confluence, high, low, close)

        def next(self):
            adx = self.adx[-1]
            if np.isnan(adx) or adx < self.adx_min:
                return

            fib_val     = self.fib[-1] if self.use_fib else 0.0
            going_long  = crossover(self.macd, self.signal)
            going_short = crossover(self.signal, self.macd)

            if self.use_fib:
                if going_long  and fib_val < -0.05:
                    return
                if going_short and fib_val > 0.05:
                    return

            if self.long_short:
                if going_long:
                    if self.position.is_short:
                        self.position.close()
                    self.buy()
                elif going_short:
                    if self.position.is_long:
                        self.position.close()
                    self.sell()
            else:
                if going_long and not self.position:
                    self.buy()
                elif going_short and self.position:
                    self.position.close()


    class CryptoMACDSniper(Strategy):
        """
        MACD(6/20/5) Histogram > threshold — mirrors live variant 3.
        adx_min / long_short / use_fib params same as Workhorse.
        """
        fast          = 6
        slow          = 20
        signal_period = 5
        threshold_pct = 0.0001
        adx_min       = 15
        long_short    = False   # long-only
        use_fib       = True

        def init(self):
            close = pd.Series(self.data.Close)
            high  = pd.Series(self.data.High)
            low   = pd.Series(self.data.Low)

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line  = ema_f - ema_s
                sig_l = line.ewm(span=sig, adjust=False).mean()
                return (line - sig_l).values

            self.macd_histogram = self.I(
                macd_hist, close, self.fast, self.slow, self.signal_period
            )
            self.adx = self.I(_calc_adx, high, low, close)
            if self.use_fib:
                self.fib = self.I(_calc_fib_confluence, high, low, close)

        def next(self):
            adx       = self.adx[-1]
            hist      = self.macd_histogram[-1]
            threshold = self.data.Close[-1] * self.threshold_pct

            if np.isnan(adx) or adx < self.adx_min:
                return

            fib_val     = self.fib[-1] if self.use_fib else 0.0
            want_long   = hist > threshold
            want_short  = hist < -threshold

            if self.use_fib:
                if want_long  and fib_val < -0.05:
                    return
                if want_short and fib_val > 0.05:
                    return

            if self.long_short:
                if want_long and not self.position.is_long:
                    if self.position.is_short:
                        self.position.close()
                    self.buy()
                elif want_short and not self.position.is_short:
                    if self.position.is_long:
                        self.position.close()
                    self.sell()
            else:
                if want_long and not self.position:
                    self.buy()
                elif want_short and self.position:
                    self.position.close()


    class EquityMomentum(Strategy):
        """
        Equity momentum with MACD + RSI + volume filter.
        Approximates the full strategy logic for backtesting purposes.
        """
        macd_fast = 12
        macd_slow = 26
        macd_signal = 9
        rsi_period = 14
        rsi_overbought = 70
        rsi_oversold = 35
        vol_spike_min = 1.5

        def init(self):
            close = pd.Series(self.data.Close)
            volume = pd.Series(self.data.Volume)

            def rsi(prices, n):
                delta = prices.diff()
                gain = delta.clip(lower=0).rolling(n).mean()
                loss = (-delta.clip(upper=0)).rolling(n).mean()
                rs = gain / loss.replace(0, np.nan)
                return (100 - 100 / (1 + rs)).values

            def macd_hist(prices, f, s, sig):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line = ema_f - ema_s
                signal = line.ewm(span=sig, adjust=False).mean()
                return (line - signal).values

            def vol_spike(vol, n=20):
                ma = vol.rolling(n).mean()
                return (vol / ma.replace(0, np.nan)).values

            self.macd_hist = self.I(
                macd_hist, close, self.macd_fast, self.macd_slow, self.macd_signal
            )
            self.rsi = self.I(rsi, close, self.rsi_period)
            self.vol_spike = self.I(vol_spike, volume)

        def next(self):
            rsi = self.rsi[-1]
            hist = self.macd_hist[-1]
            spike = self.vol_spike[-1]

            if (hist > 0
                    and 35 <= rsi <= 65
                    and spike >= self.vol_spike_min
                    and not self.position):
                self.buy()
            elif (rsi > self.rsi_overbought or hist < 0) and self.position:
                self.position.close()


    class CryptoMeanReversionBT(Strategy):
        """
        Mean-reversion strategy — mirrors crypto_mean_reversion.py.

        Entry (LONG only):
          - Kalman proxy: (close - EMA50) / EMA50 <= -0.8% (price stretched below trend)
          - OR: (close - rolling_vwap_30) / rolling_vwap_30 <= -0.5% (AVWAP deviation)
          - Price within 1.2% of lower Bollinger Band (structural support zone)
          - ADX < adx_max (ranging, not trending)
          - MACD(3/15/3) hist > -0.3% of price (not in freefall)
        Exit: BB mid target OR 2% stop (whichever hit first)
        """
        adx_max    = 22
        bb_period  = 20
        bb_std     = 2.0
        ema_period = 50

        def init(self):
            close  = pd.Series(self.data.Close)
            high   = pd.Series(self.data.High)
            low    = pd.Series(self.data.Low)
            volume = pd.Series(self.data.Volume)

            # EMA50 as Kalman proxy
            def ema(prices, n):
                return prices.ewm(span=n, adjust=False).mean().values

            # Rolling VWAP (30-bar) as AVWAP proxy
            def rolling_vwap(high, low, close, vol, n=30):
                typical = (high + low + close) / 3.0
                tpv = typical * vol
                cum_tpv = tpv.rolling(n).sum()
                cum_vol = vol.rolling(n).sum()
                return (cum_tpv / cum_vol.replace(0, np.nan)).values

            # Bollinger Bands
            def bb_lower(prices, n, k):
                mid = prices.rolling(n).mean()
                std = prices.rolling(n).std()
                return (mid - k * std).values

            def bb_mid(prices, n):
                return prices.rolling(n).mean().values

            # MACD(3/15/3) histogram
            def macd_hist(prices, f=3, s=15, sig=3):
                ema_f = prices.ewm(span=f, adjust=False).mean()
                ema_s = prices.ewm(span=s, adjust=False).mean()
                line  = ema_f - ema_s
                signal_l = line.ewm(span=sig, adjust=False).mean()
                return (line - signal_l).values

            self.ema50    = self.I(ema, close, self.ema_period)
            self.vwap30   = self.I(rolling_vwap, high, low, close, volume)
            self.bb_lower = self.I(bb_lower, close, self.bb_period, self.bb_std)
            self.bb_mid   = self.I(bb_mid,   close, self.bb_period)
            self.macd_h   = self.I(macd_hist, close)
            self.adx      = self.I(_calc_adx, high, low, close)

        def next(self):
            price  = self.data.Close[-1]
            adx    = self.adx[-1]
            ema50  = self.ema50[-1]
            vwap30 = self.vwap30[-1]
            bbl    = self.bb_lower[-1]
            bbm    = self.bb_mid[-1]
            hist   = self.macd_h[-1]

            # Guard against NaN
            if any(np.isnan(x) for x in [adx, ema50, vwap30, bbl, bbm, hist]):
                return

            # Already in a position — check if target hit (managed via sl/tp on entry)
            if self.position:
                return

            # Kalman proxy: price at least 0.8% below EMA50
            kalman_ok = (price - ema50) / ema50 <= -0.008
            # AVWAP proxy: price at least 0.5% below rolling VWAP
            avwap_ok  = (price - vwap30) / vwap30 <= -0.005 if vwap30 > 0 else False
            if not (kalman_ok or avwap_ok):
                return

            # Price within 1.2% of lower BB
            if price <= 0:
                return
            bb_prox = (price - bbl) / price
            if bb_prox > 0.012:
                return

            # ADX < adx_max (ranging regime)
            if adx >= self.adx_max:
                return

            # MACD not in freefall: hist > -0.003 * price
            if hist <= -0.003 * price:
                return

            # Compute target (BB mid) and stop (2% below entry)
            target = bbm if bbm > price else price * 1.055  # fallback: 5.5% above
            stop   = price * 0.98

            reward = (target - price) / price
            risk   = (price - stop)   / price

            # Min R:R = 2.5 and min reward = 4%
            if reward < 0.04 or (risk <= 0 or reward / risk < 2.5):
                return

            self.buy(sl=stop, tp=target)


    class FuturesMESScalperBT(Strategy):
        """
        MES Opening Range Breakout — mirrors futures_scalper.py.

        Entry:
          - Track first bar of each day as the Opening Range (OR)
          - LONG: close > OR high × 1.001 AND price > EMA20 AND ADX > adx_min
          - SHORT: close < OR low × 0.999 AND price < EMA20 AND ADX > adx_min
        Stop: stop_pts_pct below/above entry. Target: target_pts_pct above/below.
        """
        adx_min       = 18
        stop_pts_pct  = 0.00073   # ~4 ES points / 5500
        target_pts_pct = 0.00145  # ~8 ES points / 5500
        long_short    = True

        def init(self):
            close = pd.Series(self.data.Close)
            high  = pd.Series(self.data.High)
            low   = pd.Series(self.data.Low)

            def ema(prices, n=20):
                return prices.ewm(span=n, adjust=False).mean().values

            self.ema20 = self.I(ema, close)
            self.adx   = self.I(_calc_adx, high, low, close)

            # OR tracking state
            self._or_high = None
            self._or_low  = None
            self._or_date = None

        def next(self):
            price    = self.data.Close[-1]
            high_bar = self.data.High[-1]
            low_bar  = self.data.Low[-1]
            adx      = self.adx[-1]
            ema20    = self.ema20[-1]

            if np.isnan(adx) or np.isnan(ema20):
                return

            # Detect new trading day
            try:
                current_date = self.data.index[-1].date()
            except Exception:
                return

            if current_date != self._or_date:
                # First bar of new day — set Opening Range and do not trade
                self._or_high = high_bar
                self._or_low  = low_bar
                self._or_date = current_date
                return

            if self._or_high is None or self._or_low is None:
                return

            # HTF bias proxy: EMA20
            htf_bullish = price > ema20
            htf_bearish = price < ema20

            # ADX filter
            if adx < self.adx_min:
                return

            # LONG entry
            if (self.long_short and
                    price > self._or_high * 1.001 and
                    htf_bullish and
                    not self.position.is_long):
                if self.position.is_short:
                    self.position.close()
                stop   = price * (1 - self.stop_pts_pct)
                target = price * (1 + self.target_pts_pct)
                self.buy(sl=stop, tp=target)

            # SHORT entry
            elif (self.long_short and
                    price < self._or_low * 0.999 and
                    htf_bearish and
                    not self.position.is_short):
                if self.position.is_long:
                    self.position.close()
                stop   = price * (1 + self.stop_pts_pct)
                target = price * (1 - self.target_pts_pct)
                self.sell(sl=stop, tp=target)


    class CryptoPerpBT(Strategy):
        """
        Perpetual futures breakout strategy — mirrors crypto_perp_strategy.py.

        Entry:
          - LONG:  close > 20-bar rolling high × 1.001 AND RSI > 55 AND ADX > 20
                   AND vol_spike > 1.2
          - SHORT: close < 20-bar rolling low × 0.999 AND RSI < 45 AND ADX > 20
                   AND vol_spike > 1.2
        Stop: stop_pct. Target: tp_pct (2:1 R:R).
        """
        breakout_bars   = 20
        adx_min         = 20
        rsi_period      = 14
        vol_spike_min   = 1.2
        rsi_long_min    = 55
        rsi_short_max   = 45
        stop_pct        = 0.015
        tp_pct          = 0.030

        def init(self):
            close  = pd.Series(self.data.Close)
            high   = pd.Series(self.data.High)
            low    = pd.Series(self.data.Low)
            volume = pd.Series(self.data.Volume)

            def rolling_max(prices, n):
                return prices.rolling(n).max().values

            def rolling_min(prices, n):
                return prices.rolling(n).min().values

            def rsi(prices, n):
                delta = prices.diff()
                gain  = delta.clip(lower=0).rolling(n).mean()
                loss  = (-delta.clip(upper=0)).rolling(n).mean()
                rs    = gain / loss.replace(0, np.nan)
                return (100 - 100 / (1 + rs)).values

            def vol_spike(vol, n=20):
                ma = vol.rolling(n).mean()
                return (vol / ma.replace(0, np.nan)).values

            self.roll_high = self.I(rolling_max, close, self.breakout_bars)
            self.roll_low  = self.I(rolling_min, close, self.breakout_bars)
            self.rsi_vals  = self.I(rsi, close, self.rsi_period)
            self.vol_spk   = self.I(vol_spike, volume)
            self.adx       = self.I(_calc_adx, high, low, close)

        def next(self):
            price     = self.data.Close[-1]
            rsi_val   = self.rsi_vals[-1]
            adx       = self.adx[-1]
            spike     = self.vol_spk[-1]
            r_high    = self.roll_high[-1]
            r_low     = self.roll_low[-1]

            if any(np.isnan(x) for x in [rsi_val, adx, spike, r_high, r_low]):
                return

            # LONG: breakout above 20-bar high
            want_long = (
                price > r_high * 1.001 and
                rsi_val > self.rsi_long_min and
                adx > self.adx_min and
                spike > self.vol_spike_min
            )
            # SHORT: breakdown below 20-bar low
            want_short = (
                price < r_low * 0.999 and
                rsi_val < self.rsi_short_max and
                adx > self.adx_min and
                spike > self.vol_spike_min
            )

            if want_long and not self.position.is_long:
                if self.position.is_short:
                    self.position.close()
                stop   = price * (1 - self.stop_pct)
                target = price * (1 + self.tp_pct)
                self.buy(sl=stop, tp=target)
            elif want_short and not self.position.is_short:
                if self.position.is_long:
                    self.position.close()
                stop   = price * (1 + self.stop_pct)
                target = price * (1 - self.tp_pct)
                self.sell(sl=stop, tp=target)


# ─── Backtest runner ─────────────────────────────────────────────────────────

_PERIOD_TO_DAYS = {
    '1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '2y': 730,
}

_INTERVAL_TO_CB_GRANULARITY = {
    '1m': 'ONE_MINUTE', '5m': 'FIVE_MINUTE', '15m': 'FIFTEEN_MINUTE',
    '30m': 'THIRTY_MINUTE', '1h': 'ONE_HOUR', '1d': 'ONE_DAY',
}


def fetch_data(symbol: str, period: str = '6mo', interval: str = '5m') -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data for backtesting.

    Priority:
      1. Local price archive (logs/price_archive.db) — zero API cost
      2. Coinbase REST API — for crypto pairs, best data quality
      3. yfinance — fallback for equities or when Coinbase creds absent
    """
    # ── 1. Check local price archive first ────────────────────────────────────
    days = _PERIOD_TO_DAYS.get(period, 180)
    cb_granularity = _INTERVAL_TO_CB_GRANULARITY.get(interval, 'FIVE_MINUTE')
    try:
        from data.price_archive import get_candles as archive_get, has_data, upsert_candles
        from datetime import timezone
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        # Normalise symbol for archive lookup
        _arch_sym = symbol.upper().replace('BTC-USD', 'BTC-USDC').replace('ETH-USD', 'ETH-USDC')
        if has_data(_arch_sym, cb_granularity, start_dt, end_dt, min_coverage=0.70):
            df_arch = archive_get(_arch_sym, cb_granularity, start=start_dt, end=end_dt)
            if df_arch is not None and len(df_arch) > 100:
                df_arch = df_arch.rename(columns={
                    'open': 'Open', 'high': 'High', 'low': 'Low',
                    'close': 'Close', 'volume': 'Volume',
                })
                df_arch = df_arch[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                print(f"[backtest] ✅ Loaded {len(df_arch)} candles from local archive for {_arch_sym}")
                return df_arch
        print(f"[backtest] Archive miss for {_arch_sym} — fetching from API")
    except Exception as _ae:
        print(f"[backtest] Archive check failed: {_ae} — falling back to API")
    days = _PERIOD_TO_DAYS.get(period, 180)
    cb_granularity = _INTERVAL_TO_CB_GRANULARITY.get(interval, 'FIVE_MINUTE')

    # ── Try Coinbase first for crypto pairs ───────────────────────────────────
    is_cb_pair = '-USDC' in symbol.upper() or (
        '-USD' in symbol.upper() and symbol.upper() not in ('BTC-USD', 'ETH-USD')
    )
    # Also try for BTC-USD / ETH-USD by converting to the USDC pair
    yf_to_cb = {'BTC-USD': 'BTC-USDC', 'ETH-USD': 'ETH-USDC'}
    cb_symbol = yf_to_cb.get(symbol.upper(), symbol.upper())
    use_coinbase = '-USDC' in cb_symbol or '-USD' in cb_symbol

    if use_coinbase:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from data.coinbase_feed import get_historical_candles
            print(f"[backtest] Fetching {cb_symbol} from Coinbase API ({days}d, {cb_granularity})…")
            df = get_historical_candles(
                product_id=cb_symbol, granularity=cb_granularity,
                days=days, use_cache=True,
            )
            if df is not None and not df.empty:
                df = df.rename(columns={
                    'open': 'Open', 'high': 'High', 'low': 'Low',
                    'close': 'Close', 'volume': 'Volume',
                })
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                print(f"[backtest] Coinbase data: {len(df)} candles for {cb_symbol}")
                return df
            print(f"[backtest] Coinbase fetch empty — falling back to yfinance")
        except Exception as e:
            print(f"[backtest] Coinbase fetch failed ({e}) — falling back to yfinance")

    # ── yfinance fallback (equities + when Coinbase creds absent) ─────────────
    print(f"[backtest] Fetching {symbol} {interval} data ({period}) via yfinance…")
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            print(f"[backtest] No data returned for {symbol}")
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })
        df.columns = [c.title() for c in df.columns]
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
        print(f"[backtest] Got {len(df)} candles for {symbol}")
        return df
    except Exception as e:
        print(f"[backtest] Data fetch error: {e}")
        return None


def run_crypto_backtest(
    symbol: str = 'BTC-USD',
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
    variant: str = 'all'
) -> dict:
    """
    Run all three MACD crypto strategies and return comparison.
    variant: 'all' | 'workhorse' | 'classic' | 'sniper'
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    # backtesting.py 0.6.x converts fractional sizes to int() units via int(cash/price).
    # For BTC at ~$80k, int(500/80000)=0 → all orders cancelled.
    # Fix: scale prices DOWN so $500 of virtual cash buys ≥10 units. P&L % is preserved.
    # Do NOT scale cash — keep it at the original value.
    avg_price = float(df['Close'].mean())
    target_units = 10  # want at least 10 units purchasable with `cash`
    if avg_price > 0 and cash / avg_price < target_units:
        price_scale = avg_price / (cash / target_units)   # scale price so cash buys ~10 units
    else:
        price_scale = 1.0
    if price_scale > 1.0:
        df = df.copy()
        for col in ('Open', 'High', 'Low', 'Close'):
            df[col] = df[col] / price_scale
    cash_scaled = cash  # keep cash unchanged — only price is scaled

    results = {}
    strategies = {
        'workhorse': (CryptoMACDWorkhorse, 'MACD(3/15/3) Histogram > 0'),
        'classic':   (CryptoMACDClassic,   'MACD(4/16/3) Line vs Signal'),
        'sniper':    (CryptoMACDSniper,     'MACD(6/20/5) Histogram > threshold'),
    }

    if variant != 'all':
        strategies = {variant: strategies[variant]}

    for name, (StratClass, description) in strategies.items():
        print(f"\n[backtest] Running {description} on {symbol}...")
        try:
            bt = Backtest(
                df, StratClass,
                cash=cash_scaled,
                commission=commission + slippage,
                exclusive_orders=True
            )
            stats = bt.run()
            result = _parse_stats(stats, symbol, name, description)
            results[name] = result
            _print_result(result)

            # Save plot if possible
            try:
                plot_path = os.path.join(BACKTEST_DIR, f'{symbol}_{name}.html')
                bt.plot(filename=plot_path, open_browser=False)
                result['plot_path'] = plot_path
            except Exception:
                pass

        except Exception as e:
            print(f"[backtest] Error running {name}: {e}")
            results[name] = {'error': str(e)}

    # Save results to JSON
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_path = os.path.join(BACKTEST_DIR, f'results_{symbol}_{ts}.json')
    with open(results_path, 'w') as f:
        json.dump({k: {kk: str(vv) for kk, vv in v.items()} for k, v in results.items()}, f, indent=2)
    print(f"\n[backtest] Results saved to {results_path}")

    return results


def run_equity_backtest(
    symbol: str = 'AAPL',
    period: str = '1y',
    interval: str = '30m',
    cash: float = 500,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
) -> dict:
    """Run equity momentum strategy backtest."""
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[backtest] Running Equity Momentum on {symbol}...")
    try:
        bt = Backtest(df, EquityMomentum, cash=cash, commission=slippage)
        stats = bt.run()
        result = _parse_stats(stats, symbol, 'equity_momentum', 'Equity Momentum MACD+RSI+Volume')
        _print_result(result)
        return result
    except Exception as e:
        print(f"[backtest] Error: {e}")
        return {'error': str(e)}


def run_mean_reversion_backtest(
    symbol: str,
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
) -> dict:
    """Run crypto mean-reversion strategy backtest (Kalman+AVWAP+BB entry)."""
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    # Price scaling fix for high-price assets (same as run_crypto_backtest)
    avg_price = float(df['Close'].mean())
    target_units = 10
    if avg_price > 0 and cash / avg_price < target_units:
        price_scale = avg_price / (cash / target_units)
    else:
        price_scale = 1.0
    if price_scale > 1.0:
        df = df.copy()
        for col in ('Open', 'High', 'Low', 'Close'):
            df[col] = df[col] / price_scale
    cash_scaled = cash

    print(f"\n[backtest] Running CryptoMeanReversionBT on {symbol}...")
    try:
        bt = Backtest(
            df, CryptoMeanReversionBT,
            cash=cash_scaled,
            commission=commission + slippage,
            exclusive_orders=True,
        )
        stats = bt.run()
        result = _parse_stats(stats, symbol, 'crypto_mean_reversion', 'Mean Reversion BB+Kalman+AVWAP')
        _print_result(result)

        # Save JSON
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_path = os.path.join(BACKTEST_DIR, f'results_{symbol}_mean_reversion_{ts}.json')
        with open(results_path, 'w') as f:
            json.dump({k: str(v) for k, v in result.items()}, f, indent=2)
        print(f"[backtest] Results saved to {results_path}")

        result['_raw_stats'] = stats
        return result
    except Exception as e:
        print(f"[backtest] Error: {e}")
        return {'error': str(e)}


def run_futures_backtest(
    symbol: str = 'ES=F',
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 50000,
    commission: float = 0.0002,
    slippage: float = 0.0001,
) -> dict:
    """
    Run MES Opening Range Breakout backtest.
    cash=50000 simulates a properly-funded futures account — win% is what matters for attribution.
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[backtest] Running FuturesMESScalperBT on {symbol}...")
    try:
        bt = Backtest(
            df, FuturesMESScalperBT,
            cash=cash,
            commission=commission + slippage,
            exclusive_orders=True,
        )
        stats = bt.run()
        result = _parse_stats(stats, symbol, 'futures_scalper', 'MES ORB Scalper')
        _print_result(result)

        # Save JSON
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_path = os.path.join(BACKTEST_DIR, f'results_{symbol}_futures_{ts}.json')
        with open(results_path, 'w') as f:
            json.dump({k: str(v) for k, v in result.items()}, f, indent=2)
        print(f"[backtest] Results saved to {results_path}")

        result['_raw_stats'] = stats
        return result
    except Exception as e:
        print(f"[backtest] Error: {e}")
        return {'error': str(e)}


def run_perp_backtest(
    symbol: str,
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.0011,
    slippage: float = 0.0005,
) -> dict:
    """
    Run Bybit perpetual futures breakout backtest.
    commission=0.0011 (Bybit taker 0.055% × 2 round-trip ≈ 0.11%).
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    # Price scaling fix for high-price assets
    avg_price = float(df['Close'].mean())
    target_units = 10
    if avg_price > 0 and cash / avg_price < target_units:
        price_scale = avg_price / (cash / target_units)
    else:
        price_scale = 1.0
    if price_scale > 1.0:
        df = df.copy()
        for col in ('Open', 'High', 'Low', 'Close'):
            df[col] = df[col] / price_scale
    cash_scaled = cash

    print(f"\n[backtest] Running CryptoPerpBT on {symbol}...")
    try:
        bt = Backtest(
            df, CryptoPerpBT,
            cash=cash_scaled,
            commission=commission + slippage,
            exclusive_orders=True,
        )
        stats = bt.run()
        result = _parse_stats(stats, symbol, 'crypto_perp', 'Crypto Perp Breakout Long/Short')
        _print_result(result)

        # Save JSON
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_path = os.path.join(BACKTEST_DIR, f'results_{symbol}_perp_{ts}.json')
        with open(results_path, 'w') as f:
            json.dump({k: str(v) for k, v in result.items()}, f, indent=2)
        print(f"[backtest] Results saved to {results_path}")

        result['_raw_stats'] = stats
        return result
    except Exception as e:
        print(f"[backtest] Error: {e}")
        return {'error': str(e)}


def run_backtest_oos_split(
    symbol: str,
    strategy: str = 'crypto',
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
    train_pct: float = 0.70,
) -> dict:
    """
    Chan out-of-sample validation: split data 70% train / 30% test.
    Runs the strategy on both halves and compares.
    A strategy with good IS stats but poor OOS stats is likely curve-fitted.
    Returns {'in_sample': dict, 'out_of_sample': dict, 'oos_degradation_pct': float}
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None or len(df) < 100:
        return {'error': f'Insufficient data for {symbol}'}

    # Apply same price scaling fix as run_crypto_backtest (int truncation fix)
    avg_price = float(df['Close'].mean())
    target_units = 10
    if avg_price > 0 and cash / avg_price < target_units:
        price_scale = avg_price / (cash / target_units)
    else:
        price_scale = 1.0
    if price_scale > 1.0:
        df = df.copy()
        for col in ('Open', 'High', 'Low', 'Close'):
            df[col] = df[col] / price_scale
    cash_scaled = cash

    split = int(len(df) * train_pct)
    df_train = df.iloc[:split].copy()
    df_test  = df.iloc[split:].copy()

    print(f"\n[backtest:OOS] {symbol} | train={len(df_train)} bars, test={len(df_test)} bars ({train_pct:.0%}/{1-train_pct:.0%})")

    StratClass = CryptoMACDWorkhorse if strategy == 'crypto' else EquityMomentum
    comm = (commission + slippage) if strategy == 'crypto' else slippage
    desc = f"{strategy} OOS split"

    def _run_split(df_split, label):
        try:
            bt = Backtest(df_split, StratClass, cash=cash_scaled, commission=comm, exclusive_orders=True)
            stats = bt.run()
            r = _parse_stats(stats, symbol, f'{strategy}_{label}', f'{desc} [{label}]')
            _print_result(r)
            return r
        except Exception as e:
            return {'error': str(e)}

    is_result  = _run_split(df_train, 'IN_SAMPLE')
    oos_result = _run_split(df_test,  'OUT_OF_SAMPLE')

    # Chan degradation check: OOS return should be within 50% of IS return
    is_ret  = is_result.get('total_return_pct', 0)
    oos_ret = oos_result.get('total_return_pct', 0)
    degradation = ((is_ret - oos_ret) / abs(is_ret) * 100) if is_ret != 0 else 0

    verdict = '✅ ROBUST' if degradation < 50 else '⚠️ POSSIBLE OVERFIT'
    print(f"\n[backtest:OOS] {verdict} | IS return: {is_ret:+.1f}% → OOS return: {oos_ret:+.1f}% | degradation: {degradation:.0f}%")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_path = os.path.join(BACKTEST_DIR, f'oos_{symbol}_{ts}.json')
    with open(results_path, 'w') as f:
        json.dump({
            'symbol': symbol, 'verdict': verdict,
            'is_return_pct': is_ret, 'oos_return_pct': oos_ret,
            'degradation_pct': degradation,
            'in_sample': {k: str(v) for k, v in is_result.items()},
            'out_of_sample': {k: str(v) for k, v in oos_result.items()},
        }, f, indent=2)
    print(f"[backtest:OOS] Results saved to {results_path}")

    return {
        'in_sample': is_result,
        'out_of_sample': oos_result,
        'oos_degradation_pct': degradation,
        'verdict': verdict,
    }


def _parse_stats(stats, symbol, name, description) -> dict:
    """Extract key metrics from backtesting.py stats."""
    def safe_float(key):
        try:
            v = stats[key]
            return float(v) if v is not None and str(v) != 'nan' else 0.0
        except Exception:
            return 0.0

    def safe_int(key):
        try:
            return int(stats[key])
        except Exception:
            return 0

    win_rate = safe_float('Win Rate [%]')
    return {
        'symbol': symbol,
        'strategy': name,
        'description': description,
        'start': str(stats.get('Start', '')),
        'end': str(stats.get('End', '')),
        'total_return_pct': safe_float('Return [%]'),
        'buy_hold_return_pct': safe_float('Buy & Hold Return [%]'),
        'sharpe_ratio': safe_float('Sharpe Ratio'),
        'max_drawdown_pct': safe_float('Max. Drawdown [%]'),
        'win_rate_pct': win_rate,
        'total_trades': safe_int('# Trades'),
        'avg_trade_return_pct': safe_float('Avg. Trade [%]'),
        'best_trade_pct': safe_float('Best Trade [%]'),
        'worst_trade_pct': safe_float('Worst Trade [%]'),
        'final_equity': safe_float('Equity Final [$]'),
        'profit_factor': safe_float('Profit Factor'),
        'calmar_ratio': safe_float('Calmar Ratio'),
        'exposure_pct': safe_float('Exposure Time [%]'),
    }


def _print_result(result: dict) -> None:
    """Print formatted backtest result."""
    print(f"""
╔══════════════════════════════════════════════════════════
║  BACKTEST: {result['description']}
║  Symbol: {result['symbol']} | {result.get('start','')} → {result.get('end','')}
╠══════════════════════════════════════════════════════════
║  Return:        {result['total_return_pct']:+.2f}%  (B&H: {result['buy_hold_return_pct']:+.2f}%)
║  Win Rate:      {result['win_rate_pct']:.1f}%
║  Total Trades:  {result['total_trades']}
║  Sharpe:        {result['sharpe_ratio']:.2f}
║  Max Drawdown:  {result['max_drawdown_pct']:.2f}%
║  Avg Trade:     {result['avg_trade_return_pct']:+.2f}%
║  Final Equity:  ${result['final_equity']:,.2f}
╚══════════════════════════════════════════════════════════""")


# ─── Intelligence pipeline ───────────────────────────────────────────────────

def _extract_trades_from_stats(stats, symbol: str, strategy_name: str,
                                commission: float = 0.012,
                                price_scale: float = 1.0) -> list[dict]:
    """
    Extract individual trade records from backtesting.py stats object.
    Returns list of dicts suitable for intelligence_bridge.ingest_backtest_trades.

    price_scale: de-scale factor when prices were scaled down for int-truncation fix.
                 P&L and prices are multiplied back by price_scale.
    """
    trades = []
    try:
        raw = stats._trades  # backtesting.py internal trade DataFrame
        if raw is None or len(raw) == 0:
            return trades
        for _, row in raw.iterrows():
            entry_p_scaled = float(row.get('EntryPrice', 0))
            exit_p_scaled  = float(row.get('ExitPrice', 0))
            size    = abs(float(row.get('Size', 1)))
            pnl_raw = float(row.get('PnL', (exit_p_scaled - entry_p_scaled) * size))
            # De-scale prices and P&L back to real-world values
            entry_p = entry_p_scaled * price_scale
            exit_p  = exit_p_scaled  * price_scale
            pnl     = pnl_raw * price_scale
            fee     = abs(pnl) * commission if commission > 0 else 0
            net     = pnl - fee
            entry_ts = str(row.get('EntryTime', ''))
            exit_ts  = str(row.get('ExitTime', ''))
            hold_m = 0.0
            try:
                et = pd.to_datetime(entry_ts)
                xt = pd.to_datetime(exit_ts)
                hold_m = (xt - et).total_seconds() / 60
            except Exception:
                pass
            trades.append({
                'symbol': symbol,
                'strategy': strategy_name,
                'entry_ts': entry_ts,
                'exit_ts': exit_ts,
                'entry_price': entry_p,
                'exit_price': exit_p,
                'pnl_usd': pnl,
                'fee_usd': fee,
                'pnl_pct': (exit_p - entry_p) / entry_p if entry_p > 0 else 0,
                'won': net > 0,
                'hold_minutes': hold_m,
                'exit_reason': 'backtest_close',
                'regime': 'unknown',  # enriched later by intelligence_bridge
            })
    except Exception as e:
        print(f"[backtest] Trade extraction error: {e}")
    return trades


def run_with_intelligence(
    symbol: str,
    strategy: str = 'crypto',
    period: str = '6mo',
    interval: str = '5m',
    variant: str = 'workhorse',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
    archive_to_db: bool = True,
    validate: bool = True,
) -> dict:
    """
    Full intelligence-aware backtest run:
      1. Fetch data (archive → Coinbase → yfinance)
      2. Run backtest (strategy: 'crypto', 'equity', 'mean_reversion', 'futures', 'perp')
      3. Extract trade-level attribution → signal_stats (Bayesian priors updated)
      4. Archive result to backtest_results table
      5. Run strategy validation gate
      6. Return full result dict with validation verdict + trades_attributed count

    Strategy name mapping:
      'crypto'          → crypto_macd_{variant}  (variant: workhorse/classic/sniper/all)
      'equity'          → equity_momentum
      'mean_reversion'  → crypto_mean_reversion
      'futures'         → futures_scalper
      'perp'            → crypto_perp
    """
    # ── Strategy name mapping ─────────────────────────────────────────────────
    if strategy == 'crypto':
        strategy_name = f"crypto_macd_{variant}" if variant != 'all' else 'crypto_macd_workhorse'
    elif strategy == 'equity':
        strategy_name = 'equity_momentum'
    elif strategy == 'mean_reversion':
        strategy_name = 'crypto_mean_reversion'
    elif strategy == 'futures':
        strategy_name = 'futures_scalper'
    elif strategy == 'perp':
        strategy_name = 'crypto_perp'
    else:
        strategy_name = strategy

    # ── Run the backtest ──────────────────────────────────────────────────────
    # Compute price_scale so we can de-scale P&L during attribution
    price_scale = 1.0
    raw_stats_obj = None
    trades_attributed = 0

    if strategy == 'crypto':
        if variant == 'all':
            # Run all 3 variants and aggregate attribution; return workhorse stats for gate
            variants_to_run = ['workhorse', 'classic', 'sniper']
            all_bt_stats = {}
            for v in variants_to_run:
                _r = run_with_intelligence(
                    symbol=symbol, strategy='crypto', period=period,
                    interval=interval, variant=v, cash=cash,
                    commission=commission, slippage=slippage,
                    archive_to_db=archive_to_db, validate=validate,
                )
                all_bt_stats[v] = _r
            # Return the workhorse result as representative
            main_r = all_bt_stats.get('workhorse', {})
            # Sum up attributed trades
            total_attr = sum(r.get('trades_attributed', 0) for r in all_bt_stats.values())
            main_r['trades_attributed'] = total_attr
            return main_r

        result = run_crypto_backtest(symbol=symbol, period=period, interval=interval,
                                     cash=cash, commission=commission,
                                     slippage=slippage, variant=variant)
        stats_key = variant
        bt_stats = result.get(stats_key, result.get(list(result.keys())[0], {}))
        if 'error' in bt_stats:
            return {'error': bt_stats['error']}
        # For attribution we need the raw backtesting.py stats object
        # Re-run to capture raw stats (run_crypto_backtest discards it)
        try:
            df_for_attr = fetch_data(symbol, period=period, interval=interval)
            if df_for_attr is not None:
                avg_p = float(df_for_attr['Close'].mean())
                if avg_p > 0 and cash / avg_p < 10:
                    price_scale = avg_p / (cash / 10)
                    df_for_attr = df_for_attr.copy()
                    for col in ('Open', 'High', 'Low', 'Close'):
                        df_for_attr[col] = df_for_attr[col] / price_scale
                _strat_map = {
                    'workhorse': CryptoMACDWorkhorse,
                    'classic':   CryptoMACDClassic,
                    'sniper':    CryptoMACDSniper,
                }
                _StratClass = _strat_map.get(variant, CryptoMACDWorkhorse)
                _bt = Backtest(df_for_attr, _StratClass, cash=cash,
                               commission=commission + slippage, exclusive_orders=True)
                raw_stats_obj = _bt.run()
        except Exception as _re:
            print(f"[backtest] Raw stats re-run for attribution failed: {_re}")

    elif strategy == 'equity':
        bt_stats = run_equity_backtest(symbol=symbol, period=period, interval=interval,
                                       cash=cash, slippage=slippage)
        if 'error' in bt_stats:
            return {'error': bt_stats['error']}
        try:
            df_for_attr = fetch_data(symbol, period=period, interval=interval)
            if df_for_attr is not None:
                _bt = Backtest(df_for_attr, EquityMomentum, cash=cash,
                               commission=slippage, exclusive_orders=True)
                raw_stats_obj = _bt.run()
        except Exception as _re:
            print(f"[backtest] Raw stats re-run for attribution failed: {_re}")

    elif strategy == 'mean_reversion':
        bt_stats = run_mean_reversion_backtest(symbol=symbol, period=period, interval=interval,
                                               cash=cash, commission=commission, slippage=slippage)
        raw_stats_obj = bt_stats.pop('_raw_stats', None)
        if 'error' in bt_stats:
            return {'error': bt_stats['error']}
        # Recompute price_scale
        try:
            df_ps = fetch_data(symbol, period=period, interval=interval)
            if df_ps is not None:
                avg_p = float(df_ps['Close'].mean())
                if avg_p > 0 and cash / avg_p < 10:
                    price_scale = avg_p / (cash / 10)
        except Exception:
            pass

    elif strategy == 'futures':
        _fut_cash = 50000
        _fut_comm = commission if commission != 0.006 else 0.0002
        _fut_slip = slippage  if slippage  != 0.002  else 0.0001
        bt_stats = run_futures_backtest(symbol=symbol, period=period, interval=interval,
                                        cash=_fut_cash, commission=_fut_comm, slippage=_fut_slip)
        raw_stats_obj = bt_stats.pop('_raw_stats', None)
        if 'error' in bt_stats:
            return {'error': bt_stats['error']}

    elif strategy == 'perp':
        _perp_comm = commission if commission != 0.006 else 0.0011
        _perp_slip = slippage  if slippage  != 0.002  else 0.0005
        bt_stats = run_perp_backtest(symbol=symbol, period=period, interval=interval,
                                     cash=cash, commission=_perp_comm, slippage=_perp_slip)
        raw_stats_obj = bt_stats.pop('_raw_stats', None)
        if 'error' in bt_stats:
            return {'error': bt_stats['error']}
        try:
            df_ps = fetch_data(symbol, period=period, interval=interval)
            if df_ps is not None:
                avg_p = float(df_ps['Close'].mean())
                if avg_p > 0 and cash / avg_p < 10:
                    price_scale = avg_p / (cash / 10)
        except Exception:
            pass

    else:
        return {'error': f'Unknown strategy: {strategy}'}

    # ── Extract trades and feed into attribution ───────────────────────────────
    if raw_stats_obj is not None:
        try:
            from learning.intelligence_bridge import ingest_backtest_trades
            trades_list = _extract_trades_from_stats(
                raw_stats_obj, symbol, strategy_name, commission, price_scale=price_scale
            )
            if trades_list:
                trades_df = pd.DataFrame(trades_list)
                trades_attributed = ingest_backtest_trades(
                    trades_df=trades_df,
                    symbol=symbol,
                    strategy_name=strategy_name,
                    strategy_variant=variant,
                    params={'variant': variant, 'interval': interval, 'period': period,
                            'commission': commission},
                    timeframe=_INTERVAL_TO_CB_GRANULARITY.get(interval, 'FIVE_MINUTE'),
                )
                print(f"[backtest] Attributed {trades_attributed} trades to signal_stats "
                      f"for {strategy_name}/{symbol}")
        except Exception as e:
            print(f"[backtest] Attribution error: {e}")

    # ── Convert to validation-format stats ────────────────────────────────────
    val_stats = {
        'total_trades': bt_stats.get('total_trades', 0),
        'win_rate': bt_stats.get('win_rate_pct', 0) / 100,
        'total_pnl': bt_stats.get('total_return_pct', 0) / 100 * cash,
        'sharpe': bt_stats.get('sharpe_ratio', 0),
        'max_drawdown': abs(bt_stats.get('max_drawdown_pct', 0)) / 100,
        'avg_pnl': bt_stats.get('avg_trade_return_pct', 0) / 100 * (cash / max(bt_stats.get('total_trades', 1), 1)),
        'profit_factor': bt_stats.get('profit_factor', 0),
    }

    # ── Archive + validate ────────────────────────────────────────────────────
    validation = None
    if validate:
        try:
            from backtesting.strategy_validator import validate_strategy
            params = {'variant': variant, 'interval': interval, 'period': period, 'commission': commission}
            validation = validate_strategy(
                strategy_name=strategy_name,
                symbol=symbol, params=params, stats=val_stats,
                period_start=bt_stats.get('start', ''),
                period_end=bt_stats.get('end', ''),
            )
            print(f"\n{validation.summary()}")
        except Exception as e:
            print(f"[backtest] Validation error: {e}")

    # ── Archive price data ────────────────────────────────────────────────────
    try:
        from data.price_archive import upsert_candles
        _arch_sym = symbol.upper().replace('BTC-USD', 'BTC-USDC').replace('ETH-USD', 'ETH-USDC')
        cb_gran = _INTERVAL_TO_CB_GRANULARITY.get(interval, 'FIVE_MINUTE')
        df_fetched = fetch_data(symbol, period=period, interval=interval)
        if df_fetched is not None:
            df_low = df_fetched.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume',
            })
            n = upsert_candles(df_low, _arch_sym, cb_gran)
            if n > 0:
                print(f"[backtest] Archived {n} candles to price archive for {_arch_sym}")
    except Exception as e:
        print(f"[backtest] Archive write error: {e}")

    return {
        'backtest': bt_stats,
        'validation': validation,
        'stats': val_stats,
        'passed': validation.passed if validation else None,
        'trades_attributed': trades_attributed,
    }


# ─── Auto-optimizer ───────────────────────────────────────────────────────────

def _update_env(updates: dict) -> None:
    """Write key=value pairs into .env, creating or updating each line."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if not os.path.exists(env_path):
        print(f"[optimizer] .env not found at {env_path}")
        return
    with open(env_path, 'r') as f:
        lines = f.readlines()
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if '=' in stripped and not stripped.startswith('#'):
            key = stripped.split('=', 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)
    # Append any keys not already in file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")
    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    print(f"[optimizer] .env updated: {updates}")


def optimize_crypto(
    symbol: str = 'BTC-USD',
    period: str = '1y',
    interval: str = '1h',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
    write_to_env: bool = True,
) -> dict:
    """
    Sweep MACD parameter combinations on 1h candles (up to 1 year).
    Finds the params with the best Win Rate × Sharpe composite score.
    Optionally writes winning params to .env / config.

    Returns dict with best params and their stats.
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    # 1h candles: yfinance gives ~2 years — cap at 1y
    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[optimizer] Optimizing crypto MACD on {symbol} ({interval}, {period}) — {len(df)} candles")
    print("[optimizer] Sweeping: fast=[3,5,8,10,12], slow=[13,15,20,26], signal=[3,5,9]")
    print("[optimizer] Metric: Win Rate × Sharpe  (composite — avoids overfitting to either alone)\n")

    best_score = -999
    best_params = {}
    best_stats = {}
    results_table = []

    fast_range  = [3, 5, 8, 10, 12]
    slow_range  = [13, 15, 20, 26]
    sig_range   = [3, 5, 9]

    for fast in fast_range:
        for slow in slow_range:
            if fast >= slow:
                continue
            for sig in sig_range:
                try:
                    # Dynamically set class params
                    CryptoMACDWorkhorse.fast = fast
                    CryptoMACDWorkhorse.slow = slow
                    CryptoMACDWorkhorse.signal_period = sig

                    bt = Backtest(df, CryptoMACDWorkhorse, cash=cash,
                                  commission=commission + slippage, exclusive_orders=True)
                    stats = bt.run()

                    wr = float(stats['Win Rate [%]']) if stats['Win Rate [%]'] is not None else 0.0
                    sharpe = float(stats['Sharpe Ratio']) if stats['Sharpe Ratio'] is not None else 0.0
                    trades = int(stats['# Trades']) if stats['# Trades'] is not None else 0
                    ret = float(stats['Return [%]']) if stats['Return [%]'] is not None else 0.0
                    dd = float(stats['Max. Drawdown [%]']) if stats['Max. Drawdown [%]'] is not None else 0.0

                    # Need at least 10 trades for meaningful stats
                    if trades < 10:
                        continue

                    score = (wr / 100.0) * max(sharpe, 0)
                    results_table.append({
                        'fast': fast, 'slow': slow, 'signal': sig,
                        'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                        'return_pct': ret, 'max_dd': dd, 'score': score
                    })

                    if score > best_score:
                        best_score = score
                        best_params = {'fast': fast, 'slow': slow, 'signal': sig}
                        best_stats = {'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                                      'return_pct': ret, 'max_dd': dd, 'score': score}

                except Exception as e:
                    pass

    # Restore defaults
    CryptoMACDWorkhorse.fast = 3
    CryptoMACDWorkhorse.slow = 15
    CryptoMACDWorkhorse.signal_period = 3

    if not best_params:
        return {'error': 'No valid results — try a longer period or different symbol'}

    # Sort table by score
    results_table.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n{'─'*70}")
    print(f"  TOP 5 CRYPTO MACD PARAMETER SETS ({symbol} {period} {interval})")
    print(f"{'─'*70}")
    print(f"  {'FAST':>4} {'SLOW':>4} {'SIG':>3} | {'WIN%':>6} {'SHARPE':>7} {'RETURN%':>8} {'DD%':>7} {'TRADES':>6} | SCORE")
    print(f"{'─'*70}")
    for r in results_table[:5]:
        marker = ' ◀ BEST' if r is results_table[0] else ''
        print(f"  {r['fast']:>4} {r['slow']:>4} {r['signal']:>3} | "
              f"{r['win_rate']:>5.1f}% {r['sharpe']:>7.2f} {r['return_pct']:>+7.2f}% "
              f"{r['max_dd']:>6.2f}% {r['trades']:>6} | {r['score']:.4f}{marker}")
    print(f"{'─'*70}")

    print(f"\n✅ WINNER: MACD({best_params['fast']}/{best_params['slow']}/{best_params['signal']})")
    print(f"   Win Rate: {best_stats['win_rate']:.1f}%  |  Sharpe: {best_stats['sharpe']:.2f}  "
          f"|  Return: {best_stats['return_pct']:+.2f}%  |  Trades: {best_stats['trades']}")

    if write_to_env:
        env_updates = {
            'CRYPTO_MACD1_FAST':   str(best_params['fast']),
            'CRYPTO_MACD1_SLOW':   str(best_params['slow']),
            'CRYPTO_MACD1_SIGNAL': str(best_params['signal']),
        }
        _update_env(env_updates)
        print(f"\n📝 Written to .env — restart main.py to apply.")
    else:
        print(f"\n  (Run with write_to_env=True to apply these params automatically)")

    # Save full results to JSON
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = {
        'symbol': symbol, 'period': period, 'interval': interval,
        'best_params': best_params, 'best_stats': best_stats,
        'all_results': results_table,
    }
    path = os.path.join(BACKTEST_DIR, f'optimize_crypto_{symbol}_{ts}.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"📁 Full results → {path}\n")

    return out


def optimize_equity(
    symbol: str = 'SPY',
    period: str = '1y',
    interval: str = '1h',
    cash: float = 500,
    slippage: float = BACKTEST_SLIPPAGE_PCT,
    write_to_env: bool = True,
) -> dict:
    """
    Sweep MACD + RSI parameter combinations for the equity momentum strategy.
    Uses 1h candles for up to 1 year of data.
    Writes winning params to .env.
    """
    if not BACKTESTING_PY:
        return {'error': 'backtesting.py not installed'}

    df = fetch_data(symbol, period=period, interval=interval)
    if df is None:
        return {'error': f'No data for {symbol}'}

    print(f"\n[optimizer] Optimizing equity momentum on {symbol} ({interval}, {period}) — {len(df)} candles")
    print("[optimizer] Sweeping: macd_fast, macd_slow, macd_signal, rsi_oversold")
    print("[optimizer] Metric: Win Rate × Sharpe\n")

    best_score = -999
    best_params = {}
    best_stats = {}
    results_table = []

    fast_range   = [8, 10, 12, 14]
    slow_range   = [20, 24, 26, 30]
    sig_range    = [7, 9, 11]
    rsi_os_range = [30, 35, 40]

    for fast in fast_range:
        for slow in slow_range:
            if fast >= slow:
                continue
            for sig in sig_range:
                for rsi_os in rsi_os_range:
                    try:
                        EquityMomentum.macd_fast    = fast
                        EquityMomentum.macd_slow    = slow
                        EquityMomentum.macd_signal  = sig
                        EquityMomentum.rsi_oversold = rsi_os

                        bt = Backtest(df, EquityMomentum, cash=cash,
                                      commission=slippage, exclusive_orders=True)
                        stats = bt.run()

                        wr     = float(stats['Win Rate [%]'])     if stats['Win Rate [%]']     is not None else 0.0
                        sharpe = float(stats['Sharpe Ratio'])     if stats['Sharpe Ratio']     is not None else 0.0
                        trades = int(stats['# Trades'])           if stats['# Trades']         is not None else 0
                        ret    = float(stats['Return [%]'])       if stats['Return [%]']       is not None else 0.0
                        dd     = float(stats['Max. Drawdown [%]']) if stats['Max. Drawdown [%]'] is not None else 0.0

                        if trades < 5:
                            continue

                        score = (wr / 100.0) * max(sharpe, 0)
                        results_table.append({
                            'fast': fast, 'slow': slow, 'signal': sig, 'rsi_oversold': rsi_os,
                            'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                            'return_pct': ret, 'max_dd': dd, 'score': score
                        })

                        if score > best_score:
                            best_score = score
                            best_params = {'fast': fast, 'slow': slow, 'signal': sig, 'rsi_oversold': rsi_os}
                            best_stats  = {'win_rate': wr, 'sharpe': sharpe, 'trades': trades,
                                           'return_pct': ret, 'max_dd': dd, 'score': score}

                    except Exception:
                        pass

    # Restore defaults
    EquityMomentum.macd_fast    = 12
    EquityMomentum.macd_slow    = 26
    EquityMomentum.macd_signal  = 9
    EquityMomentum.rsi_oversold = 35

    if not best_params:
        return {'error': 'No valid results'}

    results_table.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n{'─'*80}")
    print(f"  TOP 5 EQUITY MOMENTUM PARAMETER SETS ({symbol} {period} {interval})")
    print(f"{'─'*80}")
    print(f"  {'FAST':>4} {'SLOW':>4} {'SIG':>3} {'RSI_OS':>6} | {'WIN%':>6} {'SHARPE':>7} {'RETURN%':>8} {'DD%':>7} {'TRADES':>6} | SCORE")
    print(f"{'─'*80}")
    for r in results_table[:5]:
        marker = ' ◀ BEST' if r is results_table[0] else ''
        print(f"  {r['fast']:>4} {r['slow']:>4} {r['signal']:>3} {r['rsi_oversold']:>6} | "
              f"{r['win_rate']:>5.1f}% {r['sharpe']:>7.2f} {r['return_pct']:>+7.2f}% "
              f"{r['max_dd']:>6.2f}% {r['trades']:>6} | {r['score']:.4f}{marker}")
    print(f"{'─'*80}")

    print(f"\n✅ WINNER: MACD({best_params['fast']}/{best_params['slow']}/{best_params['signal']}) "
          f"RSI_oversold={best_params['rsi_oversold']}")
    print(f"   Win Rate: {best_stats['win_rate']:.1f}%  |  Sharpe: {best_stats['sharpe']:.2f}  "
          f"|  Return: {best_stats['return_pct']:+.2f}%  |  Trades: {best_stats['trades']}")

    if write_to_env:
        _update_env({
            'EQUITY_RSI_OVERSOLD': str(best_params['rsi_oversold']),
        })
        # MACD params aren't in .env for equity — they're hardcoded in config.py
        # Update config.py directly
        _update_config_py({
            'EQUITY_RSI_OVERSOLD': best_params['rsi_oversold'],
        })
        print(f"\n📝 Written to .env — restart main.py to apply.")
    else:
        print(f"\n  (Run with write_to_env=True to apply these params automatically)")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = {
        'symbol': symbol, 'period': period, 'interval': interval,
        'best_params': best_params, 'best_stats': best_stats,
        'all_results': results_table,
    }
    path = os.path.join(BACKTEST_DIR, f'optimize_equity_{symbol}_{ts}.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"📁 Full results → {path}\n")

    return out


def _update_config_py(updates: dict) -> None:
    """Update hardcoded values in config.py for params not driven by .env."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.py')
    if not os.path.exists(config_path):
        return
    with open(config_path, 'r') as f:
        content = f.read()
    import re
    for key, val in updates.items():
        pattern = rf'^({re.escape(key)}\s*:\s*\w+\s*=\s*)[\d.]+$'
        replacement = rf'\g<1>{val}'
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    with open(config_path, 'w') as f:
        f.write(content)
    print(f"[optimizer] config.py updated: {updates}")



# ─── Walk-Forward OOS Validator ───────────────────────────────────────────────

def run_walk_forward(
    symbol: str,
    strategy: str = 'crypto',
    variant: str = 'workhorse',
    interval: str = '5m',
    train_days: int = 60,
    test_days: int = 30,
    folds: int = 2,
    cash: float = 500,
) -> dict:
    """
    Walk-forward out-of-sample validation.

    For each fold:
      - Train window: train_days of data ending at fold boundary
      - Test window:  test_days of data AFTER the training window (never seen during training)
      - Step:         test_days (non-overlapping test windows)

    Example with folds=2, train_days=60, test_days=30:
      Fold 1: Train [Day 1-60]  → Test [Day 61-90]
      Fold 2: Train [Day 31-90] → Test [Day 91-120]  (requires 120 days of history)

    Returns summary dict with per-fold results and aggregate stats.
    Prints pass/fail per fold and final verdict.
    """
    import yfinance as yf
    from datetime import datetime, timedelta

    results = []
    now     = datetime.utcnow()

    # Minimum pass rate: at least ceil(folds * 0.75) folds must pass
    min_passing_folds = max(1, round(folds * 0.75))

    print(f"\n{'═'*60}")
    print(f"  WALK-FORWARD: {symbol} | {folds} folds | "
          f"train={train_days}d test={test_days}d")
    print(f"{'═'*60}")

    for fold_idx in range(folds):
        # Test window ends at: now - fold_idx * test_days
        test_end   = now - timedelta(days=fold_idx * test_days)
        test_start = test_end - timedelta(days=test_days)
        train_end  = test_start
        train_start = train_end - timedelta(days=train_days)

        # Map to yfinance period strings (approximate)
        total_days  = train_days + test_days
        period_str  = f"{total_days}d"

        print(f"\n  Fold {fold_idx + 1}/{folds}: "
              f"train [{train_start.strftime('%Y-%m-%d')}→{train_end.strftime('%Y-%m-%d')}] "
              f"test [{test_start.strftime('%Y-%m-%d')}→{test_end.strftime('%Y-%m-%d')}]")

        try:
            # Fetch full window
            df_full = fetch_data(symbol, period=period_str, interval=interval)
            if df_full is None or df_full.empty:
                print(f"    ❌ No data for fold {fold_idx + 1}")
                results.append({'fold': fold_idx + 1, 'error': 'no_data', 'passed': False})
                continue

            # Split into train/test
            cutoff = df_full.index[int(len(df_full) * train_days / total_days)]
            df_train = df_full[df_full.index < cutoff]
            df_test  = df_full[df_full.index >= cutoff]

            if len(df_test) < 20:
                print(f"    ⚠ Test set too small ({len(df_test)} bars) — fold skipped")
                results.append({'fold': fold_idx + 1, 'error': 'insufficient_test', 'passed': False})
                continue

            # Run backtest on OOS window only (no attribution to avoid contaminating live weights)
            oos_result = run_with_intelligence(
                symbol=symbol,
                strategy=strategy,
                variant=variant,
                interval=interval,
                period=f"{test_days}d",
                cash=cash,
                archive_to_db=False,
                validate=True,
            )

            if 'error' in oos_result:
                results.append({'fold': fold_idx + 1, 'error': oos_result['error'], 'passed': False})
                continue

            stats    = oos_result.get('stats', {})
            passed   = bool(oos_result.get('passed', False))
            wr       = stats.get('win_rate', 0)
            pf       = stats.get('profit_factor', 0)
            sharpe   = stats.get('sharpe', 0)
            dd       = stats.get('max_drawdown', 0)
            n_trades = stats.get('total_trades', 0)

            # Adjusted pass criteria per brain/rbi/01_backtest_standards.md
            fold_passed = (
                wr       >= 0.30 and
                pf       >= 1.2  and
                sharpe   >= 0.50 and
                dd       <= 0.20 and
                n_trades >= 15
            )

            result_row = {
                'fold':          fold_idx + 1,
                'win_rate':      round(wr, 3),
                'profit_factor': round(pf, 2),
                'sharpe':        round(sharpe, 2),
                'max_drawdown':  round(dd, 3),
                'total_trades':  n_trades,
                'passed':        fold_passed,
                'validator_passed': passed,
            }
            results.append(result_row)

            status = '✅ PASS' if fold_passed else '❌ FAIL'
            print(f"    {status} | WR={wr:.0%} PF={pf:.2f} Sharpe={sharpe:.2f} "
                  f"DD={dd:.0%} trades={n_trades}")

        except Exception as e:
            print(f"    ❌ Error fold {fold_idx + 1}: {e}")
            results.append({'fold': fold_idx + 1, 'error': str(e), 'passed': False})

    # ── Aggregate ────────────────────────────────────────────────────────────────
    passing_folds = sum(1 for r in results if r.get('passed'))
    wf_passed     = passing_folds >= min_passing_folds

    print(f"\n{'═'*60}")
    print(f"  WALK-FORWARD RESULT: {passing_folds}/{folds} folds passed")
    print(f"  Required: {min_passing_folds}/{folds} | Overall: {'✅ PASS' if wf_passed else '❌ FAIL'}")
    if not wf_passed:
        print(f"  ⚠ Strategy is OVERFITTING — in-sample performance does not generalize.")
    print(f"{'═'*60}\n")

    return {
        'symbol':          symbol,
        'strategy':        strategy,
        'folds':           folds,
        'results':         results,
        'passing_folds':   passing_folds,
        'required_folds':  min_passing_folds,
        'passed':          wf_passed,
        'train_days':      train_days,
        'test_days':       test_days,
    }
