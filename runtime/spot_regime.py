"""
runtime/spot_regime.py — lightweight production regime classifier for spot scalp.
"""

from __future__ import annotations

import math

from config import (
    TAKER_FEE_PCT,
    SPOT_REGIME_ADX_CHOP,
    SPOT_REGIME_ADX_TREND,
    SPOT_REGIME_ER_CHOP,
    SPOT_REGIME_ER_CHOP_EXIT,
    SPOT_REGIME_ER_TREND,
    SPOT_REGIME_SCORE_FLOORS,
)


def calculate_fee_aware_expectancy(
    er: float,
    adx: float,
    obi: float,
    volatility: float,
    round_trip_fee: float = 0.012, # 1.2% Coinbase Taker
) -> float:
    """
    Sovereign Expectancy Math (v18.30).
    ExpectedMove = Volatility * log(1 + ER) * (1 + OBI)
    FrictionMultiplier = PredictedAlpha / round_trip_fee
    """
    # 1. Predicted Alpha (%)
    # Volatility is usually ATR/Price. 
    # We apply a logarithmic ER boost and an OBI directional multiplier.
    predicted_alpha = volatility * math.log1p(er) * (1 + abs(obi))
    
    # 2. Friction Check
    # A trade is only Sovereign if PredictedAlpha is > 2x the friction.
    return predicted_alpha / round_trip_fee


def classify_spot_regime(
    state_30m: dict,
    state_4h: dict,
    symbol: str | None = None,
) -> str:
    """v18.30: Autonomous Expectancy-Driven Regime.
    Uses ER/ADX thresholds as a baseline, but downgrades to CHOP if expectancy is insufficient.
    """
    er = float(state_30m.get("er") or 0.0)
    adx = float(state_30m.get("adx") or 0.0)
    obi = float(state_30m.get("obi") or 0.0)
    vol = float(state_30m.get("volatility") or 0.01) # Default 1% vol

    # 1. Base Classification (Legacy Thresholds)
    if er > SPOT_REGIME_ER_TREND and adx > SPOT_REGIME_ADX_TREND:
        base_regime = "TREND"
    elif er < SPOT_REGIME_ER_CHOP and adx < SPOT_REGIME_ADX_CHOP:
        base_regime = "CHOP"
    else:
        base_regime = "NEUTRAL"

    # 2. v18.30: Sovereign Expectancy Gate
    # Even if technicals look like TREND, if volatility is too low for fees, we silence.
    expectancy = calculate_fee_aware_expectancy(er, adx, obi, vol)
    
    prior: str | None = None
    if symbol:
        try:
            from logging_db.trade_logger import load_spot_regime_state
            prior = load_spot_regime_state(symbol)
        except Exception:
            prior = None

    # Hysteresis & Veto
    if expectancy < 2.0:
        regime = "CHOP" # Rational Silence
    elif base_regime == "TREND" and expectancy > 2.5:
        regime = "TREND"
    elif prior == "TREND" and er > SPOT_REGIME_ER_CHOP_EXIT and expectancy > 2.0:
        regime = "TREND" # Stay in Trend
    elif prior == "NEUTRAL" and er > SPOT_REGIME_ER_CHOP_EXIT and expectancy > 2.0:
        regime = "NEUTRAL" # Stay in Neutral
    elif base_regime == "CHOP":
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
