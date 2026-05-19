"""
risk/drawdown_controller.py — Daily loss halt, fee-drag circuit breaker,
and 5-level drawdown heat system.

Heat levels replace cliff-edge halts with smooth position-size degradation:
  Level 0 (≥ -1.0%): NORMAL   — 100% size
  Level 1 (≥ -1.5%): CAUTION  —  75% size
  Level 2 (≥ -2.5%): WARNING  —  50% size
  Level 3 (≥ -3.5%): DANGER   —  25% size
  Level 4 (≥ -4.0%): HALT     —   0% size (all new entries blocked)

The hard halt at MAX_DAILY_LOSS_PCT (4%) remains unchanged — this is the
safety net. Heat levels engage earlier to preserve capital smoothly.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT
from logging_db.trade_logger import get_todays_pnl, get_todays_fees, get_all_time_stats


# ─── Heat level thresholds (fraction of real balance) ────────────────────────
_HEAT_THRESHOLDS = [
    (0.040, 4, 'HALT',    0.00),   # ≥ -4.0% → HALT all entries
    (0.035, 3, 'DANGER',  0.25),   # ≥ -3.5% → 25% size
    (0.025, 2, 'WARNING', 0.50),   # ≥ -2.5% → 50% size
    (0.015, 1, 'CAUTION', 0.75),   # ≥ -1.5% → 75% size
    (0.000, 0, 'NORMAL',  1.00),   # baseline  → 100% size
]


def get_heat_level(paper: bool) -> dict:
    """
    Compute the current drawdown heat level from today's P&L.

    Returns dict:
      level       : int   0–4
      label       : str   'NORMAL' | 'CAUTION' | 'WARNING' | 'DANGER' | 'HALT'
      size_factor : float 1.0 → 0.75 → 0.50 → 0.25 → 0.0
      daily_pnl   : float today's P&L in dollars
      pct_drawn   : float today's drawdown as a fraction of real balance
    """
    daily_pnl = get_todays_pnl()
    all_time  = get_all_time_stats()
    real_balance = max(ACCOUNT_SIZE + all_time['total_pnl'], 1.0)
    pct_drawn = -daily_pnl / real_balance  # positive = loss

    for threshold, level, label, factor in _HEAT_THRESHOLDS:
        if pct_drawn >= threshold:
            return {
                'level': level,
                'label': label,
                'size_factor': factor,
                'daily_pnl': daily_pnl,
                'pct_drawn': pct_drawn,
            }

    return {'level': 0, 'label': 'NORMAL', 'size_factor': 1.0,
            'daily_pnl': daily_pnl, 'pct_drawn': pct_drawn}


def check_daily_loss(paper: bool) -> tuple:
    """
    Check whether today's P&L has breached the hard daily loss limit.

    Returns: (ok: bool, reason: str)
    ok=False means the caller should halt trading (heat level 4).
    Uses real balance (ACCOUNT_SIZE + all-time P&L) as the loss base.
    """
    heat = get_heat_level(paper)
    if heat['level'] == 4:
        daily_pnl = heat['daily_pnl']
        real_balance = max(ACCOUNT_SIZE + get_all_time_stats()['total_pnl'], 1.0)
        max_loss = real_balance * MAX_DAILY_LOSS_PCT
        return False, f"Daily loss limit hit: ${daily_pnl:.2f} (max ${max_loss:.2f})"
    return True, ''


def check_fee_drag(paper: bool) -> tuple:
    """
    Check whether today's fees exceed the daily fee-drag cap.
    Prevents runaway fee bleed on volatile days with many small losses.

    Returns: (ok: bool, reason: str)
    """
    fees = get_todays_fees()
    real_balance = max(ACCOUNT_SIZE + get_all_time_stats()['total_pnl'], 1.0)
    limit = real_balance * MAX_DAILY_FEE_DRAG_PCT
    if fees > limit:
        return False, f"Daily fee limit: ${fees:.2f} (max ${limit:.2f})"
    return True, ''
