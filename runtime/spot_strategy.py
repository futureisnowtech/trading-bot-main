"""
runtime/spot_strategy.py — symbol-specific live policy for crypto spot scalps.

This module keeps the spot strategy auditable:
- a finite setup library
- symbol preferences instead of hard-coded setup lockouts
- dynamic opportunistic allowance when derivative evidence is unusually strong
"""

from __future__ import annotations

from typing import Any

KNOWN_SETUP_FAMILIES: tuple[str, ...] = (
    "impulse_continuation",
    "pullback_reclaim",
    "compression_breakout",
    "trend_resume_after_shakeout",
    "compression_expansion_retest",
)

_SETUP_LIBRARY: dict[str, dict[str, float | str]] = {
    "impulse_continuation": {
        "min_score": 0.62,
        "preferred_floor_delta": -0.5,
        "opportunistic_floor_delta": 0.5,
        "wildcard_floor_delta": 1.5,
        "group": "momentum",
    },
    "pullback_reclaim": {
        "min_score": 0.58,
        "preferred_floor_delta": -0.5,
        "opportunistic_floor_delta": 0.0,
        "wildcard_floor_delta": 1.0,
        "group": "reclaim",
    },
    "compression_breakout": {
        "min_score": 0.62,
        "preferred_floor_delta": -0.25,
        "opportunistic_floor_delta": 0.5,
        "wildcard_floor_delta": 1.5,
        "group": "breakout",
    },
    "trend_resume_after_shakeout": {
        "min_score": 0.60,
        "preferred_floor_delta": -0.5,
        "opportunistic_floor_delta": 0.0,
        "wildcard_floor_delta": 1.0,
        "group": "resume",
    },
    "compression_expansion_retest": {
        "min_score": 0.64,
        "preferred_floor_delta": -0.25,
        "opportunistic_floor_delta": 0.5,
        "wildcard_floor_delta": 1.5,
        "group": "breakout",
    },
}


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("-USD", "").replace("USD", "").replace("USDT", "")


def _tupled(values: Any, *, upper: bool = True) -> tuple[str, ...]:
    if values is None:
        return tuple()
    if isinstance(values, str):
        items = [v.strip() for v in values.split(",") if v.strip()]
    else:
        items = [str(v).strip() for v in values if str(v).strip()]
    return tuple(v.upper() if upper else v for v in items)


def _setup_value(policy: dict[str, Any], setup_family: str, key: str) -> float:
    setup_cfg = (policy.get("setup_overrides") or {}).get(setup_family, {})
    base = _SETUP_LIBRARY.get(setup_family, {})
    return float(setup_cfg.get(key, base.get(key, 0.0)))


