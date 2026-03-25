"""
data/indicators.py
Centralized indicator calculations using pandas-ta.
All strategy files import from here — one source of truth.
"""
import pandas as pd
import numpy as np
from typing import Optional
import pytz

try:
    import pandas_ta as ta
    PANDAS_TA = True
except ImportError:
    PANDAS_TA = False
    print("[indicators] pandas-ta not found, using fallback calculations")


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
            df['bb_upper'] = bbands.iloc[:, 0]
            df['bb_mid'] = bbands.iloc[:, 1]
            df['bb_lower'] = bbands.iloc[:, 2]
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
        print(f"[indicators] rv_ratio failed: {e}")

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
        print(f"[indicators] ou_halflife failed: {e}")

    # ─── 3. Anchored VWAP — UTC daily anchor (avwap_utc, avwap_dev) ──────────
    try:
        df['avwap_utc'] = _avwap_utc(df)
        df['avwap_dev'] = (df['close'] - df['avwap_utc']) / df['avwap_utc']
    except Exception as e:
        print(f"[indicators] avwap_utc failed: {e}")

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
        print(f"[indicators] autocorr_ret failed: {e}")
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
        print(f"[indicators] amihud failed: {e}")

    # ─── 6. Kyle's Lambda (kyle_lambda, kyle_lambda_pct) ─────────────────────
    try:
        df['kyle_lambda'] = _kyle_lambda_rolling(df['close'], df['volume'])
        df['kyle_lambda_pct'] = df['kyle_lambda'].rolling(240, min_periods=30).apply(
            lambda x: float(pd.Series(x).rank(pct=True).iloc[-1] * 100), raw=False
        )
    except Exception as e:
        print(f"[indicators] kyle_lambda failed: {e}")

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
        _prev_squeeze = df['squeeze_on'].shift(1).fillna(False).astype(bool)
        df['squeeze_fired'] = (~df['squeeze_on']) & _prev_squeeze

        squeeze_count = pd.Series(0, index=df.index)
        count = 0
        for i in range(len(df)):
            if df['squeeze_on'].iloc[i]:
                count += 1
            else:
                count = 0
            squeeze_count.iloc[i] = count
        df['squeeze_bars'] = squeeze_count
        # squeeze_direction: +1 if trend up at fire, -1 if down, 0 if no fire
        # Tells agents WHICH direction the coiled energy is releasing
        ema_slope = ema20_kc.diff(3)
        squeeze_dir_arr = np.where(ema_slope > 0, 1, np.where(ema_slope < 0, -1, 0))
        df['squeeze_direction'] = np.where(
            df['squeeze_fired'].values, squeeze_dir_arr, 0
        ).astype(int)
    except Exception as e:
        print(f"[indicators] squeeze failed: {e}")

    # ─── 8. Kalman Filter Price Estimate (kalman_price, kalman_dev) ───────────
    try:
        df['kalman_price'] = _kalman_filter(df['close'])
        df['kalman_dev'] = (df['close'] - df['kalman_price']) / df['kalman_price'].clip(lower=0.001)
    except Exception as e:
        print(f"[indicators] kalman failed: {e}")

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
        print(f"[indicators] session_active failed: {e}")
        df['session_active'] = True

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
