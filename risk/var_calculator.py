"""
risk/var_calculator.py — Historical Value-at-Risk (VaR) calculator.
New capability added in Sprint 1, Task 3.

Uses historical simulation (no distributional assumptions).
VaR(95%) = the P&L loss that is not exceeded 95% of the time.
"""
import math
import sys
import os
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def calc_var(pnl_series: List[float], confidence: float = 0.95) -> float:
    """
    Compute historical VaR for a series of P&L values.

    Args:
        pnl_series: list of P&L values (negative = loss, positive = win)
        confidence: 0.95 for 95% VaR, 0.99 for 99% VaR

    Returns: VaR as a positive dollar amount.
    Example: calc_var(trades, 0.95) = 45.00 means 95% of the time
             you won't lose more than $45.
    """
    if not pnl_series or len(pnl_series) < 5:
        return 0.0
    sorted_pnl = sorted(pnl_series)
    idx = int(math.floor((1 - confidence) * len(sorted_pnl)))
    idx = max(0, min(idx, len(sorted_pnl) - 1))
    return abs(sorted_pnl[idx])


def get_portfolio_var(paper: bool = True, window: int = 90) -> dict:
    """
    Compute VaR from trade history in the database.

    Args:
        paper: whether to use paper or live trades
        window: number of most recent trades to use

    Returns: {var_95, var_99, window_trades, worst_trade}
    """
    try:
        import sqlite3
        from config import DB_PATH, PAPER_TRADING
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=? AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT ?",
            (int(paper), window)
        )
        rows = cur.fetchall()
        conn.close()

        pnl_series = [r['pnl_usd'] for r in rows]
        if not pnl_series:
            return {'var_95': 0.0, 'var_99': 0.0, 'window_trades': 0, 'worst_trade': 0.0}

        return {
            'var_95': round(calc_var(pnl_series, 0.95), 2),
            'var_99': round(calc_var(pnl_series, 0.99), 2),
            'window_trades': len(pnl_series),
            'worst_trade': round(abs(min(pnl_series)), 2),
        }
    except Exception as e:
        return {'error': str(e), 'var_95': 0.0, 'var_99': 0.0, 'window_trades': 0, 'worst_trade': 0.0}
