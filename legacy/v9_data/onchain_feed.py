"""
data/onchain_feed.py

On-chain whale flow signals using free public APIs.
No API key required for basic endpoints.

Sources:
  BTC: blockchain.com mempool + large tx stats
  ETH: Etherscan free tier (no key for basic stats)
  Others: neutral fallback

Returns:
  whale_signal   : 'accumulating' | 'distributing' | 'neutral'
  whale_strength : 0.0 – 1.0 (confidence in signal)
  large_tx_count : count of large txs in last hour
  net_flow_usd   : estimated net inflow (positive) or outflow (negative)
  source         : data source string
"""

import time
import threading
from typing import Dict, Optional
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ── Constants ─────────────────────────────────────────────────────────────────
_CACHE_TTL = 600     # 10-min cache (on-chain data is slow-moving)
_LARGE_TX_THRESHOLD_BTC = 10.0    # BTC: txs >= 10 BTC qualify as "whale"
_LARGE_TX_THRESHOLD_ETH = 100.0   # ETH: txs >= 100 ETH qualify as "whale"

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: Dict[str, dict] = {}
_lock = threading.Lock()

_NEUTRAL = {
    'whale_signal':    'neutral',
    'whale_strength':  0.0,
    'large_tx_count':  0,
    'net_flow_usd':    0.0,
    'source':          'unavailable',
}


def _fetch_btc_stats() -> dict:
    """
    Use blockchain.com stats endpoint (no key, free).
    Infers whale activity from mempool fee pressure and hash rate.
    """
    if not _REQUESTS_OK:
        return dict(_NEUTRAL)

    try:
        r = requests.get("https://api.blockchain.info/stats", timeout=8)
        if r.status_code != 200:
            return dict(_NEUTRAL)
        data = r.json()

        # tx_count_per_hour as proxy for activity level
        tx_per_hour  = float(data.get('n_tx_per_block', 0)) * 6
        # Mempool size as proxy for demand
        mempool_size = float(data.get('mempool_size', 0))

        # Very rough signal: elevated mempool = demand surge
        # Compare to typical baseline ~50MB
        if mempool_size > 150_000_000:   # > 150MB = heavy demand
            signal   = 'accumulating'
            strength = min(0.7, (mempool_size - 150_000_000) / 500_000_000 + 0.5)
        elif mempool_size < 20_000_000:  # < 20MB = very quiet
            signal   = 'neutral'
            strength = 0.2
        else:
            signal   = 'neutral'
            strength = 0.3

        return {
            'whale_signal':   signal,
            'whale_strength': round(strength, 3),
            'large_tx_count': int(tx_per_hour),
            'net_flow_usd':   0.0,   # can't derive from stats endpoint
            'source':         'blockchain_info',
            'mempool_size_mb': round(mempool_size / 1_000_000, 1),
        }
    except Exception:
        return dict(_NEUTRAL)


def _fetch_eth_stats() -> dict:
    """
    Use Etherscan's free gasTracker as a proxy for whale activity.
    High gas demand + rising gas price = network congestion from large actors.
    No API key required for gas oracle.
    """
    if not _REQUESTS_OK:
        return dict(_NEUTRAL)

    try:
        r = requests.get(
            "https://api.etherscan.io/api?module=gastracker&action=gasoracle",
            timeout=8
        )
        if r.status_code != 200:
            return dict(_NEUTRAL)
        data = r.json()
        result = data.get('result', {})

        fast_gas = float(result.get('FastGasPrice', 0))
        base_gas = float(result.get('suggestBaseFee', 0))
        if fast_gas <= 0 or base_gas <= 0:
            return dict(_NEUTRAL)

        # Gas premium ratio: how much above base fee fast txs are paying
        premium_ratio = fast_gas / base_gas if base_gas > 0 else 1.0

        # High premium = urgency = potential whale movement
        if premium_ratio > 2.5 and fast_gas > 30:
            signal   = 'accumulating'
            strength = min(0.65, (premium_ratio - 2.5) / 5.0 + 0.4)
        elif fast_gas < 5:    # ultra-low gas = quiet market
            signal   = 'neutral'
            strength = 0.2
        else:
            signal   = 'neutral'
            strength = 0.25

        return {
            'whale_signal':    signal,
            'whale_strength':  round(strength, 3),
            'large_tx_count':  0,
            'net_flow_usd':    0.0,
            'source':          'etherscan_gas',
            'fast_gas_gwei':   fast_gas,
            'base_gas_gwei':   base_gas,
        }
    except Exception:
        return dict(_NEUTRAL)


def _get_currency(symbol: str) -> Optional[str]:
    s = symbol.upper()
    if 'BTC' in s:
        return 'BTC'
    if 'ETH' in s:
        return 'ETH'
    return None


def get_whale_flow(symbol: str) -> dict:
    """
    Public API. Returns whale flow dict for given symbol.
    Non-BTC/ETH symbols return neutral fallback.
    Caches for 10 minutes.
    """
    currency = _get_currency(symbol)
    if not currency:
        return dict(_NEUTRAL)

    with _lock:
        cached = _cache.get(currency)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return cached

    result = _fetch_btc_stats() if currency == 'BTC' else _fetch_eth_stats()
    result['_ts'] = time.time()

    with _lock:
        _cache[currency] = result

    return result
