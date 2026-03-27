"""
risk/edge_monitor.py — Rolling edge monitor per market.

Reads the last N completed trades for a given market and computes:
  - win_rate_20:     fraction of winning trades in the window
  - profit_factor_20: gross_profit / gross_loss
  - sharpe_20:       mean(pnl) / std(pnl) (raw, not annualized — trade-basis Sharpe)
  - edge_score:      weighted composite [0, 1]

Edge score formula (weights sum to 1.0):
  edge = 0.40 * norm_wr + 0.35 * norm_pf + 0.25 * norm_sharpe

  Normalisation ranges (calibrated to a $500 account):
    win_rate:       [0.30, 0.70] → [0, 1]
    profit_factor:  [0.80, 2.00] → [0, 1]
    sharpe:         [-1.0, 2.0]  → [0, 1]

Auto-actions (stateful — resets on process restart, by design):
  edge < 0.30 for 2 consecutive windows → reduce next position 50% + dashboard notify
  edge > 0.70 for 2 consecutive windows → increase toward Kelly max + dashboard notify

Market mapping from strategy name:
  strategy containing 'crypto' or 'perp'  → 'crypto'
  strategy containing 'poly'              → 'polymarket'
  strategy containing 'futures' or 'mes'  → 'mes'
"""
import os
import sys
import math
import sqlite3
from datetime import datetime
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, PAPER_TRADING

# ─── Constants ───────────────────────────────────────────────────────────────
WINDOW = 20               # rolling trade window
EDGE_LOW_THRESHOLD  = 0.30  # below this = degraded edge
EDGE_HIGH_THRESHOLD = 0.70  # above this = strong edge
CONSECUTIVE_TRIGGER = 2   # windows in a row before auto-action fires

# ─── Consecutive window counters (in-memory, resets on restart) ──────────────
_consecutive_low:  dict = {}   # market → int (consecutive low-edge windows)
_consecutive_high: dict = {}   # market → int (consecutive high-edge windows)

# ─── Market-name helpers ─────────────────────────────────────────────────────

def strategy_to_market(strategy: str) -> str:
    """Map a strategy name string to a market label."""
    s = strategy.lower()
    if 'poly' in s:
        return 'polymarket'
    if 'futures' in s or 'mes' in s or 'scalp' in s:
        return 'mes'
    return 'crypto'   # default — covers crypto_ai, crypto_macd, perp, etc.


def _market_strategy_filter(market: str) -> str:
    """Return a SQL LIKE pattern that matches strategy names for the given market."""
    if market == 'polymarket':
        return '%poly%'
    if market == 'mes':
        return '%futures%'
    # crypto catches everything except the above two
    return '%'   # handled with an exclusion clause in the query


# ─── Data reader ─────────────────────────────────────────────────────────────

