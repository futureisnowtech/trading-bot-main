"""
indicators/orderbook.py — Order book depth and imbalance.

Uses live book data from RealtimeFeeds (bookTicker) or REST fallback.

Imbalance at 3 depth levels:
  L1: best bid vs ask (immediate pressure, microsecond horizon)
  L5: top 5 levels (seconds to minute)
  L20: full depth (minutes to hour)

Outputs:
  ob_imbalance_l1       : bid_qty / (bid_qty + ask_qty) at L1 (0-1, >0.5 = bullish)
  ob_imbalance_l5       : same for L5
  ob_imbalance_l20      : same for L20
  wall_above_dist_pct   : % to nearest ask wall (>3× avg level size)
  wall_below_dist_pct   : % to nearest bid wall
  spread_pct            : bid-ask spread as % of mid
  ob_pressure           : 'bullish' | 'bearish' | 'neutral'
"""

import logging
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

_BINANCE_BASE = 'https://fapi.binance.com'
_CACHE_TTL = 5   # 5s cache — order book changes fast
_lock = threading.RLock()
_cache: dict = {}


def _fetch_orderbook_rest(symbol: str, limit: int = 20) -> Optional[dict]:
    """Fetch depth snapshot from Binance REST."""
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(
            f'{_BINANCE_BASE}/fapi/v1/depth',
            params={'symbol': symbol, 'limit': limit},
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _calc_imbalance(bids: list, asks: list, levels: int) -> float:
    """
    Compute bid/(bid+ask) imbalance for top N levels.
    Returns 0.5 (neutral) if data unavailable.
    """
    if not bids or not asks:
        return 0.5
    bid_vol = sum(float(b[1]) for b in bids[:levels])
    ask_vol = sum(float(a[1]) for a in asks[:levels])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.5
    return round(bid_vol / total, 4)


def _find_wall(levels: list, current_price: float, side: str) -> float:
    """
    Find nearest order wall (level with >3× average size).
    Returns % distance to wall from current price.
    """
    if not levels or current_price == 0:
        return 100.0

    sizes = [float(l[1]) for l in levels]
    avg_size = sum(sizes) / len(sizes) if sizes else 1.0
    threshold = avg_size * 3.0

    for level in levels:
        price = float(level[0])
        size = float(level[1])
        if size >= threshold:
            dist_pct = abs(price - current_price) / current_price * 100
            return round(dist_pct, 4)
    return 100.0


def get_orderbook_signal(symbol: str, current_price: float = 0.0,
                         feeds=None) -> dict:
    """
    Returns order book imbalance and wall analysis.

    Args:
        symbol:        e.g. 'BTCUSDT'
        current_price: current mark/close price for wall distance
        feeds:         RealtimeFeeds instance (optional, falls back to REST)
    """
    neutral = {
        'ob_imbalance_l1': 0.5,
        'ob_imbalance_l5': 0.5,
        'ob_imbalance_l20': 0.5,
        'wall_above_dist_pct': 100.0,
        'wall_below_dist_pct': 100.0,
        'spread_pct': 0.0,
        'ob_pressure': 'neutral',
        'source': 'unavailable',
    }

    sym = symbol.upper()
    with _lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

    # Try live feeds first (bookTicker = L1 only)
    if feeds is not None:
        book = feeds.get_book(sym)
        if book:
            bid = book.get('bid', 0)
            ask = book.get('ask', 0)
            spread = book.get('spread_pct', 0.0)
            if bid > 0 and ask > 0:
                # With bookTicker we only have L1
                l1_imbal = bid / (bid + ask) if (bid + ask) > 0 else 0.5
                result = {
                    'ob_imbalance_l1': round(l1_imbal, 4),
                    'ob_imbalance_l5': 0.5,   # not available from bookTicker
                    'ob_imbalance_l20': 0.5,
                    'wall_above_dist_pct': 100.0,
                    'wall_below_dist_pct': 100.0,
                    'spread_pct': spread,
                    'ob_pressure': 'bullish' if l1_imbal > 0.55 else ('bearish' if l1_imbal < 0.45 else 'neutral'),
                    'source': 'websocket_l1',
                    '_ts': time.time(),
                }
                with _lock:
                    _cache[sym] = result
                return {k: v for k, v in result.items() if k != '_ts'}

    # Fallback: REST depth snapshot
    book = _fetch_orderbook_rest(sym, limit=20)
    if not book:
        return neutral

    bids = book.get('bids', [])
    asks = book.get('asks', [])

    if not bids or not asks:
        return neutral

    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
    price = current_price if current_price > 0 else mid
    spread_pct = ((float(asks[0][0]) - float(bids[0][0])) / mid * 100) if mid > 0 else 0.0

    l1 = _calc_imbalance(bids, asks, 1)
    l5 = _calc_imbalance(bids, asks, 5)
    l20 = _calc_imbalance(bids, asks, 20)

    wall_above = _find_wall(asks, price, 'ask')
    wall_below = _find_wall(bids, price, 'bid')

    # Pressure: use L5 as primary signal
    if l5 > 0.58:
        pressure = 'bullish'
    elif l5 < 0.42:
        pressure = 'bearish'
    else:
        pressure = 'neutral'

    result = {
        'ob_imbalance_l1': l1,
        'ob_imbalance_l5': l5,
        'ob_imbalance_l20': l20,
        'wall_above_dist_pct': wall_above,
        'wall_below_dist_pct': wall_below,
        'spread_pct': round(spread_pct, 4),
        'ob_pressure': pressure,
        'source': 'rest_depth',
        '_ts': time.time(),
    }
    with _lock:
        _cache[sym] = result

    return {k: v for k, v in result.items() if k != '_ts'}