def _edge_conditions(override: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = override.get("edge_conditions") or ()
    conditions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        op = str(item.get("operator") or item.get("op") or "gte").strip().lower()
        if not field:
            continue
        conditions.append(
            {
                "field": field,
                "operator": op,
                "value": item.get("value"),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return tuple(conditions)


def _required_setup_families(policy: dict[str, Any]) -> tuple[str, ...]:
    required: list[str] = []
    for cond in policy.get("edge_conditions") or ():
        if (
            str(cond.get("field") or "") == "setup_family"
            and str(cond.get("operator") or "") == "eq"
            and str(cond.get("value") or "") in KNOWN_SETUP_FAMILIES
        ):
            required.append(str(cond.get("value")))
    return tuple(required)


def _exit_targets(profile: str, regime: str, policy: dict[str, Any]) -> tuple[float, float]:
    import config as _cfg

    profile_map = getattr(_cfg, "SPOT_EXIT_PROFILE_TARGETS", {})
    chosen = str(profile or "").strip().lower()
    regime_key = str(regime or "NEUTRAL").upper()
    targets = profile_map.get(chosen, {})
    if regime_key in targets:
        target_r, trail_r = targets[regime_key]
        return float(target_r), float(trail_r)
    target_r = float(policy["target_r_by_regime"].get(regime_key, policy["target_r_by_regime"]["NEUTRAL"]))
    trail_r = float(
        policy["trail_arm_r_by_regime"].get(
            regime_key,
            policy["trail_arm_r_by_regime"]["NEUTRAL"],
        )
    )
    return target_r, trail_r


def _edge_state_value(spot_state: dict[str, Any], field: str) -> Any:
    frames = (spot_state or {}).get("frames") or {}
    s5 = frames.get("5m") or {}
    s30 = frames.get("30m") or {}
    mapping = {
        "regime": str(spot_state.get("regime") or "").upper(),
        "setup_family": str(spot_state.get("setup_family") or ""),
        "setup_score": float(spot_state.get("setup_score") or 0.0),
        "structure": float(s5.get("structure_component") or 0.0),
        "vol_quality": float(s30.get("volatility_quality") or 0.0),
        "a5": float(s5.get("a") or 0.0),
        "v30": float(s30.get("v") or 0.0),
        "mom_impulse": float(s5.get("momentum_impulse") or 0.0),
        "participation": float(s5.get("participation_component") or 0.0),
        "path_eff": float(s5.get("path_efficiency") or 0.0),
        "confirm_count": int(spot_state.get("structural_confirm_count") or 0),
        "frame_5m": float(s5.get("frame_score") or 0.0),
        "frame_30m": float(s30.get("frame_score") or 0.0),
    }
    return mapping.get(field)


def _edge_condition_matches(spot_state: dict[str, Any], condition: dict[str, Any]) -> bool:
    actual = _edge_state_value(spot_state, str(condition.get("field") or ""))
    op = str(condition.get("operator") or "gte").lower()
    expected = condition.get("value")
    if actual is None:
        return False
    if op == "eq":
        return str(actual) == str(expected)
    if op == "gte":
        return float(actual) >= float(expected)
    if op == "gt":
        return float(actual) > float(expected)
    if op == "lte":
        return float(actual) <= float(expected)
    if op == "lt":
        return float(actual) < float(expected)
    if op == "in":
        return str(actual) in {str(v) for v in expected}
    return False


def _default_edge_reason(condition: dict[str, Any]) -> str:
    field = str(condition.get("field") or "")
    return {
        "setup_family": "edge_setup_family_mismatch",
        "setup_score": "edge_setup_score_too_low",
        "structure": "edge_structure_component_too_low",
        "vol_quality": "edge_volatility_quality_too_low",
        "a5": "edge_acceleration_too_low",
        "v30": "edge_30m_velocity_too_low",
        "mom_impulse": "edge_momentum_impulse_too_low",
        "participation": "edge_participation_too_low",
        "path_eff": "edge_path_efficiency_too_low",
        "confirm_count": "edge_confirm_count_too_low",
        "frame_5m": "edge_frame_score_5m_too_low",
        "frame_30m": "edge_frame_score_30m_too_low",
        "regime": "edge_regime_mismatch",
    }.get(field, "edge_condition_failed")


def _edge_condition_label(condition: dict[str, Any]) -> str:
    field = str(condition.get("field") or "")
    value = condition.get("value")
    field_labels = {
        "setup_family": "setup",
        "setup_score": "setup score",
        "structure": "5m structure",
        "vol_quality": "30m vol quality",
        "a5": "5m accel",
        "v30": "30m velocity",
        "mom_impulse": "5m momentum impulse",
        "participation": "5m participation",
        "path_eff": "5m path efficiency",
        "confirm_count": "confirm count",
        "frame_5m": "5m frame",
        "frame_30m": "30m frame",
        "regime": "regime",
    }
    op = str(condition.get("operator") or "gte")
    op_label = {"eq": "=", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<", "in": "in"}.get(op, op)
    if isinstance(value, float):
        value_text = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        value_text = str(value)
    return f"{field_labels.get(field, field)} {op_label} {value_text}"


def known_setup_families() -> tuple[str, ...]:
    return KNOWN_SETUP_FAMILIES


def get_spot_strategy(symbol: str) -> dict[str, Any]:
    import config as _cfg

    clean = _clean_symbol(symbol)
    override = dict(getattr(_cfg, "SPOT_SYMBOL_STRATEGY_OVERRIDES", {}).get(clean, {}))
    edge_conditions = _edge_conditions(override)
    allowed_regimes = _tupled(
        override.get("allowed_regimes", getattr(_cfg, "SPOT_ALLOWED_REGIMES", {"TREND", "NEUTRAL"}))
    )
    preferred_setups = _tupled(
        override.get("preferred_setups", ()),
        upper=False,
    )
    allowed_setups = _tupled(
        override.get("allowed_setups", KNOWN_SETUP_FAMILIES),
        upper=False,
    ) or KNOWN_SETUP_FAMILIES
    score_floors = {
        "TREND": float(
            override.get("score_floors", {}).get(
                "TREND",
                getattr(_cfg, "SPOT_REGIME_SCORE_FLOORS", {"TREND": 58.0})["TREND"],
            )
        ),
        "NEUTRAL": float(
            override.get("score_floors", {}).get(
                "NEUTRAL",
                getattr(_cfg, "SPOT_REGIME_SCORE_FLOORS", {"NEUTRAL": 58.0})["NEUTRAL"],
            )
        ),
        "CHOP": float(
            override.get("score_floors", {}).get(
                "CHOP",
                getattr(_cfg, "SPOT_REGIME_SCORE_FLOORS", {"CHOP": 66.0})["CHOP"],
            )
        ),
    }
    score_weights = override.get("score_weights", {})
    target_r_by_regime = override.get(
        "target_r_by_regime",
        getattr(_cfg, "SPOT_TARGET_R_BY_REGIME", {"TREND": 0.85, "NEUTRAL": 0.65, "CHOP": 0.50}),
    )
    trail_arm_r_by_regime = override.get(
        "trail_arm_r_by_regime",
        getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"TREND": 0.55, "NEUTRAL": 0.40, "CHOP": 0.30}),
    )
    policy = {
        "symbol": clean,
        "enabled": bool(
            override.get(
                "enabled",
                clean
                in {
                    s.upper()
                    for s in getattr(
                        _cfg,
                        "SPOT_STRATEGY_SYMBOLS",
                        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
                    )
                },
            )
        ),
        "allowed_regimes": allowed_regimes,
        "allowed_setups": tuple(s for s in allowed_setups if s in KNOWN_SETUP_FAMILIES),
        "preferred_setups": tuple(s for s in preferred_setups if s in KNOWN_SETUP_FAMILIES),
        "required_setup_families": tuple(s for s in _required_setup_families({"edge_conditions": edge_conditions})),
        "edge_profile": str(override.get("edge_profile") or "").strip().lower(),
        "edge_conditions": edge_conditions,
        "edge_metrics": dict(override.get("edge_metrics") or {}),
        "opportunistic_setup_score": float(override.get("opportunistic_setup_score", 0.74)),
        "wildcard_setup_score": float(override.get("wildcard_setup_score", 0.84)),
        "score_floors": score_floors,
        "score_weights": {
            "TREND": {
                "composite": float(
                    score_weights.get("TREND", {}).get(
                        "composite",
                        getattr(_cfg, "SPOT_SCALP_SCORE_WEIGHT_COMPOSITE", 0.60),
                    )
                ),
                "derivative": float(
                    score_weights.get("TREND", {}).get(
                        "derivative",
                        getattr(_cfg, "SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE", 0.40),
                    )
                ),
            },
            "NEUTRAL": {
                "composite": float(
                    score_weights.get("NEUTRAL", {}).get(
                        "composite",
                        getattr(_cfg, "SPOT_NEUTRAL_SCORE_WEIGHT_COMPOSITE", 0.90),
                    )
                ),
                "derivative": float(
                    score_weights.get("NEUTRAL", {}).get(
                        "derivative",
                        getattr(_cfg, "SPOT_NEUTRAL_SCORE_WEIGHT_DERIVATIVE", 0.10),
                    )
                ),
            },
            "CHOP": {
                "composite": float(
                    score_weights.get("CHOP", {}).get(
                        "composite",
                        getattr(_cfg, "SPOT_SCALP_SCORE_WEIGHT_COMPOSITE", 0.60),
                    )
                ),
                "derivative": float(
                    score_weights.get("CHOP", {}).get(
                        "derivative",
                        getattr(_cfg, "SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE", 0.40),
                    )
                ),
            },
        },
        "min_confirm_count": int(override.get("min_confirm_count", 2)),
        "min_5m_frame": float(override.get("min_5m_frame", 0.0)),
        "min_30m_frame": float(override.get("min_30m_frame", 0.0)),
        "min_momentum_impulse": float(override.get("min_momentum_impulse", -1.0)),
        "min_structure_component": float(override.get("min_structure_component", -1.0)),
        "min_path_efficiency": float(
            override.get(
                "min_path_efficiency",
                getattr(_cfg, "SPOT_MIN_PATH_EFFICIENCY", 0.20),
            )
        ),
        "min_participation_component": float(override.get("min_participation_component", -1.0)),
        "min_volatility_quality": float(override.get("min_volatility_quality", -1.0)),
        "target_r_by_regime": {
            "TREND": float(
                target_r_by_regime.get(
                    "TREND",
                    getattr(_cfg, "SPOT_TARGET_R_BY_REGIME", {"TREND": 0.85})["TREND"],
                )
            ),
            "NEUTRAL": float(
                target_r_by_regime.get(
                    "NEUTRAL",
                    getattr(_cfg, "SPOT_TARGET_R_BY_REGIME", {"NEUTRAL": 0.65})["NEUTRAL"],
                )
            ),
            "CHOP": float(
                target_r_by_regime.get(
                    "CHOP",
                    getattr(_cfg, "SPOT_TARGET_R_BY_REGIME", {"CHOP": 0.50})["CHOP"],
                )
            ),
        },
        "trail_arm_r_by_regime": {
            "TREND": float(
                trail_arm_r_by_regime.get(
                    "TREND",
                    getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"TREND": 0.55})["TREND"],
                )
            ),
            "NEUTRAL": float(
                trail_arm_r_by_regime.get(
                    "NEUTRAL",
                    getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"NEUTRAL": 0.40})["NEUTRAL"],
                )
            ),
            "CHOP": float(
                trail_arm_r_by_regime.get(
                    "CHOP",
                    getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"CHOP": 0.30})["CHOP"],
                )
            ),
        },
        "setup_overrides": dict(override.get("setup_overrides", {})),
    }
    return policy


