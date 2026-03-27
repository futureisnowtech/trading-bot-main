"""
risk/stop_loss_manager.py — Stop-loss, take-profit, and trailing-stop logic.
Pure price math — no DB reads, no side effects.
Extracted from risk_manager.py (Sprint 1, Task 3).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import EQUITY_STOP_LOSS_PCT, CRYPTO_STOP_LOSS_PCT


def calc_stop_loss(entry: float, strategy: str, atr: float = 0.0) -> float:
    """
    Compute hard stop-loss price.
    If ATR is provided, ATR-adaptive stop is used (capped at 1.5× the fixed pct).
    """
    pct = EQUITY_STOP_LOSS_PCT if 'equity' in strategy else CRYPTO_STOP_LOSS_PCT
    if atr > 0:
        pct = min(atr * 2 / entry, pct * 1.5)
    return entry * (1 - pct)


def calc_take_profit(entry: float, strategy: str, atr: float = 0.0) -> float:
    """
    Compute take-profit price at 3:1 R/R (equity) or 2:1 R/R (crypto).
    Stop is calculated first, then target = entry + (entry - stop) * rr.
    """
    stop = calc_stop_loss(entry, strategy, atr)
    rr = 3.0  # 3:1 R:R for all strategies — matches config (CRYPTO/EQUITY_TAKE_PROFIT_PCT)
    return entry + (entry - stop) * rr


def should_exit(pos: dict, strategy: str, current_price: float) -> tuple:
    """
    Check whether an open position should be exited based on hard stop,
    take-profit target, or trailing stop.

    Args:
        pos: position dict with keys: stop, target, high_since_entry, entry, direction
        strategy: strategy name string
        current_price: latest price

    Returns: (exit: bool, reason: str)
    """
    direction = pos.get('direction', 'LONG')
    trail_pct = 0.07 if 'equity' in strategy else 0.04
    # Entry buffer before trailing kicks in:
    # Crypto: 0.5% — fast-moving, don't need price to run 3% before protecting profit
    # Equity: 2.0% — daily candles, needs room to breathe
    entry_buffer = 1.005 if ('crypto' in strategy or 'mean_reversion' in strategy) else 1.02

    if direction == 'LONG':
        if current_price <= pos['stop']:
            return True, f"Hard stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
        if current_price >= pos['target']:
            return True, f"Take profit hit ${current_price:.4f} (target: ${pos['target']:.4f})"
        trailing = pos['high_since_entry'] * (1 - trail_pct)
        if current_price > pos['entry'] * entry_buffer and current_price <= trailing:
            return True, f"Trailing stop triggered ${current_price:.4f}"
    else:  # SHORT
        if current_price >= pos['stop']:
            return True, f"Short stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
        if current_price <= pos['target']:
            return True, f"Short target hit ${current_price:.4f} (target: ${pos['target']:.4f})"
        trailing = pos['high_since_entry'] * (1 + trail_pct)
        short_buffer = 2 - entry_buffer  # mirror: 0.995 for crypto, 0.98 for equity
        if current_price < pos['entry'] * short_buffer and current_price >= trailing:
            return True, f"Short trailing stop triggered ${current_price:.4f}"

    return False, ''
