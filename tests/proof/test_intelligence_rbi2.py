import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from config import RBI_LEARNING_EPOCH
from intelligence.cerebro import create_experiment_from_insight, generate_insights, get_cerebro_status, process_experiments
from intelligence.outcomes import record_outcome
from intelligence.rbi2 import (
    _chronological_event_split,
    _learning_gate,
    _production_probability,
    get_active_model_weights,
    promote_challenger,
    train_challenger,
)
from intelligence.schema import DDL, connect, init_intelligence_db


def _seed(
    db_path: str,
    count: int = 32,
    *,
    spacing_hours: int = 8,
    learning_epoch: str = RBI_LEARNING_EPOCH,
) -> None:
    init_intelligence_db(db_path)
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with connect(db_path) as conn:
        for idx in range(count):
            ticker = f"KXTEST-{idx:03d}"
            settled = (start + timedelta(hours=idx * spacing_hours)).isoformat()
            outcome = 1 if idx % 2 == 0 else 0
            q_gfs = 0.85 if outcome else 0.15
            q_ecmwf = 0.55 if outcome else 0.45
            conn.execute(
                """INSERT INTO intelligence_predictions
                   (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                    weather_mode, decision, q_gfs, q_ecmwf, learning_epoch,
                    features_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'HIGH', 'entered', ?, ?, ?, '{}', ?)""",
                (
                    f"scan:{ticker}", "scan", settled, ticker, ticker,
                    q_gfs, q_ecmwf, learning_epoch, settled,
                ),
            )
        conn.commit()
    for idx in range(count):
        ticker = f"KXTEST-{idx:03d}"
        settled = (start + timedelta(hours=idx * spacing_hours)).isoformat()
        record_outcome(
            ticker=ticker,
            market_result="YES" if idx % 2 == 0 else "NO",
            market_status="finalized",
            official=True,
            source="kalshi_market_finalized",
            source_payload={"ticker": ticker, "result": idx % 2},
            settled_at=settled,
            db_path=db_path,
        )


def test_rbi2_requires_explicit_promotion(tmp_path):
    db_path = str(tmp_path / "rbi2.db")
    _seed(db_path)
    before = get_active_model_weights("HIGH", db_path=db_path)
    challenger = train_challenger(db_path=db_path)
    assert challenger["status"] == "challenger_ready"
    assert challenger["validation"]["independent_unit"] == "event_key"
    assert challenger["validation"]["probability_transform"].endswith(
        "apply_divergence_probability_guard"
    )
    assert challenger["validation"]["independent_event_count"] == 32
    assert get_active_model_weights("HIGH", db_path=db_path) == before
    promote_challenger(challenger["artifact_id"], promoted_by="test", reason="validated", db_path=db_path)
    after = get_active_model_weights("HIGH", db_path=db_path)
    assert after["artifact_id"] == challenger["artifact_id"]
    assert after["gfs"] > before["gfs"]


def test_rbi2_scores_the_exact_production_log_odds_path_with_hrrr():
    from forecast.pricing_engine import log_odds_blend
    from forecast.primitives import apply_divergence_probability_guard

    row = {
        "q_gfs": 0.91,
        "q_ecmwf": 0.22,
        "q_hrrr": 0.78,
        "hours_to_resolution": 9.0,
    }
    weights = {"gfs": 0.65, "ecmwf": 0.35}

    replayed = _production_probability(row, weights)
    raw_production = log_odds_blend(0.91, 0.22, 0.78, weights, 9.0)
    production = apply_divergence_probability_guard(
        raw_production,
        row["q_gfs"],
        row["q_ecmwf"],
    )
    arithmetic = 0.65 * 0.91 + 0.35 * 0.22

    assert replayed == pytest.approx(production)
    assert replayed != pytest.approx(raw_production)
    assert replayed != pytest.approx(arithmetic)


def test_rbi2_holdout_is_chronological_and_event_grouped():
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    rows = []
    for event_idx in range(24):
        settled_at = (start + timedelta(hours=event_idx * 8)).isoformat()
        for strike_idx in range(3):
            rows.append(
                {
                    "ticker": f"KXGROUP-{event_idx:02d}-T{strike_idx}",
                    "event_key": f"KXGROUP-{event_idx:02d}",
                    "settled_at": settled_at,
                }
            )

    train, holdout, train_keys, holdout_keys = _chronological_event_split(rows)

    assert len(train_keys) == 18
    assert len(holdout_keys) == 6
    assert set(train_keys).isdisjoint(holdout_keys)
    assert {row["event_key"] for row in train} == set(train_keys)
    assert {row["event_key"] for row in holdout} == set(holdout_keys)
    assert max(row["settled_at"] for row in train) < min(
        row["settled_at"] for row in holdout
    )


def test_rbi2_learning_gate_counts_independent_events_not_sibling_markets():
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    rows = [
        {
            "ticker": f"KXSIBLING-26JUL23-T{strike}",
            "event_key": "KXSIBLING-26JUL23",
            "evaluated_at": (start + timedelta(days=8)).isoformat(),
            "settled_at": (start + timedelta(days=8)).isoformat(),
        }
        for strike in range(40)
    ]

    gate = _learning_gate(rows)

    assert gate["sample_count"] == 40
    assert gate["independent_event_count"] == 1
    assert gate["passed"] is False


