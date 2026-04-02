"""
data/cumulative_delta.py

Cumulative delta: net buy vs sell volume pressure per symbol.

Uses Binance aggTrades endpoint (public, no key needed).
Aggregates taker buy vs taker sell volume over a rolling window.
Positive delta = buyers in control. Negative = sellers.

Returns:
  delta_value   : raw net delta (buy_vol - sell_vol) in base units
  delta_pct     : delta as % of total volume (-1.0 to +1.0)
  delta_trend   : 'bullish' | 'bearish' | 'neutral'
  delta_accel   : whether delta is accelerating in current direction
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
_WINDOW_SECONDS = 1800       # 30-min rolling window
_BINANCE_FUTURES_BASE = "https://fapi.binance.com"
_BINANCE_SPOT_BASE    = "https://api.binance.com"
_CACHE_TTL_SECONDS    = 60   # refresh every 60s per symbol

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: Dict[str, dict] = {}
_lock = threading.Lock()


def _symbol_to_binance(symbol: str) -> str:
    """Convert BTC-USDC or BTCUSDT → BTCUSDT for Binance REST."""
    return symbol.replace('-', '').replace('USDC', 'USDT').upper()


def _fetch_agg_trades(symbol: str, window_ms: int = 1_800_000) -> Optional[list]:
    """
    Fetch recent aggTrades from Binance futures (fallback: spot).
    Returns list of trade dicts or None on failure.
    """
    if not _REQUESTS_OK:
        return None

    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - window_ms
    bsym     = _symbol_to_binance(symbol)

    # Try futures first (most perp pairs live here)
    for base, path in [
        (_BINANCE_FUTURES_BASE, f"/fapi/v1/aggTrades?symbol={bsym}&startTime={start_ms}&endTime={end_ms}&limit=1000"),
        (_BINANCE_SPOT_BASE,    f"/api/v3/aggTrades?symbol={bsym}&startTime={start_ms}&endTime={end_ms}&limit=1000"),
    ]:
        try:
            r = requests.get(base + path, timeout=8)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _compute_delta(trades: list) -> dict:
    """
    Compute cumulative delta from aggTrades list.
    Binance: 'm' field = True means the buyer is market maker = SELL order hit.
    So m=True → taker SELL; m=False → taker BUY.
    """
    buy_vol  = 0.0
    sell_vol = 0.0

    for t in trades:
        qty = float(t.get('q', 0))
        is_sell = bool(t.get('m', False))   # m=True → sell pressure
        if is_sell:
            sell_vol += qty
        else:
            buy_vol  += qty

    total = buy_vol + sell_vol
    if total == 0:
        return {'delta_value': 0.0, 'delta_pct': 0.0,
                'delta_trend': 'neutral', 'delta_accel': False,
                'buy_vol': 0.0, 'sell_vol': 0.0}

    delta_val = buy_vol - sell_vol
    delta_pct = delta_val / total   # -1.0 to +1.0

    if delta_pct > 0.08:
        trend = 'bullish'
    elif delta_pct < -0.08:
        trend = 'bearish'
    else:
        trend = 'neutral'

    return {
        'delta_value':  round(delta_val, 4),
        'delta_pct':    round(delta_pct, 4),
        'delta_trend':  trend,
        'delta_accel':  False,   # updated below if we have prior snapshot
        'buy_vol':      round(buy_vol,  4),
        'sell_vol':     round(sell_vol, 4),
    }


def _split_half_delta(trades: list) -> tuple:
    """Compute delta for first vs second half of the window to detect acceleration."""
    if not trades:
        return 0.0, 0.0

    mid = len(trades) // 2
    first  = trades[:mid]
    second = trades[mid:]

    def _net(chunk):
        b = sum(float(t['q']) for t in chunk if not t.get('m', True))
        s = sum(float(t['q']) for t in chunk if     t.get('m', True))
        tot = b + s
        return (b - s) / tot if tot > 0 else 0.0

    return _net(first), _net(second)


def get_cumulative_delta(symbol: str) -> dict:
    """
    Public API. Returns cumulative delta dict for given symbol.

    Returns neutral dict on any error (never raises).
    Caches for 60s per symbol.
    """
    _neutral = {
        'delta_value': 0.0,
        'delta_pct':   0.0,
        'delta_trend': 'neutral',
        'delta_accel': False,
        'buy_vol':     0.0,
        'sell_vol':    0.0,
        'source':      'unavailable',
    }

    with _lock:
        cached = _cache.get(symbol)
        if cached and (time.time() - cached['_ts']) < _CACHE_TTL_SECONDS:
            return cached

    trades = _fetch_agg_trades(symbol)
    if not trades:
        return _neutral

    result = _compute_delta(trades)

    # Acceleration check: is delta strengthening in its own direction?
    first_pct, second_pct = _split_half_delta(trades)
    if result['delta_trend'] == 'bullish':
        result['delta_accel'] = second_pct > first_pct + 0.03
    elif result['delta_trend'] == 'bearish':
        result['delta_accel'] = second_pct < first_pct - 0.03

    result['source'] = 'binance_agg'
    result['_ts']    = time.time()

    with _lock:
        _cache[symbol] = result

    return result