def strategy_spot_symbols() -> list[str]:
    import config as _cfg

    enabled = []
    for symbol in getattr(
        _cfg,
        "SPOT_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
    ):
        if get_spot_strategy(symbol)["enabled"]:
            enabled.append(_clean_symbol(symbol))
    return enabled


def setup_policy_for_symbol(symbol: str, setup_family: str, setup_score: float) -> dict[str, Any]:
    policy = get_spot_strategy(symbol)
    family = str(setup_family or "")
    score = float(setup_score or 0.0)
    if family not in KNOWN_SETUP_FAMILIES:
        return {
            "family": family,
            "setup_score": score,
            "preference": "unknown",
            "allowed": False,
            "reason": "unknown_setup_family",
        }
    required = set(policy.get("required_setup_families") or ())
    if required and family not in required:
        edge_reason = ""
        for cond in policy.get("edge_conditions") or ():
            if str(cond.get("field") or "") == "setup_family":
                edge_reason = str(cond.get("reason") or "")
                break
        return {
            "family": family,
            "setup_score": score,
            "preference": "disallowed",
            "allowed": False,
            "reason": edge_reason or "edge_setup_family_mismatch",
        }
    if family not in set(policy["allowed_setups"]):
        return {
            "family": family,
            "setup_score": score,
            "preference": "disallowed",
            "allowed": False,
            "reason": "setup_family_not_allowed",
        }
    if family in required or family in set(policy["preferred_setups"]):
        return {
            "family": family,
            "setup_score": score,
            "preference": "preferred",
            "allowed": True,
            "reason": "",
        }
    return {
        "family": family,
        "setup_score": score,
        "preference": "opportunistic",
        "allowed": True,
        "reason": "",
    }


