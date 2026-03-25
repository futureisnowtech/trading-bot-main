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
from datetime import datetime
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import add_all_indicators, get_fib_levels, fib_confluence

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

    For Coinbase-traded pairs (symbol contains '-USDC' or '-USD' matching a CB pair),
    we prefer the Coinbase Advanced Trade API so backtests use the actual price feed
    the live bot trades on.  Falls back to yfinance if Coinbase credentials are absent
    or the fetch fails (e.g. for equity symbols).
    """
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
    slippage: float = 0.002,
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
    slippage: float = 0.002,
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


def run_backtest_oos_split(
    symbol: str,
    strategy: str = 'crypto',
    period: str = '6mo',
    interval: str = '5m',
    cash: float = 500,
    commission: float = 0.006,
    slippage: float = 0.002,
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
    slippage: float = 0.002,
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
    slippage: float = 0.002,
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
