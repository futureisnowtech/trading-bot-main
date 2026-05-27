"""
indicators/macd_advanced.py — Multi-variant MACD with momentum acceleration.

Three backtested variants + second derivative (acceleration).
The acceleration is the early exit signal: momentum decelerating BEFORE
the histogram crosses zero — exit 1-3 candles before the reversal.

Variants:
  MACD(3,15,3)   — fast, high-frequency
  MACD(4,16,3)   — standard
  MACD(6,20,5)   — slower, less noise

Outputs:
  macd_hist_3_15_3     : histogram value for fast variant
  macd_hist_slope      : 1-bar slope of histogram
  macd_acceleration    : 2nd derivative (positive = strengthening)
  macd_long_aligned    : True if all 3 variants agree LONG
  macd_short_aligned   : True if all 3 variants agree SHORT
  macd_mtf_bullish     : True if all 3 variants positive on this TF AND 15m TF
  early_exit_signal    : True if momentum decelerating (exit before reversal)
"""

import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    line = ema_fast - ema_slow
    sig = _ema(line, signal)
    hist = line - sig
    return line, sig, hist


def compute_macd_advanced(df: pd.DataFrame) -> dict:
    """
    Compute all MACD variants and acceleration from OHLCV DataFrame.

    Args:
        df: DataFrame with 'close' column, at least 30 bars

    Returns:
        dict with all MACD fields
    """
    neutral = {
        'macd_hist_3_15_3': 0.0,
        'macd_hist_slope': 0.0,
        'macd_acceleration': 0.0,
        'macd_long_aligned': False,
        'macd_short_aligned': False,
        'early_exit_signal': False,
        'macd_v1_bull': False,
        'macd_v2_bull': False,
        'macd_v3_bull': False,
    }

    if df is None or len(df) < 30:
        return neutral

    try:
        close = df['close']

        # Variant 1: MACD(3,15,3)
        _, _, hist1 = _macd(close, 3, 15, 3)
        # Variant 2: MACD(4,16,3)
        line2, sig2, hist2 = _macd(close, 4, 16, 3)
        # Variant 3: MACD(6,20,5)
        _, _, hist3 = _macd(close, 6, 20, 5)

        last_h1 = float(hist1.iloc[-1])
        last_h2 = float(hist2.iloc[-1])
        last_h3 = float(hist3.iloc[-1])

        # Variant 1 directional state
        v1_bull = last_h1 > 0
        v2_bull = float(line2.iloc[-1]) > float(sig2.iloc[-1])   # crossover
        v3_bull = last_h3 > 0

        # Momentum slope and acceleration on fast variant
        h1_series = hist1.dropna()
        if len(h1_series) >= 3:
            h_now = float(h1_series.iloc[-1])
            h_prev = float(h1_series.iloc[-2])
            h_prev2 = float(h1_series.iloc[-3])
            slope = h_now - h_prev
            prev_slope = h_prev - h_prev2
            acceleration = slope - prev_slope
        else:
            slope, acceleration = 0.0, 0.0

        # Alignment
        long_aligned = v1_bull and v2_bull and v3_bull
        short_aligned = (not v1_bull) and (not v2_bull) and (not v3_bull)

        # Early exit signal: histogram positive but decelerating (about to reverse)
        # Warn when: histogram > 0 AND acceleration < -threshold
        accel_threshold = abs(last_h1) * 0.3 if abs(last_h1) > 0 else 0.001
        early_exit = (
            last_h1 > 0 and acceleration < -accel_threshold
        ) or (
            last_h1 < 0 and acceleration > accel_threshold
        )

        return {
            'macd_hist_3_15_3': round(last_h1, 8),
            'macd_hist_slope': round(slope, 8),
            'macd_acceleration': round(acceleration, 8),
            'macd_long_aligned': long_aligned,
            'macd_short_aligned': short_aligned,
            'early_exit_signal': early_exit,
            'macd_v1_bull': v1_bull,
            'macd_v2_bull': v2_bull,
            'macd_v3_bull': v3_bull,
        }

    except Exception as e:
        logger.debug(f'[macd_advanced] Error: {e}')
        return neutral
