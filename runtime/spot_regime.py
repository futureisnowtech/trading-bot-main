"""
runtime/spot_regime.py — lightweight production regime classifier for spot scalp.
"""

from __future__ import annotations

from config import SPOT_REGIME_SCORE_FLOORS


def classify_spot_regime(state_30m: dict, state_4h: dict) -> str:
    score_30m = float(state_30m.get("frame_score") or 0.0)
    score_4h = float(state_4h.get("frame_score") or 0.0)
    z_30m = float(state_30m.get("z") or 0.0)
    v_30m = float(state_30m.get("v") or 0.0)
    rv_ratio = float(state_30m.get("rv_ratio") or state_4h.get("rv_ratio") or 1.0)

    if score_4h >= 56.0 and score_30m >= 53.0 and v_30m > 0 and z_30m >= 0.10:
        return "TREND"
    if abs(z_30m) < 0.12 or (rv_ratio > 1.35 and abs(v_30m) < 0.08):
        return "CHOP"
    return "NEUTRAL"


def score_floor_for_regime(
    regime: str,
    structural_confirm_count: int = 0,
    setup_family: str = "",
) -> float:
    base = float(SPOT_REGIME_SCORE_FLOORS.get(regime, SPOT_REGIME_SCORE_FLOORS["NEUTRAL"]))
    if regime in {"TREND", "NEUTRAL"} and setup_family == "impulse_continuation":
        base -= 1.0
    if regime != "CHOP" and structural_confirm_count >= 3:
        base -= 1.0
    if regime == "CHOP" and setup_family == "compression_breakout":
        base += 1.0
    return max(58.0, min(base, 70.0))
