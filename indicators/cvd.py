"""
indicators/cvd.py — Cumulative Volume Delta

The most important indicator in crypto. Measures real buying vs selling pressure.
Wraps data/cumulative_delta.py and adds divergence detection on OHLCV DataFrames.

Outputs per symbol:
  cvd_value             : raw net delta (buy_vol - sell_vol)
  cvd_slope_5c          : delta_pct slope over last 5 bars
  cvd_slope_20c         : delta_pct slope over last 20 bars
  cvd_divergence_type   : 'bullish_div' | 'bearish_div' | 'none'
  cvd_divergence_strength: 0.0 – 1.0
  cvd_vs_price_corr_20c : pearson correlation CVD vs price over 20 bars
  cvd_trend_aligned     : bool — CVD direction matches price direction
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from data.cumulative_delta import get_cumulative_delta
    _DELTA_OK = True
except ImportError:
    _DELTA_OK = False


# ── Divergence detection ──────────────────────────────────────────────────────

def _find_pivot_high(series: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    """Returns boolean Series: True at swing highs."""
    pivots = pd.Series(False, index=series.index)
    for i in range(left, len(series) - right):
        window = series.iloc[i - left: i + right + 1]
        if series.iloc[i] == window.max():
            pivots.iloc[i] = True
    return pivots


def _find_pivot_low(series: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    """Returns boolean Series: True at swing lows."""
    pivots = pd.Series(False, index=series.index)
    for i in range(left, len(series) - right):
        window = series.iloc[i - left: i + right + 1]
        if series.iloc[i] == window.min():
            pivots.iloc[i] = True
    return pivots


def compute_cvd_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute CVD and divergence signals from an OHLCV DataFrame.

    The DataFrame must have columns: open, high, low, close, volume.
    Returns the same DataFrame with CVD columns appended.
    """
    if df.empty or len(df) < 5:
        return df

    df = df.copy()

    # CVD proxy from candle body (no tick data needed)
    # buy_vol = volume when close > open; sell_vol = volume when close < open
    df['_body_dir'] = np.sign(df['close'] - df['open'])
    df['_buy_vol'] = df['volume'] * (df['_body_dir'] > 0).astype(float)
    df['_sell_vol'] = df['volume'] * (df['_body_dir'] < 0).astype(float)
    # Equal body: split 50/50
    equal_mask = df['_body_dir'] == 0
    df.loc[equal_mask, '_buy_vol'] = df.loc[equal_mask, 'volume'] * 0.5
    df.loc[equal_mask, '_sell_vol'] = df.loc[equal_mask, 'volume'] * 0.5

    df['_net_delta'] = df['_buy_vol'] - df['_sell_vol']
    df['cvd_value'] = df['_net_delta'].cumsum()

    # Normalize CVD by rolling volume
    roll_vol = df['volume'].rolling(20, min_periods=1).sum()
    df['cvd_normalized'] = df['cvd_value'] / (roll_vol + 1e-9)

    # Slopes
    df['cvd_slope_5c'] = df['cvd_value'].diff(5) / (df['cvd_value'].abs().rolling(5).mean() + 1e-9)
    df['cvd_slope_20c'] = df['cvd_value'].diff(20) / (df['cvd_value'].abs().rolling(20).mean() + 1e-9)

    # Correlation CVD vs price over 20 bars
    df['cvd_vs_price_corr_20c'] = (
        df['cvd_value'].rolling(20).corr(df['close'])
        .fillna(0)
    )

    # Divergence detection (last 30 bars window)
    n = min(len(df), 30)
    recent = df.tail(n)

    price_ph = _find_pivot_high(recent['close'])
    price_pl = _find_pivot_low(recent['close'])
    cvd_ph = _find_pivot_high(recent['cvd_value'])
    cvd_pl = _find_pivot_low(recent['cvd_value'])

    bearish_div = False
    bullish_div = False
    div_strength = 0.0

    ph_idx = recent.index[price_ph]
    cvd_ph_idx = recent.index[cvd_ph]

    if len(ph_idx) >= 2 and len(cvd_ph_idx) >= 2:
        price_last_h = recent.loc[ph_idx[-1], 'close']
        price_prev_h = recent.loc[ph_idx[-2], 'close']
        cvd_last_h = recent.loc[cvd_ph_idx[-1], 'cvd_value']
        cvd_prev_h = recent.loc[cvd_ph_idx[-2], 'cvd_value']

        if price_last_h > price_prev_h and cvd_last_h < cvd_prev_h:
            bearish_div = True
            price_move = (price_last_h - price_prev_h) / (abs(price_prev_h) + 1e-9)
            cvd_move = (cvd_last_h - cvd_prev_h) / (abs(cvd_prev_h) + 1e-9)
            if price_move > 0:
                div_strength = min(1.0, abs(price_move - cvd_move) / price_move)

    pl_idx = recent.index[price_pl]
    cvd_pl_idx = recent.index[cvd_pl]

    if len(pl_idx) >= 2 and len(cvd_pl_idx) >= 2:
        price_last_l = recent.loc[pl_idx[-1], 'close']
        price_prev_l = recent.loc[pl_idx[-2], 'close']
        cvd_last_l = recent.loc[cvd_pl_idx[-1], 'cvd_value']
        cvd_prev_l = recent.loc[cvd_pl_idx[-2], 'cvd_value']

        if price_last_l < price_prev_l and cvd_last_l > cvd_prev_l:
            bullish_div = True
            price_move = (price_prev_l - price_last_l) / (abs(price_prev_l) + 1e-9)
            cvd_move = (cvd_last_l - cvd_prev_l) / (abs(cvd_prev_l) + 1e-9)
            if price_move > 0:
                div_strength = min(1.0, abs(price_move + cvd_move) / price_move)

    df['cvd_divergence_type'] = 'none'
    if bullish_div:
        df['cvd_divergence_type'] = 'bullish_div'
    elif bearish_div:
        df['cvd_divergence_type'] = 'bearish_div'

    df['cvd_divergence_strength'] = div_strength

    # Trend alignment
    price_dir = df['close'].diff(5).iloc[-1] if len(df) > 5 else 0
    cvd_dir = df['cvd_value'].diff(5).iloc[-1] if len(df) > 5 else 0
    df['cvd_trend_aligned'] = bool(np.sign(price_dir) == np.sign(cvd_dir))

    # Cleanup temp cols
    df.drop(columns=['_body_dir', '_buy_vol', '_sell_vol', '_net_delta'], inplace=True, errors='ignore')
    return df


