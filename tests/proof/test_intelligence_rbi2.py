import json
from datetime import datetime, timedelta, timezone

from intelligence.cerebro import create_experiment_from_insight, generate_insights, get_cerebro_status
from intelligence.outcomes import record_outcome
from intelligence.rbi2 import get_active_model_weights, promote_challenger, train_challenger
from intelligence.schema import connect, init_intelligence_db


def _seed(db_path: str, count: int = 32) -> None:
    init_intelligence_db(db_path)
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with connect(db_path) as conn:
        for idx in range(count):
            ticker = f"KXTEST-{idx:03d}"
            settled = (start + timedelta(hours=idx)).isoformat()
            outcome = 1 if idx % 2 == 0 else 0
            q_gfs = 0.85 if outcome else 0.15
            q_ecmwf = 0.55 if outcome else 0.45
            conn.execute(
                """INSERT INTO intelligence_predictions
                   (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                    weather_mode, decision, q_gfs, q_ecmwf, features_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'HIGH', 'entered', ?, ?, '{}', ?)""",
                (f"scan:{ticker}", "scan", settled, ticker, ticker, q_gfs, q_ecmwf, settled),
            )
        conn.commit()
    for idx in range(count):
        ticker = f"KXTEST-{idx:03d}"
        settled = (start + timedelta(hours=idx)).isoformat()
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
    assert get_active_model_weights("HIGH", db_path=db_path) == before
    promote_challenger(challenger["artifact_id"], promoted_by="test", reason="validated", db_path=db_path)
    after = get_active_model_weights("HIGH", db_path=db_path)
    assert after["artifact_id"] == challenger["artifact_id"]
    assert after["gfs"] > before["gfs"]


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
    db_path = str(tmp_path / "cerebro.db")
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
