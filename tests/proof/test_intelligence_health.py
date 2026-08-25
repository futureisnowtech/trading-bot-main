from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from intelligence.health import get_rbi_evidence_health
from intelligence.schema import init_intelligence_db


def _iso(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_rbi_evidence_health_reports_latest_current_epoch_trace_and_run(tmp_path):
    db_path = str(tmp_path / "health.db")
    init_intelligence_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO intelligence_predictions
               (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                decision, q_gfs, q_ecmwf, learning_epoch, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "old:one",
                "old",
                _iso(20),
                "KXHIGHCHI-OLD",
                "KXHIGHCHI-OLD",
                "econ_veto",
                0.71,
                0.63,
                "old-epoch",
                _iso(20),
            ),
        )
        conn.execute(
            """INSERT INTO intelligence_predictions
               (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                decision, q_gfs, q_ecmwf, learning_epoch, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "current:one",
                "current",
                _iso(10),
                "KXHIGHDEN-CURRENT",
                "KXHIGHDEN-CURRENT",
                "econ_veto",
                0.68,
                0.61,
                "current-epoch",
                _iso(10),
            ),
        )
        conn.execute(
            """INSERT INTO intelligence_predictions
               (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                decision, learning_epoch, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "current:latest-unpriced",
                "current",
                _iso(5),
                "KXHIGHOKC-UNPRICED",
                "KXHIGHOKC-UNPRICED",
                "econ_veto",
                "current-epoch",
                _iso(5),
            ),
        )
        conn.execute(
            """INSERT INTO intelligence_runs
               (run_id, component, started_at, completed_at, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("run-1", "rbi2_cerebro", _iso(4), _iso(3), "COMPLETE"),
        )
        conn.commit()

    payload = get_rbi_evidence_health(
        db_path=db_path,
        learning_epoch="current-epoch",
    )

    assert payload["status"] == "healthy"
    assert payload["latest_prediction_at"]
    assert payload["latest_prediction_age_seconds"] is not None
    assert payload["latest_valid_prediction_at"]
    assert payload["latest_valid_prediction_age_seconds"] is not None
    assert payload["latest_run"]["run_id"] == "run-1"
    assert payload["latest_run"]["status"] == "COMPLETE"


def test_rbi_evidence_health_is_bounded_and_reports_degraded_truth(tmp_path):
    db_path = str(tmp_path / "bounded.db")
    init_intelligence_db(db_path)
    with sqlite3.connect(db_path) as conn:
        for idx in range(6):
            conn.execute(
                """INSERT INTO intelligence_predictions
                   (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                    decision, q_gfs, q_ecmwf, learning_epoch, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"scan:{idx}",
                    "scan",
                    _iso(idx),
                    f"KXHIGHCHI-{idx}",
                    f"KXHIGHCHI-{idx}",
                    "econ_veto",
                    0.70,
                    0.60,
                    "old-epoch",
                    _iso(idx),
                ),
            )
        conn.execute(
            """INSERT INTO intelligence_runs
               (run_id, component, started_at, completed_at, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("run-failed", "rbi2_cerebro", _iso(2), _iso(1), "FAILED"),
        )
        conn.commit()

    payload = get_rbi_evidence_health(
        db_path=db_path,
        learning_epoch="current-epoch",
        recent_window=3,
    )

    assert payload["status"] == "degraded"
    assert payload["recent_rows_inspected"] == 3
    assert "no_valid_current_epoch_pricing_trace_in_recent_window" in payload["issues"]
    assert "latest_intelligence_run_failed" in payload["issues"]


def test_rbi_evidence_health_never_raises_for_missing_database(tmp_path):
    payload = get_rbi_evidence_health(db_path=str(tmp_path / "missing.db"))

    assert payload["status"] == "unknown"
    assert payload["issues"]
    assert payload["issues"][0].startswith("evidence_health_unavailable:")


def test_sentinel_alerts_when_valid_rbi_trace_is_stale(monkeypatch):
    import intelligence.health as ih
    from runtime.sentinel import _check_rbi_evidence

    monkeypatch.setattr(
        ih,
        "get_rbi_evidence_health",
        lambda: {
            "status": "healthy",
            "issues": [],
            "latest_prediction_age_seconds": 60.0,
            "latest_valid_prediction_age_seconds": 31 * 60.0,
            "latest_run": {"status": "COMPLETE", "age_seconds": 60.0},
        },
    )

    key, is_bad, message = _check_rbi_evidence()

    assert key == "rbi_evidence"
    assert is_bad is True
    assert "valid_pricing_trace_stale_over_30m" in message


def test_sentinel_accepts_fresh_complete_rbi_evidence(monkeypatch):
    import intelligence.health as ih
    from runtime.sentinel import _check_rbi_evidence

    monkeypatch.setattr(
        ih,
        "get_rbi_evidence_health",
        lambda: {
            "status": "healthy",
            "issues": [],
            "latest_prediction_age_seconds": 60.0,
            "latest_valid_prediction_age_seconds": 120.0,
            "latest_run": {"status": "COMPLETE", "age_seconds": 60.0},
        },
    )

    _key, is_bad, message = _check_rbi_evidence()

    assert is_bad is False
    assert message.endswith("before any promotion.")