def _get_market_trades(market: str, window: int, paper: bool) -> list:
    """
    Return the most recent `window` completed trades for `market`.

    Each row is a dict with at minimum: pnl_usd, value_usd, fee_usd, won.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        if market == 'polymarket':
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND strategy LIKE '%poly%' AND pnl_usd != 0 "
                "ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()
        elif market == 'mes':
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND (strategy LIKE '%futures%' OR strategy LIKE '%mes%') "
                "AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()
        else:
            # crypto = everything that is not poly or mes
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND strategy NOT LIKE '%poly%' "
                "AND strategy NOT LIKE '%futures%' AND strategy NOT LIKE '%mes%' "
                "AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()

        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ─── Edge computation ─────────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _compute_edge_score(trades: list) -> dict:
    """
    Compute edge metrics from a list of trade dicts.

    Returns dict with: win_rate, profit_factor, sharpe, edge_score, n_trades.
    """
    n = len(trades)
    if n == 0:
        return {'win_rate': 0.0, 'profit_factor': 0.0, 'sharpe': 0.0,
                'edge_score': 0.0, 'n_trades': 0}

    pnls = [float(t.get('pnl_usd', 0.0)) for t in trades]

    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = len(wins) / n

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / max(gross_loss, 1e-10)

    # Trade-basis Sharpe: mean / std of individual P&L
    if n < 2:
        sharpe = 0.0
    else:
        mean_pnl = sum(pnls) / n
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)
        std_pnl  = math.sqrt(variance) if variance > 0 else 1e-10
        sharpe   = mean_pnl / std_pnl

    # Normalise each component to [0, 1]
    norm_wr     = _clamp((win_rate - 0.30) / (0.70 - 0.30), 0.0, 1.0)
    norm_pf     = _clamp((profit_factor - 0.80) / (2.00 - 0.80), 0.0, 1.0)
    norm_sharpe = _clamp((sharpe - (-1.0)) / (2.0 - (-1.0)), 0.0, 1.0)

    edge_score = 0.40 * norm_wr + 0.35 * norm_pf + 0.25 * norm_sharpe

    return {
        'win_rate':      round(win_rate, 4),
        'profit_factor': round(profit_factor, 4),
        'sharpe':        round(sharpe, 4),
        'edge_score':    round(_clamp(edge_score, 0.0, 1.0), 4),
        'n_trades':      n,
    }


# ─── Public API ──────────────────────────────────────────────────────────────

def get_edge_score(
    market: str = 'crypto',
    window: int = WINDOW,
    paper: Optional[bool] = None,
) -> dict:
    """
    Compute the rolling edge score for `market`.

    Args:
        market:  'crypto' | 'polymarket' | 'mes'
        window:  Number of trades in the rolling window (default 20).
        paper:   True = paper trades, False = live. Defaults to config.PAPER_TRADING.

    Returns dict:
        edge_score     : float [0, 1]
        win_rate       : float [0, 1]
        profit_factor  : float ≥ 0
        sharpe         : float
        n_trades       : int   (actual trades found, ≤ window)
        sufficient     : bool  True when n_trades >= window (reliable signal)
        market         : str
    """
    if paper is None:
        paper = PAPER_TRADING

    trades = _get_market_trades(market, window, paper)
    metrics = _compute_edge_score(trades)

    metrics['sufficient'] = metrics['n_trades'] >= window
    metrics['market']     = market

    return metrics


def check_edge_actions(
    market: str = 'crypto',
    paper: Optional[bool] = None,
) -> Optional[str]:
    """
    Evaluate current edge and fire auto-actions if thresholds are breached
    for CONSECUTIVE_TRIGGER consecutive windows.

    Returns:
        'size_down' — if edge is degraded and auto-action fired
        'size_up'   — if edge is strong and auto-action fired
        None        — no action taken
    """
    if paper is None:
        paper = PAPER_TRADING

    metrics = get_edge_score(market=market, paper=paper)

    # Need at least half the window filled for the signal to be meaningful
    if metrics['n_trades'] < WINDOW // 2:
        return None

    score = metrics['edge_score']
    action_taken = None

    # ── Low edge tracking ─────────────────────────────────────────────────────
    if score < EDGE_LOW_THRESHOLD:
        _consecutive_low[market]  = _consecutive_low.get(market, 0) + 1
        _consecutive_high[market] = 0
        if _consecutive_low[market] >= CONSECUTIVE_TRIGGER:
            _fire_notification(
                market,
                f"[EdgeMonitor] {market.upper()} edge degraded: "
                f"score={score:.2f} (WR={metrics['win_rate']:.0%} "
                f"PF={metrics['profit_factor']:.2f} Sharpe={metrics['sharpe']:.2f}) — "
                f"position size REDUCED 50% until edge recovers",
                level='WARNING',
            )
            action_taken = 'size_down'
    # ── High edge tracking ────────────────────────────────────────────────────
    elif score > EDGE_HIGH_THRESHOLD:
        _consecutive_high[market]  = _consecutive_high.get(market, 0) + 1
        _consecutive_low[market]   = 0
        if _consecutive_high[market] >= CONSECUTIVE_TRIGGER:
            _fire_notification(
                market,
                f"[EdgeMonitor] {market.upper()} edge strong: "
                f"score={score:.2f} (WR={metrics['win_rate']:.0%} "
                f"PF={metrics['profit_factor']:.2f} Sharpe={metrics['sharpe']:.2f}) — "
                f"position size allowed toward Kelly max",
                level='INFO',
            )
            action_taken = 'size_up'
    else:
        _consecutive_low[market]  = 0
        _consecutive_high[market] = 0

    return action_taken


def get_edge_size_factor(
    market: str = 'crypto',
    paper: Optional[bool] = None,
) -> float:
    """
    Return the size factor from current edge state.

    When edge is in CONSECUTIVE_TRIGGER consecutive low windows → 0.50
    Otherwise → 1.00 (edge_score is used as a multiplier in unified_sizer separately)
    """
    if paper is None:
        paper = PAPER_TRADING

    consecutive_low = _consecutive_low.get(market, 0)
    if consecutive_low >= CONSECUTIVE_TRIGGER:
        return 0.50
    return 1.00


def _fire_notification(market: str, message: str, level: str = 'INFO') -> None:
    """Write to system_events → dashboard Notifications panel."""
    try:
        from logging_db.trade_logger import log_event
        log_event(level, 'edge_monitor', message)
    except Exception:
        print(f"[edge_monitor] {level}: {message}")
