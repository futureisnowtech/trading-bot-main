"""
data/edge_monitor.py — Rolling edge score monitor with multi-window memory.

Philosophical basis (Philosophical Supplement §1):
  "Require two consecutive windows of degraded performance before acting on it.
   Use it for gradual sizing adjustments, never for abrupt halts."

Window = last 20 closed trades per strategy.
Edge score = profit_factor - 1.0
  0.0 = break even (PF 1.0)
  0.5 = good (PF 1.5)
  1.0 = excellent (PF 2.0)

Response table:
  edge_score >= 0.45          → STRONG/OK  : 1.00× sizing
  edge_score in [0.30, 0.45) → 1 bad window: 0.75× sizing
  edge_score in [0.30, 0.45) → 2+ windows : 0.50× sizing
  edge_score < 0.30           → BLOCKED    : block new entries (PF < 1.30 = neg. EV)
  window_trades < 10          → UNCERTAIN  : no gate (not enough data)

Cached 5 minutes per strategy to avoid hammering the DB each scan cycle.
"""
import os
import sqlite3
from datetime import datetime
from typing import Dict, Tuple

_WINDOW_SIZE = 20           # trades per evaluation window
_BAD_THRESHOLD = 0.45       # PF < 1.45 = degraded window
_BLOCK_THRESHOLD = 0.30     # PF < 1.30 = block entries (negative expected value)
_MIN_TRADES_TO_GATE = 10    # require at least this many trades before gating
_CACHE_MINUTES = 5

_cache: Dict[str, dict] = {}

# Resolve DB path from config without circular import risk
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_PROJ_ROOT, 'logs', 'trades.db')


def _conn():
    if not os.path.exists(_DB_PATH):
        return None
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def get_edge_state(strategy: str, paper: bool = True) -> dict:
    """
    Compute rolling edge state for a strategy.

    Returns:
      edge_score          float   — current window profit_factor - 1
      consecutive_bad     int     — how many consecutive windows were degraded
      sizing_multiplier   float   — 1.0 / 0.75 / 0.50 / 0.0
      should_block        bool    — True when edge_score < BLOCK_THRESHOLD
      window_trades       int     — trades in current evaluation window
      status              str     — 'STRONG' | 'OK' | 'DEGRADED' | 'UNCERTAIN' | 'BLOCKED'
    """
    cached = _cache.get(strategy)
    if cached:
        age = (datetime.now() - cached['computed_at']).total_seconds() / 60
        if age < _CACHE_MINUTES:
            return {k: v for k, v in cached.items() if k != 'computed_at'}

    conn = _conn()
    if conn is None:
        return _default_state(status='UNCERTAIN')

    try:
        cur = conn.cursor()
        # Fetch last 2 windows to detect consecutive degradation
        cur.execute("""
            SELECT pnl_usd FROM trades
            WHERE strategy=? AND paper=? AND pnl_usd != 0
            ORDER BY ts DESC
            LIMIT ?
        """, (strategy, int(paper), _WINDOW_SIZE * 2))
        rows = [r['pnl_usd'] for r in cur.fetchall()]
        conn.close()
    except Exception:
        conn.close()
        return _default_state(status='UNCERTAIN')

    window_trades = min(len(rows), _WINDOW_SIZE)

    if window_trades < _MIN_TRADES_TO_GATE:
        result = _default_state(window_trades=window_trades, status='UNCERTAIN')
        _store_cache(strategy, result)
        return result

    # ── Current window ────────────────────────────────────────────────────────
    current_pnls = rows[:_WINDOW_SIZE]
    edge_score, pf = _compute_edge(current_pnls)

    # ── Previous window (for consecutive-bad detection) ───────────────────────
    prev_bad = False
    if len(rows) >= _WINDOW_SIZE * 2:
        prev_pnls = rows[_WINDOW_SIZE:_WINDOW_SIZE * 2]
        prev_edge, _ = _compute_edge(prev_pnls)
        prev_bad = prev_edge < _BAD_THRESHOLD

    # ── Graduated response ────────────────────────────────────────────────────
    consecutive_bad = 0
    if edge_score < _BAD_THRESHOLD:
        consecutive_bad = 2 if prev_bad else 1

    should_block = edge_score < _BLOCK_THRESHOLD

    if should_block:
        multiplier, status = 0.0, 'BLOCKED'
    elif consecutive_bad >= 2:
        multiplier, status = 0.50, 'DEGRADED'
    elif consecutive_bad == 1:
        multiplier, status = 0.75, 'DEGRADED'
    elif edge_score >= 0.60:
        multiplier, status = 1.0, 'STRONG'
    else:
        multiplier, status = 1.0, 'OK'

    result = {
        'edge_score':          round(edge_score, 3),
        'profit_factor':       round(pf, 3),
        'consecutive_bad':     consecutive_bad,
        'sizing_multiplier':   multiplier,
        'should_block':        should_block,
        'window_trades':       window_trades,
        'status':              status,
    }
    _store_cache(strategy, result)
    return result


def is_in_stop_cooldown(strategy: str, symbol: str, paper: bool = True) -> Tuple[bool, str]:
    """
    Check if a full-stop-loss hit has occurred in the last 30 minutes for this symbol.
    Called before any new entry — prevents re-entering a symbol immediately after a stop.

    Philosophical basis (§6): "After any trade that hits its full stop loss,
    no new entries for 30 minutes in that market."
    """
    conn = _conn()
    if conn is None:
        return False, ''
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ts FROM trades
            WHERE strategy=? AND symbol=? AND paper=?
              AND pnl_usd < 0
              AND (LOWER(notes) LIKE '%stop%' OR LOWER(notes) LIKE '%hard stop%')
              AND ts >= datetime('now', '-30 minutes')
            ORDER BY ts DESC LIMIT 1
        """, (strategy, symbol, int(paper)))
        row = cur.fetchone()
        conn.close()
        if row:
            return True, f"30-min stop cooldown active: {symbol} stopped out at {row['ts'][:19]}"
        return False, ''
    except Exception:
        return False, ''


def format_edge_context(state: dict) -> str:
    """One-line summary for agent context injection."""
    return (
        f"Edge: {state['status']} | PF={state.get('profit_factor', '?'):.2f} "
        f"| score={state['edge_score']:.2f} "
        f"| {state['window_trades']} trades "
        f"| consecutive_bad={state['consecutive_bad']} "
        f"| sizing={state['sizing_multiplier']:.0%}"
    )


def invalidate_cache(strategy: str = None) -> None:
    """Call after any trade closes to force fresh recomputation."""
    if strategy:
        _cache.pop(strategy, None)
    else:
        _cache.clear()


def _compute_edge(pnls: list) -> Tuple[float, float]:
    gross_wins   = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    if gross_losses == 0:
        pf = 2.0 if gross_wins > 0 else 1.0
    else:
        pf = gross_wins / gross_losses
    return max(0.0, pf - 1.0), pf


def _default_state(window_trades: int = 0, status: str = 'UNCERTAIN') -> dict:
    return {
        'edge_score':        0.50,   # neutral — don't gate on insufficient data
        'profit_factor':     1.50,
        'consecutive_bad':   0,
        'sizing_multiplier': 1.0,
        'should_block':      False,
        'window_trades':     window_trades,
        'status':            status,
    }


def _store_cache(strategy: str, result: dict) -> None:
    entry = dict(result)
    entry['computed_at'] = datetime.now()
    _cache[strategy] = entry
