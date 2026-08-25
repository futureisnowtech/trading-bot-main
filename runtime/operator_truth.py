"""Canonical operator-truth helpers for live Kalshi status and drift detection."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from config import DB_PATH, TRADE_DATA_START_DATE

FORECAST_HEARTBEAT_STALE_SECONDS = 15 * 60
BASE_GFS_WEIGHT = 0.60
BASE_ECMWF_WEIGHT = 0.40


def get_production_policy_status(
    *,
    balance_usd: float = 0.0,
    db_path: str = DB_PATH,
    learning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the versioned policy that actually governs a production entry."""
    import config
    from forecast.covariance_engine import (
        AUTHORITATIVE_COVARIANCE_MODE,
        NON_AUTHORITATIVE_COVARIANCE_MODE,
        get_station_history_status,
    )
    from forecast.pricing_engine import PHYSICS_METHOD, PRODUCTION_MODEL_PATH
    from intelligence.health import get_rbi_evidence_health
    from runtime.build_info import get_build_info

    build = get_build_info()
    learning_status = learning or get_weather_learning_status(db_path=db_path)
    gate = learning_status.get("learning_gate") or {}
    return {
        "version": str(build.get("app_version") or ""),
        "build_sha": str(build.get("build_sha") or build.get("sha") or ""),
        "short_sha": str(
            build.get("build_short_sha") or build.get("short_sha") or ""
        ),
        "execution": {
            "entry_route": "taker_only_ioc",
            "resting_entry_orders_allowed": False,
            "max_entry_slippage": float(config.KALSHI_MAX_ENTRY_SLIPPAGE),
        },
        "probability": {
            "model_path": PRODUCTION_MODEL_PATH,
            "physics_method": PHYSICS_METHOD,
            "commercial_open_meteo_ensemble_enabled": False,
            "aigfs_role": "uncertainty_scaler",
            "hrrr_role": "optional_near_term_daily_high",
            "metar_role": "near_term_cooling_entry_veto",
        },
        "rbi2": {
            "status": str(learning_status.get("status") or "unknown"),
            "champion_artifact_id": str(
                learning_status.get("champion_artifact_id") or ""
            ),
            "adaptive_active": bool(learning_status.get("adaptive_active")),
            "learning_epoch": str(gate.get("learning_epoch") or ""),
            "observed_days": float(gate.get("observed_days") or 0.0),
            "minimum_days": float(gate.get("minimum_days") or config.RBI_MIN_DAYS),
            "independent_event_count": int(
                gate.get("independent_event_count") or 0
            ),
            "required_independent_events": int(
                gate.get("required_independent_events")
                or config.RBI_MIN_NEW_CLEAN_TRADES
            ),
            "learning_gate_passed": bool(gate.get("passed")),
            "promotion_mode": "human_approved",
            "official_outcomes_only": True,
            "evidence_health": get_rbi_evidence_health(
                db_path=db_path,
                learning_epoch=str(gate.get("learning_epoch") or config.RBI_LEARNING_EPOCH),
            ),
        },
        "risk": {
            "max_deployed_pct": float(config.KALSHI_MAX_DEPLOYED_PCT),
            "max_concurrent_positions": int(config.KALSHI_MAX_CONCURRENT_POSITIONS),
            "max_qty_per_position": int(config.KALSHI_MAX_QTY_PER_POSITION),
            "base_position_cap_usd": float(config.KALSHI_MAX_USD_PER_POSITION),
            "max_risk_per_event_pct": float(config.KALSHI_MAX_RISK_PER_EVENT_PCT),
            "kelly_cap": float(config.KALSHI_KELLY_CAP),
            "hub_exposure_cap_usd": float(
                config.get_kalshi_hub_exposure_cap(balance_usd)
            ),
            "hub_exposure_rule": (
                f"max(${config.KALSHI_HUB_EXPOSURE_MIN_USD:g}, "
                f"{config.KALSHI_HUB_EXPOSURE_PCT:.0%} of live balance)"
            ),
            "minimum_model_headroom_f": float(config.KALSHI_MIN_MODEL_HEADROOM_F),
            "covariance_authoritative_mode": AUTHORITATIVE_COVARIANCE_MODE,
            "covariance_non_authoritative_mode": NON_AUTHORITATIVE_COVARIANCE_MODE,
            "covariance_errors_fail_closed": True,
            "covariance_history": get_station_history_status(db_path),
        },
    }


def _connect_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _json_or_empty(value: Any) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reason_code(reason: Any) -> str:
    token = str(reason or "").strip()
    if not token:
        return ""
    for splitter in (" (", ":"):
        if splitter in token:
            token = token.split(splitter, 1)[0]
            break
    return token.strip()


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_lane_heartbeat_age_seconds(value: Any) -> float | None:
    heartbeat = _parse_utc(value)
    if heartbeat is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - heartbeat).total_seconds())


def is_lane_heartbeat_fresh(
    value: Any,
    *,
    stale_after_seconds: int = FORECAST_HEARTBEAT_STALE_SECONDS,
) -> bool:
    age_seconds = get_lane_heartbeat_age_seconds(value)
    if age_seconds is None:
        return False
    return age_seconds <= max(1, int(stale_after_seconds))


