"""
dashboard/data/account.py — Ledgerless Truth Layer (v19.1.ARCH)
Strictly broker-first. PnL derived from (Live Balance - Seed Capital).
"""

from datetime import datetime
import db as _db
from data.balance import get_coinbase_balance, get_spot_balance_summary

_q = _db._q
_q1 = _db._q1

# Ledgerless Cutoff: Any trade before this is considered "Legacy/Contaminated"
LEDGERLESS_CUTOFF = "2026-05-26 00:00:00"

def get_account():
    """
    v19.1: Absolute Account Truth.
    Equity = Perp Balance + Spot Equity.
    PnL = Equity - Starting Capital.
    """
    try:
        from runtime.live_account import get_live_account_size
        base = float(get_live_account_size())
    except Exception:
        base = 5000.0

    perp = get_coinbase_balance()
    spot = get_spot_balance_summary()
    
    # Total combined equity across both Coinbase lanes
    equity = perp["balance"] + spot["spot_equity"]
    net_pnl = equity - base
    
    return equity, False, base

def get_today_pnl():
    """Mathematical PnL for today (resets at 00:00 local)."""
    # For now, return a placeholder or calculate based on session start
    # To keep it simple and ledgerless, we use the net_pnl from get_account
    # until we have a session-start cache.
    equity, _, base = get_account()
    return round(equity - base, 2)

def get_equity_curve(*, current_only: bool = False):
    """
    Filtered equity curve. 
    Only shows trades from the LEDGERLESS_CUTOFF onwards.
    """
    return _q(
        """SELECT ts, SUM(pnl_usd) OVER (ORDER BY ts) AS cum_pnl
           FROM trades
           WHERE ts >= ? AND paper=0 AND pnl_usd != 0
           ORDER BY ts""",
        (LEDGERLESS_CUTOFF,),
    )

def get_drawdown(*, current_only: bool = False):
    """Drawdown based on filtered ledgerless data."""
    curve = get_equity_curve(current_only=current_only)
    if not curve:
        return {"max_dd_usd": 0.0, "max_dd_pct": 0.0, "current_dd_usd": 0.0, "current_dd_pct": 0.0}
    
    pnls = [r["cum_pnl"] for r in curve if r["cum_pnl"] is not None]
    if not pnls:
        return {"max_dd_usd": 0.0, "max_dd_pct": 0.0, "current_dd_usd": 0.0, "current_dd_pct": 0.0}
    
    peak = max(0, pnls[0])
    max_dd = 0.0
    for p in pnls:
        if p > peak: peak = p
        dd = peak - p
        if dd > max_dd: max_dd = dd
    
    current_peak = max(pnls)
    current_dd = max(0.0, current_peak - pnls[-1])
    
    _, _, base = get_account()
    return {
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / base * 100, 2) if base else 0.0,
        "current_dd_usd": round(current_dd, 2),
        "current_dd_pct": round(current_dd / base * 100, 2) if base else 0.0,
    }

def get_trade_log(limit=50, *, current_only: bool = False):
    """Pristine ledgerless trade log only."""
    return _q(
        """SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd, notes
           FROM trades
           WHERE ts >= ? AND paper=0
           ORDER BY ts DESC LIMIT ?""",
        (LEDGERLESS_CUTOFF, limit),
    )
