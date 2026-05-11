"""
runtime/spot_regime.py — lightweight production regime classifier for spot scalp.
"""

from __future__ import annotations

from config import SPOT_REGIME_SCORE_FLOORS


def classify_spot_regime(state_30m: dict, state_4h: dict) -> str:
    er = float(state_30m.get("er") or 0.0)
    adx = float(state_30m.get("adx") or 0.0)

    if er > 0.6 and adx > 25.0:
        return "TREND"
    if er < 0.3 and adx < 20.0:
        return "CHOP"
    return "NEUTRAL"


def score_floor_for_regime(
    regime: str,
    structural_confirm_count: int = 0,
    setup_family: str = "",
    setup_score: float = 0.0,
    symbol: str | None = None,
) -> float:
    if symbol:
        from runtime.spot_strategy import score_floor_for_symbol

        return score_floor_for_symbol(
            symbol,
            regime,
            structural_confirm_count=structural_confirm_count,
            setup_family=setup_family,
            setup_score=setup_score,
        )
    base = float(
        SPOT_REGIME_SCORE_FLOORS.get(regime, SPOT_REGIME_SCORE_FLOORS["NEUTRAL"])
    )
    if regime in {"TREND", "NEUTRAL", "CHOP"} and setup_family == "impulse_continuation":
        base -= 1.0
    if structural_confirm_count >= 3:
        base -= 1.0
    # v18.17: Allow lower bound to drop to 40.0 instead of 54.0 so the 48.0 regime floor is respected
    return max(40.0, min(base, 70.0))