def test_rbi2_enforces_seven_day_current_epoch_learning_period(tmp_path):
    db_path = str(tmp_path / "learning-period.db")
    _seed(db_path, spacing_hours=1)

    result = train_challenger(db_path=db_path)

    assert result["status"] == "learning_period_active"
    assert result["learning_gate"]["sample_count"] == 32
    assert result["learning_gate"]["observed_days"] < 7.0
    assert result["learning_gate"]["passed"] is False
    assert get_active_model_weights("HIGH", db_path=db_path)["artifact_id"] == "rbi2-baseline-60-40"
    with connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM intelligence_model_artifacts WHERE status='challenger'"
        ).fetchone()[0] == 0


def test_rbi2_ignores_evidence_from_prior_learning_epoch(tmp_path):
    db_path = str(tmp_path / "old-epoch.db")
    _seed(db_path, learning_epoch="retired-probability-path")

    result = train_challenger(db_path=db_path)

    assert result["status"] == "learning_period_active"
    assert result["learning_gate"]["sample_count"] == 0
    assert result["learning_gate"]["learning_epoch"] == RBI_LEARNING_EPOCH


def test_rbi2_promotion_rechecks_learning_period(tmp_path):
    db_path = str(tmp_path / "promotion-gate.db")
    _seed(db_path)
    challenger = train_challenger(db_path=db_path)
    assert challenger["status"] == "challenger_ready"

    invalid = dict(challenger["validation"])
    invalid["observed_learning_days"] = 0.0
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE intelligence_model_artifacts SET validation_json=? WHERE artifact_id=?",
            (json.dumps(invalid, sort_keys=True), challenger["artifact_id"]),
        )
        conn.commit()

    with pytest.raises(ValueError, match="learning days"):
        promote_challenger(
            challenger["artifact_id"],
            promoted_by="test",
            reason="must remain blocked",
            db_path=db_path,
        )


def test_intelligence_schema_migrates_existing_predictions_table(tmp_path):
    db_path = str(tmp_path / "schema-migration.db")
    old_ddl = DDL.replace("    learning_epoch TEXT NOT NULL DEFAULT '',\n", "")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(old_ddl)

    init_intelligence_db(db_path)

    with connect(db_path) as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(intelligence_predictions)").fetchall()
        }
    assert "learning_epoch" in columns


def test_outcomes_are_revision_aware_and_cerebro_starts_empty(tmp_path):
    db_path = str(tmp_path / "lineage.db")
    first = record_outcome(ticker="KXONE", market_result="YES", market_status="finalized", official=True,
                           source="kalshi", source_payload={"v": 1}, db_path=db_path)
    second = record_outcome(ticker="KXONE", market_result="NO", market_status="finalized", official=True,
                            source="kalshi", source_payload={"v": 2}, db_path=db_path)
    assert first["revision"] == 1
    assert second["revision"] == 2
    with connect(db_path) as conn:
        rows = conn.execute("SELECT market_result, current FROM intelligence_outcomes ORDER BY revision").fetchall()
    assert [(row["market_result"], row["current"]) for row in rows] == [("YES", 0), ("NO", 1)]
    assert get_cerebro_status(db_path)["latest_insights"] == []


def test_cerebro_experiment_flow_is_operator_reachable(tmp_path):
    import config
    import runtime.approvals as approvals

    db_path = str(tmp_path / "cerebro.db")
    old_db = config.DB_PATH
    config.DB_PATH = db_path
    try:
        _seed(db_path)
        train_challenger(db_path=db_path)
        created = generate_insights(db_path=db_path)
        assert created["created_count"] >= 1

        insight_id = str(created["created"][0])
        experiment = create_experiment_from_insight(insight_id, approved_by="test", db_path=db_path)

        assert experiment["status"] == "APPROVED_FOR_SHADOW"
        assert experiment["change_spec"]["proposal_type"] == "promote_rbi_artifact"

        status = get_cerebro_status(db_path)
        assert status["experiment_count"] == 1
        assert status["approved_experiment_count"] == 1
        assert status["latest_experiments"][0]["experiment_id"] == experiment["experiment_id"]

        with connect(db_path) as conn:
            conn.execute(
                "UPDATE cerebro_insights SET status='CONFIRMED', scored_count=20, resolution_note='confirmed prospectively' WHERE insight_id=?",
                (insight_id,),
            )
            conn.commit()

        processed = process_experiments(db_path=db_path)
        pending = approvals.list_pending()
        refreshed = get_cerebro_status(db_path)

        assert processed["action_pending"] == 1
        assert pending
        assert pending[0]["action"] == "promote_rbi_artifact"
        assert refreshed["latest_experiments"][0]["status"] == "ACTION_PENDING"
    finally:
        config.DB_PATH = old_db
