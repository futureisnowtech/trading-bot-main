"""
risk/drawdown_controller.py — Daily loss halt and fee-drag circuit breakers.
Extracted from risk_manager.py (Sprint 1, Task 3).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT
from logging_db.trade_logger import get_todays_pnl, get_todays_fees, get_all_time_stats


def check_daily_loss(paper: bool) -> tuple:
    """
    Check whether today's P&L has breached the daily loss limit.

    Returns: (ok: bool, reason: str)
    ok=False means the caller should halt trading.
    Uses real balance (ACCOUNT_SIZE + all-time P&L) as the loss base.
    """
    daily_pnl = get_todays_pnl(paper=paper)
    all_time = get_all_time_stats(paper=paper)
    real_balance = ACCOUNT_SIZE + all_time['total_pnl']
    max_loss = real_balance * MAX_DAILY_LOSS_PCT
    if daily_pnl < -max_loss:
        return False, f"Daily loss limit hit: ${daily_pnl:.2f} (max ${max_loss:.2f})"
    return True, ''


def check_fee_drag(paper: bool) -> tuple:
    """
    Check whether today's fees exceed the daily fee-drag cap.
    Prevents runaway fee bleed on volatile days with many small losses.

    Returns: (ok: bool, reason: str)
    """
    fees = get_todays_fees(paper=paper)
    limit = ACCOUNT_SIZE * MAX_DAILY_FEE_DRAG_PCT
    if fees > limit:
        return False, f"Daily fee limit: ${fees:.2f} (max ${limit:.2f})"
    return True, ''
