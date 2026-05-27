"""
indicators/atr_regime.py — ATR with volatility regime classification.

Computes ATR at three timescales and classifies the current volatility regime.
Used for: stop placement, position sizing, regime-adjusted signal thresholds.

Outputs:
  atr_7           : fast ATR (scalp stops)
  atr_14          : standard ATR
  atr_21          : slow ATR (swing stops)
  atr_normalized  : atr_14 / price (removes absolute price bias)
  atr_ratio       : atr_7 / atr_21 (expansion vs compression)
  atr_regime      : 1=compressing | 2=normal | 3=expanding
  stop_scalp      : entry ± (atr_7 * 1.5)
  stop_swing      : entry ± (atr_21 * 2.5)
  vol_regime_mult : position size multiplier (0.7 – 1.3)
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


def _true_range(df: pd.DataFrame) -> pd.Series:
    """Standard true range: max of H-L, H-prev_C, prev_C-L."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low'] - df['close'].shift(1)).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


def compute_atr_regime(df: pd.DataFrame) -> dict:
    """
    Compute ATR regime from OHLCV DataFrame.

    Args:
        df: DataFrame with open, high, low, close, volume columns (at least 25 bars)

    Returns:
        dict with all ATR regime fields, or neutral defaults if insufficient data
    """
    neutral = {
        'atr_7': 0.0,
        'atr_14': 0.0,
        'atr_21': 0.0,
        'atr_normalized': 0.0,
        'atr_ratio': 1.0,
        'atr_regime': 2,
        'stop_scalp': 0.0,
        'stop_swing': 0.0,
        'vol_regime_mult': 1.0,
    }

    if df is None or len(df) < 22:
        return neutral

    try:
        tr = _true_range(df)
        current_price = float(df['close'].iloc[-1])

        atr_7 = float(tr.ewm(span=7, adjust=False).mean().iloc[-1])
        atr_14 = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
        atr_21 = float(tr.ewm(span=21, adjust=False).mean().iloc[-1])

        atr_normalized = atr_14 / current_price if current_price > 0 else 0.0
        atr_ratio = atr_7 / atr_21 if atr_21 > 0 else 1.0

        # Regime classification
        if atr_ratio > 1.5:
            regime = 3      # expanding — widen stops, reduce size
            vol_mult = 0.75
        elif atr_ratio < 0.7:
            regime = 1      # compressing — tighter stops, squeeze incoming
            vol_mult = 1.10
        else:
            regime = 2      # normal
            vol_mult = 1.0

        return {
            'atr_7': round(atr_7, 6),
            'atr_14': round(atr_14, 6),
            'atr_21': round(atr_21, 6),
            'atr_normalized': round(atr_normalized, 6),
            'atr_ratio': round(atr_ratio, 3),
            'atr_regime': regime,
            'stop_scalp': round(atr_7 * 1.5, 6),
            'stop_swing': round(atr_21 * 2.5, 6),
            'vol_regime_mult': vol_mult,
        }
    except Exception as e:
        logger.debug(f'[atr_regime] Error: {e}')
        return neutral


def get_stop_distance(df: pd.DataFrame, direction: str = 'long') -> float:
    """
    Returns recommended stop distance (in price units) for the current regime.
    Uses structure-based stop (beyond nearest swing) with ATR floor.

    Args:
        df:        OHLCV DataFrame
        direction: 'long' or 'short'
    """
    if df is None or len(df) < 10:
        return 0.0

    atr = compute_atr_regime(df)
    atr_floor = atr['atr_7'] * 1.2

    # Find nearest swing point
    lows = df['low'].tail(20)
    highs = df['high'].tail(20)

    if direction == 'long':
        swing_low = float(lows.nsmallest(3).iloc[-1])
        current_price = float(df['close'].iloc[-1])
        structure_dist = current_price - swing_low
    else:
        swing_high = float(highs.nlargest(3).iloc[-1])
        current_price = float(df['close'].iloc[-1])
        structure_dist = swing_high - current_price

    # Use larger of structure distance and ATR floor
    stop_dist = max(structure_dist, atr_floor)

    # Maximum: atr_21 * 4.0 (Correction 8: wide stops → reduce size, not kill trade)
    max_stop = atr['atr_21'] * 4.0
    if stop_dist > max_stop:
        stop_dist = max_stop   # caller should reduce position size proportionally

    return round(stop_dist, 6)
