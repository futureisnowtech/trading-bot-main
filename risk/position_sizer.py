"""
risk/position_sizer.py — Kelly-dynamic position sizing with heat-level scaling.
Extracted from risk_manager.py (Sprint 1, Task 3).

Sizing pipeline (applied in order, each can only reduce size):
  1. Drawdown heat level  → multiplies base by 0.25–1.0 (CAUTION/WARNING/DANGER)
  2. Losing streak clamp  → further 50% if last-5 WR == 0%
  3. Kelly scale          → 25%-fractional Kelly (floor 50%, cap 100% of post-heat size)
  4. Confidence fallback  → 60–100% if Kelly not yet active
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging_db.trade_logger import get_kelly_stats, log_event


def size_from_kelly(strategy: str, symbol: str, base_size: float,
                    confidence: float, paper: bool) -> float:
    """
    Apply heat-adjusted Kelly sizing to base_size.

    Returns final position size in USD (always ≤ base_size, never < 0).
    """
    # ── Step 1: Heat level scaling ────────────────────────────────────────────
    from risk.drawdown_controller import get_heat_level
    heat = get_heat_level(paper=paper)
    heat_factor = heat['size_factor']

    if heat['level'] > 0:
        log_event('INFO', 'risk',
                  f"[Heat] {strategy}/{symbol}: {heat['label']} "
                  f"(day={heat['daily_pnl']:+.2f}, {heat['pct_drawn']:.1%} drawn) "
                  f"→ size ×{heat_factor:.2f}")

    heat_adjusted = base_size * heat_factor
    if heat_adjusted <= 0:
        return 0.0

    # ── Step 2 + 3: Losing streak clamp + Kelly ───────────────────────────────
    kelly = get_kelly_stats(strategy=strategy, paper=paper, window=50)

    losing_streak = False
    try:
        recent = get_kelly_stats(strategy=strategy, paper=paper, window=5)
        if recent['n_trades'] >= 5 and recent['win_rate'] == 0.0:
            losing_streak = True
            log_event('INFO', 'risk',
                      f"[Kelly] {strategy}/{symbol}: 5-trade losing streak — clamping to 50%")
    except Exception:
        pass

    if losing_streak:
        kelly_factor = 0.50
    elif kelly['n_trades'] >= 15 and kelly['kelly_25pct'] > 0:
        kelly_factor = min(kelly['kelly_25pct'] / 0.10, 1.0)
        kelly_factor = max(0.50, kelly_factor)
        log_event('INFO', 'risk',
                  f"[Kelly] {strategy}/{symbol}: f*={kelly['kelly_full']:.3f} "
                  f"25%Kelly={kelly['kelly_25pct']:.3f} scale={kelly_factor:.2f} "
                  f"(p={kelly['win_rate']:.0%} b={kelly['b_ratio']:.2f} n={kelly['n_trades']})")
    else:
        kelly_factor = max(0.60, min(float(confidence), 1.0))

    return round(heat_adjusted * kelly_factor, 2)
