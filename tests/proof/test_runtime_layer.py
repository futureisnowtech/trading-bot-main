"""
tests/proof/test_runtime_layer.py — Runtime truth layer invariants for the lean Kalshi stack.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


def _insert_event_raw(db_path: Path, source: str, message: str, level: str = "ERROR") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?,?,?,?)",
            (ts, level, source, message),
        )


def test_runtime_tables_init(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "system_runtime_state" in tables
    assert "lane_runtime_state" in tables


def test_upsert_system_state_round_trips(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(
        db_path=db,
        process_mode="live",
        active_lanes='["forecast"]',
        launch_readiness_state="READY",
    )

    state = rs.get_system_state(db_path=db)
    assert state["process_mode"] == "live"
    assert "forecast" in state["active_lanes"]
    assert state["launch_readiness_state"] == "READY"


def test_upsert_lane_state_forecast_round_trips(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        enabled=1,
        active=1,
        connected=1,
        tradable=1,
        health="OK",
        readiness_state="OPERATIONAL",
    )

    lane = rs.get_lane_state("forecast", db_path=db)
    assert lane["lane_id"] == "forecast"
    assert lane["connected"] == 1
    assert lane["readiness_state"] == "OPERATIONAL"


def test_mark_lane_heartbeat_updates_timestamp(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state("forecast", db_path=db, enabled=1)
    rs.mark_lane_heartbeat("forecast", db_path=db)

    lane = rs.get_lane_state("forecast", db_path=db)
    assert lane["last_heartbeat_at"]


def test_incident_ingest_groups_forecast_errors(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    for _ in range(5):
        _insert_event_raw(
            proof_runtime.db_path,
            source="ForecastRunner",
            message="quote harvest timeout",
            level="ERROR",
        )

    upserted = it.ingest_system_events(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert upserted == 1
    assert len(incidents) == 1
    assert incidents[0]["lane_id"] == "forecast"
    assert incidents[0]["count"] >= 5


def test_incident_ingest_non_forecast_sources_fall_back_to_system(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    _insert_event_raw(
        proof_runtime.db_path,
        source="telegram_daemon",
        message="operator command failed",
        level="ERROR",
    )

    it.ingest_system_events(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert incidents
    assert incidents[0]["lane_id"] == "system"


def test_position_reconciliation_logs_completion_event(proof_runtime, monkeypatch):
    import runtime.position_reconciler as pr

    broker = MagicMock()
    broker.connect.return_value = True
    broker.get_positions.return_value = [{"ticker": "KXHIGHNY-26JUN04-B80.5"}]

    with patch("execution.kalshi_broker.get_kalshi_broker", return_value=broker):
        pr.run_reconciliation(db_path=str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            """
            SELECT source, message
            FROM system_events
            WHERE source='PositionReconciler'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert "Kalshi holdings=1" in row[1]


def test_lane_economics_forecast_is_zero_cost():
    from runtime.economics import get_lane_economics, is_trade_viable

    econ = get_lane_economics("forecast")
    assert econ.lane_id == "forecast"
    assert econ.taker_fee_pct == 0.0
    assert econ.min_viable_edge_pct == 0.0
    assert is_trade_viable("forecast", 0.0) is True
