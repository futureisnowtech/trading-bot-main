"""
runtime/spot_strategy.py — symbol-specific live policy for crypto spot scalps.

This module keeps the spot strategy auditable:
- a finite setup library
- symbol preferences instead of hard-coded setup lockouts
- dynamic opportunistic allowance when derivative evidence is unusually strong
"""

from __future__ import annotations

import logging
from typing import Any
import system_state

logger = logging.getLogger(__name__)

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
    clean = str(symbol or "").upper().replace("/", "-")
    for suffix in ("-USDC", "-USDT", "-USD", "USDC", "USDT", "USD"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean.replace("-", "")


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


def _load_db_conditions(symbol: str) -> tuple[dict[str, Any], ...] | None:
    """
    Load active derived conditions from spot_edge_conditions DB table.
    Returns None if table doesn't exist or no active rows for symbol.
    Returns empty tuple () if the calibrator has run but derived zero conditions
    (meaning the bot should trade freely — all conditions are good).
    """
    import json as _json

    try:
        import sqlite3 as _sq

        try:
            import logging_db.trade_logger as _tl

            _db = str(_tl.DB_PATH)
        except Exception:
            import os as _os

            _db = _os.path.join(_os.path.dirname(__file__), "../logs/trades.db")

        conn = _sq.connect(_db)
        conn.row_factory = _sq.Row
        # Check if calibrator has ever run for this symbol (any row, active or not)
        ever_calibrated = conn.execute(
            "SELECT COUNT(*) FROM spot_edge_conditions WHERE symbol=?", (symbol,)
        ).fetchone()[0]
        if ever_calibrated == 0:
            conn.close()
            return (
                None  # Never calibrated — use config conditions (now empty = open gate)
            )
        rows = conn.execute(
            "SELECT field, operator, value, reason FROM spot_edge_conditions "
            "WHERE symbol=? AND active=1",
            (symbol,),
        ).fetchall()
        conn.close()
        conditions = []
        for r in rows:
            try:
                val = _json.loads(r["value"])
            except Exception:
                val = r["value"]
            conditions.append(
                {
                    "field": r["field"],
                    "operator": r["operator"],
                    "value": val,
                    "reason": r["reason"],
                }
            )
        return tuple(conditions)
    except Exception:
        return None


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


def _taker_score_surcharge(symbol: str, execution_route: str) -> float:
    route = str(execution_route or "").strip().lower()
    if route not in {"taker_fallback", "taker_market"}:
        return 0.0
    clean = _clean_symbol(symbol)
    if clean in {"SOL", "XRP", "DOGE"}:
        return 4.0
    return 3.0


def _synthetic_score_surcharge(synthetic_candidate: bool) -> float:
    return 2.0 if synthetic_candidate else 0.0


def _resolved_exit_profile(profile: str, regime: str) -> str:
    import config as _cfg

    regime_key = str(regime or "NEUTRAL").upper()
    return str(
        getattr(_cfg, "SPOT_TINY_LIVE_EXIT_PROFILE_BY_REGIME", {}).get(regime_key)
        or profile
        or ""
    ).strip().lower()


def _exit_targets(
    profile: str, regime: str, policy: dict[str, Any]
) -> tuple[float, float]:
    import config as _cfg

    profile_map = getattr(_cfg, "SPOT_EXIT_PROFILE_TARGETS", {})
    regime_key = str(regime or "NEUTRAL").upper()
    chosen = _resolved_exit_profile(profile, regime)
    targets = profile_map.get(chosen, {})
    if regime_key in targets:
        target_r, trail_r = targets[regime_key]
        return float(target_r), float(trail_r)
    target_r = float(
        policy["target_r_by_regime"].get(
            regime_key, policy["target_r_by_regime"]["NEUTRAL"]
        )
    )
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


def _tv_context_policy() -> dict[str, Any]:
    import config as _cfg

    return {
        "enabled": bool(getattr(_cfg, "TV_SIGNALS_ENABLED", False)),
        "profile_name": str(getattr(_cfg, "TV_SIGNAL_PROFILE_NAME", "") or "").strip(),
        "mode": str(getattr(_cfg, "TV_SIGNAL_MODE", "context_filter") or "")
        .strip()
        .lower(),
        "boost": float(getattr(_cfg, "TV_SIGNAL_BOOST_CONVICTION", 0) or 0.0),
        "max_age_seconds": float(
            getattr(_cfg, "TV_SIGNAL_MAX_AGE_SECONDS", 300) or 300.0
        ),
        "timeframe_min": int(getattr(_cfg, "TV_HTF_TIMEFRAME_MINUTES", 240) or 240),
        "block_short": bool(getattr(_cfg, "TV_BLOCK_ON_HTF_SHORT", True)),
        "block_close": bool(getattr(_cfg, "TV_BLOCK_ON_HTF_CLOSE", True)),
    }


def tv_context_score_adjustment(
    symbol: str,
    direction: str = "LONG",
    tv_context: dict[str, Any] | None = None,
) -> tuple[float, str]:
    policy = _tv_context_policy()
    if not policy["enabled"] or not isinstance(tv_context, dict):
        return 0.0, ""
    if policy["mode"] == "monitor_only":
        return 0.0, ""

    clean = _clean_symbol(symbol)
    payload_symbol = _clean_symbol(str(tv_context.get("symbol") or clean))
    if payload_symbol and payload_symbol != clean:
        return 0.0, ""

    profile_name = str(tv_context.get("profile_name") or "").strip()
    if (
        policy["profile_name"]
        and profile_name
        and profile_name != policy["profile_name"]
    ):
        return 0.0, ""

    try:
        tf_min = int(float(tv_context.get("tf_min") or 0))
    except Exception:
        tf_min = 0
    if tf_min and tf_min < int(policy["timeframe_min"]):
        return 0.0, ""

    try:
        age_seconds = float(tv_context.get("age_seconds") or 0.0)
    except Exception:
        age_seconds = 0.0
    if age_seconds and age_seconds > float(policy["max_age_seconds"]):
        return 0.0, ""

    htf_bias = str(
        tv_context.get("htf_bias") or tv_context.get("direction") or ""
    ).upper()
    normalized_direction = str(direction or "LONG").upper()
    if htf_bias == "SHORT" and policy["block_short"] and normalized_direction == "LONG":
        return 0.0, "tv_htf_short_bias_block"
    if htf_bias == "CLOSE" and policy["block_close"] and normalized_direction == "LONG":
        return 0.0, "tv_htf_close_bias_block"
    if htf_bias == normalized_direction:
        return float(policy["boost"]), ""
    return 0.0, ""


def _edge_condition_matches(
    spot_state: dict[str, Any], condition: dict[str, Any]
) -> bool:
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
    op_label = {
        "eq": "=",
        "gte": ">=",
        "gt": ">",
        "lte": "<=",
        "lt": "<",
        "in": "in",
    }.get(op, op)
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
    edge_conditions = _load_db_conditions(clean) or edge_conditions
    allowed_regimes = _tupled(getattr(_cfg, "SPOT_ALLOWED_REGIMES", {"TREND", "NEUTRAL"}))
    preferred_setups = _tupled(override.get("preferred_setups", ()), upper=False)
    allowed_setups = tuple(
        str(s).strip().lower()
        for s in getattr(_cfg, "SPOT_ALLOWED_SETUP_FAMILIES_TINY_LIVE", KNOWN_SETUP_FAMILIES)
        if str(s).strip().lower() in KNOWN_SETUP_FAMILIES
    ) or KNOWN_SETUP_FAMILIES
    score_floors = {
        "TREND": float(
            getattr(_cfg, "SPOT_TINY_LIVE_SCORE_FLOORS", {"TREND": 58.0})["TREND"]
        ),
        "NEUTRAL": float(
            getattr(_cfg, "SPOT_TINY_LIVE_SCORE_FLOORS", {"NEUTRAL": 60.0})["NEUTRAL"]
        ),
        "CHOP": float(
            getattr(_cfg, "SPOT_TINY_LIVE_SCORE_FLOORS", {"CHOP": 99.0})["CHOP"]
        ),
    }
    score_weights = getattr(_cfg, "SPOT_TINY_LIVE_SCORE_WEIGHTS", {})
    target_r_by_regime = override.get(
        "target_r_by_regime",
        getattr(
            _cfg,
            "SPOT_TARGET_R_BY_REGIME",
            {"TREND": 0.85, "NEUTRAL": 0.65, "CHOP": 0.50},
        ),
    )
    trail_arm_r_by_regime = override.get(
        "trail_arm_r_by_regime",
        getattr(
            _cfg,
            "SPOT_TRAIL_ARM_R_BY_REGIME",
            {"TREND": 0.55, "NEUTRAL": 0.40, "CHOP": 0.30},
        ),
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
        "preferred_setups": tuple(
            s for s in preferred_setups if s in KNOWN_SETUP_FAMILIES and s in allowed_setups
        ),
        "required_setup_families": tuple(
            s for s in _required_setup_families({"edge_conditions": edge_conditions})
        ),
        "edge_profile": str(override.get("edge_profile") or "").strip().lower(),
        "edge_conditions": edge_conditions,
        "edge_metrics": dict(override.get("edge_metrics") or {}),
        "opportunistic_setup_score": float(
            override.get("opportunistic_setup_score", 0.74)
        ),
        "wildcard_setup_score": float(override.get("wildcard_setup_score", 0.84)),
        "score_floors": score_floors,
        "score_weights": {
            "TREND": {
                "composite": float(
                    score_weights.get("TREND", {}).get("composite", 0.70)
                ),
                "derivative": float(
                    score_weights.get("TREND", {}).get("derivative", 0.30)
                ),
            },
            "NEUTRAL": {
                "composite": float(
                    score_weights.get("NEUTRAL", {}).get("composite", 0.90)
                ),
                "derivative": float(
                    score_weights.get("NEUTRAL", {}).get("derivative", 0.10)
                ),
            },
            "CHOP": {
                "composite": float(
                    score_weights.get("CHOP", {}).get("composite", 0.90)
                ),
                "derivative": float(
                    score_weights.get("CHOP", {}).get("derivative", 0.10)
                ),
            },
        },
        "min_confirm_count": int(
            getattr(_cfg, "SPOT_TINY_LIVE_MIN_CONFIRMS", {"TREND": 2})["TREND"]
        ),
        "min_5m_frame": float(
            getattr(_cfg, "SPOT_TINY_LIVE_MIN_5M_FRAME", {"TREND": 52.0})["TREND"]
        ),
        "min_30m_frame": float(
            getattr(_cfg, "SPOT_TINY_LIVE_MIN_30M_FRAME", {"TREND": 55.0})["TREND"]
        ),
        "min_momentum_impulse": float(
            getattr(
                _cfg, "SPOT_TINY_LIVE_MIN_MOMENTUM_IMPULSE", {"TREND": 0.000001}
            )["TREND"]
        ),
        "min_structure_component": float(
            getattr(
                _cfg, "SPOT_TINY_LIVE_MIN_STRUCTURE_COMPONENT", {"TREND": 0.000001}
            )["TREND"]
        ),
        "min_path_efficiency": float(
            getattr(_cfg, "SPOT_MIN_PATH_EFFICIENCY", 0.20)
        ),
        "min_participation_component": float(
            getattr(
                _cfg,
                "SPOT_TINY_LIVE_MIN_PARTICIPATION_COMPONENT",
                {"TREND": -999.0},
            )["TREND"]
        ),
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
                    getattr(_cfg, "SPOT_TARGET_R_BY_REGIME", {"NEUTRAL": 0.65})[
                        "NEUTRAL"
                    ],
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
                    getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"TREND": 0.55})[
                        "TREND"
                    ],
                )
            ),
            "NEUTRAL": float(
                trail_arm_r_by_regime.get(
                    "NEUTRAL",
                    getattr(_cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", {"NEUTRAL": 0.40})[
                        "NEUTRAL"
                    ],
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


def setup_policy_for_symbol(
    symbol: str, setup_family: str, setup_score: float
) -> dict[str, Any]:
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
        "conditions_summary": " + ".join(
            _edge_condition_label(cond) for cond in conditions
        ),
        "metrics": dict(policy.get("edge_metrics") or {}),
    }


def score_floor_for_symbol(
    symbol: str,
    regime: str,
    *,
    structural_confirm_count: int = 0,
    setup_family: str = "",
    setup_score: float = 0.0,
    execution_route: str = "",
    synthetic_candidate: bool = False,
) -> float:
    regime_key = str(regime or "NEUTRAL").upper()
    import config as _cfg

    floors = getattr(
        _cfg,
        "SPOT_TINY_LIVE_SCORE_FLOORS",
        {"TREND": 58.0, "NEUTRAL": 60.0, "CHOP": 99.0},
    )
    base = float(floors.get(regime_key, floors["NEUTRAL"]))
    return max(35.0, min(base, 99.0))


def final_score_for_symbol(
    symbol: str,
    existing_composite: float,
    derivative_score: float,
    regime: str,
    *,
    direction: str = "LONG",
    tv_context: dict[str, Any] | None = None,
) -> float:
    policy = get_spot_strategy(symbol)
    weights = policy["score_weights"].get(
        str(regime or "NEUTRAL").upper(), policy["score_weights"]["NEUTRAL"]
    )
    base = round(
        float(existing_composite) * float(weights["composite"])
        + float(derivative_score) * float(weights["derivative"]),
        1,
    )
    boost, _ = tv_context_score_adjustment(
        symbol,
        direction=direction,
        tv_context=tv_context,
    )
    return round(base + boost, 1)


def target_r_for_symbol(symbol: str, regime: str) -> float:
    policy = get_spot_strategy(symbol)
    target_r, _ = _exit_targets(str(policy.get("edge_profile") or ""), regime, policy)
    return float(target_r)


def trail_arm_r_for_symbol(symbol: str, regime: str) -> float:
    policy = get_spot_strategy(symbol)
    _, trail_r = _exit_targets(str(policy.get("edge_profile") or ""), regime, policy)
    return float(trail_r)


def exit_profile_for_symbol(symbol: str, regime: str) -> str:
    policy = get_spot_strategy(symbol)
    return _resolved_exit_profile(str(policy.get("edge_profile") or ""), regime)


def spot_quality_block_reason(
    symbol: str,
    spot_state: dict[str, Any] | None,
    *,
    final_spot_score: float | None = None,
    execution_route: str = "",
    synthetic_candidate: bool = False,
    tv_context: dict[str, Any] | None = None,
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
    
    # Push to system state
    system_state.state.update_strategy(
        active_symbol=clean,
        signal=setup_family,
        obi=float((spot_state.get("frames") or {}).get("5m", {}).get("obi") or 0.0),
        microprice=float(spot_state.get("microprice") or 0.0),
        mid_price=float(spot_state.get("mid_price") or 0.0)
    )
    system_state.state.update_prometheus()

    floor = score_floor_for_symbol(
        clean,
        regime,
        structural_confirm_count=confirm_count,
        setup_family=setup_family,
        setup_score=setup_score,
        execution_route=execution_route,
        synthetic_candidate=synthetic_candidate,
    )
    frames = spot_state.get("frames") or {}
    s5 = frames.get("5m") or {}
    s30 = frames.get("30m") or {}

    if policy["allowed_regimes"] and regime not in set(policy["allowed_regimes"]):
        return f"spot_regime_not_allowed:{regime}", floor
    if regime == "CHOP":
        return "spot_regime_not_allowed:CHOP", floor

    import config as _qs_cfg

    setup_policy = setup_policy_for_symbol(clean, setup_family, setup_score)
    if not setup_policy["allowed"]:
        return str(setup_policy["reason"] or "setup_family_not_allowed"), floor

    _, tv_block = tv_context_score_adjustment(
        clean,
        direction="LONG",
        tv_context=tv_context,
    )
    if tv_block:
        return tv_block, floor

    for condition in policy.get("edge_conditions") or ():
        if not _edge_condition_matches(spot_state, condition):
            return str(
                condition.get("reason") or _default_edge_reason(condition)
            ), floor

    if final_spot_score is not None and float(final_spot_score) < float(floor):
        return "below_regime_floor", floor
    regime_min_confirms = int(
        getattr(_qs_cfg, "SPOT_TINY_LIVE_MIN_CONFIRMS", {"TREND": 2, "NEUTRAL": 3})[
            regime
        ]
    )
    if confirm_count < regime_min_confirms:
        return "structural_confirm_count_too_low", floor
    min_5m_frame = float(
        getattr(_qs_cfg, "SPOT_TINY_LIVE_MIN_5M_FRAME", {"TREND": 52.0, "NEUTRAL": 55.0})[
            regime
        ]
    )
    if float(s5.get("frame_score") or 0.0) < min_5m_frame:
        return "frame_score_5m_too_low", floor
    min_30m_frame = float(
        getattr(_qs_cfg, "SPOT_TINY_LIVE_MIN_30M_FRAME", {"TREND": 55.0, "NEUTRAL": 58.0})[
            regime
        ]
    )
    if float(s30.get("frame_score") or 0.0) < min_30m_frame:
        return "frame_score_30m_too_low", floor
    min_momentum = float(
        getattr(
            _qs_cfg,
            "SPOT_TINY_LIVE_MIN_MOMENTUM_IMPULSE",
            {"TREND": 0.000001, "NEUTRAL": 0.000001},
        )[regime]
    )
    if float(s5.get("momentum_impulse") or 0.0) < min_momentum:
        return "momentum_impulse_too_low", floor
    min_structure = float(
        getattr(
            _qs_cfg,
            "SPOT_TINY_LIVE_MIN_STRUCTURE_COMPONENT",
            {"TREND": 0.000001, "NEUTRAL": 0.0},
        )[regime]
    )
    if float(s5.get("structure_component") or 0.0) < min_structure:
        return "structure_component_too_low", floor
    if float(s5.get("path_efficiency") or 0.0) < float(policy["min_path_efficiency"]):
        return "path_efficiency_too_low", floor
    min_participation = float(
        getattr(
            _qs_cfg,
            "SPOT_TINY_LIVE_MIN_PARTICIPATION_COMPONENT",
            {"TREND": -999.0, "NEUTRAL": 0.000001},
        )[regime]
    )
    if float(s5.get("participation_component") or 0.0) < min_participation:
        return "participation_component_too_low", floor
    if float(s30.get("volatility_quality") or 0.0) < float(
        policy["min_volatility_quality"]
    ):
        return "volatility_quality_too_low", floor
    return "", floor
