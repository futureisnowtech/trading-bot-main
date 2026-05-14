"""
runtime/spot_regime.py — lightweight production regime classifier for spot scalp.
"""

from __future__ import annotations

from config import (
    SPOT_REGIME_ADX_CHOP,
    SPOT_REGIME_ADX_TREND,
    SPOT_REGIME_ER_CHOP,
    SPOT_REGIME_ER_CHOP_EXIT,
    SPOT_REGIME_ER_TREND,
    SPOT_REGIME_SCORE_FLOORS,
)


def classify_spot_regime(
    state_30m: dict,
    state_4h: dict,
    symbol: str | None = None,
) -> str:
    """v18.19: sticky NEUTRAL↔CHOP transition + env-configurable thresholds.

    When ``symbol`` is provided, the prior persisted regime is consulted so the
    NEUTRAL→CHOP boundary uses a wider exit cutoff (10pt hysteresis band).
    State is loaded from / persisted to ``spot_regime_state`` (table created in
    ``logging_db.trade_logger.init_db``).
    """
    er = float(state_30m.get("er") or 0.0)
    adx = float(state_30m.get("adx") or 0.0)

    prior: str | None = None
    if symbol:
        try:
            from logging_db.trade_logger import load_spot_regime_state

            prior = load_spot_regime_state(symbol)
        except Exception:
            prior = None

    if er > SPOT_REGIME_ER_TREND and adx > SPOT_REGIME_ADX_TREND:
        regime = "TREND"
    else:
        chop_cutoff = (
            SPOT_REGIME_ER_CHOP_EXIT if prior == "NEUTRAL" else SPOT_REGIME_ER_CHOP
        )
        if er < chop_cutoff and adx < SPOT_REGIME_ADX_CHOP:
            regime = "CHOP"
        else:
            regime = "NEUTRAL"

    if symbol and regime != prior:
        try:
            from logging_db.trade_logger import save_spot_regime_state

            save_spot_regime_state(symbol, regime)
        except Exception:
            pass
    return regime


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
