"""
runtime/spot_regime.py — lightweight production regime classifier for spot scalp.
"""

from __future__ import annotations


def classify_spot_regime(state_30m: dict, state_4h: dict) -> str:
    score_30m = float(state_30m.get("frame_score") or 0.0)
    score_4h = float(state_4h.get("frame_score") or 0.0)
    z_30m = float(state_30m.get("z") or 0.0)
    v_30m = float(state_30m.get("v") or 0.0)
    rv_ratio = float(state_30m.get("rv_ratio") or state_4h.get("rv_ratio") or 1.0)

    if score_4h >= 58.0 and score_30m >= 55.0 and v_30m > 0:
        return "TREND"
    if abs(z_30m) < 0.15 or (rv_ratio > 1.30 and abs(v_30m) < 0.10):
        return "CHOP"
    return "NEUTRAL"
