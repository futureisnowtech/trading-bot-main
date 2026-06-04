"""Forecast control snapshot helpers for dashboard proof tests."""

from __future__ import annotations

import sqlite3

import dashboard.data.forecast as forecast_data


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(forecast_data._resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_forecast_control_snapshot() -> dict:
    readiness = forecast_data.get_forecast_readiness()
    health = forecast_data.get_forecast_health()
    contradictions: list[str] = []

    runtime_state = None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT readiness_state
            FROM lane_runtime_state
            WHERE lane_id='forecast'
            LIMIT 1
            """
        ).fetchone()
        runtime_state = row["readiness_state"] if row else ""

    if health["underliers_visible"] > 0 and runtime_state == "NO_UNDERLIERS":
        contradictions.append("Runtime says NO_UNDERLIERS but forecast markets are present.")

    return {
        "health": health,
        "readiness": readiness,
        "contradictions": contradictions,
    }
