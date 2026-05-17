"""
risk/spot_economics_gate.py — cost-aware economics gate for spot scalp entries.
"""

from __future__ import annotations

import os

from config import SPOT_SCALP_SYMBOL_CONFIG
from runtime.spot_regime import score_floor_for_regime

SPOT_TAKER_FEE_PCT: float = float(os.getenv("SPOT_TAKER_FEE_PCT", "0.006"))
SPOT_MAKER_FEE_PCT: float = float(os.getenv("SPOT_MAKER_FEE_PCT", "0.004"))


def check_spot_economics(
    symbol: str,
    size_usd: float,
    final_spot_score: float,
    stop_pct: float,
    target_r: float,
    spread_pct: float,
    bid_depth_usd: float,
    ask_depth_usd: float,
    regime: str,
    execution_route_guess: str = "maker_first",
    paper: bool = False,
    structural_confirm_count: int = 0,
    setup_family: str = "",
    setup_score: float = 0.0,
) -> dict:
    clean = symbol.upper()
    cfg = SPOT_SCALP_SYMBOL_CONFIG.get(clean, {})
    # v18.34: Relaxed gates for higher trade frequency
    spread_cap = float(cfg.get("spread_cap_pct", 0.004))
    depth_min = float(cfg.get("depth_min_usd", 1000))
    fee_leg = SPOT_MAKER_FEE_PCT if execution_route_guess == "maker_first" else SPOT_TAKER_FEE_PCT
    total_fee_pct = fee_leg + SPOT_TAKER_FEE_PCT
    total_cost_pct = total_fee_pct + max(0.0, spread_pct / 2.0)
    fee_usd = size_usd * total_fee_pct
    target_pct = stop_pct * target_r
    net_target_pct = target_pct - total_cost_pct
    net_stop_pct = stop_pct + total_cost_pct
    projected_net_win_usd = size_usd * net_target_pct
    net_rr = net_target_pct / max(net_stop_pct, 1e-9)
    score_floor = score_floor_for_regime(
        regime,
        structural_confirm_count=structural_confirm_count,
        setup_family=setup_family,
        setup_score=setup_score,
        symbol=clean,
    )

    if final_spot_score < score_floor:
        return {
            "approved": False,
            "reason": "below_regime_floor",
            "gate_class": "quality",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if spread_pct > spread_cap:
        return {
            "approved": False,
            "reason": "spread_cap_exceeded",
            "gate_class": "microstructure",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if min(bid_depth_usd or 0.0, ask_depth_usd or 0.0) > 0 and min(
        bid_depth_usd or 0.0, ask_depth_usd or 0.0
    ) < depth_min:
        return {
            "approved": False,
            "reason": "depth_below_minimum",
            "gate_class": "microstructure",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if net_target_pct <= 0:
        return {
            "approved": False,
            "reason": "non_positive_net_target",
            "gate_class": "economics",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if projected_net_win_usd <= (1.0 * fee_usd):
        return {
            "approved": False,
            "reason": "projected_net_win_too_small",
            "gate_class": "economics",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if net_rr < 1.05:
        return {
            "approved": False,
            "reason": "net_rr_below_minimum",
            "gate_class": "economics",
            "score_floor": score_floor,
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "net_rr": net_rr,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    return {
        "approved": True,
        "reason": "approved",
        "gate_class": "approved",
        "score_floor": score_floor,
        "fee_usd": fee_usd,
        "edge_score": net_target_pct - net_stop_pct,
        "net_target_pct": net_target_pct,
        "net_stop_pct": net_stop_pct,
        "net_rr": net_rr,
        "projected_net_win_usd": projected_net_win_usd,
        "total_cost_pct": total_cost_pct,
    }


# v18.19: exit-side economics gate. Discretionary exits (trailing_stop,
# stagnation_exit, thesis_decay) must clear net fees before firing. Hard stops,
# fee-inflated targets, and EOD flatten bypass this via mandatory=True.
SPOT_EXIT_MIN_NET_USD: float = float(os.getenv("SPOT_EXIT_MIN_NET_USD", "0.05"))


def economics_ok_to_exit(
    symbol: str,
    entry_price: float,
    current_price: float,
    qty: float,
    entry_fee_usd: float = 0.0,
    execution_route_guess: str = "taker",
    min_net_usd: float | None = None,
    mandatory: bool = False,
) -> tuple[bool, str]:
    """Return (allow_exit, reason).

    The point of this gate is to block "pyrrhic winners" — trades where the
    price moved in our favor (gross > 0) but not enough to clear fees. Without
    this, the bot logs a "win" while net P&L is negative.

    Allow:
      * mandatory=True (hard stops, fee-inflated targets, EOD flatten)
      * gross_usd <= 0 (genuine loser — discipline > economics; free the capital)
      * net_usd >= min_net_usd (clean winner)

    Block only when gross_usd > 0 AND net_usd < min_net_usd (the pyrrhic case).
    """
    if mandatory:
        return True, "mandatory"
    if entry_price <= 0 or current_price <= 0 or qty <= 0:
        return True, "skip_invalid_inputs"
    fee_pct = SPOT_MAKER_FEE_PCT if execution_route_guess == "maker" else SPOT_TAKER_FEE_PCT
    exit_fee_usd = current_price * qty * fee_pct
    gross_usd = (current_price - entry_price) * qty
    net_usd = gross_usd - float(entry_fee_usd or 0.0) - exit_fee_usd
    threshold = SPOT_EXIT_MIN_NET_USD if min_net_usd is None else float(min_net_usd)
    if gross_usd <= 0:
        return True, f"genuine_loser gross={gross_usd:.4f}"
    if net_usd >= threshold:
        return True, f"net_ok={net_usd:.4f}"
    return False, f"pyrrhic_winner gross={gross_usd:.4f} net={net_usd:.4f}"
