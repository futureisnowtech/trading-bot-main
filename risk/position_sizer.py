"""
risk/position_sizer.py — Kelly-dynamic position sizing.
Extracted from risk_manager.py (Sprint 1, Task 3).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging_db.trade_logger import get_kelly_stats, log_event


def size_from_kelly(strategy: str, symbol: str, base_size: float,
                    confidence: float, paper: bool) -> float:
    """
    Apply 25%-fractional Kelly sizing (or streak clamp) to base_size.

    Rules:
    - 5-trade losing streak (0% WR on last 5) → clamp to 50% of base.
    - 15+ trades with positive Kelly → scale by Kelly factor (floor 50%, cap 100%).
    - Fallback: confidence-proportional scale (60–100%).

    Returns final position size in USD (always ≤ base_size).
    """
    kelly = get_kelly_stats(strategy=strategy, paper=paper, window=50)

    losing_streak = False
    try:
        recent = get_kelly_stats(strategy=strategy, paper=paper, window=5)
        if recent['n_trades'] >= 5 and recent['win_rate'] == 0.0:
            losing_streak = True
            log_event('INFO', 'risk',
                      f"[Kelly] {strategy}/{symbol}: 5-trade losing streak — clamping size to 50%")
    except Exception:
        pass

    if losing_streak:
        size_factor = 0.50
    elif kelly['n_trades'] >= 15 and kelly['kelly_25pct'] > 0:
        kelly_factor = min(kelly['kelly_25pct'] / 0.10, 1.0)
        kelly_factor = max(0.50, kelly_factor)
        size_factor = kelly_factor
        log_event('INFO', 'risk',
                  f"[Kelly] {strategy}/{symbol}: f*={kelly['kelly_full']:.3f} "
                  f"25%Kelly={kelly['kelly_25pct']:.3f} scale={size_factor:.2f} "
                  f"(p={kelly['win_rate']:.0%} b={kelly['b_ratio']:.2f} n={kelly['n_trades']})")
    else:
        size_factor = max(0.60, min(float(confidence), 1.0))

    return round(base_size * size_factor, 2)
