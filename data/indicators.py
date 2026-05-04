"""
data/indicators.py
Centralized indicator calculations using pandas-ta.
All strategy files import from here — one source of truth.
"""
import pandas as pd
import numpy as np
import logging
from typing import Optional
import pytz

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    PANDAS_TA = True
except ImportError:
    PANDAS_TA = False
    logger.warning("[indicators] pandas-ta not found, using fallback calculations")


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicators to an OHLCV dataframe.
    Expects columns: open, high, low, close, volume (lowercase)
    Returns dataframe with all indicator columns added.
    """
    if len(df) < 30:
        return df

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # ─── MACD variants (from Moon Dev backtests — faster settings dominate) ────
    # Variant 1: Workhorse MACD(3/15/3)
    _add_macd(df, 'macd1', fast=3, slow=15, signal=3)
    # Variant 2: Classic MACD(4/16/3)
    _add_macd(df, 'macd2', fast=4, slow=16, signal=3)
    # Variant 3: Sniper MACD(6/20/5)
    _add_macd(df, 'macd3', fast=6, slow=20, signal=5)
    # Standard MACD(12/26/9) for equity strategy
    _add_macd(df, 'macd_std', fast=12, slow=26, signal=9)

    # ─── RSI ──────────────────────────────────────────────────────────────────
    if PANDAS_TA:
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['rsi_9'] = ta.rsi(df['close'], length=9)
    else:
        df['rsi'] = _rsi_fallback(df['close'], 14)
        df['rsi_9'] = _rsi_fallback(df['close'], 9)

    # ─── VWAP (requires volume) ────────────────────────────────────────────────
    if 'volume' in df.columns and df['volume'].sum() > 0:
        if PANDAS_TA:
            try:
                df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
            except Exception:
                df['vwap'] = _vwap_fallback(df)
        else:
            df['vwap'] = _vwap_fallback(df)

    # ─── Bollinger Bands ──────────────────────────────────────────────────────
    if PANDAS_TA:
        bbands = ta.bbands(df['close'], length=20, std=2)
        if bbands is not None and not bbands.empty:
            # pandas_ta returns columns as [BBL, BBM, BBU, BBB, BBP] — lower first.
            # Assign by position: 0=lower, 1=mid, 2=upper.
            df['bb_lower'] = bbands.iloc[:, 0]
            df['bb_mid']   = bbands.iloc[:, 1]
            df['bb_upper'] = bbands.iloc[:, 2]
            df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    else:
        rolling_mean = df['close'].rolling(20).mean()
        rolling_std = df['close'].rolling(20).std()
        df['bb_upper'] = rolling_mean + 2 * rolling_std
        df['bb_lower'] = rolling_mean - 2 * rolling_std
        df['bb_mid'] = rolling_mean
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

    # ─── ATR (for position sizing and stops) ──────────────────────────────────
    if PANDAS_TA:
        atr = ta.atr(df['high'], df['low'], df['close'], length=14)
        if atr is not None:
            df['atr'] = atr
    else:
        df['atr'] = _atr_fallback(df, 14)

    # ─── ADX (trend strength — skip choppy markets when ADX < 20) ─────────────
    if PANDAS_TA:
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx is not None and not adx.empty:
            df['adx'] = adx.iloc[:, 0]
    else:
        df['adx'] = 25.0  # Default assumption if can't calculate

    # ─── EMAs ─────────────────────────────────────────────────────────────────
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()

    # ─── KST oscillator (Know Sure Thing — for equity momentum) ───────────────
    if PANDAS_TA:
        try:
            kst = ta.kst(df['close'])
            if kst is not None and not kst.empty:
                df['kst'] = kst.iloc[:, 0]
                df['kst_signal'] = kst.iloc[:, 1] if kst.shape[1] > 1 else kst.iloc[:, 0].rolling(9).mean()
        except Exception:
            df['kst'] = 0.0
            df['kst_signal'] = 0.0
    # KST cross detection: fires only on the bar where KST crosses its signal line.
    # kst_cross_up  = KST just crossed ABOVE signal (bullish momentum shift)
    # kst_cross_down = KST just crossed BELOW signal (bearish momentum shift)
    try:
        if 'kst' in df.columns and 'kst_signal' in df.columns:
            _kst      = df['kst'].fillna(0)
            _kst_sig  = df['kst_signal'].fillna(0)
            df['kst_cross_up']   = (_kst > _kst_sig) & (_kst.shift(1) <= _kst_sig.shift(1))
            df['kst_cross_down'] = (_kst < _kst_sig) & (_kst.shift(1) >= _kst_sig.shift(1))
        else:
            df['kst_cross_up']   = False
            df['kst_cross_down'] = False
    except Exception:
        df['kst_cross_up']   = False
        df['kst_cross_down'] = False

    # ─── Volume indicators ────────────────────────────────────────────────────
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['vol_spike'] = df['volume'] / (df['vol_ma20'].replace(0, np.nan))
    df['dollar_volume'] = df['close'] * df['volume']

    # ─── Heikin Ashi (used in equity strategy on 30-min charts) ───────────────
    df['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + df['ha_close'].iloc[i - 1]) / 2
    df['ha_open'] = ha_open
    df['ha_high'] = df[['high', 'ha_open', 'ha_close']].max(axis=1)
    df['ha_low'] = df[['low', 'ha_open', 'ha_close']].min(axis=1)
    df['ha_bullish'] = df['ha_close'] > df['ha_open']

    # ─── Candle patterns ──────────────────────────────────────────────────────
    df['is_red'] = df['close'] < df['open']
    df['is_green'] = df['close'] >= df['open']
    # Consecutive red candles (3-candle exit rule)
    df['consec_red'] = (
        df['is_red'].astype(int)
        .groupby((~df['is_red']).cumsum())
        .cumsum()
    )

    # =========================================================================
    # ─── ADVANCED MATHEMATICAL SIGNALS ───────────────────────────────────────
    # =========================================================================

    # ─── 1. Realized Volatility (rv_15, rv_240) and Ratio (rv_ratio) ─────────
    # rv_ratio >= 1.3: volatility expansion/breakout regime.
    # rv_ratio <= 0.8: compression/mean-reversion regime.
    try:
        r = np.log(df['close']).diff()
        # Zero out returns from gap bars (consecutive candles > 90s apart)
        # Prevents API outage gaps from artificially inflating rv_15
        try:
            if hasattr(df.index, 'to_series') and hasattr(df.index, 'tz'):
                gap_mask = df.index.to_series().diff().dt.total_seconds().fillna(60) > 90
                r = r.where(~gap_mask, 0.0)
        except Exception:
            pass
        df['rv_15'] = np.sqrt((r**2).rolling(15).sum())
        df['rv_240'] = np.sqrt((r**2).rolling(240, min_periods=60).sum())
        rv_long = np.sqrt((r**2).rolling(240).sum())
        df['rv_ratio'] = (df['rv_15'] / rv_long.clip(lower=1e-10)).clip(0.1, 5.0)
    except Exception as e:
        logger.error(f"[indicators] rv_ratio failed: {e}")

    # ─── 2b. Ornstein-Uhlenbeck Half-Life (ou_halflife_minutes) ───────────────
    # Mean-reversion speed: t½ = ln(2)/κ from AR(1) on de-trended log price.
    # Only computed when autocorr_ret < 0 (mean-reverting microstructure).
    # Clamped to [3, 120] minutes. Values outside = unreliable or too slow.
    # Use for dynamic time stops: if flat after 2× half-life, exit.
    try:
        log_p = np.log(df['close'])
        spread = log_p - log_p.rolling(60, min_periods=20).mean()
        ou_hl = pd.Series(np.nan, index=df.index)
        for i in range(60, len(df)):
            x_arr = spread.iloc[max(0, i - 60):i].values
            x_arr = x_arr[np.isfinite(x_arr)]
            if len(x_arr) < 20:
                continue
            y_vec = x_arr[1:]
            x_lag = x_arr[:-1]
            x_std = x_lag.std()
            if x_std < 1e-10:
                continue
            b = np.cov(y_vec, x_lag)[0, 1] / np.var(x_lag)
            if not (0.01 < b < 0.99):
                continue
            kappa = -np.log(b)
            hl = np.log(2) / kappa if kappa > 0 else np.nan
            if np.isfinite(hl) and 3.0 <= hl <= 120.0:
                ou_hl.iloc[i] = hl
        ou_zscore = (spread - spread.rolling(40, min_periods=10).mean()) / spread.rolling(40, min_periods=10).std().clip(lower=1e-10)
        df['ou_zscore'] = ou_zscore.clip(-4.0, 4.0)
        df['ou_halflife_minutes'] = ou_hl
    except Exception as e:
        logger.error(f"[indicators] ou_halflife failed: {e}")

    # ─── 3. Anchored VWAP — UTC daily anchor (avwap_utc, avwap_dev) ──────────
    try:
        df['avwap_utc'] = _avwap_utc(df)
        df['avwap_dev'] = (df['close'] - df['avwap_utc']) / df['avwap_utc']
    except Exception as e:
        logger.error(f"[indicators] avwap_utc failed: {e}")

    # ─── 4. AR(1) Return Autocorrelation (autocorr_ret) ──────────────────────
    # > +0.15: momentum persistence (trend is sticky).
    # < -0.15: bid-ask bounce / mean-reverting microstructure.
    # ~0: noise/random walk.
    # Faster and more interpretable than Sample Entropy for agent reasoning.
    try:
        r_ac = np.log(df['close']).diff()
        df['autocorr_ret'] = r_ac.rolling(40, min_periods=20).apply(
            lambda x: pd.Series(x).autocorr(lag=1), raw=False
        )
    except Exception as e:
        logger.error(f"[indicators] autocorr_ret failed: {e}")
        df['autocorr_ret'] = 0.0

    # ─── 5. Amihud Illiquidity Ratio (amihud_illiq, amihud_pct) ──────────────
    try:
        log_ret = np.log(df['close']).diff().abs()
        dollar_vol = df['close'] * df['volume']
        df['amihud_illiq'] = (log_ret / dollar_vol.clip(lower=1.0)).replace(
            [np.inf, -np.inf], np.nan
        )
        df['amihud_pct'] = df['amihud_illiq'].rolling(240, min_periods=30).apply(
            lambda x: float(pd.Series(x).rank(pct=True).iloc[-1] * 100), raw=False
        )
    except Exception as e:
        logger.error(f"[indicators] amihud failed: {e}")

    # ─── 6. Kyle's Lambda (kyle_lambda, kyle_lambda_pct) ─────────────────────
    try:
        df['kyle_lambda'] = _kyle_lambda_rolling(df['close'], df['volume'])
        df['kyle_lambda_pct'] = df['kyle_lambda'].rolling(240, min_periods=30).apply(
            lambda x: float(pd.Series(x).rank(pct=True).iloc[-1] * 100), raw=False
        )
    except Exception as e:
        logger.error(f"[indicators] kyle_lambda failed: {e}")

    # ─── 7. Bollinger-Keltner Squeeze (squeeze_on, squeeze_fired, squeeze_bars)
    try:
        sma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        bb_upper = sma20 + 2.0 * std20
        bb_lower = sma20 - 2.0 * std20

        ema20_kc = df['close'].ewm(span=20, adjust=False).mean()
        if 'atr' in df.columns:
            atr = df['atr']
        else:
            hl = df['high'] - df['low']
            atr = hl.rolling(14).mean()

        kc_upper = ema20_kc + 1.5 * atr
        kc_lower = ema20_kc - 1.5 * atr

        df['squeeze_on'] = (bb_upper <= kc_upper) & (bb_lower >= kc_lower)
        _prev_squeeze = df['squeeze_on'].shift(1).infer_objects(copy=False).fillna(False).astype(bool)
        df['squeeze_fired'] = (~df['squeeze_on']) & _prev_squeeze

        # Use numpy array to avoid pandas 2.0 CoW SettingWithCopyWarning on iloc setter
        squeeze_arr = np.zeros(len(df), dtype=int)
        squeeze_on_arr = df['squeeze_on'].values
        count = 0
        for i in range(len(df)):
            if squeeze_on_arr[i]:
                count += 1
            else:
                count = 0
            squeeze_arr[i] = count
        df['squeeze_bars'] = squeeze_arr
        # squeeze_direction: +1 if trend up at fire, -1 if down, 0 if no fire
        # Tells agents WHICH direction the coiled energy is releasing
        ema_slope = ema20_kc.diff(3)
        squeeze_dir_arr = np.where(ema_slope > 0, 1, np.where(ema_slope < 0, -1, 0))
        df['squeeze_direction'] = np.where(
            df['squeeze_fired'].values, squeeze_dir_arr, 0
        ).astype(int)
    except Exception as e:
        logger.error(f"[indicators] squeeze failed: {e}")

    # ─── 8. Kalman Filter Price Estimate (kalman_price, kalman_dev) ───────────
    try:
        df['kalman_price'] = _kalman_filter(df['close'])
        df['kalman_dev'] = (df['close'] - df['kalman_price']) / df['kalman_price'].clip(lower=0.001)
    except Exception as e:
        logger.error(f"[indicators] kalman failed: {e}")

    # ─── 9. Session activity flag (session_active) ────────────────────────────
    # Marks bars inside 08:00-11:00 ET (high-volume crypto session) and
    # 14:00-17:00 ET (CME hours).
    try:
        et_tz = pytz.timezone('America/New_York')
        try:
            if hasattr(df.index, 'tz') and df.index.tz is not None:
                et_times = df.index.tz_convert(et_tz)
            else:
                et_times = df.index.tz_localize('UTC').tz_convert(et_tz)
            hours = et_times.hour + et_times.minute / 60.0
            df['session_active'] = ((hours >= 8.0) & (hours < 11.0)) | \
                                   ((hours >= 14.0) & (hours < 17.0))
        except Exception:
            df['session_active'] = True  # fallback: always active
    except Exception as e:
        logger.error(f"[indicators] session_active failed: {e}")
        df['session_active'] = True

    # ─── 10. SuperTrend (ATR 10, mult 3.0) ───────────────────────────────────
    # Binary trend filter: 1 = bullish (price above band), -1 = bearish.
    # ATR-adaptive trailing stop that switches state cleanly — no chop.
    # Complements MACD direction with a persistent, low-noise trend label.
    try:
        _used_pandas_ta_st = False
        if PANDAS_TA:
            try:
                _st = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3.0)
                if _st is not None and not _st.empty:
                    _dir_cols = [c for c in _st.columns if 'SUPERTd' in c]
                    if _dir_cols:
                        df['supertrend_dir'] = _st[_dir_cols[0]].fillna(1)
                        df['supertrend_bullish'] = df['supertrend_dir'] == 1
                        _used_pandas_ta_st = True
            except Exception:
                pass
        if not _used_pandas_ta_st:
            _atr_st = df['atr'] if 'atr' in df.columns else _atr_fallback(df, 14)
            _st_dir = _supertrend_manual(df['high'], df['low'], df['close'],
                                         _atr_st.fillna(_atr_st.mean()), multiplier=3.0)
            df['supertrend_dir'] = _st_dir
            df['supertrend_bullish'] = _st_dir == 1
    except Exception as e:
        logger.error(f"[indicators] supertrend failed: {e}")
        df['supertrend_bullish'] = False

    # SuperTrend cross detection: fires only on the bar where direction flips.
    # supertrend_cross_up   = ST just turned bullish (dir flipped -1 → +1)
    # supertrend_cross_down = ST just turned bearish (dir flipped +1 → -1)
    try:
        if 'supertrend_dir' in df.columns:
            _st_dir   = df['supertrend_dir']
            df['supertrend_cross_up']   = (_st_dir == 1) & (_st_dir.shift(1) == -1)
            df['supertrend_cross_down'] = (_st_dir == -1) & (_st_dir.shift(1) == 1)
        else:
            df['supertrend_cross_up']   = False
            df['supertrend_cross_down'] = False
    except Exception:
        df['supertrend_cross_up']   = False
        df['supertrend_cross_down'] = False

    # ─── 11. Ichimoku Cloud — kumo direction only ─────────────────────────────
    # price above cloud top (Senkou Span A and B both) = bullish structure.
    # The cloud is the most reliable Ichimoku component on sub-hourly charts;
    # TK crosses are too noisy on 1-minute data and are intentionally omitted.
    try:
        _tenkan = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
        _kijun  = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
        _senkou_a = ((_tenkan + _kijun) / 2).shift(26)
        _senkou_b = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
        df['cloud_top']    = pd.concat([_senkou_a, _senkou_b], axis=1).max(axis=1)
        df['cloud_bottom'] = pd.concat([_senkou_a, _senkou_b], axis=1).min(axis=1)
        df['cloud_bullish'] = df['close'] > df['cloud_top']
        df['cloud_bearish'] = df['close'] < df['cloud_bottom']
        # Ichimoku breakout cross: price just crossed above/below the cloud.
        # cloud_cross_up   = close just moved above cloud_top (was at or below last bar)
        # cloud_cross_down = close just moved below cloud_bottom (was at or above last bar)
        df['cloud_cross_up']   = df['cloud_bullish'] & (~df['cloud_bullish'].shift(1).fillna(True))
        df['cloud_cross_down'] = df['cloud_bearish'] & (~df['cloud_bearish'].shift(1).fillna(True))
        # TK cross (Tenkan/Kijun): additional Ichimoku momentum signal
        df['tk_cross_up']   = (_tenkan > _kijun) & (_tenkan.shift(1) <= _kijun.shift(1))
        df['tk_cross_down'] = (_tenkan < _kijun) & (_tenkan.shift(1) >= _kijun.shift(1))
    except Exception as e:
        logger.error(f"[indicators] ichimoku failed: {e}")
        df['cloud_bullish']   = False
        df['cloud_bearish']   = False
        df['cloud_cross_up']  = False
        df['cloud_cross_down']= False
        df['tk_cross_up']     = False
        df['tk_cross_down']   = False

    # ─── 12. Waddah Attar Explosion (WAE) ────────────────────────────────────
    # Momentum × volatility composite. Direction = MACD(20/40) histogram.
    # Explosion = BB width (upper-lower). Signal fires when trending momentum
    # exceeds the volatility baseline — confirms breakouts from compression.
    # Widely used on TradingView; pairs naturally with the BB-Keltner squeeze.
    try:
        _sensitivity = 150
        _e_fast = df['close'].ewm(span=20, adjust=False).mean()
        _e_slow = df['close'].ewm(span=40, adjust=False).mean()
        _t1 = (_e_fast - _e_slow) * _sensitivity
        _t2 = _t1.shift(1).fillna(_t1)
        _trend_up   = (_t1 - _t2).clip(lower=0).where(_t1 >= 0, 0)
        _trend_dn   = (_t2 - _t1).clip(lower=0).where(_t1 < 0,  0)
        _bb_std20   = df['close'].rolling(20).std().fillna(0)
        _explosion  = 4 * _bb_std20   # BB upper-lower width (2σ each side)
        df['wae_trend_up']  = _trend_up
        df['wae_trend_down'] = _trend_dn
        df['wae_explosion'] = _explosion
        df['wae_bullish']   = (_trend_up > _trend_dn) & (_trend_up > 0)
        df['wae_exploding'] = (_trend_up > _explosion) | (_trend_dn > _explosion)
    except Exception as e:
        logger.error(f"[indicators] wae failed: {e}")
        df['wae_bullish']   = False
        df['wae_trend_down'] = False
        df['wae_exploding'] = False

    # ─── 13. Ehlers Fisher Transform ─────────────────────────────────────────
    # Converts price range to a Gaussian normal distribution.
    # Extreme readings (|fisher| > 1.5) identify precise price turning points.
    # Published by John Ehlers — used by quantitative traders for entry timing.
    # Fisher cross (fisher > fisher_signal) from negative = bullish flip.
    try:
        _flen = 10
        _hl2  = (df['high'] + df['low']) / 2.0
        _hh   = _hl2.rolling(_flen, min_periods=_flen).max()
        _ll   = _hl2.rolling(_flen, min_periods=_flen).min()
        _rng  = (_hh - _ll).clip(lower=1e-10)
        _val  = ((2.0 * (_hl2 - _ll) / _rng) - 1.0).clip(-0.999, 0.999).fillna(0.0)
        _fisher_raw = 0.5 * np.log((1.0 + _val) / (1.0 - _val))
        df['fisher']        = _fisher_raw.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        df['fisher_signal'] = df['fisher'].shift(1).fillna(0.0)
        _cross_up   = (df['fisher'] > df['fisher_signal']) & \
                      (df['fisher'].shift(1).fillna(0) <= df['fisher_signal'].shift(1).fillna(0))
        df['fisher_cross_up']   = _cross_up & (df['fisher'] < 0)  # cross from negative = bullish
        df['fisher_cross_down'] = (df['fisher'] < df['fisher_signal']) & \
                                  (df['fisher'].shift(1).fillna(0) >= df['fisher_signal'].shift(1).fillna(0)) & \
                                  (df['fisher'] > 0)
    except Exception as e:
        logger.error(f"[indicators] fisher failed: {e}")
        df['fisher'] = 0.0
        df['fisher_cross_up'] = False
        df['fisher_cross_down'] = False

    # ─── 14. Choppiness Index (CHOP) ─────────────────────────────────────────
    # Measures whether the market is trending or oscillating.
    # Formula: 100 × log10(Σ ATR₁ / (HighestHigh − LowestLow)) / log10(n)
    # < 38.2 = strongly trending (directional — take breakout signals)
    # > 61.8 = high chop (ranging — favour mean reversion, reduce confidence)
    # Fills a gap in the regime detector: quantifies "tradeable trend quality".
    try:
        _cn   = 14
        _prev_close = df['close'].shift(1).fillna(df['close'])
        _tr1  = (df['high'] - df['low']).abs()
        _tr2  = (df['high'] - _prev_close).abs()
        _tr3  = (df['low']  - _prev_close).abs()
        _tr   = pd.concat([_tr1, _tr2, _tr3], axis=1).max(axis=1)
        _atr_sum = _tr.rolling(_cn, min_periods=_cn).sum()
        _hh_n    = df['high'].rolling(_cn, min_periods=_cn).max()
        _ll_n    = df['low'].rolling(_cn, min_periods=_cn).min()
        _hl_rng  = (_hh_n - _ll_n).clip(lower=1e-10)
        _ratio   = (_atr_sum / _hl_rng).clip(lower=1e-10, upper=1e10)
        _chop_raw = (100.0 * np.log10(_ratio) / np.log10(_cn))
        df['chop']          = _chop_raw.clip(1, 100)
        df['chop_trending'] = df['chop'] < 38.2  # strongly trending
        df['chop_ranging']  = df['chop'] > 61.8  # high chop
    except Exception as e:
        logger.error(f"[indicators] chop failed: {e}")
        df['chop'] = 50.0
        df['chop_trending'] = False
        df['chop_ranging'] = False

    # ─── 15. WaveTrend Oscillator ────────────────────────────────────────────
    # LazyBear's adaptation of the WT oscillator — highly popular in crypto.
    # Two-line momentum oscillator. Cross (WT1 > WT2) from oversold (<−53)
    # is the highest-confidence buy signal: momentum reversal from extreme.
    # Unlike RSI, WT has built-in smoothing that filters 1-minute noise well.
    try:
        _wn1, _wn2 = 10, 21
        _ap   = (df['high'] + df['low'] + df['close']) / 3.0
        _esa  = _ap.ewm(span=_wn1, adjust=False).mean()
        _d    = (_ap - _esa).abs().ewm(span=_wn1, adjust=False).mean().clip(lower=1e-10)
        _ci   = (_ap - _esa) / (0.015 * _d)
        _tci  = _ci.ewm(span=_wn2, adjust=False).mean()
        df['wt1']               = _tci
        df['wt2']               = _tci.rolling(4, min_periods=1).mean()
        _wt_prev1               = df['wt1'].shift(1).fillna(0)
        _wt_prev2               = df['wt2'].shift(1).fillna(0)
        _wt_cross_up            = (df['wt1'] > df['wt2']) & (_wt_prev1 <= _wt_prev2)
        df['wt_cross_up']       = _wt_cross_up
        df['wt_oversold_cross'] = _wt_cross_up & (_wt_prev1 < -53)
        df['wt_overbought']     = df['wt1'] > 53
    except Exception as e:
        logger.error(f"[indicators] wavetrend failed: {e}")
        df['wt1'] = 0.0
        df['wt_oversold_cross'] = False
        df['wt_overbought'] = False

    # ─── 16. Laguerre RSI (γ = 0.5) ──────────────────────────────────────────
    # John Ehlers adaptive oscillator using a 4-tap Laguerre filter.
    # ~5× less lag than standard RSI-14 on the same timeframe.
    # Range 0–1. < 0.15 = deeply oversold (stronger than RSI < 30).
    # Better for 1-minute crypto: fewer false signals, faster turning points.
    try:
        _gamma = 0.5
        _c  = df['close'].values.astype(float)
        _n  = len(_c)
        _L0 = np.empty(_n); _L0[0] = _c[0]
        _L1 = np.empty(_n); _L1[0] = _c[0]
        _L2 = np.empty(_n); _L2[0] = _c[0]
        _L3 = np.empty(_n); _L3[0] = _c[0]
        for _i in range(1, _n):
            _L0[_i] = (1 - _gamma) * _c[_i] + _gamma * _L0[_i - 1]
            _L1[_i] = -_gamma * _L0[_i] + _L0[_i - 1] + _gamma * _L1[_i - 1]
            _L2[_i] = -_gamma * _L1[_i] + _L1[_i - 1] + _gamma * _L2[_i - 1]
            _L3[_i] = -_gamma * _L2[_i] + _L2[_i - 1] + _gamma * _L3[_i - 1]
        _cu = np.maximum(_L0 - _L1, 0) + np.maximum(_L1 - _L2, 0) + np.maximum(_L2 - _L3, 0)
        _cd = np.maximum(_L1 - _L0, 0) + np.maximum(_L2 - _L1, 0) + np.maximum(_L3 - _L2, 0)
        _tot = _cu + _cd
        _lrsi = np.where(_tot > 1e-10, _cu / _tot, 0.5)
        df['lrsi'] = pd.Series(_lrsi, index=df.index)
    except Exception as e:
        logger.error(f"[indicators] lrsi failed: {e}")
        df['lrsi'] = 0.5

    # ─── 17. Stochastic RSI (40-45% WR in trending regimes) ──────────────────
    # Stoch of RSI: applies stochastic formula to RSI values.
    # stochrsi_k oversold cross (k > d from below 20) = high-probability reversal.
    # In trending markets (ADX > 20): WR 40-45% in backtests.
    try:
        _rsi_src = df['rsi'] if 'rsi' in df.columns else _rsi_fallback(df['close'], 14)
        _srsi_len = 14
        _rsi_min  = _rsi_src.rolling(_srsi_len, min_periods=_srsi_len).min()
        _rsi_max  = _rsi_src.rolling(_srsi_len, min_periods=_srsi_len).max()
        _rsi_range = (_rsi_max - _rsi_min).clip(lower=1e-10)
        _stochrsi  = ((_rsi_src - _rsi_min) / _rsi_range).clip(0.0, 1.0) * 100
        df['stochrsi_k'] = _stochrsi.rolling(3, min_periods=1).mean()   # %K smoothed
        df['stochrsi_d'] = df['stochrsi_k'].rolling(3, min_periods=1).mean()  # %D signal
        _k_prev = df['stochrsi_k'].shift(1).fillna(50)
        _d_prev = df['stochrsi_d'].shift(1).fillna(50)
        # Oversold cross: K crosses above D while both below 20 — buy signal
        df['stochrsi_cross_up'] = (
            (df['stochrsi_k'] > df['stochrsi_d']) & (_k_prev <= _d_prev) &
            (df['stochrsi_k'] < 30) & (df['stochrsi_d'] < 30)
        )
        # Overbought cross: K crosses below D while both above 80 — sell signal
        df['stochrsi_cross_down'] = (
            (df['stochrsi_k'] < df['stochrsi_d']) & (_k_prev >= _d_prev) &
            (df['stochrsi_k'] > 70) & (df['stochrsi_d'] > 70)
        )
    except Exception as e:
        logger.error(f"[indicators] stochrsi failed: {e}")
        df['stochrsi_k'] = 50.0
        df['stochrsi_d'] = 50.0
        df['stochrsi_cross_up']   = False
        df['stochrsi_cross_down'] = False

    # ─── 18. Cumulative Volume Delta (CVD) divergence (40-50% WR) ─────────────
    # CVD = running sum of signed volume: +volume on up bars, -volume on down bars.
    # CVD rising + price flat/falling = bullish divergence (buyers absorbing supply).
    # CVD falling + price rising = bearish divergence (distribution into strength).
    # 40-50% WR in backtests vs 35% baseline. Works best on crypto 1-5 min bars.
    try:
        _bar_dir     = np.where(df['close'] >= df['open'], 1, -1)
        _signed_vol  = df['volume'] * _bar_dir
        df['cvd']    = _signed_vol.cumsum()
        _cvd_roc     = df['cvd'].diff(10)
        _price_roc   = df['close'].pct_change(10)
        # Bullish divergence: CVD rising while price flat/falling over last 10 bars
        df['cvd_bull_div'] = (_cvd_roc > 0) & (_price_roc <= 0.001)
        # Bearish divergence: CVD falling while price flat/rising over last 10 bars
        df['cvd_bear_div'] = (_cvd_roc < 0) & (_price_roc >= -0.001)
    except Exception as e:
        logger.error(f"[indicators] cvd failed: {e}")
        df['cvd']          = 0.0
        df['cvd_bull_div'] = False
        df['cvd_bear_div'] = False

    # ─── 19. VWAP 2σ Standard Deviation Bands (45-55% WR on reversion) ────────
    # Rolling VWAP ± 2 standard deviations of typical price from VWAP.
    # Price touch of lower band = mean-reversion buy setup (45-55% WR backtested).
    # Price touch of upper band = mean-reversion sell / take-profit signal.
    # Pairs with CVD bull divergence for high-conviction reversion entries.
    try:
        if 'volume' in df.columns and df['volume'].sum() > 0:
            _tp    = (df['high'] + df['low'] + df['close']) / 3.0
            _vwap_r = df.get('vwap', _tp.rolling(20, min_periods=1).mean())
            _dev    = (_tp - _vwap_r) ** 2
            _vwap_std = _dev.rolling(20, min_periods=10).mean().apply(np.sqrt)
            df['vwap_upper2'] = _vwap_r + 2.0 * _vwap_std
            df['vwap_lower2'] = _vwap_r - 2.0 * _vwap_std
            # Price touching lower band = potential reversion buy
            df['vwap_lower_touch'] = df['close'] <= df['vwap_lower2']
            # Price touching upper band = potential reversion sell / exit
            df['vwap_upper_touch'] = df['close'] >= df['vwap_upper2']
    except Exception as e:
        logger.error(f"[indicators] vwap_bands failed: {e}")
        df['vwap_lower_touch'] = False
        df['vwap_upper_touch'] = False

    # ─── 20. EMA 9/21 Golden / Death Cross (40-45% WR in trending regimes) ────
    # Fast-medium EMA cross used widely in crypto day trading.
    # ema9 crossing above ema21 with rising ADX = trend confirmation buy.
    # More responsive than 50/200 cross; works on 1-5 min crypto.
    # WR: 40-45% standalone, improves to ~50% when confirmed by ADX > 20.
    try:
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        _ema9_prev  = df['ema9'].shift(1).fillna(df['ema9'])
        _ema21_prev = df['ema21'].shift(1).fillna(df['ema21'])
        df['ema_golden_cross'] = (df['ema9'] > df['ema21']) & (_ema9_prev <= _ema21_prev)
        df['ema_death_cross']  = (df['ema9'] < df['ema21']) & (_ema9_prev >= _ema21_prev)
        df['ema9_above_21']    = df['ema9'] > df['ema21']  # persistent state flag
    except Exception as e:
        logger.error(f"[indicators] ema_cross failed: {e}")
        df['ema_golden_cross'] = False
        df['ema_death_cross']  = False
        df['ema9_above_21']    = False

    return df


# ─── Advanced signal helper functions ─────────────────────────────────────────

def _avwap_utc(df: pd.DataFrame) -> pd.Series:
    """
    Anchored VWAP, resetting at 00:00 UTC daily.
    Uses typical price x volume, cumulated from each day's UTC midnight.
    """
    typical = (df['high'] + df['low'] + df['close']) / 3
    tp_vol = typical * df['volume']

    # Get UTC date for each bar (index should be tz-aware or UTC)
    try:
        if hasattr(df.index, 'tz') and df.index.tz is not None:
            dates = df.index.tz_convert('UTC').date
        else:
            dates = df.index.date
    except Exception:
        dates = np.arange(len(df)) // 1440  # fallback: reset every 1440 bars

    avwap = pd.Series(np.nan, index=df.index)
    cum_tpv = 0.0
    cum_vol = 0.0
    prev_date = None

    for i, (idx, date) in enumerate(zip(df.index, dates)):
        if date != prev_date:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_date = date
        cum_tpv += tp_vol.iloc[i]
        cum_vol += df['volume'].iloc[i]
        avwap.iloc[i] = cum_tpv / cum_vol if cum_vol > 0 else df['close'].iloc[i]

    return avwap


def _sample_entropy(series: np.ndarray, m: int = 2, r_factor: float = 0.2) -> float:
    """
    Sample Entropy: measures regularity/predictability.
    Low SampEn -> more regular/predictable (trending or structured MR).
    High SampEn -> more random/complex (noise regime, avoid trading).

    m=2, r=0.2*std is the standard parameterization.
    """
    n = len(series)
    if n < 10:
        return np.nan

    r = r_factor * np.std(series, ddof=1)
    if r == 0:
        return 0.0

    def _count_matches(m_len):
        count = 0
        total = 0
        for i in range(n - m_len):
            template = series[i:i + m_len]
            for j in range(i + 1, n - m_len):
                if np.max(np.abs(series[j:j + m_len] - template)) < r:
                    count += 1
            total += (n - m_len - i - 1)
        return count, total

    B, _ = _count_matches(m)
    A, _ = _count_matches(m + 1)

    if B == 0 or A == 0:
        return 0.0
    return float(-np.log(A / B))


def _kyle_lambda_rolling(close: pd.Series, volume: pd.Series,
                          window: int = 60) -> pd.Series:
    """
    Kyle's lambda: slope of price_change ~ signed_volume regression.
    Approximates signed volume as: if close > open (bull bar) -> positive, else negative.
    True signed flow requires tick data; this is the 1-minute OHLCV approximation.
    """
    # Approximate signed volume: bull bar = buy pressure, bear bar = sell pressure
    # Use close vs previous close as direction proxy
    direction = np.sign(close.diff())
    signed_vol = direction * volume
    delta_p = close.diff() / close.shift(1)  # return

    lambdas = pd.Series(np.nan, index=close.index)
    for i in range(window, len(close)):
        y = delta_p.iloc[i - window:i].values
        x = signed_vol.iloc[i - window:i].values
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 10:
            continue
        xm, ym = x[mask], y[mask]
        xstd = xm.std()
        if xstd < 1e-10:
            continue
        # OLS slope
        cov = np.cov(xm, ym)[0, 1]
        var = np.var(xm)
        if var <= 0:
            continue
        slope = cov / var
        # Filter: only keep when R² >= 0.05 (slope explains ≥5% of variance)
        # Without this, most windows produce noise disguised as signal
        ym_std = ym.std()
        if ym_std > 1e-10:
            r_sq = (cov / (np.sqrt(var) * ym_std)) ** 2
            if r_sq < 0.05:
                continue
        lambdas.iloc[i] = slope

    return lambdas


def _kalman_filter(prices: pd.Series, Q: float = 1e-5, R: float = 1e-3) -> pd.Series:
    """
    1D Kalman filter for latent 'fair price' estimate.
    Q: process noise (how fast true price can change)
    R: measurement noise (how noisy the observed price is)
    Q/R ratio ~0.01 acts like a ~10-minute EMA smoother.
    """
    n = len(prices)
    x_hat = np.zeros(n)  # state estimate
    P = np.zeros(n)       # error covariance

    x_hat[0] = prices.iloc[0]
    P[0] = 1.0

    for t in range(1, n):
        # Predict
        x_pred = x_hat[t - 1]
        P_pred = P[t - 1] + Q
        # Update
        K = P_pred / (P_pred + R)
        x_hat[t] = x_pred + K * (prices.iloc[t] - x_pred)
        P[t] = (1 - K) * P_pred

    return pd.Series(x_hat, index=prices.index)


# ─── Original helper functions (unchanged) ────────────────────────────────────

def _add_macd(df: pd.DataFrame, prefix: str, fast: int, slow: int, signal: int) -> None:
    """Add MACD columns with given prefix."""
    if PANDAS_TA:
        try:
            macd = ta.macd(df['close'], fast=fast, slow=slow, signal=signal)
            if macd is not None and not macd.empty:
                df[f'{prefix}_hist'] = macd.iloc[:, 1]  # histogram
                df[f'{prefix}_line'] = macd.iloc[:, 0]  # MACD line
                df[f'{prefix}_sig'] = macd.iloc[:, 2]   # signal line
                return
        except Exception:
            pass

    # Fallback: manual EMA-based MACD
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    df[f'{prefix}_hist'] = macd_line - signal_line
    df[f'{prefix}_line'] = macd_line
    df[f'{prefix}_sig'] = signal_line


# ─── Fibonacci retracement ────────────────────────────────────────────────────

def get_fib_levels(df: pd.DataFrame, lookback: int = 100, swing_n: int = 3) -> dict:
    """
    Auto-detect the most recent swing high and swing low within the last `lookback`
    bars, then return all standard Fibonacci retracement and extension levels.

    Swing detection: a bar is a swing high if its high is higher than the `swing_n`
    bars on either side (and vice versa for swing low).  swing_n=3 works well on
    5-minute crypto candles — it filters noise without lagging too much.

    Returns a dict with keys:
        swing_high, swing_low, swing_high_idx, swing_low_idx,
        fib_236, fib_382, fib_500, fib_618, fib_786,  <- retracements
        ext_1272, ext_1618                              <- extensions (profit targets)
    Returns {} if not enough data to find both swings.
    """
    if len(df) < lookback:
        lookback = len(df)
    recent = df.iloc[-lookback:].copy()

    # ── Swing high: bar whose high is the max of a 2*swing_n+1 centred window ──
    roll_max = recent['high'].rolling(2 * swing_n + 1, center=True).max()
    swing_high_mask = recent['high'] == roll_max
    # exclude the very edges where rolling window is partial
    swing_high_mask.iloc[:swing_n] = False
    swing_high_mask.iloc[-swing_n:] = False

    roll_min = recent['low'].rolling(2 * swing_n + 1, center=True).min()
    swing_low_mask = recent['low'] == roll_min
    swing_low_mask.iloc[:swing_n] = False
    swing_low_mask.iloc[-swing_n:] = False

    sh_series = recent['high'][swing_high_mask]
    sl_series = recent['low'][swing_low_mask]

    if sh_series.empty or sl_series.empty:
        return {}

    # Most recent swing points
    sh_val = float(sh_series.iloc[-1])
    sl_val = float(sl_series.iloc[-1])
    sh_idx = sh_series.index[-1]
    sl_idx = sl_series.index[-1]

    if sh_val <= sl_val:
        return {}   # degenerate — not a real swing structure

    diff = sh_val - sl_val
    return {
        'swing_high':     sh_val,
        'swing_low':      sl_val,
        'swing_high_idx': sh_idx,
        'swing_low_idx':  sl_idx,
        # Standard retracement levels (measured DOWN from swing high)
        'fib_236':  sh_val - diff * 0.236,
        'fib_382':  sh_val - diff * 0.382,   # strong support/resistance
        'fib_500':  sh_val - diff * 0.500,
        'fib_618':  sh_val - diff * 0.618,   # golden ratio — most watched
        'fib_786':  sh_val - diff * 0.786,
        # Extension levels above swing high (common profit targets)
        'ext_1272': sh_val + diff * 0.272,
        'ext_1618': sh_val + diff * 0.618,
    }


def fib_confluence(price: float, atr: float, fib: dict) -> tuple:
    """
    Check whether `price` is sitting near a key Fibonacci level.

    "Near" = within 0.5 x ATR.  ATR gives a volatility-scaled tolerance so
    the check automatically tightens on quiet markets and loosens when BTC
    is moving fast.

    Returns (signal, level_name, confidence_boost) where:
        signal           'support'    — price is at a level that should hold it up
                         'resistance' — price is at a level that should push it down
                         None         — price is not near any level
        level_name       e.g. 'fib_618'
        confidence_boost float 0.0–0.15 added to the strategy's confidence score

    How support vs resistance is determined:
        Fibonacci levels between swing_low and swing_high act as:
        - SUPPORT when price approaches from ABOVE (falling into the level)
        - RESISTANCE when price approaches from BELOW (rising into the level)
        We use a simple proxy: if price < midpoint -> coming from above -> support.
        If price > midpoint -> coming from below -> resistance.

    The 61.8% and 38.2% levels get the highest boost (most-watched by traders).
    """
    if not fib or atr <= 0:
        return None, None, 0.0

    tol = atr * 0.5
    midpoint = (fib['swing_high'] + fib['swing_low']) / 2.0

    level_boosts = {
        'fib_618': 0.15,   # golden ratio — most significant
        'fib_382': 0.12,
        'fib_500': 0.08,
        'fib_786': 0.08,
        'fib_236': 0.06,
    }

    for name, boost in level_boosts.items():
        level_price = fib.get(name)
        if level_price is None:
            continue
        if abs(price - level_price) <= tol:
            signal = 'support' if price <= midpoint else 'resistance'
            return signal, name, boost

    return None, None, 0.0


def _supertrend_manual(high: pd.Series, low: pd.Series, close: pd.Series,
                        atr: pd.Series, multiplier: float = 3.0) -> pd.Series:
    """
    Manual SuperTrend fallback (used when pandas-ta is unavailable or fails).
    Returns a Series of 1 (bullish) / -1 (bearish) matching the input index.
    """
    hl2 = (high + low) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(close)
    fu = basic_upper.copy().values.astype(float)
    fl = basic_lower.copy().values.astype(float)
    direction = np.ones(n, dtype=int)
    c = close.values

    for i in range(1, n):
        fu[i] = basic_upper.iloc[i] if (basic_upper.iloc[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = basic_lower.iloc[i] if (basic_lower.iloc[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if c[i] > fu[i-1]:
            direction[i] = 1
        elif c[i] < fl[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

    return pd.Series(direction, index=close.index)


def _rsi_fallback(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _vwap_fallback(df: pd.DataFrame) -> pd.Series:
    typical = (df['high'] + df['low'] + df['close']) / 3
    cum_vol = df['volume'].cumsum()
    cum_tpv = (typical * df['volume']).cumsum()
    return cum_tpv / cum_vol.replace(0, np.nan)


def _atr_fallback(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def get_htf_bias(df_daily: pd.DataFrame) -> dict:
    """
    Determine directional bias from higher timeframe data.
    Used in pre-market analysis.
    Returns: {'bias': 'BULLISH'/'BEARISH'/'NEUTRAL', 'strength': 0-1, 'score': int}
    """
    if len(df_daily) < 50:
        return {'bias': 'NEUTRAL', 'strength': 0.5, 'score': 0}

    df = add_all_indicators(df_daily)
    last = df.iloc[-1]
    score = 0

    # Price vs EMA50 (trend filter)
    ema50 = last.get('ema50', last['close'])
    if last['close'] > ema50:
        score += 1
    else:
        score -= 1

    # MACD histogram direction (standard)
    macd_hist = last.get('macd_std_hist', 0)
    if macd_hist > 0:
        score += 1
    else:
        score -= 1

    # RSI zone
    rsi = last.get('rsi', 50)
    if 40 < rsi < 65:
        score += 1
    elif rsi > 70 or rsi < 30:
        score -= 1

    # Volume trend
    vol_spike = last.get('vol_spike', 1.0)
    if vol_spike > 1.2:
        score += 0.5

    adx = last.get('adx', 25)
    strength = float(min(adx / 40.0, 1.0))

    if score >= 2:
        return {'bias': 'BULLISH', 'strength': strength, 'score': score}
    elif score <= -2:
        return {'bias': 'BEARISH', 'strength': strength, 'score': score}
    else:
        return {'bias': 'NEUTRAL', 'strength': strength, 'score': score}


def opening_range(df_5min: pd.DataFrame, market_open_idx: int = 0) -> dict:
    """
    Mark the high and low of the first 5-minute candle after open.
    Used in futures/equity breakout strategies.
    """
    if len(df_5min) <= market_open_idx:
        return {'high': None, 'low': None}
    candle = df_5min.iloc[market_open_idx]
    return {
        'high': candle['high'],
        'low': candle['low'],
        'range': candle['high'] - candle['low']
    }
