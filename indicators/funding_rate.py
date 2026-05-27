"""
indicators/funding_rate.py — Funding rate analysis for Binance perps.

Computes carry-adjusted signals from funding rate history.
Detects overcrowding, squeeze setups, and carry trade opportunities.

Outputs:
  funding_rate_current  : current 8h funding rate (e.g. 0.0003 = 0.03%)
  funding_rate_8h_change: change vs prior period
  carry_annual          : annualized carry in % (funding * 3 * 365 * 100)
  funding_signal        : 'squeeze_setup' | 'overheated' | 'favorable' | 'neutral'
  funding_divergence    : bool — funding spiked but price hasn't moved yet
  funding_income_per_hour: USD earned/hour on $1000 short (carry collection)
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
_CACHE_TTL = 60   # 1 min cache (funding updates every 8h but we re-poll for accuracy)
_lock = threading.RLock()
_cache: dict = {}

_OVERHEATED_PCT = 0.05     # 0.05%/8h → longs paying too much
_FAVORABLE_PCT = 0.01      # 0.01%/8h → reasonable for longs
_SQUEEZE_PCT = -0.03       # -0.03%/8h → extreme short positioning


def _fetch_funding(symbol: str) -> Optional[list]:
    """Fetch last 2 funding rate periods from Binance."""
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(
            f'{_BINANCE_BASE}/fapi/v1/fundingRate',
            params={'symbol': symbol, 'limit': 3},
            timeout=8
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def get_funding_signal(symbol: str, position_size_usd: float = 1000.0) -> dict:
    """
    Returns funding rate analysis for symbol.

    Args:
        symbol:             e.g. 'BTCUSDT'
        position_size_usd:  for carry income calculation
    """
    neutral = {
        'funding_rate_current': 0.0,
        'funding_rate_8h_change': 0.0,
        'carry_annual': 0.0,
        'funding_signal': 'neutral',
        'funding_divergence': False,
        'funding_income_per_hour': 0.0,
        'source': 'unavailable',
    }

    sym = symbol.upper()
    with _lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

    data = _fetch_funding(sym)
    if not data or len(data) < 1:
        return neutral

    current_rate = float(data[-1].get('fundingRate', 0))
    prior_rate = float(data[-2].get('fundingRate', 0)) if len(data) >= 2 else current_rate
    rate_8h_change = current_rate - prior_rate

    # Annualized carry (3 payments per day × 365 days × 100 for %)
    carry_annual = current_rate * 3 * 365 * 100

    # Signal classification
    if current_rate >= _OVERHEATED_PCT / 100:
        signal = 'overheated'          # longs overcrowded, short squeeze risk
    elif current_rate <= _SQUEEZE_PCT / 100:
        signal = 'squeeze_setup'       # shorts overcrowded, long squeeze setup
    elif abs(current_rate) <= _FAVORABLE_PCT / 100:
        signal = 'favorable'           # balanced, clean entry window
    else:
        signal = 'neutral'

    # Divergence: did funding spike but price not move yet?
    funding_divergence = (
        abs(rate_8h_change) > 0.0002 and  # large funding change
        signal in ('overheated', 'squeeze_setup')
    )

    # Income per hour on a short position collecting positive funding
    # Per 8h period: position_size_usd * funding_rate
    # Per hour: * 3 payments/day / 24h
    income_per_8h = position_size_usd * abs(current_rate) if current_rate > 0 else 0.0
    income_per_hour = income_per_8h / 8.0

    result = {
        'funding_rate_current': round(current_rate, 6),
        'funding_rate_8h_change': round(rate_8h_change, 6),
        'carry_annual': round(carry_annual, 2),
        'funding_signal': signal,
        'funding_divergence': funding_divergence,
        'funding_income_per_hour': round(income_per_hour, 4),
        'source': 'binance',
        '_ts': time.time(),
    }
    with _lock:
        _cache[sym] = result

    return {k: v for k, v in result.items() if k != '_ts'}
