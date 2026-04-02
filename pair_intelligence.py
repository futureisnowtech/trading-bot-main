"""
pair_intelligence.py — Per-pair win rate by hour, volatility profile, correlation cluster.

Reads from the trade_attribution table to build intelligence per symbol.
Used by signal_engine.py and position_manager.py for score adjustments.

Outputs per symbol:
  hourly_win_rates    : dict {hour: win_rate} for last 90 days
  best_hours          : list of top 3 hours by win rate (min 5 trades)
  avg_vol_per_session : session (ASIA/LONDON/NY) volatility profile
  corr_cluster        : which correlation cluster this pair belongs to
  pair_edge_score     : 0-1, overall edge quality for this pair
  trade_count_90d     : total trades in last 90 days
  win_rate_90d        : overall win rate last 90 days
  avg_pnl_90d         : average P&L per trade (after fees)
"""

import logging
import time
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Correlation clusters — manually seeded, updated by _update_clusters() after data accumulates
# These group assets that tend to move together; helps avoid over-concentration
_BASE_CLUSTERS = {
    'BTC_CORE':  ['BTCUSDT', 'ETHUSDT', 'WBTCUSDT'],
    'SOL_DEFI':  ['SOLUSDT', 'RAYUSDT', 'JUPUSDT', 'MEWUSDT'],
    'ETH_L2':    ['ETHUSDT', 'ARBUSDT', 'OPUSDT', 'MATICUSDT', 'STRKUSDT'],
    'AI_TOKENS': ['FETUSDT', 'AGIXUSDT', 'RNDRUSDT', 'WLDUSDT'],
    'MEME':      ['DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'FLOKIUSDT', 'BONKUSDT'],
    'BNB_CHAIN': ['BNBUSDT', 'CAKEUSDT'],
    'BLUE_CHIP': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT'],
}

_CACHE_TTL = 3600   # 1 hour
_lock = threading.RLock()
_cache: Dict[str, Dict] = {}
_cluster_cache: Dict[str, str] = {}   # symbol → cluster name


def _get_db():
    """Get trade logger connection (lazy import to avoid circular deps)."""
    try:
        from logging_db.trade_logger import get_logger
        return get_logger()
    except Exception:
        return None


def _query_pair_trades(symbol: str, days: int = 90) -> List[Dict]:
    """
    Query trade_attribution for completed trades on this symbol.
    Returns list of {won, pnl_usd, entry_ts, technical_score, ml_score, composite_score}.
    """
    db = _get_db()
    if db is None:
        return []

    try:
        cutoff = time.time() - days * 86400
        conn = db.conn
        rows = conn.execute("""
            SELECT t.won, t.pnl_usd, t.ts, ta.technical_score, ta.ml_score, ta.composite_score
            FROM trades t
            LEFT JOIN trade_attribution ta ON t.id = ta.trade_id
            WHERE t.symbol LIKE ? AND t.ts > ? AND t.action = 'SELL'
              AND t.paper = 1
        """, (f'%{symbol.replace("USDT","")}%', cutoff)).fetchall()

        return [
            {
                'won': bool(r[0]),
                'pnl_usd': float(r[1] or 0),
                'ts': float(r[2] or 0),
                'technical_score': float(r[3] or 0),
                'ml_score': float(r[4] or 0),
                'composite_score': float(r[5] or 0),
            }
            for r in rows
        ]
    except Exception as e:
        logger.debug(f'[pair_intel] DB query error for {symbol}: {e}')
        return []


def _compute_hourly_win_rates(trades: List[Dict]) -> Dict[int, float]:
    """Compute win rate by UTC hour (0-23) for a list of trades."""
    from collections import defaultdict
    by_hour_wins   = defaultdict(int)
    by_hour_total  = defaultdict(int)

    for t in trades:
        ts = t['ts']
        if ts <= 0:
            continue
        import datetime
        hour = datetime.datetime.utcfromtimestamp(ts).hour
        by_hour_total[hour] += 1
        if t['won']:
            by_hour_wins[hour] += 1

    result = {}
    for h in range(24):
        total = by_hour_total[h]
        if total >= 3:
            result[h] = round(by_hour_wins[h] / total, 3)
    return result


def _get_cluster(symbol: str) -> str:
    """Return the correlation cluster name for a symbol."""
    if symbol in _cluster_cache:
        return _cluster_cache[symbol]

    # Find first matching cluster
    for cluster_name, members in _BASE_CLUSTERS.items():
        if symbol in members:
            _cluster_cache[symbol] = cluster_name
            return cluster_name

    # Default: assign to generic by base asset
    base = symbol.replace('USDT', '')
    cluster = f'GENERIC_{base}'
    _cluster_cache[symbol] = cluster
    return cluster


def _compute_session_volatility(trades: List[Dict]) -> Dict[str, float]:
    """
    Average abs(pnl_pct) per session.
    Sessions: ASIA (0-8 UTC), LONDON (8-16 UTC), NY (16-24 UTC).
    """
    import datetime
    sessions = {'ASIA': [], 'LONDON': [], 'NY': []}

    for t in trades:
        ts = t['ts']
        if ts <= 0:
            continue
        hour = datetime.datetime.utcfromtimestamp(ts).hour
        pnl = abs(t['pnl_usd'])

        if hour < 8:
            sessions['ASIA'].append(pnl)
        elif hour < 16:
            sessions['LONDON'].append(pnl)
        else:
            sessions['NY'].append(pnl)

    result = {}
    for sess, vals in sessions.items():
        result[sess] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return result


def get_pair_intelligence(symbol: str, force_refresh: bool = False) -> Dict:
    """
    Return intelligence profile for a trading pair.

    Args:
        symbol:        e.g. 'BTCUSDT'
        force_refresh: bypass cache

    Returns:
        dict with hourly_win_rates, best_hours, corr_cluster, pair_edge_score, etc.
    """
    neutral = {
        'symbol': symbol,
        'hourly_win_rates': {},
        'best_hours': [],
        'session_volatility': {'ASIA': 0.0, 'LONDON': 0.0, 'NY': 0.0},
        'corr_cluster': _get_cluster(symbol),
        'pair_edge_score': 0.5,
        'trade_count_90d': 0,
        'win_rate_90d': 0.5,
        'avg_pnl_90d': 0.0,
        'data_quality': 'insufficient',
    }

    with _lock:
        cached = _cache.get(symbol)
        if not force_refresh and cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

    trades = _query_pair_trades(symbol, days=90)

    if len(trades) < 5:
        result = neutral.copy()
        result['_ts'] = time.time()
        with _lock:
            _cache[symbol] = result
        return neutral

    # Win rate
    wins = sum(1 for t in trades if t['won'])
    wr = wins / len(trades)

    # Average P&L
    avg_pnl = sum(t['pnl_usd'] for t in trades) / len(trades)

    # Hourly win rates
    hourly_wr = _compute_hourly_win_rates(trades)

    # Best hours: top 3 by win rate (min 5 trades each)
    from collections import defaultdict
    by_hour = defaultdict(list)
    import datetime
    for t in trades:
        if t['ts'] > 0:
            h = datetime.datetime.utcfromtimestamp(t['ts']).hour
            by_hour[h].append(t['won'])

    best_hours = sorted(
        [(h, sum(wins)/len(wins), len(wins))
         for h, wins in by_hour.items()
         if len(wins) >= 5],
        key=lambda x: x[1],
        reverse=True
    )[:3]

    # Session volatility
    session_vol = _compute_session_volatility(trades)

    # Pair edge score: composite of WR and avg P&L quality
    edge_score = (wr * 0.6) + (min(1.0, max(0.0, (avg_pnl + 5) / 10)) * 0.4)
    edge_score = round(float(edge_score), 4)

    # Data quality
    if len(trades) >= 50:
        quality = 'high'
    elif len(trades) >= 20:
        quality = 'medium'
    else:
        quality = 'low'

    result = {
        'symbol': symbol,
        'hourly_win_rates': hourly_wr,
        'best_hours': [(h, round(wr, 3), cnt) for h, wr, cnt in best_hours],
        'session_volatility': session_vol,
        'corr_cluster': _get_cluster(symbol),
        'pair_edge_score': edge_score,
        'trade_count_90d': len(trades),
        'win_rate_90d': round(wr, 4),
        'avg_pnl_90d': round(avg_pnl, 4),
        'data_quality': quality,
        '_ts': time.time(),
    }

    with _lock:
        _cache[symbol] = result

    return {k: v for k, v in result.items() if k != '_ts'}


def get_current_hour_multiplier(symbol: str) -> float:
    """
    Return a sizing multiplier for current UTC hour based on historical WR.

    Returns:
        1.2 if current hour has WR >= 60%
        1.0 if WR 45-60% or insufficient data
        0.8 if WR < 40%
    """
    import datetime
    current_hour = datetime.datetime.utcnow().hour
    intel = get_pair_intelligence(symbol)
    hourly_wr = intel.get('hourly_win_rates', {})

    if current_hour not in hourly_wr:
        return 1.0   # no data for this hour

    wr = hourly_wr[current_hour]
    if wr >= 0.60:
        return 1.2
    elif wr < 0.40:
        return 0.8
    return 1.0


def get_cluster_exposure(open_symbols: List[str]) -> Dict[str, int]:
    """
    Return how many open positions are in each correlation cluster.
    Used by risk_engine.py to enforce cluster concentration limits.
    """
    from collections import Counter
    return dict(Counter(_get_cluster(sym) for sym in open_symbols))


def get_portfolio_correlation_flag(new_symbol: str,
                                    open_symbols: List[str],
                                    max_cluster_positions: int = 3) -> bool:
    """
    Returns True if adding new_symbol would breach cluster concentration limit.
    Caller should reduce size or skip if True.
    """
    if not open_symbols:
        return False

    cluster = _get_cluster(new_symbol)
    exposure = get_cluster_exposure(open_symbols)
    current_count = exposure.get(cluster, 0)

    return current_count >= max_cluster_positions


def update_pair_stats_from_db(symbols: Optional[List[str]] = None):
    """
    Refresh pair intelligence cache from DB.
    Called by learning_loop.py after trade closes.
    """
    if symbols is None:
        # Refresh all cached symbols
        with _lock:
            symbols = list(_cache.keys())

    for sym in symbols:
        try:
            get_pair_intelligence(sym, force_refresh=True)
        except Exception as e:
            logger.debug(f'[pair_intel] refresh error {sym}: {e}')