def get_cvd_signal(symbol: str, df: pd.DataFrame) -> dict:
    """
    Returns latest CVD signal dict for a symbol.
    Combines live cumulative_delta feed with candle-based divergence detection.

    Args:
        symbol: e.g. 'BTCUSDT'
        df:     OHLCV DataFrame (at least 30 bars)
    """
    result = {
        'cvd_value_normalized': 0.0,
        'cvd_slope_5c': 0.0,
        'cvd_slope_20c': 0.0,
        'cvd_divergence_type': 'none',
        'cvd_divergence_strength': 0.0,
        'cvd_vs_price_corr_20c': 0.0,
        'cvd_trend_aligned': False,
        'bullish_divergence': False,
        'bearish_divergence': False,
    }

    if df is None or df.empty:
        return result

    try:
        df_cvd = compute_cvd_from_df(df)
        last = df_cvd.iloc[-1]

        result['cvd_value_normalized'] = float(last.get('cvd_normalized', 0.0))
        result['cvd_slope_5c'] = float(last.get('cvd_slope_5c', 0.0))
        result['cvd_slope_20c'] = float(last.get('cvd_slope_20c', 0.0))
        result['cvd_divergence_type'] = str(last.get('cvd_divergence_type', 'none'))
        result['cvd_divergence_strength'] = float(last.get('cvd_divergence_strength', 0.0))
        result['cvd_vs_price_corr_20c'] = float(last.get('cvd_vs_price_corr_20c', 0.0))
        result['cvd_trend_aligned'] = bool(last.get('cvd_trend_aligned', False))
        result['bullish_divergence'] = result['cvd_divergence_type'] == 'bullish_div'
        result['bearish_divergence'] = result['cvd_divergence_type'] == 'bearish_div'

    except Exception as e:
        logger.debug(f'[cvd] Error for {symbol}: {e}')

    # Enrich with live tick-level delta if available
    if _DELTA_OK:
        try:
            live = get_cumulative_delta(symbol)
            # Override normalized value with live feed if available
            if live.get('source') != 'unavailable':
                result['cvd_value_normalized'] = float(live.get('delta_pct', 0.0))
        except Exception:
            pass

    return result