def _normalize_lane_state(lane_state: dict[str, Any]) -> dict[str, Any]:
    state = dict(lane_state or {})
    heartbeat_at = state.get("last_heartbeat_at")
    age_seconds = get_lane_heartbeat_age_seconds(heartbeat_at)
    heartbeat_stale = age_seconds is None or age_seconds > FORECAST_HEARTBEAT_STALE_SECONDS

    state["heartbeat_age_seconds"] = age_seconds
    state["heartbeat_stale"] = heartbeat_stale

    if bool(state.get("active")) and heartbeat_stale:
        state["active"] = 0
        state["connected"] = 0
        state["tradable"] = 0
        state["health"] = "WARN"
        state["blocked_reason"] = "stale_runtime_heartbeat"
        state["action_needed"] = "restart_execution_engine"
        state["readiness_state"] = "STALE_HEARTBEAT"

    return state


def _normalize_broker_position(position: dict) -> dict:
    yes_leg_entry_price = float(
        position.get("yes_leg_entry_price")
        or position.get("entry_price")
        or position.get("entry")
        or position.get("avg_entry")
        or 0.0
    )
    return {
        "ticker": str(position.get("local_symbol") or ""),
        "side": str(position.get("side") or "").upper(),
        "right": str(position.get("right") or ""),
        "qty": float(position.get("qty") or 0.0),
        "entry_price": yes_leg_entry_price,
        "yes_leg_entry_price": yes_leg_entry_price,
        "held_side_entry_price": position.get("held_side_entry_price"),
        "market_exposure_usd": position.get("market_exposure_usd")
        or position.get("market_exposure_dollars"),
        "forecast_yes_prob": position.get("forecast_yes_prob"),
        "entered_at": position.get("entered_at"),
        "source": "broker",
    }


def _normalize_db_position(position: sqlite3.Row | dict) -> dict:
    row = dict(position)
    return {
        "ticker": str(row.get("ticker") or ""),
        "side": str(row.get("side") or "").upper(),
        "qty": float(row.get("qty") or 0.0),
        "entry_price": float(row.get("entry_price") or 0.0),
        "yes_leg_entry_price": float(row.get("entry_price") or 0.0),
        "opened_at": row.get("opened_at"),
        "source": "db",
    }


def _position_key(position: dict) -> tuple[str, str]:
    return (
        str(position.get("ticker") or ""),
        str(position.get("side") or "").upper(),
    )


def _position_drift(broker_positions: list[dict], db_positions: list[dict]) -> dict:
    broker_map = {_position_key(pos): pos for pos in broker_positions}
    db_map = {_position_key(pos): pos for pos in db_positions}

    broker_only = sorted(
        [
            broker_map[key]
            for key in broker_map.keys() - db_map.keys()
            if broker_map[key]["ticker"]
        ],
        key=lambda pos: (pos["ticker"], pos["side"]),
    )
    db_only = sorted(
        [
            db_map[key]
            for key in db_map.keys() - broker_map.keys()
            if db_map[key]["ticker"]
        ],
        key=lambda pos: (pos["ticker"], pos["side"]),
    )

    qty_mismatches = []
    entry_mismatches = []
    for key in broker_map.keys() & db_map.keys():
        b_pos = broker_map[key]
        d_pos = db_map[key]
        if abs(float(b_pos["qty"]) - float(d_pos["qty"])) > 1e-9:
            qty_mismatches.append(
                {
                    "ticker": b_pos["ticker"],
                    "side": b_pos["side"],
                    "broker_qty": b_pos["qty"],
                    "db_qty": d_pos["qty"],
                }
            )
        if abs(float(b_pos["entry_price"]) - float(d_pos["entry_price"])) > 1e-9:
            entry_mismatches.append(
                {
                    "ticker": b_pos["ticker"],
                    "side": b_pos["side"],
                    "broker_entry_price": b_pos["entry_price"],
                    "db_entry_price": d_pos["entry_price"],
                }
            )

    return {
        "has_drift": bool(broker_only or db_only or qty_mismatches or entry_mismatches),
        "broker_only": broker_only,
        "db_only": db_only,
        "qty_mismatches": sorted(qty_mismatches, key=lambda item: (item["ticker"], item["side"])),
        "entry_mismatches": sorted(entry_mismatches, key=lambda item: (item["ticker"], item["side"])),
    }


def get_recent_veto_summary(
    *, db_path: str = DB_PATH, lookback_hours: int = 6, limit: int = 200
) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    records: list[dict] = []
    reasons = Counter()

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, ticker, veto_reason, rank_score, ev, position_contracts, size_usd, details_json
                FROM recent_vetoes
                WHERE ts >= ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
    except Exception:
        rows = []

    if rows:
        for row in rows:
            reason = str(row["veto_reason"] or "unknown").strip() or "unknown"
            reasons[reason] += 1
            records.append(
                {
                    "ts": row["ts"],
                    "ticker": row["ticker"],
                    "reason": reason,
                    "rank_score": _coerce_float(row["rank_score"]),
                    "ev": _coerce_float(row["ev"]),
                    "position_contracts": int(row["position_contracts"] or 0),
                    "size_usd": _coerce_float(row["size_usd"]),
                    "details": _json_or_empty(row["details_json"]),
                }
            )
        return {
            "lookback_hours": lookback_hours,
            "count": len(records),
            "top_reasons": [
                {"reason": reason, "count": count}
                for reason, count in reasons.most_common(8)
            ],
            "recent_records": records[:12],
        }

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, source, message
                FROM system_events
                WHERE source='ForecastRunner'
                  AND level IN ('WARNING', 'ERROR')
                  AND ts >= ?
                  AND message LIKE '% vetoed: %'
                ORDER BY ts DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
    except Exception as exc:
        return {
            "lookback_hours": lookback_hours,
            "count": 0,
            "top_reasons": [],
            "recent_records": [],
            "error": str(exc),
        }

    for row in rows:
        message = str(row["message"] or "")
        _prefix, _sep, reason = message.partition(" vetoed: ")
        reason = reason.strip() or "unknown"
        reasons[reason] += 1
        records.append(
            {
                "ts": row["ts"],
                "reason": reason,
                "message": message,
            }
        )

    return {
        "lookback_hours": lookback_hours,
        "count": len(records),
        "top_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(8)
        ],
        "recent_records": records[:12],
    }


