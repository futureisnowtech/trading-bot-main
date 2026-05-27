"""
indicators/rsi_advanced.py — RSI with divergence detection and failure swings.

Three periods (9, 14, 21) + divergence types + failure swing detection.

Divergence types:
  Classic bullish:  price lower low, RSI higher low       → reversal signal
  Classic bearish:  price higher high, RSI lower high     → reversal signal
  Hidden bullish:   price higher low, RSI lower low       → continuation long
  Hidden bearish:   price lower high, RSI higher high     → continuation short

Failure swings:
  Bullish FS: RSI oversold → pulls back → fails to reach 70 → breaks prior high
  Bearish FS: RSI overbought → pulls back → fails to reach 30 → breaks prior low

Outputs:
  rsi_14, rsi_9, rsi_21
  rsi_slope_3c           : 3-bar slope
  rsi_divergence_type    : 'classic_bull' | 'classic_bear' | 'hidden_bull' | 'hidden_bear' | 'none'
  rsi_failure_swing      : 'bullish' | 'bearish' | 'none'
  rsi_centerline_cross   : 'cross_up' | 'cross_down' | 'none'
"""

import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def compute_rsi_advanced(df: pd.DataFrame) -> dict:
    """
    Compute RSI signals from OHLCV DataFrame.

    Args:
        df: DataFrame with 'close' column, at least 30 bars
    """
    neutral = {
        'rsi_14': 50.0,
        'rsi_9': 50.0,
        'rsi_21': 50.0,
        'rsi_slope_3c': 0.0,
        'rsi_divergence_type': 'none',
        'rsi_failure_swing': 'none',
        'rsi_centerline_cross': 'none',
    }

    if df is None or len(df) < 25:
        return neutral

    try:
        close = df['close']
        rsi14 = _rsi(close, 14)
        rsi9 = _rsi(close, 9)
        rsi21 = _rsi(close, 21)

        last14 = float(rsi14.iloc[-1])
        last9 = float(rsi9.iloc[-1])
        last21 = float(rsi21.iloc[-1])

        # Slope
        slope = float(rsi14.iloc[-1] - rsi14.iloc[-4]) if len(rsi14) >= 4 else 0.0

        # Centerline cross
        cross = 'none'
        if len(rsi14) >= 2:
            prev14 = float(rsi14.iloc[-2])
            if prev14 < 50 <= last14:
                cross = 'cross_up'
            elif prev14 > 50 >= last14:
                cross = 'cross_down'

        # Divergence detection (last 30 bars)
        n = min(len(df), 30)
        recent_close = close.tail(n)
        recent_rsi = rsi14.tail(n)

        div_type = 'none'
        try:
            # Find price and RSI pivot highs/lows
            ph_price = (recent_close.shift(1) < recent_close) & (recent_close.shift(-1) < recent_close)
            pl_price = (recent_close.shift(1) > recent_close) & (recent_close.shift(-1) > recent_close)
            ph_rsi = (recent_rsi.shift(1) < recent_rsi) & (recent_rsi.shift(-1) < recent_rsi)
            pl_rsi = (recent_rsi.shift(1) > recent_rsi) & (recent_rsi.shift(-1) > recent_rsi)

            price_highs = recent_close[ph_price].values
            price_lows = recent_close[pl_price].values
            rsi_highs = recent_rsi[ph_rsi].values
            rsi_lows = recent_rsi[pl_rsi].values

            # Classic bearish: price HH, RSI LH
            if len(price_highs) >= 2 and len(rsi_highs) >= 2:
                if price_highs[-1] > price_highs[-2] and rsi_highs[-1] < rsi_highs[-2]:
                    div_type = 'classic_bear'

            # Classic bullish: price LL, RSI HL
            if div_type == 'none' and len(price_lows) >= 2 and len(rsi_lows) >= 2:
                if price_lows[-1] < price_lows[-2] and rsi_lows[-1] > rsi_lows[-2]:
                    div_type = 'classic_bull'

            # Hidden bullish (continuation): price HL, RSI LL
            if div_type == 'none' and len(price_lows) >= 2 and len(rsi_lows) >= 2:
                if price_lows[-1] > price_lows[-2] and rsi_lows[-1] < rsi_lows[-2]:
                    div_type = 'hidden_bull'

            # Hidden bearish (continuation): price LH, RSI HH
            if div_type == 'none' and len(price_highs) >= 2 and len(rsi_highs) >= 2:
                if price_highs[-1] < price_highs[-2] and rsi_highs[-1] > rsi_highs[-2]:
                    div_type = 'hidden_bear'
        except Exception:
            pass

        # Failure swing detection
        failure_swing = 'none'
        rsi_vals = rsi14.dropna().tail(15).values
        if len(rsi_vals) >= 8:
            # Bullish: touched oversold (<30), pulled back, failed to reach 70 again,
            # then crossed above pullback high
            if any(v < 32 for v in rsi_vals[:-3]):
                peak_after = max(rsi_vals[-6:-3])
                if peak_after < 65 and rsi_vals[-1] > peak_after:
                    failure_swing = 'bullish'

            # Bearish: touched overbought (>70), pulled back, failed to reach 30,
            # then crossed below pullback low
            if failure_swing == 'none' and any(v > 68 for v in rsi_vals[:-3]):
                trough_after = min(rsi_vals[-6:-3])
                if trough_after > 35 and rsi_vals[-1] < trough_after:
                    failure_swing = 'bearish'

        return {
            'rsi_14': round(last14, 2),
            'rsi_9': round(last9, 2),
            'rsi_21': round(last21, 2),
            'rsi_slope_3c': round(slope, 3),
            'rsi_divergence_type': div_type,
            'rsi_failure_swing': failure_swing,
            'rsi_centerline_cross': cross,
        }

    except Exception as e:
        logger.debug(f'[rsi_advanced] Error: {e}')
        return neutral
