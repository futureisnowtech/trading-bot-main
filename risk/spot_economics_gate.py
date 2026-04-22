"""
risk/spot_economics_gate.py — cost-aware economics gate for spot scalp entries.
"""

from __future__ import annotations

import os

from config import SPOT_SCALP_SYMBOL_CONFIG

SPOT_TAKER_FEE_PCT: float = float(os.getenv("SPOT_TAKER_FEE_PCT", "0.006"))
SPOT_MAKER_FEE_PCT: float = float(os.getenv("SPOT_MAKER_FEE_PCT", "0.004"))


def _min_score_for_regime(regime: str) -> float:
    return {"TREND": 62.0, "NEUTRAL": 66.0, "CHOP": 70.0}.get(regime, 66.0)


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
) -> dict:
    clean = symbol.upper()
    cfg = SPOT_SCALP_SYMBOL_CONFIG.get(clean, {})
    spread_cap = float(cfg.get("spread_cap_pct", 0.0025))
    depth_min = float(cfg.get("depth_min_usd", 5000))
    fee_leg = SPOT_MAKER_FEE_PCT if execution_route_guess == "maker_first" else SPOT_TAKER_FEE_PCT
    total_fee_pct = fee_leg + SPOT_TAKER_FEE_PCT
    total_cost_pct = total_fee_pct + max(0.0, spread_pct / 2.0)
    fee_usd = size_usd * total_fee_pct
    target_pct = stop_pct * target_r
    net_target_pct = target_pct - total_cost_pct
    net_stop_pct = stop_pct + total_cost_pct
    projected_net_win_usd = size_usd * net_target_pct

    if final_spot_score < _min_score_for_regime(regime):
        return {
            "approved": False,
            "reason": "below_regime_floor",
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if spread_pct > spread_cap:
        return {
            "approved": False,
            "reason": "spread_cap_exceeded",
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if min(bid_depth_usd or 0.0, ask_depth_usd or 0.0) > 0 and min(
        bid_depth_usd or 0.0, ask_depth_usd or 0.0
    ) < depth_min:
        return {
            "approved": False,
            "reason": "depth_below_minimum",
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if net_target_pct <= 0:
        return {
            "approved": False,
            "reason": "non_positive_net_target",
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    if projected_net_win_usd < fee_usd + 0.01:
        return {
            "approved": False,
            "reason": "projected_net_win_too_small",
            "fee_usd": fee_usd,
            "edge_score": net_target_pct - net_stop_pct,
            "net_target_pct": net_target_pct,
            "net_stop_pct": net_stop_pct,
            "projected_net_win_usd": projected_net_win_usd,
            "total_cost_pct": total_cost_pct,
        }
    return {
        "approved": True,
        "reason": "approved",
        "fee_usd": fee_usd,
        "edge_score": net_target_pct - net_stop_pct,
        "net_target_pct": net_target_pct,
        "net_stop_pct": net_stop_pct,
        "projected_net_win_usd": projected_net_win_usd,
        "total_cost_pct": total_cost_pct,
    }