def setup_preference_for_symbol(symbol: str, setup_family: str) -> str:
    policy = get_spot_strategy(symbol)
    family = str(setup_family or "")
    if family not in KNOWN_SETUP_FAMILIES:
        return "unknown"
    if family not in set(policy["allowed_setups"]):
        return "disallowed"
    if family in set(policy["preferred_setups"]):
        return "preferred"
    return "opportunistic"


def edge_policy_for_symbol(symbol: str) -> dict[str, Any]:
    policy = get_spot_strategy(symbol)
    conditions = tuple(policy.get("edge_conditions") or ())
    return {
        "symbol": policy["symbol"],
        "profile": str(policy.get("edge_profile") or "balanced"),
        "conditions": conditions,
        "conditions_summary": " + ".join(_edge_condition_label(cond) for cond in conditions),
        "metrics": dict(policy.get("edge_metrics") or {}),
    }


def score_floor_for_symbol(
    symbol: str,
    regime: str,
    *,
    structural_confirm_count: int = 0,
    setup_family: str = "",
    setup_score: float = 0.0,
) -> float:
    policy = get_spot_strategy(symbol)
    regime_key = str(regime or "NEUTRAL").upper()
    base = float(policy["score_floors"].get(regime_key, policy["score_floors"]["NEUTRAL"]))
    family = str(setup_family or "")
    if family in KNOWN_SETUP_FAMILIES and (
        policy.get("preferred_setups") or policy.get("required_setup_families")
    ):
        setup_policy = setup_policy_for_symbol(symbol, family, setup_score)
        if setup_policy["preference"] == "preferred":
            base += _setup_value(policy, family, "preferred_floor_delta")
        elif setup_policy["preference"] == "opportunistic":
            base += _setup_value(policy, family, "opportunistic_floor_delta")
        elif setup_policy["preference"] == "wildcard":
            base += _setup_value(policy, family, "wildcard_floor_delta")
    if regime_key in {"TREND", "NEUTRAL"} and family == "impulse_continuation" and (
        policy.get("preferred_setups") or policy.get("required_setup_families")
    ):
        base -= 0.5
    if regime_key != "CHOP" and structural_confirm_count >= 3:
        base -= 1.0
    if regime_key == "CHOP" and family in {"compression_breakout", "compression_expansion_retest"}:
        base += 1.0
    return max(55.0, min(base, 72.0))


