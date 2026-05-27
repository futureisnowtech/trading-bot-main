"""
indicators/liquidation_levels.py — Liquidation level estimation and cascade risk.

Uses Binance futures liquidation data + OI to estimate where cascades cluster.

Cascade risk score (0-100):
  Based on: OI size, leverage ratios, distance to cluster,
  funding rate extremity, recent OI growth rate.

Outputs:
  nearest_long_liq_dist_pct  : % distance to nearest long liquidation cluster
  nearest_short_liq_dist_pct : % distance to nearest short liquidation cluster
  long_liq_cluster_usd       : estimated USD value at long liq cluster
  short_liq_cluster_usd      : estimated USD value at short liq cluster
  cascade_risk_score         : 0-100 (higher = more cascade risk)
  liq_magnet_direction       : 'long_cascade' | 'short_cascade' | 'neutral'
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
_CACHE_TTL = 120
_lock = threading.RLock()
_cache: dict = {}


def _fetch_recent_liquidations(symbol: str) -> list:
    """Fetch recent forced liquidation orders from Binance."""
    if not _REQUESTS_OK:
        return []
    try:
        r = requests.get(
            f'{_BINANCE_BASE}/fapi/v1/allForceOrders',
            params={'symbol': symbol, 'limit': 50},
            timeout=8
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def _estimate_cascade_risk(oi_change_pct_4h: float, funding_rate: float,
                            nearest_dist_pct: float, ls_ratio: float) -> int:
    """
    Score cascade risk 0-100 from available signals.
    Higher = more dangerous (likely cascade).
    """
    score = 0

    # OI growth (rapid accumulation = crowded)
    if oi_change_pct_4h > 10:
        score += 25
    elif oi_change_pct_4h > 5:
        score += 15
    elif oi_change_pct_4h > 2:
        score += 8

    # Funding extremity
    abs_funding = abs(funding_rate)
    if abs_funding > 0.001:   # > 0.1%/8h = extreme
        score += 25
    elif abs_funding > 0.0005:
        score += 15
    elif abs_funding > 0.0002:
        score += 8

    # Distance to nearest liq cluster
    if nearest_dist_pct < 2:
        score += 30
    elif nearest_dist_pct < 5:
        score += 20
    elif nearest_dist_pct < 8:
        score += 10

    # L/S ratio extremity (crowded positioning)
    if ls_ratio > 2.0 or ls_ratio < 0.5:
        score += 20
    elif ls_ratio > 1.5 or ls_ratio < 0.7:
        score += 10

    return min(100, score)


def get_liquidation_signal(symbol: str, current_price: float,
                            oi_change_pct_4h: float = 0.0,
                            funding_rate: float = 0.0,
                            ls_ratio: float = 1.0) -> dict:
    """
    Returns liquidation level analysis.

    Args:
        symbol:          e.g. 'BTCUSDT'
        current_price:   current price for distance calculations
        oi_change_pct_4h: from open_interest indicator
        funding_rate:    from funding_rate indicator
        ls_ratio:        from open_interest indicator
    """
    neutral = {
        'nearest_long_liq_dist_pct': 10.0,
        'nearest_short_liq_dist_pct': 10.0,
        'long_liq_cluster_usd': 0.0,
        'short_liq_cluster_usd': 0.0,
        'cascade_risk_score': 0,
        'liq_magnet_direction': 'neutral',
        'source': 'estimated',
    }

    if current_price <= 0:
        return neutral

    sym = symbol.upper()
    with _lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

    # Fetch recent liquidation events
    liqs = _fetch_recent_liquidations(sym)

    long_liq_prices = []
    short_liq_prices = []
    long_liq_usd = 0.0
    short_liq_usd = 0.0

    for liq in liqs:
        side = liq.get('s', '')     # 'BUY' = liquidated short, 'SELL' = liquidated long
        price = float(liq.get('ap', liq.get('p', 0)))
        qty = float(liq.get('q', 0))
        value = price * qty

        if side == 'SELL':          # long liquidation
            long_liq_prices.append(price)
            long_liq_usd += value
        elif side == 'BUY':         # short liquidation
            short_liq_prices.append(price)
            short_liq_usd += value

    # Nearest cluster distances
    if long_liq_prices:
        nearest_long = min(long_liq_prices, key=lambda p: abs(p - current_price))
        long_dist = abs(current_price - nearest_long) / current_price * 100
    else:
        long_dist = 10.0

    if short_liq_prices:
        nearest_short = min(short_liq_prices, key=lambda p: abs(p - current_price))
        short_dist = abs(current_price - nearest_short) / current_price * 100
    else:
        short_dist = 10.0

    nearest_dist = min(long_dist, short_dist)
    cascade_risk = _estimate_cascade_risk(oi_change_pct_4h, funding_rate,
                                          nearest_dist, ls_ratio)

    # Magnet direction: price being pushed toward larger cluster
    if long_liq_usd > short_liq_usd * 1.5 and long_liq_prices:
        below_price = [p for p in long_liq_prices if p < current_price]
        magnet = 'long_cascade' if below_price else 'neutral'
    elif short_liq_usd > long_liq_usd * 1.5 and short_liq_prices:
        above_price = [p for p in short_liq_prices if p > current_price]
        magnet = 'short_cascade' if above_price else 'neutral'
    else:
        magnet = 'neutral'

    result = {
        'nearest_long_liq_dist_pct': round(long_dist, 3),
        'nearest_short_liq_dist_pct': round(short_dist, 3),
        'long_liq_cluster_usd': round(long_liq_usd, 0),
        'short_liq_cluster_usd': round(short_liq_usd, 0),
        'cascade_risk_score': cascade_risk,
        'liq_magnet_direction': magnet,
        'source': 'binance_force_orders' if liqs else 'estimated',
        '_ts': time.time(),
    }
    with _lock:
        _cache[sym] = result

    return {k: v for k, v in result.items() if k != '_ts'}
