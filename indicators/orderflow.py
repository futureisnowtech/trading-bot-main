"""
indicators/orderflow.py — Order flow analysis: buy/sell delta, POC, volume profile.

Derives from OHLCV + optional tick-level data (aggTrades).

Outputs:
  buy_volume_ratio    : buy_vol / total_vol last 5 bars (0-1)
  volume_spike_5c     : current volume / 20-bar avg (ratio)
  volume_spike_20c    : 20-bar sum / 60-bar avg (ratio)
  dollar_volume_norm  : dollar volume normalized to 30-day avg
  volume_trend_slope  : linear slope of volume over last 10 bars
  vol_at_price_pct    : % of session volume at current price level
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


def compute_orderflow(df: pd.DataFrame) -> dict:
    """
    Compute order flow metrics from OHLCV DataFrame.

    Args:
        df: DataFrame with open, high, low, close, volume columns (at least 25 bars)
    """
    neutral = {
        'buy_volume_ratio': 0.5,
        'volume_spike_5c': 1.0,
        'volume_spike_20c': 1.0,
        'dollar_volume_normalized': 1.0,
        'volume_trend_slope': 0.0,
        'volume_at_price_level_pct': 0.0,
    }

    if df is None or len(df) < 10:
        return neutral

    try:
        # Buy volume proxy: if close > open → buy vol = total vol
        body_dir = np.sign(df['close'] - df['open'])
        buy_vol = df['volume'] * (body_dir > 0).astype(float)
        sell_vol = df['volume'] * (body_dir < 0).astype(float)
        # Equal close == open: split 50/50
        equal_mask = body_dir == 0
        buy_vol[equal_mask] = df['volume'][equal_mask] * 0.5

        # Buy volume ratio (last 5 bars)
        last5_buy = float(buy_vol.tail(5).sum())
        last5_total = float(df['volume'].tail(5).sum())
        buy_ratio = last5_buy / (last5_total + 1e-9)

        # Volume spike ratios
        avg_vol_20 = float(df['volume'].tail(20).mean())
        avg_vol_60 = float(df['volume'].tail(60).mean()) if len(df) >= 60 else avg_vol_20
        curr_vol = float(df['volume'].iloc[-1])
        sum_20 = float(df['volume'].tail(20).sum())

        spike_5c = curr_vol / (avg_vol_20 + 1e-9)
        spike_20c = sum_20 / (avg_vol_60 * 20 + 1e-9)

        # Dollar volume normalized
        dollar_vol = df['close'] * df['volume']
        avg_dollar_30 = float(dollar_vol.tail(30).mean()) if len(df) >= 30 else float(dollar_vol.mean())
        curr_dollar = float(dollar_vol.iloc[-1])
        dollar_norm = curr_dollar / (avg_dollar_30 + 1e-9)

        # Volume trend slope (linear regression last 10 bars)
        vol_slope = 0.0
        if len(df) >= 10:
            y = df['volume'].tail(10).values.astype(float)
            x = np.arange(len(y))
            if len(y) > 1:
                coef = np.polyfit(x, y, 1)
                # Normalize slope by mean volume
                vol_slope = float(coef[0]) / (float(y.mean()) + 1e-9)

        # Volume at current price level (last 20 bars)
        vol_at_price = 0.0
        if len(df) >= 5:
            current_price = float(df['close'].iloc[-1])
            # Price band = ±0.5%
            band = current_price * 0.005
            recent = df.tail(20)
            mask = (recent['close'] >= current_price - band) & (recent['close'] <= current_price + band)
            vol_in_band = float(recent['volume'][mask].sum())
            total_vol = float(recent['volume'].sum())
            vol_at_price = vol_in_band / (total_vol + 1e-9)

        return {
            'buy_volume_ratio': round(float(buy_ratio), 4),
            'volume_spike_5c': round(spike_5c, 3),
            'volume_spike_20c': round(spike_20c, 3),
            'dollar_volume_normalized': round(dollar_norm, 3),
            'volume_trend_slope': round(vol_slope, 4),
            'volume_at_price_level_pct': round(vol_at_price, 4),
        }

    except Exception as e:
        logger.debug(f'[orderflow] Error: {e}')
        return neutral