def get_yes_path_audit_summary(
    *,
    db_path: str = DB_PATH,
    start_date: str = TRADE_DATA_START_DATE,
    limit: int = 20000,
) -> dict:
    try:
        from intelligence.schema import init_intelligence_db

        init_intelligence_db(db_path)
    except Exception:
        pass

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT evaluated_at, ticker, chosen_side, decision, decision_reason,
                       q_champion, yes_ask, no_ask, weather_mode, features_json
                FROM intelligence_predictions
                WHERE evaluated_at >= ?
                  AND chosen_side='YES'
                ORDER BY evaluated_at DESC, id DESC
                LIMIT ?
                """,
                (start_date, max(1, int(limit))),
            ).fetchall()
    except Exception as exc:
        return {
            "start_date": start_date,
            "chosen_yes_count": 0,
            "entered_yes_count": 0,
            "blocked_yes_count": 0,
            "high_conf_blocked_yes_count": 0,
            "high_conf_entered_yes_count": 0,
            "top_blockers": [],
            "gate_flag_counts": {},
            "recent_samples": [],
            "error": str(exc),
        }

    if not rows:
        return {
            "start_date": start_date,
            "chosen_yes_count": 0,
            "entered_yes_count": 0,
            "blocked_yes_count": 0,
            "high_conf_blocked_yes_count": 0,
            "high_conf_entered_yes_count": 0,
            "top_blockers": [],
            "gate_flag_counts": {},
            "recent_samples": [],
        }

    chosen_yes_count = len(rows)
    entered_yes_count = 0
    blocked_yes_count = 0
    high_conf_blocked_yes_count = 0
    high_conf_entered_yes_count = 0
    blocker_stats: dict[str, dict[str, Any]] = {}
    gate_flag_counts: Counter[str] = Counter()
    recent_samples: list[dict[str, Any]] = []

    def _bucket_for(reason: str) -> dict[str, Any]:
        bucket = blocker_stats.get(reason)
        if bucket is None:
            bucket = {
                "count": 0,
                "held_prob_sum": 0.0,
                "held_prob_n": 0,
                "yes_ask_sum": 0.0,
                "yes_ask_n": 0,
                "spread_abs_sum": 0.0,
                "spread_abs_n": 0,
                "spread_ratio_sum": 0.0,
                "spread_ratio_n": 0,
                "yes_net_edge_sum": 0.0,
                "yes_net_edge_n": 0,
            }
            blocker_stats[reason] = bucket
        return bucket

    for row in rows:
        features = _json_or_empty(row["features_json"])
        audit = features.get("audit") if isinstance(features.get("audit"), dict) else {}
        gate_flags = audit.get("gate_flags") if isinstance(audit.get("gate_flags"), dict) else {}
        held_prob = (
            _coerce_float(audit.get("chosen_side_probability"))
            or _coerce_float(features.get("confidence"))
            or _coerce_float(row["q_champion"])
        )
        yes_ask = _coerce_float(audit.get("ask_yes")) or _coerce_float(row["yes_ask"])
        spread_abs = _coerce_float(audit.get("spread_abs"))
        spread_ratio = _coerce_float(audit.get("spread_ratio"))
        yes_net_edge = _coerce_float(audit.get("yes_net_edge"))
        decision = str(row["decision"] or "").strip()
        reason = _reason_code(audit.get("reason_code") or row["decision_reason"]) or "unknown"

        if len(recent_samples) < 8:
            recent_samples.append(
                {
                    "evaluated_at": row["evaluated_at"],
                    "ticker": row["ticker"],
                    "decision": decision,
                    "reason": reason,
                    "held_probability": held_prob,
                    "yes_ask": yes_ask,
                    "spread_abs": spread_abs,
                    "yes_net_edge": yes_net_edge,
                }
            )

        if decision == "entered":
            entered_yes_count += 1
            if held_prob is not None and held_prob >= 0.90:
                high_conf_entered_yes_count += 1
            continue

        blocked_yes_count += 1
        if held_prob is not None and held_prob >= 0.90:
            high_conf_blocked_yes_count += 1
        bucket = _bucket_for(reason)
        bucket["count"] += 1
        if held_prob is not None:
            bucket["held_prob_sum"] += held_prob
            bucket["held_prob_n"] += 1
        if yes_ask is not None:
            bucket["yes_ask_sum"] += yes_ask
            bucket["yes_ask_n"] += 1
        if spread_abs is not None:
            bucket["spread_abs_sum"] += spread_abs
            bucket["spread_abs_n"] += 1
        if spread_ratio is not None:
            bucket["spread_ratio_sum"] += spread_ratio
            bucket["spread_ratio_n"] += 1
        if yes_net_edge is not None:
            bucket["yes_net_edge_sum"] += yes_net_edge
            bucket["yes_net_edge_n"] += 1
        for flag, enabled in gate_flags.items():
            if enabled:
                gate_flag_counts[str(flag)] += 1

    def _avg(total: float, count: int) -> float | None:
        if count <= 0:
            return None
        return round(total / count, 4)

    top_blockers = [
        {
            "reason": reason,
            "count": int(stats["count"]),
            "avg_held_probability": _avg(stats["held_prob_sum"], stats["held_prob_n"]),
            "avg_yes_ask": _avg(stats["yes_ask_sum"], stats["yes_ask_n"]),
            "avg_spread_abs": _avg(stats["spread_abs_sum"], stats["spread_abs_n"]),
            "avg_spread_ratio": _avg(stats["spread_ratio_sum"], stats["spread_ratio_n"]),
            "avg_yes_net_edge": _avg(stats["yes_net_edge_sum"], stats["yes_net_edge_n"]),
        }
        for reason, stats in sorted(
            blocker_stats.items(),
            key=lambda item: (-int(item[1]["count"]), item[0]),
        )[:8]
    ]

    return {
        "start_date": start_date,
        "chosen_yes_count": chosen_yes_count,
        "entered_yes_count": entered_yes_count,
        "blocked_yes_count": blocked_yes_count,
        "high_conf_blocked_yes_count": high_conf_blocked_yes_count,
        "high_conf_entered_yes_count": high_conf_entered_yes_count,
        "top_blockers": top_blockers,
        "gate_flag_counts": dict(
            sorted(gate_flag_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "recent_samples": recent_samples,
    }


def get_recent_execution_summary(
    *, db_path: str = DB_PATH, lookback_hours: int = 6, limit: int = 200
) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))).isoformat()
    records: list[dict] = []

    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, source, message
                FROM system_events
                WHERE source='ForecastRunner'
                  AND level IN ('WARNING', 'ERROR')
                  AND ts >= ?
                  AND (
                        message LIKE '% execution_result: %'
                     OR message LIKE '% execution_blocked: %'
                  )
                ORDER BY ts DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(limit))),
            ).fetchall()
    except Exception as exc:
        return {
            "lookback_hours": lookback_hours,
            "count": 0,
            "top_outcomes": [],
            "recent_records": [],
            "error": str(exc),
        }

    outcomes = Counter()
    for row in rows:
        message = str(row["message"] or "")
        if " execution_result: " in message:
            _prefix, _sep, outcome = message.partition(" execution_result: ")
        else:
            _prefix, _sep, outcome = message.partition(" execution_blocked: ")
        outcome = outcome.strip() or "unknown"
        outcomes[outcome] += 1
        records.append(
            {
                "ts": row["ts"],
                "outcome": outcome,
                "message": message,
            }
        )

    return {
        "lookback_hours": lookback_hours,
        "count": len(records),
        "top_outcomes": [
            {"outcome": outcome, "count": count}
            for outcome, count in outcomes.most_common(8)
        ],
        "recent_records": records[:12],
    }


def get_weather_learning_status(*, db_path: str = DB_PATH) -> dict:
    from intelligence.rbi2 import get_rbi2_status
    status = get_rbi2_status(db_path=db_path)
    champion = status.get("champion") or {}
    learning_gate = status.get("learning_gate") or {}
    weights = champion.get("weights") or {"GLOBAL": {"gfs": BASE_GFS_WEIGHT, "ecmwf": BASE_ECMWF_WEIGHT}}
    global_weights = weights.get("GLOBAL") or {}
    champion_artifact_id = str(champion.get("artifact_id") or "rbi2-baseline-60-40")
    learned_active = champion_artifact_id != "rbi2-baseline-60-40"
    learning_complete = bool(learning_gate.get("passed"))
    return {
        "adaptive_active": learned_active,
        "status": (
            "rbi2_learned_active"
            if learned_active
            else "rbi2_awaiting_promotion"
            if learning_complete
            else "rbi2_learning_period"
        ),
        "disabled_reason": "",
        "champion_artifact_id": champion_artifact_id,
        "official_sample_count": status.get("official_sample_count", 0),
        "learning_gate": learning_gate,
        "base_blend": {"gfs_weight": BASE_GFS_WEIGHT, "ecmwf_weight": BASE_ECMWF_WEIGHT},
        "global_blend": {
            "segment": "GLOBAL",
            "sample_size": champion.get("sample_size", 0),
            "effective_weight": 1.0,
            "gfs_weight": global_weights.get("gfs", BASE_GFS_WEIGHT),
            "ecmwf_weight": global_weights.get("ecmwf", BASE_ECMWF_WEIGHT),
            "shrinkage": 0.0,
            "lookback_days": 0,
            "ts": champion.get("promoted_at", ""),
        },
        "mode_blends": weights,
        "calibration": {
            "promotion_mode": "human_approved",
            "official_outcomes_only": True,
            "minimum_learning_days": learning_gate.get("minimum_days", 7),
            "learning_epoch": learning_gate.get("learning_epoch", ""),
        },
    }


def _is_weather_ticker(ticker: str) -> bool:
    try:
        from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key
        from forecast.weather_contracts import weather_mode_for_ticker

        mode = weather_mode_for_ticker(ticker)
        if mode is None:
            return False

        city_key = resolve_weather_city_key(ticker)
        if city_key is None or city_key not in STATIONS:
            return False

        return True
    except Exception:
        token = str(ticker or "").upper()
        return any(
            prefix in token
            for prefix in (
                "KXHIGH",
                "KXHIGHT",
                "KXLOW",
                "KXLOWT",
                "KXRAIN",
                "KXSNOW",
                "KXWIND",
                "KXTEMP",
            )
        )


def _sample_weather_contracts(active_contracts: list[dict], *, limit: int) -> list[dict]:
    try:
        from data.kalshi_weather_monitor import _resolve_weather_series
    except Exception:
        return []

    sample: list[dict] = []
    seen_series: set[str] = set()
    for contract in active_contracts:
        ticker = str(contract.get("local_symbol") or "")
        if not _is_weather_ticker(ticker):
            continue
        series = _resolve_weather_series(ticker)
        if not series or series in seen_series:
            continue
        seen_series.add(series)
        sample.append(contract)
        if len(sample) >= max(1, int(limit)):
            break
    return sample


def get_weather_provider_status(
    *,
    db_path: str = DB_PATH,
    contract_limit: int = 8,
) -> dict:
    payload = {
        "data_present": False,
        "provider_mode": "",
        "forecast_source": "",
        "sample_ticker": "",
        "weather_age_minutes": None,
        "freshness_limit_minutes": None,
        "active_weather_contracts": 0,
        "checked_contracts": 0,
        "series_freshness": [],
        "stale_series": [],
        "hydration": {
            "mode": "read_only_shared_truth",
            "attempted": False,
        },
    }

    try:
        from data.kalshi_weather_monitor import get_contract_weather_data
        from forecast.db import get_active_contracts
        from forecast.weather_contracts import (
            is_hourly_weather_contract,
            weather_freshness_limit_minutes,
        )

        active = get_active_contracts(db_path=db_path)
        weather_contracts = [
            contract for contract in active if _is_weather_ticker(str(contract.get("local_symbol") or ""))
        ]
        payload["active_weather_contracts"] = len(weather_contracts)
        sample_contracts = _sample_weather_contracts(
            weather_contracts,
            limit=max(1, int(contract_limit)),
        )

        for contract in sample_contracts:
            payload["checked_contracts"] += 1
            ticker = str(contract.get("local_symbol") or "")
            contract_name = str(contract.get("contract_name") or "")
            weather = get_contract_weather_data(
                ticker,
                contract_name=contract_name,
                strike=_coerce_float(contract.get("strike")),
                resolution_at=str(contract.get("resolution_at") or ""),
                last_trade_at=str(contract.get("last_trade_at") or ""),
            )
            if not weather:
                continue

            age_minutes = None
            ts_value = _coerce_float(weather.get("timestamp"))
            if ts_value is not None:
                age_minutes = round(max(0.0, (time.time() - ts_value) / 60.0), 2)

            # Each series is judged against its own SPEC §4.5 window: an hourly
            # contract on 40-minute-old data is stale even though a daily high
            # on the same snapshot is fine.
            entry = {
                "ticker": ticker,
                "hourly": is_hourly_weather_contract(ticker, contract_name=contract_name),
                "age_minutes": age_minutes,
                "limit_minutes": weather_freshness_limit_minutes(
                    ticker, contract_name=contract_name
                ),
            }
            payload["series_freshness"].append(entry)
            if age_minutes is not None and age_minutes > entry["limit_minutes"]:
                payload["stale_series"].append(entry)

            if not payload["data_present"]:
                payload.update(
                    {
                        "data_present": True,
                        "provider_mode": str(weather.get("provider_mode") or ""),
                        "forecast_source": str(weather.get("forecast_source") or ""),
                        "sample_ticker": ticker,
                        "weather_age_minutes": age_minutes,
                        "freshness_limit_minutes": entry["limit_minutes"],
                    }
                )

        # Worst breach first, so the gate reports the most-overdue series.
        payload["stale_series"].sort(
            key=lambda item: float(item["age_minutes"]) - float(item["limit_minutes"]),
            reverse=True,
        )
    except Exception as exc:
        payload["error"] = str(exc)

    return payload


def _describe_stale_series(entry: dict[str, Any]) -> str:
    return (
        f"{float(entry.get('age_minutes') or 0.0):.0f}m old "
        f"> {int(entry.get('limit_minutes') or 0)}m limit for "
        f"{entry.get('ticker') or 'unknown'}"
    )


def get_provider_staleness_findings(
    provider: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return ``(blocker, warning)`` for weather-snapshot staleness.

    The release gate is a global kill switch, so only a *systemically* stale
    provider -- every sampled series past its own SPEC §4.5 window -- closes it.
    Partial staleness is a warning instead: ``forecast.strategy_engine`` still
    vetoes each stale contract at entry, so no stale trade slips through while
    the lanes that do have fresh data keep trading.

    Handles both the per-series shape written by ``get_weather_provider_status``
    and the flat single-sample shape carried by release artifacts from older
    builds, where only the headline sample age was recorded.
    """
    status = provider if isinstance(provider, dict) else {}

    stale_series = status.get("stale_series")
    dated_series = [
        item
        for item in (status.get("series_freshness") or [])
        if isinstance(item, dict) and item.get("age_minutes") is not None
    ]
    if isinstance(stale_series, list) and dated_series:
        if not stale_series:
            return "", ""
        worst = _describe_stale_series(stale_series[0])
        if len(stale_series) >= len(dated_series):
            return f"stale_weather_model_data ({worst})", ""
        return "", (
            f"partial_stale_weather_model_data "
            f"({len(stale_series)}/{len(dated_series)} series, worst {worst})"
        )

    age_minutes = _coerce_float(status.get("weather_age_minutes"))
    if age_minutes is None:
        return "", ""
    limit_minutes = _coerce_float(status.get("freshness_limit_minutes"))
    if limit_minutes is None:
        sample_ticker = str(status.get("sample_ticker") or "")
        try:
            from forecast.weather_contracts import weather_freshness_limit_minutes

            limit_minutes = float(weather_freshness_limit_minutes(sample_ticker))
        except Exception:
            from config import KALSHI_DATA_FRESHNESS_MINUTES

            limit_minutes = float(KALSHI_DATA_FRESHNESS_MINUTES)
    if age_minutes > limit_minutes:
        # Single-sample payload: that one series is the whole sample, so a
        # breach is systemic by definition.
        return (
            f"stale_weather_model_data ({age_minutes:.0f}m old "
            f"> {int(limit_minutes)}m limit)"
        ), ""
    return "", ""


