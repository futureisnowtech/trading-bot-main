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
    is_crypto = 'equity' not in strategy

    # Trailing stop parameters:
    # Crypto: 2% trail (tighter than 4% — locks in gains faster)
    # Equity: 7% trail (needs room on daily candles)
    trail_pct = 0.14 if not is_crypto else 0.10

    # Activation: crypto activates when 40% of target range is reached (earlier than 0.5%)
    # Equity: still uses 2% from entry buffer
    entry = pos['entry']
    target = pos.get('target', entry * (1.09 if not is_crypto else 1.045))
    entry_to_target = target - entry if direction == 'LONG' else entry - target

    if is_crypto and entry_to_target > 0:
        # Activate at 40% of the way to target — locks in partial gain early
        activation_price = (entry + entry_to_target * 0.40
                            if direction == 'LONG'
                            else entry - entry_to_target * 0.40)
    else:
        # Equity: activate once price is 2% in our favor
        activation_price = entry * 1.02 if direction == 'LONG' else entry * 0.98

    stop_price   = pos.get('stop')
    target_price = pos.get('target')

    if direction == 'LONG':
        if stop_price is not None and current_price <= stop_price:
            return True, f"Hard stop hit ${current_price:.4f} (stop: ${stop_price:.4f})"
        if target_price is not None and current_price >= target_price:
            return True, f"Take profit hit ${current_price:.4f} (target: ${target_price:.4f})"
        _hse = pos.get('high_since_entry')
        if _hse is None:
            return False, ''
        trailing = _hse * (1 - trail_pct)
        if current_price >= activation_price and current_price <= trailing:
            return True, (f"Trailing stop triggered ${current_price:.4f} "
                          f"(trail from high ${pos['high_since_entry']:.4f}, {trail_pct*100:.0f}% trail)")
    else:  # SHORT
        if stop_price is not None and current_price >= stop_price:
            return True, f"Short stop hit ${current_price:.4f} (stop: ${stop_price:.4f})"
        if target_price is not None and current_price <= target_price:
            return True, f"Short target hit ${current_price:.4f} (target: ${target_price:.4f})"
        _hse = pos.get('high_since_entry')
        if _hse is None:
            return False, ''
        trailing = _hse * (1 + trail_pct)
        if current_price <= activation_price and current_price >= trailing:
            return True, (f"Short trailing stop triggered ${current_price:.4f} "
                          f"(trail from low ${pos['high_since_entry']:.4f}, {trail_pct*100:.0f}% trail)")

    return False, ''