def final_score_for_symbol(
    symbol: str,
    existing_composite: float,
    derivative_score: float,
    regime: str,
) -> float:
    policy = get_spot_strategy(symbol)
    weights = policy["score_weights"].get(str(regime or "NEUTRAL").upper(), policy["score_weights"]["NEUTRAL"])
    return round(
        float(existing_composite) * float(weights["composite"])
        + float(derivative_score) * float(weights["derivative"]),
        1,
    )


def target_r_for_symbol(symbol: str, regime: str) -> float:
    policy = get_spot_strategy(symbol)
    target_r, _ = _exit_targets(str(policy.get("edge_profile") or ""), regime, policy)
    return float(target_r)


def trail_arm_r_for_symbol(symbol: str, regime: str) -> float:
    policy = get_spot_strategy(symbol)
    _, trail_r = _exit_targets(str(policy.get("edge_profile") or ""), regime, policy)
    return float(trail_r)


def spot_quality_block_reason(
    symbol: str,
    spot_state: dict[str, Any] | None,
    *,
    final_spot_score: float | None = None,
) -> tuple[str, float]:
    policy = get_spot_strategy(symbol)
    clean = _clean_symbol(symbol)
    if not policy["enabled"]:
        return "spot_strategy_symbol_disabled", 0.0
    if not spot_state:
        return "spot_state_unavailable", 0.0

    regime = str(spot_state.get("regime") or "NEUTRAL").upper()
    setup_family = str(spot_state.get("setup_family") or "")
    setup_score = float(spot_state.get("setup_score") or 0.0)
    confirm_count = int(spot_state.get("structural_confirm_count") or 0)
    floor = score_floor_for_symbol(
        clean,
        regime,
        structural_confirm_count=confirm_count,
        setup_family=setup_family,
        setup_score=setup_score,
    )
    frames = spot_state.get("frames") or {}
    s5 = frames.get("5m") or {}
    s30 = frames.get("30m") or {}

    if policy["allowed_regimes"] and regime not in set(policy["allowed_regimes"]):
        return f"spot_regime_not_allowed:{regime}", floor

    setup_policy = setup_policy_for_symbol(clean, setup_family, setup_score)
    if not setup_policy["allowed"]:
        return str(setup_policy["reason"] or "setup_family_not_allowed"), floor

    for condition in policy.get("edge_conditions") or ():
        if not _edge_condition_matches(spot_state, condition):
            return str(condition.get("reason") or _default_edge_reason(condition)), floor

    if final_spot_score is not None and float(final_spot_score) < float(floor):
        return "below_regime_floor", floor
    if float(s5.get("v") or 0.0) <= 0 or float(s5.get("a") or 0.0) <= 0:
        return "5m_derivative_not_positive", floor
    if confirm_count < int(policy["min_confirm_count"]):
        return "structural_confirm_count_too_low", floor
    if float(s5.get("frame_score") or 0.0) < float(policy["min_5m_frame"]):
        return "frame_score_5m_too_low", floor
    if float(s30.get("frame_score") or 0.0) < float(policy["min_30m_frame"]):
        return "frame_score_30m_too_low", floor
    if float(s5.get("momentum_impulse") or 0.0) < float(policy["min_momentum_impulse"]):
        return "momentum_impulse_too_low", floor
    if float(s5.get("structure_component") or 0.0) < float(policy["min_structure_component"]):
        return "structure_component_too_low", floor
    if float(s5.get("path_efficiency") or 0.0) < float(policy["min_path_efficiency"]):
        return "path_efficiency_too_low", floor
    if float(s5.get("participation_component") or 0.0) < float(
        policy["min_participation_component"]
    ):
        return "participation_component_too_low", floor
    if float(s30.get("volatility_quality") or 0.0) < float(policy["min_volatility_quality"]):
        return "volatility_quality_too_low", floor
    return "", floor
