"""
indicators/open_interest.py — Open Interest analysis for Binance perps.

Tracks OI changes to determine whether price moves are backed by new money
or are liquidation-driven (weak).

OI trend signals:
  price_rising + oi_rising + oi_delta > 0  → STRONG_BULL (real buying)
  price_rising + oi_falling               → SHORT_SQUEEZE (weak, may reverse)
  price_falling + oi_rising + oi_delta < 0 → STRONG_BEAR (real selling)
  price_falling + oi_falling              → LONG_LIQUIDATION (may bounce)

Outputs:
  oi_change_pct_4h    : OI % change over 4h
  oi_delta            : OI change × price direction
  ls_ratio            : long/short ratio
  ls_ratio_change_1h  : rate of change in L/S ratio
  oi_signal           : 'strong_bull' | 'short_squeeze' | 'strong_bear' | 'long_liq' | 'neutral'
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
_oi_history: dict = {}      # symbol → list of (ts, oi_value)
_ls_history: dict = {}      # symbol → list of (ts, ls_ratio)


def _fetch_oi(symbol: str) -> Optional[float]:
    """Fetch current open interest in base currency."""
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(f'{_BINANCE_BASE}/fapi/v1/openInterest',
                         params={'symbol': symbol}, timeout=8)
        if r.status_code == 200:
            return float(r.json().get('openInterest', 0))
    except Exception:
        pass
    return None


def _fetch_ls_ratio(symbol: str) -> Optional[float]:
    """Fetch top-trader long/short account ratio."""
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(f'{_BINANCE_BASE}/futures/data/topLongShortAccountRatio',
                         params={'symbol': symbol, 'period': '1h', 'limit': 2}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data:
                return float(data[-1].get('longShortRatio', 1.0))
    except Exception:
        pass
    return None


def get_oi_signal(symbol: str, current_price: float, prev_price: float) -> dict:
    """
    Returns open interest analysis.

    Args:
        symbol:        e.g. 'BTCUSDT'
        current_price: latest mark/close price
        prev_price:    price 4h ago (for direction detection)
    """
    neutral = {
        'oi_change_pct_4h': 0.0,
        'oi_delta': 0.0,
        'ls_ratio': 1.0,
        'ls_ratio_change_1h': 0.0,
        'oi_signal': 'neutral',
        'source': 'unavailable',
    }

    sym = symbol.upper()
    with _lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

    now = time.time()

    # Fetch current OI
    current_oi = _fetch_oi(sym)
    if current_oi is None:
        return neutral

    # Store in history
    with _lock:
        hist = _oi_history.setdefault(sym, [])
        hist.append((now, current_oi))
        # Keep 8h of 2-min samples = 240 entries
        while hist and (now - hist[0][0]) > 28800:
            hist.pop(0)

    # OI 4h change
    oi_change_pct_4h = 0.0
    with _lock:
        hist = _oi_history.get(sym, [])
    four_h_ago = now - 14400
    past = [v for ts, v in hist if ts <= four_h_ago]
    if past:
        oi_4h_ago = past[-1]
        if oi_4h_ago > 0:
            oi_change_pct_4h = (current_oi - oi_4h_ago) / oi_4h_ago * 100

    # OI delta = change × price direction
    price_dir = 1 if current_price > prev_price else -1 if current_price < prev_price else 0
    oi_delta = oi_change_pct_4h * price_dir

    # Long/short ratio
    ls_ratio = _fetch_ls_ratio(sym) or 1.0
    with _lock:
        lsh = _ls_history.setdefault(sym, [])
        lsh.append((now, ls_ratio))
        while lsh and (now - lsh[0][0]) > 7200:
            lsh.pop(0)

    ls_change_1h = 0.0
    with _lock:
        lsh = _ls_history.get(sym, [])
    one_h_ago = now - 3600
    past_ls = [v for ts, v in lsh if ts <= one_h_ago]
    if past_ls:
        ls_change_1h = ls_ratio - past_ls[-1]

    # Signal classification
    price_rising = current_price > prev_price
    oi_rising = oi_change_pct_4h > 1.0

    if price_rising and oi_rising and oi_delta > 0:
        signal = 'strong_bull'
    elif price_rising and not oi_rising:
        signal = 'short_squeeze'
    elif not price_rising and oi_rising and oi_delta < 0:
        signal = 'strong_bear'
    elif not price_rising and not oi_rising:
        signal = 'long_liq'
    else:
        signal = 'neutral'

    result = {
        'oi_change_pct_4h': round(oi_change_pct_4h, 3),
        'oi_delta': round(oi_delta, 3),
        'ls_ratio': round(ls_ratio, 3),
        'ls_ratio_change_1h': round(ls_change_1h, 3),
        'oi_signal': signal,
        'source': 'binance',
        '_ts': now,
    }
    with _lock:
        _cache[sym] = result

    return {k: v for k, v in result.items() if k != '_ts'}
