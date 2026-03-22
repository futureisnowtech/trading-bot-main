"""
data/indicators.py
Centralized indicator calculations using pandas-ta.
All strategy files import from here — one source of truth.
"""
import pandas as pd
import numpy as np
from typing import Optional

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

    # ─── EMA 50 ───────────────────────────────────────────────────────────────
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

    return df


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
