"""Bounded, read-only health telemetry for the RBI 2.0 evidence loop."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DB_PATH, RBI_LEARNING_EPOCH


RECENT_PREDICTION_WINDOW = 1000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime) -> float | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _readonly_connection(db_path: str) -> sqlite3.Connection:
    # ``mode=ro`` makes the observability boundary incapable of migrating or
    # mutating the live trading database.  The path is resolved so relative test
    # and production paths both produce a valid SQLite file URI.
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def get_rbi_evidence_health(
    *,
    db_path: str = DB_PATH,
    learning_epoch: str = RBI_LEARNING_EPOCH,
    recent_window: int = RECENT_PREDICTION_WINDOW,
) -> dict[str, Any]:
    """Return bounded evidence/run freshness without ever blocking execution.

    The prediction query walks at most ``recent_window`` primary-key rows.  This
    is deliberate: production's database is multi-gigabyte and telemetry must
    not turn a missing current-epoch trace into a full-table scan.
    """
    now = _utc_now()
    payload: dict[str, Any] = {
        "status": "unknown",
        "learning_epoch": str(learning_epoch or ""),
        "recent_window_limit": max(1, min(int(recent_window), 10_000)),
        "recent_rows_inspected": 0,
        "latest_prediction_at": "",
        "latest_prediction_age_seconds": None,
        "latest_valid_prediction_at": "",
        "latest_valid_prediction_age_seconds": None,
        "latest_valid_prediction_id": None,
        "latest_run": {},
        "issues": [],
    }

    try:
        with closing(_readonly_connection(db_path)) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('intelligence_predictions', 'intelligence_runs')"
                ).fetchall()
            }
            if "intelligence_predictions" not in tables:
                payload["issues"].append("intelligence_predictions_table_missing")
                return payload

            rows = conn.execute(
                """SELECT id, evaluated_at, learning_epoch, q_gfs, q_ecmwf
                   FROM intelligence_predictions
                   ORDER BY id DESC
                   LIMIT ?""",
                (payload["recent_window_limit"],),
            ).fetchall()
            payload["recent_rows_inspected"] = len(rows)

            if rows:
                latest_at = str(rows[0]["evaluated_at"] or "")
                payload["latest_prediction_at"] = latest_at
                payload["latest_prediction_age_seconds"] = _age_seconds(
                    latest_at,
                    now=now,
                )
            else:
                payload["issues"].append("no_prediction_evidence")

            valid = next(
                (
                    row
                    for row in rows
                    if str(row["learning_epoch"] or "") == str(learning_epoch or "")
                    and row["q_gfs"] is not None
                    and row["q_ecmwf"] is not None
                ),
                None,
            )
            if valid is None:
                payload["issues"].append(
                    "no_valid_current_epoch_pricing_trace_in_recent_window"
                )
            else:
                valid_at = str(valid["evaluated_at"] or "")
                payload["latest_valid_prediction_at"] = valid_at
                payload["latest_valid_prediction_age_seconds"] = _age_seconds(
                    valid_at,
                    now=now,
                )
                payload["latest_valid_prediction_id"] = int(valid["id"])

            if "intelligence_runs" not in tables:
                payload["issues"].append("intelligence_runs_table_missing")
            else:
                run_columns = {
                    str(row["name"])
                    for row in conn.execute(
                        "PRAGMA table_info(intelligence_runs)"
                    ).fetchall()
                }
                required = {
                    "run_id",
                    "component",
                    "started_at",
                    "completed_at",
                    "status",
                }
                if not required.issubset(run_columns):
                    payload["issues"].append("intelligence_runs_schema_incomplete")
                else:
                    run = conn.execute(
                        """SELECT run_id, component, started_at, completed_at, status
                           FROM intelligence_runs
                           ORDER BY rowid DESC
                           LIMIT 1"""
                    ).fetchone()
                    if run is None:
                        payload["issues"].append("no_intelligence_run_recorded")
                    else:
                        run_payload = dict(run)
                        run_payload["age_seconds"] = _age_seconds(
                            run_payload.get("completed_at")
                            or run_payload.get("started_at"),
                            now=now,
                        )
                        payload["latest_run"] = run_payload
                        run_status = str(run_payload.get("status") or "").upper()
                        if run_status == "FAILED":
                            payload["issues"].append("latest_intelligence_run_failed")
                        elif run_status not in {"RUNNING", "COMPLETE"}:
                            payload["issues"].append(
                                f"latest_intelligence_run_status_unknown:{run_status or 'EMPTY'}"
                            )

        payload["status"] = "healthy" if not payload["issues"] else "degraded"
        return payload
    except Exception as exc:
        # This is telemetry, never a trading dependency.  Callers receive an
        # explicit unknown state rather than an exception or a false healthy bit.
        payload["issues"] = [f"evidence_health_unavailable:{exc}"]
        return payload
