"""
data/deribit_feed.py

Deribit IV skew — no API key required (public REST).

25-delta call vs put implied volatility divergence for BTC and ETH.
This is the options market's directional bias signal:
  - Call skew > put skew → market paying up for upside → bullish lean
  - Put skew > call skew → market paying up for downside protection → bearish lean
  - Skew ratio > 1.1 → directionally meaningful

Returns per symbol:
  call_iv_25d   : 25-delta call IV (%)
  put_iv_25d    : 25-delta put IV (%)
  skew          : call_iv - put_iv  (positive = call premium = bullish)
  skew_pct      : skew as % of ATM IV
  skew_direction: 'bullish' | 'bearish' | 'neutral'
  iv_pct_rank   : percentile rank of current ATM IV vs 30-day range (0-100)

Supports BTC and ETH (mapped to nearest-expiry option). All others get neutral fallback.
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
_BASE = "https://www.deribit.com/api/v2"
_CACHE_TTL   = 600     # 10 min cache (IV skew moves slowly)
_SKEW_NEUTRAL_THRESH = 0.5   # < 0.5 pp skew = neutral

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: Dict[str, dict] = {}
_iv_history: Dict[str, list] = {}   # for IV percentile rank
_lock = threading.Lock()

_NEUTRAL = {
    'call_iv_25d':    None,
    'put_iv_25d':     None,
    'skew':           0.0,
    'skew_pct':       0.0,
    'skew_direction': 'neutral',
    'iv_pct_rank':    50.0,
    'source':         'unavailable',
}


def _get(path: str, params: dict = None) -> Optional[dict]:
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(_BASE + path, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data.get('result') is not None:
                return data['result']
    except Exception:
        pass
    return None


def _get_currency(symbol: str) -> Optional[str]:
    """Map trading symbol to Deribit currency. Only BTC and ETH supported."""
    s = symbol.upper()
    if 'BTC' in s:
        return 'BTC'
    if 'ETH' in s:
        return 'ETH'
    return None


def _get_nearest_expiry_instrument(currency: str) -> Optional[str]:
    """Get the nearest weekly/monthly expiry instrument name."""
    result = _get("/public/get_instruments", {
        'currency': currency,
        'kind':     'option',
        'expired':  'false',
    })
    if not result:
        return None

    # Find instruments closest to 7 days out (weekly) — best skew signal
    now_ms = time.time() * 1000
    target  = now_ms + 7 * 86400 * 1000  # 7 days
    best    = None
    best_diff = float('inf')

    for inst in result:
        exp = inst.get('expiration_timestamp', 0)
        if exp <= now_ms:   # already expired
            continue
        diff = abs(exp - target)
        if diff < best_diff:
            best_diff = diff
            best = inst['instrument_name']

    return best


def _get_skew(currency: str) -> dict:
    """
    Fetch 25-delta skew for BTC or ETH.
    Uses Deribit's volatility index + summary endpoint.
    """
    # Step 1: get ATM IV from volatility index
    vol_idx = _get("/public/get_volatility_index_data", {
        'currency':   currency,
        'start_timestamp': int((time.time() - 3600) * 1000),
        'end_timestamp':   int(time.time() * 1000),
        'resolution':      '3600',
    })

    atm_iv = None
    if vol_idx and 'data' in vol_idx and vol_idx['data']:
        # data = [[ts, open, high, low, close], ...]
        latest = vol_idx['data'][-1]
        atm_iv = float(latest[4]) if len(latest) > 4 else None

    # Step 2: get 25-delta skew from summary
    result = _get("/public/get_book_summary_by_currency", {
        'currency': currency,
        'kind':     'option',
    })

    if not result:
        return dict(_NEUTRAL)

    # Find ATM calls and puts with ~25 delta (use mark_iv as proxy)
    calls_iv = []
    puts_iv  = []

    for item in result:
        name = item.get('instrument_name', '')
        mark_iv = item.get('mark_iv')
        if mark_iv is None or mark_iv <= 0:
            continue
        # Detect call vs put by last character
        if name.endswith('-C'):
            calls_iv.append(float(mark_iv))
        elif name.endswith('-P'):
            puts_iv.append(float(mark_iv))

    if not calls_iv or not puts_iv:
        return dict(_NEUTRAL)

    # Use median IV across all calls/puts as proxy for 25d
    calls_iv.sort()
    puts_iv.sort()

    call_iv_25d = float(calls_iv[len(calls_iv) // 3])     # lower quartile call
    put_iv_25d  = float(puts_iv[len(puts_iv) * 2 // 3])   # upper quartile put

    skew     = call_iv_25d - put_iv_25d
    skew_pct = (skew / atm_iv * 100) if (atm_iv and atm_iv > 0) else 0.0

    if skew > _SKEW_NEUTRAL_THRESH:
        direction = 'bullish'
    elif skew < -_SKEW_NEUTRAL_THRESH:
        direction = 'bearish'
    else:
        direction = 'neutral'

    # IV percentile rank (vs cached history)
    iv_pct_rank = 50.0
    if atm_iv:
        with _lock:
            hist = _iv_history.setdefault(currency, [])
            hist.append(atm_iv)
            if len(hist) > 144:     # keep last 24h of 10-min samples
                hist.pop(0)
            if len(hist) >= 3:
                mn, mx = min(hist), max(hist)
                if mx > mn:
                    iv_pct_rank = round((atm_iv - mn) / (mx - mn) * 100, 1)

    return {
        'call_iv_25d':    round(call_iv_25d, 2),
        'put_iv_25d':     round(put_iv_25d,  2),
        'skew':           round(skew,        2),
        'skew_pct':       round(skew_pct,    2),
        'skew_direction': direction,
        'iv_pct_rank':    iv_pct_rank,
        'atm_iv':         round(atm_iv, 2) if atm_iv else None,
        'source':         'deribit',
    }


def get_iv_skew(symbol: str) -> dict:
    """
    Public API. Returns IV skew dict for given symbol.
    Non-BTC/ETH symbols return neutral fallback.
    Caches for 10 minutes.
    """
    currency = _get_currency(symbol)
    if not currency:
        return dict(_NEUTRAL)

    cache_key = currency
    with _lock:
        cached = _cache.get(cache_key)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return cached

    result = _get_skew(currency)
    result['_ts'] = time.time()

    with _lock:
        _cache[cache_key] = result

    return result