def get_balance_truth_status(
    *,
    truth: dict | None = None,
    db_path: str = DB_PATH,
    tolerance_usd: float = 1.0,
) -> dict:
    if truth is None:
        truth = get_live_kalshi_status(
            db_path=db_path,
            connect=False,
            sync_broker=False,
            include_recent_vetoes=False,
            include_recent_execution=False,
        )

    lane = truth.get("forecast_lane") or {}
    snapshot = truth.get("forecast_snapshot") or {}
    broker_balance = _coerce_float(truth.get("balance_usd"))
    runtime_balance = _coerce_float(lane.get("buying_power_usd"))
    if runtime_balance is None:
        runtime_balance = _coerce_float(snapshot.get("equity"))

    comparison_available = broker_balance is not None and runtime_balance is not None
    delta_usd = None
    balance_ok = broker_balance is not None
    if comparison_available:
        delta_usd = round(float(broker_balance) - float(runtime_balance), 2)
        balance_ok = abs(delta_usd) <= max(0.0, float(tolerance_usd))

    return {
        "broker_balance_usd": broker_balance,
        "runtime_balance_usd": runtime_balance,
        "comparison_available": comparison_available,
        "delta_usd": delta_usd,
        "tolerance_usd": float(tolerance_usd),
        "balance_ok": balance_ok,
    }


