"""Canonical operator-truth helpers for live Kalshi status and drift detection."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from config import DB_PATH

FORECAST_HEARTBEAT_STALE_SECONDS = 15 * 60
BASE_GFS_WEIGHT = 0.60
BASE_ECMWF_WEIGHT = 0.40


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
    return {
        "ticker": str(position.get("local_symbol") or ""),
        "side": str(position.get("side") or "").upper(),
        "right": str(position.get("right") or ""),
        "qty": float(position.get("qty") or 0.0),
        "entry_price": float(
            position.get("entry_price")
            or position.get("entry")
            or position.get("avg_entry")
            or 0.0
        ),
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
    payload = {
        "adaptive_active": False,
        "base_blend": {
            "gfs_weight": BASE_GFS_WEIGHT,
            "ecmwf_weight": BASE_ECMWF_WEIGHT,
        },
        "global_blend": {
            "segment": "STATIC",
            "sample_size": 0,
            "effective_weight": 0.0,
            "gfs_weight": BASE_GFS_WEIGHT,
            "ecmwf_weight": BASE_ECMWF_WEIGHT,
            "shrinkage": 0.0,
            "lookback_days": 30,
            "ts": "",
        },
        "mode_blends": [],
        "calibration": {},
    }

    try:
        with _connect_db(db_path) as conn:
            calibration_row = conn.execute(
                """
                SELECT ts, brier_score, win_rate, ensemble_accuracy, sample_size, edge_decay
                FROM weather_calibration
                ORDER BY ts DESC
                LIMIT 1
                """
            ).fetchone()
            if calibration_row:
                payload["calibration"] = dict(calibration_row)

            skill_rows = conn.execute(
                """
                SELECT segment, ts, sample_size, effective_weight, gfs_brier, ecmwf_brier,
                       gfs_weight, ecmwf_weight, shrinkage, lookback_days
                FROM weather_model_skill_state
                ORDER BY CASE WHEN segment='GLOBAL' THEN 0 ELSE 1 END, sample_size DESC, segment ASC
                """
            ).fetchall()
    except Exception as exc:
        payload["error"] = str(exc)
        return payload

    if not skill_rows:
        return payload

    rows = [dict(row) for row in skill_rows]
    global_row = next((row for row in rows if str(row.get("segment") or "").upper() == "GLOBAL"), None)
    if global_row:
        payload["global_blend"] = global_row
        payload["adaptive_active"] = int(global_row.get("sample_size") or 0) > 0
    else:
        payload["global_blend"]["ts"] = str(rows[0].get("ts") or "")

    payload["mode_blends"] = [
        row
        for row in rows
        if str(row.get("segment") or "").upper() not in {"", "GLOBAL"}
    ]
    if not payload["adaptive_active"]:
        payload["adaptive_active"] = any(int(row.get("sample_size") or 0) > 0 for row in payload["mode_blends"])
    return payload


def _is_weather_ticker(ticker: str) -> bool:
    try:
        from forecast.weather_contracts import weather_mode_for_ticker

        return weather_mode_for_ticker(str(ticker or "")) is not None
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
        "active_weather_contracts": 0,
        "checked_contracts": 0,
        "hydration": {
            "mode": "read_only_shared_truth",
            "attempted": False,
        },
    }

    try:
        from data.kalshi_weather_monitor import get_contract_weather_data
        from forecast.db import get_active_contracts

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
            weather = get_contract_weather_data(
                ticker,
                contract_name=str(contract.get("contract_name") or ""),
                strike=_coerce_float(contract.get("strike")),
                resolution_at=str(contract.get("resolution_at") or ""),
                last_trade_at=str(contract.get("last_trade_at") or ""),
            )
            if not weather:
                continue

            age_minutes = None
            ts_value = _coerce_float(weather.get("timestamp"))
            if ts_value is not None:
                age_minutes = max(0.0, (time.time() - ts_value) / 60.0)

            payload.update(
                {
                    "data_present": True,
                    "provider_mode": str(weather.get("provider_mode") or ""),
                    "forecast_source": str(weather.get("forecast_source") or ""),
                    "sample_ticker": ticker,
                    "weather_age_minutes": (
                        round(age_minutes, 2) if age_minutes is not None else None
                    ),
                }
            )
            break
    except Exception as exc:
        payload["error"] = str(exc)

    return payload


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
    payload["weather_learning"] = get_weather_learning_status(db_path=db_path)
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
    artifact_sha = str(artifact.get("audited_sha") or "").strip()
    build_sha = str(build.get("sha") or "").strip()
    artifact_matches_build = bool(artifact_sha and build_sha and artifact_sha == build_sha)
    artifact_blockers = [
        str(item or "").strip()
        for item in (artifact.get("blockers") or [])
        if str(item or "").strip()
    ]

    if truth is None:
        truth = get_live_kalshi_status(
            db_path=db_path,
            connect=False,
            sync_broker=False,
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
            from config import KALSHI_DATA_FRESHNESS_MINUTES

            age_minutes = provider.get("weather_age_minutes")
            if (
                age_minutes is not None
                and float(age_minutes) > float(KALSHI_DATA_FRESHNESS_MINUTES)
            ):
                blockers.append(
                    f"stale_ensemble_data ({float(age_minutes):.0f}m old)"
                )

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
            "artifact_sha": artifact_sha,
            "artifact_matches_build": artifact_matches_build,
            "metadata_stale": bool(build.get("metadata_stale")),
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
