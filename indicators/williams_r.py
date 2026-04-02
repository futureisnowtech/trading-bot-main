"""
indicators/williams_r.py — Williams %R with momentum.

Oversold exit signal:
  WR below -80 → crosses back above -80 AND crosses -50 within 3 candles → bounce

Outputs:
  williams_r_14    : current Williams %R value
  williams_r_momentum : wr_now - wr_5_periods_ago
  wr_oversold      : True if WR < -80
  wr_overbought    : True if WR > -20
  wr_exit_signal   : True if WR crossed from oversold to above -80
"""

import logging
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


def compute_williams_r(df: pd.DataFrame, period: int = 14) -> dict:
    """
    Compute Williams %R from OHLCV DataFrame.

    Args:
        df:     DataFrame with high, low, close columns (at least period+5 bars)
        period: lookback period (default 14)
    """
    neutral = {
        'williams_r_14': -50.0,
        'williams_r_momentum': 0.0,
        'wr_oversold': False,
        'wr_overbought': False,
        'wr_exit_signal': False,
    }

    if df is None or len(df) < period + 5:
        return neutral

    try:
        high_max = df['high'].rolling(period).max()
        low_min = df['low'].rolling(period).min()

        wr = -100 * (high_max - df['close']) / (high_max - low_min + 1e-9)

        last_wr = float(wr.iloc[-1])
        prev5_wr = float(wr.iloc[-6]) if len(wr) >= 6 else last_wr
        momentum = last_wr - prev5_wr

        # Oversold exit: WR was below -80, now crossed above -80
        wr_exit = False
        if len(wr) >= 3:
            recent = wr.dropna().tail(4).values
            if len(recent) >= 2:
                was_oversold = any(v < -80 for v in recent[:-1])
                now_above = recent[-1] > -80
                wr_exit = was_oversold and now_above

        return {
            'williams_r_14': round(last_wr, 2),
            'williams_r_momentum': round(momentum, 2),
            'wr_oversold': last_wr < -80,
            'wr_overbought': last_wr > -20,
            'wr_exit_signal': wr_exit,
        }

    except Exception as e:
        logger.debug(f'[williams_r] Error: {e}')
        return neutral