def get_live_kalshi_status(
    *,
    db_path: str = DB_PATH,
    connect: bool = True,
    sync_broker: bool = True,
    include_recent_vetoes: bool = True,
    include_recent_execution: bool = True,
) -> dict:
    """Return broker-first live truth for Telegram, HUD, and operator analysis."""
    from execution.kalshi_broker import get_kalshi_broker

    broker = get_kalshi_broker()
    broker_connected = broker.is_connected()
    broker_error = ""

    if connect and not broker_connected:
        try:
            broker_connected = bool(broker.connect())
        except Exception as exc:
            broker_error = str(exc)
            broker_connected = False

    if broker_connected and sync_broker:
        try:
            broker.sync_positions()
        except Exception as exc:
            if not broker_error:
                broker_error = f"sync_positions_failed: {exc}"

    balance_usd = 0.0
    broker_positions: list[dict] = []
    if broker_connected:
        try:
            balance_usd = float(broker.get_account_balance() or 0.0)
        except Exception as exc:
            if not broker_error:
                broker_error = f"get_account_balance_failed: {exc}"
        try:
            broker_positions = [
                _normalize_broker_position(pos)
                for pos in broker.get_positions()
                if float(pos.get("qty") or 0.0) > 0
            ]
        except Exception as exc:
            if not broker_error:
                broker_error = f"get_positions_failed: {exc}"

    db_positions: list[dict] = []
    active_markets = 0
    lane_state = {}
    snapshot = {}
    db_error = ""
    try:
        with _connect_db(db_path) as conn:
            db_positions = [
                _normalize_db_position(row)
                for row in conn.execute(
                    """
                    SELECT ticker, qty, entry_price, side, opened_at
                    FROM forecast_positions
                    WHERE active = 1 AND qty > 0
                    ORDER BY opened_at ASC
                    """
                ).fetchall()
            ]

            row = conn.execute(
                "SELECT COUNT(*) AS n FROM forecast_markets WHERE active=1"
            ).fetchone()
            active_markets = int((row["n"] if row else 0) or 0)

            lane_row = conn.execute(
                "SELECT * FROM lane_runtime_state WHERE lane_id='forecast'"
            ).fetchone()
            if lane_row:
                lane_state = _normalize_lane_state(dict(lane_row))
                snapshot = _json_or_empty(lane_row["snapshot_json"])
                lane_state.pop("snapshot_json", None)
    except Exception as exc:
        db_error = str(exc)

    drift = _position_drift(broker_positions, db_positions)

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "broker_connected": broker_connected,
        "broker_error": broker_error,
        "db_error": db_error,
        "balance_usd": round(balance_usd, 2),
        "active_markets": active_markets,
        "broker_positions_count": len(broker_positions),
        "db_positions_count": len(db_positions),
        "broker_positions": broker_positions,
        "db_positions": db_positions,
        "position_drift": drift,
        "forecast_lane": lane_state,
        "forecast_snapshot": snapshot,
    }
    if include_recent_vetoes:
        payload["recent_vetoes"] = get_recent_veto_summary(db_path=db_path)
    if include_recent_execution:
        payload["recent_execution"] = get_recent_execution_summary(db_path=db_path)
    payload["yes_path_audit"] = get_yes_path_audit_summary(db_path=db_path)
    payload["weather_learning"] = get_weather_learning_status(db_path=db_path)
    payload["production_policy"] = get_production_policy_status(
        balance_usd=balance_usd,
        db_path=db_path,
        learning=payload["weather_learning"],
    )
    return payload


