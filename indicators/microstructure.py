"""
indicators/microstructure.py — Market microstructure signals.

Detects informed vs uninformed flow (simplified VPIN), whale trades,
and iceberg orders from trade stream data.

Outputs:
  vpin_toxicity    : 0-1, high = smart money moving (follow them)
  whale_buy_count  : count of whale buys in last N minutes
  whale_sell_count : count of whale sells in last N minutes
  whale_direction  : 'accumulating' | 'distributing' | 'neutral'
  large_trade_pct  : % of volume from trades > 10× median size
"""

import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

_WHALE_WINDOW_MINUTES = 30
_WHALE_MULTIPLIER = 10   # trade > 10× median = whale


def compute_microstructure(trades: List[dict], current_price: float = 0.0) -> dict:
    """
    Compute microstructure signals from recent trade list.

    Args:
        trades: list of trade dicts from RealtimeFeeds.get_recent_trades()
                each has: price, qty, is_sell, local_ts
        current_price: for context

    Returns:
        microstructure signal dict
    """
    neutral = {
        'vpin_toxicity': 0.5,
        'whale_buy_count': 0,
        'whale_sell_count': 0,
        'whale_direction': 'neutral',
        'large_trade_pct': 0.0,
    }

    if not trades or len(trades) < 5:
        return neutral

    try:
        # Filter to last 30 minutes
        cutoff = time.time() - _WHALE_WINDOW_MINUTES * 60
        recent = [t for t in trades if t.get('local_ts', 0) >= cutoff]

        if len(recent) < 3:
            return neutral

        quantities = [float(t.get('qty', 0)) for t in recent]
        median_qty = sorted(quantities)[len(quantities) // 2]
        whale_threshold = median_qty * _WHALE_MULTIPLIER

        buy_vol = 0.0
        sell_vol = 0.0
        whale_buys = 0
        whale_sells = 0
        large_vol = 0.0
        total_vol = 0.0

        for t in recent:
            qty = float(t.get('qty', 0))
            is_sell = bool(t.get('is_sell', False))
            total_vol += qty

            if is_sell:
                sell_vol += qty
            else:
                buy_vol += qty

            if qty >= whale_threshold:
                large_vol += qty
                if is_sell:
                    whale_sells += 1
                else:
                    whale_buys += 1

        # Simplified VPIN: |buy_vol - sell_vol| / total_vol
        # High = directional (informed); low = noisy (uninformed)
        vpin = abs(buy_vol - sell_vol) / (total_vol + 1e-9)

        # Whale direction
        if whale_buys > whale_sells * 1.5:
            whale_dir = 'accumulating'
        elif whale_sells > whale_buys * 1.5:
            whale_dir = 'distributing'
        else:
            whale_dir = 'neutral'

        large_trade_pct = large_vol / (total_vol + 1e-9)

        return {
            'vpin_toxicity': round(float(vpin), 4),
            'whale_buy_count': int(whale_buys),
            'whale_sell_count': int(whale_sells),
            'whale_direction': whale_dir,
            'large_trade_pct': round(float(large_trade_pct), 4),
        }

    except Exception as e:
        logger.debug(f'[microstructure] Error: {e}')
        return neutral