def get_release_status(
    *,
    db_path: str = DB_PATH,
    truth: dict | None = None,
) -> dict:
    from runtime.build_info import get_build_info
    from runtime.incident_tracker import get_incident_summary, get_open_incidents
    from runtime.release_gate import (
        PASSING_VERDICTS,
        VERDICT_BLOCKED,
        VERDICT_PASS_WITH_WARNINGS,
        VERDICT_READY_FOR_LIVE,
        is_infrastructure_reason,
        load_release_audit_artifact,
    )

    build = get_build_info()
    artifact = load_release_audit_artifact()
    artifact_details = artifact.get("details") if isinstance(artifact, dict) else {}
    artifact_live_truth = (
        artifact_details.get("live_truth") if isinstance(artifact_details, dict) else {}
    ) or {}
    artifact_provider = (
        artifact_details.get("provider_status") if isinstance(artifact_details, dict) else {}
    ) or {}
    artifact_balance = (
        artifact_details.get("balance_truth") if isinstance(artifact_details, dict) else {}
    ) or {}

    artifact_verdict = str(artifact.get("verdict") or "")
    artifact_mode = str(artifact.get("mode") or "").strip()
    artifact_sha = str(artifact.get("audited_sha") or "").strip()
    build_sha = str(build.get("sha") or "").strip()
    artifact_matches_build = bool(artifact_sha and build_sha and artifact_sha == build_sha)
    artifact_blockers = [
        str(item or "").strip()
        for item in (artifact.get("blockers") or [])
        if str(item or "").strip()
    ]

    deploy_pending = artifact_mode == "deploy_pending" and artifact_matches_build

    if truth is None:
        truth = get_live_kalshi_status(
            db_path=db_path,
            connect=deploy_pending,
            sync_broker=deploy_pending,
            include_recent_execution=False,
        )
        if artifact_matches_build and artifact_live_truth and not bool(truth.get("broker_connected")):
            truth["broker_connected"] = bool(artifact_live_truth.get("broker_connected"))
            truth["broker_error"] = str(
                truth.get("broker_error") or artifact_live_truth.get("broker_error") or ""
            )
            if truth.get("balance_usd") in (None, 0, 0.0):
                truth["balance_usd"] = artifact_live_truth.get("balance_usd")
            if not truth.get("active_markets"):
                truth["active_markets"] = int(artifact_live_truth.get("active_markets") or 0)
            if not truth.get("forecast_lane") and artifact_live_truth.get("lane"):
                truth["forecast_lane"] = dict(artifact_live_truth.get("lane") or {})

    lane = truth.get("forecast_lane") or {}
    veto_summary = truth.get("recent_vetoes") or get_recent_veto_summary(db_path=db_path)
    incident_summary = get_incident_summary(db_path=db_path)
    open_incidents = get_open_incidents(db_path=db_path)
    provider = get_weather_provider_status(db_path=db_path)
    if artifact_matches_build and artifact_provider.get("data_present") and not provider.get("data_present"):
        provider = dict(artifact_provider)
    balance_truth = get_balance_truth_status(truth=truth, db_path=db_path)
    if artifact_matches_build and artifact_balance.get("balance_ok") and not balance_truth.get("balance_ok"):
        balance_truth = dict(artifact_balance)
    try:
        from data.kalshi_weather_monitor import get_hourly_city_support_summary
        from forecast.weather_contracts import live_entry_scope

        hourly_support = get_hourly_city_support_summary()
        entry_scope = live_entry_scope()
    except Exception:
        hourly_support = {
            "universe_city_count": 0,
            "resolver_ready_city_count": 0,
            "explicit_hourly_series_city_count": 0,
            "resolver_ready_cities": [],
            "explicit_hourly_series_cities": [],
        }
        entry_scope = "UNKNOWN"

    blockers: list[str] = []
    warnings: list[str] = []

    if not artifact:
        blockers.append("release_audit_missing")
    elif artifact_verdict not in PASSING_VERDICTS:
        if artifact_blockers and artifact_matches_build:
            blockers.extend(artifact_blockers)
        else:
            blockers.append(f"release_audit_not_passing ({artifact_verdict or 'UNKNOWN'})")
    elif build_sha and not artifact_sha:
        blockers.append("release_audit_sha_missing")
    elif build_sha and not artifact_matches_build:
        blockers.append(
            f"release_audit_sha_mismatch ({artifact_sha or 'missing'} != {build_sha})"
        )

    if not bool(truth.get("broker_connected")):
        blockers.append(
            str(truth.get("broker_error") or "broker_disconnected")
        )
    broker_error = str(truth.get("broker_error") or "")
    if broker_error and any(
        token in broker_error
        for token in (
            "get_account_balance_failed",
            "get_positions_failed",
            "sync_positions_failed",
        )
    ):
        blockers.append(broker_error)

    if bool(lane.get("heartbeat_stale")):
        blockers.append("stale_runtime_heartbeat")

    if int(incident_summary.get("by_severity", {}).get("CRITICAL", 0) or 0) > 0:
        blockers.append("unresolved_critical_incidents")

    if build.get("metadata_stale"):
        blockers.append("deploy_runtime_metadata_stale")

    if int(truth.get("active_markets") or 0) > 0:
        provider_mode = str(provider.get("provider_mode") or "").strip()
        if not provider.get("data_present"):
            blockers.append("weather_provider_unavailable")
        elif not provider_mode:
            blockers.append("provider_mode_unknown")
        else:
            staleness_blocker, staleness_warning = get_provider_staleness_findings(
                provider
            )
            if staleness_blocker:
                blockers.append(staleness_blocker)
            if staleness_warning:
                warnings.append(staleness_warning)

    if not balance_truth.get("balance_ok"):
        if balance_truth.get("comparison_available"):
            blockers.append(
                f"balance_truth_mismatch ({balance_truth.get('delta_usd')} usd)"
            )
        else:
            blockers.append("get_account_balance_failed")

    top_warning_reasons = [
        row
        for row in (veto_summary.get("top_reasons") or [])
        if not is_infrastructure_reason(str(row.get("reason") or ""))
    ][:5]
    if top_warning_reasons:
        warnings.append(
            ", ".join(
                f"{row.get('reason')} x{row.get('count')}"
                for row in top_warning_reasons[:3]
            )
        )

    artifact_warnings = artifact.get("warnings") or []
    for warning in artifact_warnings:
        text = str(warning or "").strip()
        if text:
            warnings.append(text)

    deduped_blockers: list[str] = []
    seen_blockers: set[str] = set()
    for item in blockers:
        text = str(item or "").strip()
        if text and text not in seen_blockers:
            seen_blockers.add(text)
            deduped_blockers.append(text)

    deduped_warnings: list[str] = []
    seen_warnings: set[str] = set()
    for item in warnings:
        text = str(item or "").strip()
        if text and text not in seen_warnings:
            seen_warnings.add(text)
            deduped_warnings.append(text)

    if deduped_blockers:
        verdict = VERDICT_BLOCKED
    elif artifact_verdict == VERDICT_PASS_WITH_WARNINGS:
        verdict = VERDICT_PASS_WITH_WARNINGS
    elif artifact_verdict == VERDICT_READY_FOR_LIVE:
        verdict = VERDICT_READY_FOR_LIVE
    else:
        verdict = VERDICT_PASS_WITH_WARNINGS if deduped_warnings else VERDICT_BLOCKED

    return {
        "current_release_verdict": verdict,
        "entries_allowed": verdict in PASSING_VERDICTS,
        "entry_scope": entry_scope,
        "hourly_city_support": hourly_support,
        "last_audit_at": str(artifact.get("as_of") or ""),
        "last_successful_audit_at": str(
            artifact.get("last_successful_audit_at")
            or artifact.get("as_of")
            or ""
        ),
        "provider_mode": str(provider.get("provider_mode") or ""),
        "provider_status": provider,
        "balance_truth": balance_truth,
        "heartbeat_fresh": not bool(lane.get("heartbeat_stale")),
        "heartbeat_age_seconds": lane.get("heartbeat_age_seconds"),
        "top_infrastructure_blockers": deduped_blockers[:6],
        "top_non_blocking_veto_reasons": top_warning_reasons,
        "deploy_parity": {
            "build_sha": build_sha,
            "embedded_build_sha": str(build.get("build_sha") or ""),
            "metadata_sha": str(build.get("metadata_sha") or ""),
            "artifact_sha": artifact_sha,
            "artifact_matches_build": artifact_matches_build,
            "metadata_stale": bool(build.get("metadata_stale")),
            "build_sha_mismatch": bool(build.get("build_sha_mismatch")),
            "version": str(build.get("app_version") or ""),
            "deployed_at_utc": str(build.get("deployed_at_utc") or ""),
        },
        "open_incidents": incident_summary,
        "critical_incidents": [
            {
                "source": row.get("source"),
                "severity": row.get("severity"),
                "sample_message": row.get("sample_message"),
            }
            for row in open_incidents
            if str(row.get("severity") or "").upper() == "CRITICAL"
        ][:5],
        "artifact_verdict": artifact_verdict,
        "artifact_entries_allowed": bool(artifact.get("entries_allowed")),
        "warnings": deduped_warnings[:6],
    }
