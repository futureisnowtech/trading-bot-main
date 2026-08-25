from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _candidate(reason: str, *, decision: str) -> dict:
    return {
        "contract": {
            "local_symbol": "KXHIGHTNYC-26AUG14-T85",
            "contract_name": "Will the maximum temperature in NYC be above 85 on Aug 14, 2026?",
            "strike": 85.0,
            "last_trade_at": "2026-08-14T21:00:00+00:00",
        },
        "snapshot": SimpleNamespace(
            ticker="KXHIGHTNYC-26AUG14-T85",
            contract_name="Will the maximum temperature in NYC be above 85 on Aug 14, 2026?",
            yes_quote={"bid": 0.63, "ask": 0.72, "ts": "2026-08-14T12:00:00+00:00"},
            no_quote={"bid": 0.24, "ask": 0.30, "ts": "2026-08-14T12:00:00+00:00"},
        ),
        "result": SimpleNamespace(
            side="YES",
            q_hat=0.74,
            ev=0.11,
            ev_yes=0.11,
            ev_no=-1.0,
            confidence=0.74,
            hours_to_resolution=6.0,
            position_contracts=3,
            top_factors=["thermal_support"],
            weather_mode="HIGH",
            ask_yes=0.72,
            ask_no=0.30,
            veto_reason=reason,
        ),
        "decision": decision,
    }


def test_record_prediction_persists_side_specific_audit_payload(tmp_path):
    from intelligence.evidence import record_prediction
    from intelligence.schema import connect, init_intelligence_db

    db_path = str(tmp_path / "evidence.db")
    init_intelligence_db(db_path)

    record_prediction(
        scan_id="scan-1",
        candidate=_candidate(
            "price_bracket_veto (ask_yes=0.72 outside $0.20-$0.70 value zone)",
            decision="econ_veto",
        ),
        decision="econ_veto",
        reason="price_bracket_veto (ask_yes=0.72 outside $0.20-$0.70 value zone)",
        db_path=db_path,
    )

    with connect(db_path) as conn:
        row = conn.execute("SELECT features_json FROM intelligence_predictions").fetchone()

    payload = json.loads(row["features_json"])
    audit = payload["audit"]
    assert audit["reason_code"] == "price_bracket_veto"
    assert audit["chosen_side"] == "YES"
    assert audit["price_bracket_min"] == 0.20
    assert audit["price_bracket_max"] == 0.70
    assert audit["gate_flags"]["price_bracket_high"] is True
    assert audit["gate_flags"]["expensive_yes_headroom_veto"] is True
    assert audit["yes_net_edge"] is not None


def test_record_prediction_uses_decision_trace_without_refetch_or_repricing(
    tmp_path, monkeypatch
):
    from data import kalshi_weather_monitor
    from forecast import pricing_engine
    from intelligence.evidence import record_prediction
    from intelligence.schema import connect, init_intelligence_db

    db_path = str(tmp_path / "trace.db")
    init_intelligence_db(db_path)
    candidate = _candidate("submitted", decision="entered")
    candidate["result"].pricing_trace = {
        "q_gfs": 0.81,
        "q_ecmwf": 0.57,
        "q_aigfs": 0.68,
        "q_hrrr": 0.76,
        "q_hat": 0.73,
        "gfs_weight": 0.31,
        "ecmwf_weight": 0.21,
        "hrrr_weight": 0.48,
        "lambda_scaler": 1.07,
        "model_path": "decision-time-production-path",
        "physics_method": "thermal_energy_balance_v1",
        "physics_validation_status": "active",
        "provider_mode": "deterministic_multi_model",
        "provider_at": "2026-08-14T11:55:00+00:00",
    }

    def _forbidden(*args, **kwargs):
        raise AssertionError("decision evidence must not refetch or reprice")

    monkeypatch.setattr(kalshi_weather_monitor, "get_contract_weather_data", _forbidden)
    monkeypatch.setattr(pricing_engine, "calculate_pricing", _forbidden)

    prediction_id = record_prediction(
        scan_id="trace-scan",
        candidate=candidate,
        decision="entered",
        reason="submitted",
        db_path=db_path,
    )

    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT q_gfs, q_ecmwf, q_hrrr, q_champion, q_baseline,
                      provider_at, features_json
               FROM intelligence_predictions WHERE id=?""",
            (prediction_id,),
        ).fetchone()
    provider = json.loads(row["features_json"])["provider"]

    assert row["q_gfs"] == pytest.approx(0.81)
    assert row["q_ecmwf"] == pytest.approx(0.57)
    assert row["q_hrrr"] == pytest.approx(0.76)
    # The governed decision probability may include downstream guardrails, so
    # it is persisted from StrategyResult instead of replacing it with q_hat.
    assert row["q_champion"] == pytest.approx(0.74)
    assert row["q_baseline"] is not None
    assert row["provider_at"] == "2026-08-14T11:55:00+00:00"
    assert provider["model_path"] == "decision-time-production-path"
    assert provider["model_probabilities"]["aigfs"] == pytest.approx(0.68)


def test_no_side_evidence_keeps_q_hat_on_yes_basis(tmp_path):
    from intelligence.evidence import record_prediction
    from intelligence.schema import connect, init_intelligence_db

    candidate = _candidate("submitted", decision="entered")
    candidate["result"].side = "NO"
    candidate["result"].q_hat = 0.18
    candidate["result"].confidence = 0.82
    candidate["result"].ask_yes = 0.84
    candidate["result"].ask_no = 0.18
    candidate["result"].pricing_trace = {
        "q_gfs": 0.15,
        "q_ecmwf": 0.23,
        "q_decision_guarded": 0.18,
    }

    db_path = str(tmp_path / "no-side-basis.db")
    init_intelligence_db(db_path)
    prediction_id = record_prediction(
        scan_id="no-side-basis",
        candidate=candidate,
        decision="entered",
        reason="submitted",
        db_path=db_path,
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT chosen_side, q_champion, features_json "
            "FROM intelligence_predictions WHERE id=?",
            (prediction_id,),
        ).fetchone()
    audit = json.loads(row["features_json"])["audit"]

    assert row["chosen_side"] == "NO"
    assert row["q_champion"] == pytest.approx(0.18)
    assert audit["q_hat_yes"] == pytest.approx(0.18)
    assert audit["chosen_side_probability"] == pytest.approx(0.82)
    assert audit["chosen_side_price"] == pytest.approx(0.18)


def test_post_pricing_veto_preserves_probability_trace_through_evidence(
    tmp_path, monkeypatch
):
    import forecast.pricing_engine as pe
    import forecast.strategy_engine as se
    from intelligence.evidence import record_prediction
    from intelligence.schema import connect, init_intelligence_db

    provider_at = datetime.now(timezone.utc).timestamp()
    weather = {
        "timestamp": provider_at,
        "provider_mode": "deterministic_multi_model",
        "members_high": [90.0],
        "ecmwf": {"members_high": [89.0]},
        "aigefs": {"members_high": [88.0]},
        "sigma_high": 1.0,
        "intraday": {},
    }
    pricing = {
        "q_hat": 0.80,
        "q_hat_raw": 0.81,
        "q_gfs": 0.78,
        "q_ecmwf": 0.76,
        "q_aigfs": 0.77,
        "q_hrrr": None,
        "lambda_scaler": 1.0,
        "gfs_weight": 0.60,
        "ecmwf_weight": 0.40,
        "hrrr_weight": 0.0,
        "physics_gfs": {"adjustment_f": 0.2},
        "physics_ecmwf": {"adjustment_f": 0.1},
        "consensus_projection": 90.0,
        "model_path": "decision-time-production-path",
        "physics_method": "bounded_heuristic_v1",
        "physics_validation_status": "active",
    }
    monkeypatch.setattr(se, "get_weather_data", lambda _ticker: weather)
    monkeypatch.setattr(
        se, "get_contract_weather_data", lambda *_args, **_kwargs: weather
    )
    monkeypatch.setattr(pe, "calculate_pricing", lambda *_args, **_kwargs: pricing)
    monkeypatch.setattr(se, "KALSHI_MIN_ENTRY_PRICE", 0.34)

    expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    contract = {
        "local_symbol": "KXHIGHLAX-26AUG26-T85",
        "contract_name": "Will the high temperature in Los Angeles be above 85?",
        "strike": 85.0,
        "last_trade_at": expiry.isoformat(),
    }
    quote_at = datetime.now(timezone.utc).isoformat()
    yes_quote = {"bid": 0.08, "ask": 0.10, "spread": 0.02, "ts": quote_at}
    no_quote = {"bid": 0.88, "ask": 0.90, "spread": 0.02, "ts": quote_at}

    result = se.evaluate_contract(
        contract=contract,
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=yes_quote,
        no_quote=no_quote,
        bankroll=50.0,
    )

    assert result is not None
    assert result.econ_approved is False
    assert result.position_contracts == 0
    assert result.veto_reason.startswith("penny_veto")
    assert result.q_hat == pytest.approx(0.80)
    assert result.pricing_trace["q_decision_guarded"] == pytest.approx(0.80)
    assert result.pricing_trace["q_gfs"] == pytest.approx(0.78)
    assert result.pricing_trace["q_ecmwf"] == pytest.approx(0.76)
    assert result.pricing_trace["q_aigfs"] == pytest.approx(0.77)

    db_path = str(tmp_path / "veto-trace.db")
    init_intelligence_db(db_path)
    candidate = {
        "contract": contract,
        "snapshot": SimpleNamespace(
            ticker=contract["local_symbol"],
            contract_name=contract["contract_name"],
            yes_quote=yes_quote,
            no_quote=no_quote,
        ),
        "result": result,
    }
    prediction_id = record_prediction(
        scan_id="post-pricing-veto",
        candidate=candidate,
        decision="econ_veto",
        reason=result.veto_reason,
        db_path=db_path,
    )

    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT q_gfs, q_ecmwf, q_champion, features_json
                 FROM intelligence_predictions WHERE id=?""",
            (prediction_id,),
        ).fetchone()
    features = json.loads(row["features_json"])

    assert row["q_gfs"] == pytest.approx(0.78)
    assert row["q_ecmwf"] == pytest.approx(0.76)
    assert row["q_champion"] == pytest.approx(0.80)
    assert features["provider"]["provider_mode"] == "deterministic_multi_model"
    assert features["provider"]["model_path"] == "decision-time-production-path"
    assert features["provider"]["model_probabilities"]["aigfs"] == pytest.approx(0.77)


def test_pre_pricing_weather_veto_is_unscored(monkeypatch):
    import forecast.strategy_engine as se

    monkeypatch.setattr(se, "get_weather_data", lambda _ticker: None)
    expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    result = se.evaluate_contract(
        contract={
            "local_symbol": "KXHIGHLAX-26AUG26-T85",
            "contract_name": "Will the high temperature in Los Angeles be above 85?",
            "strike": 85.0,
            "last_trade_at": expiry.isoformat(),
        },
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote={"ask": 0.50},
        no_quote={"ask": 0.50},
        bankroll=50.0,
    )

    assert result is not None
    assert result.veto_reason == "missing_weather_data"
    assert result.q_hat == 0.0
    assert result.pricing_trace == {}


def test_yes_path_audit_summary_aggregates_blockers_and_entries(tmp_path):
    import runtime.operator_truth as ot
    from intelligence.evidence import record_prediction
    from intelligence.schema import init_intelligence_db

    db_path = str(tmp_path / "truth.db")
    init_intelligence_db(db_path)

    record_prediction(
        scan_id="scan-1",
        candidate=_candidate(
            "price_bracket_veto (ask_yes=0.72 outside $0.20-$0.70 value zone)",
            decision="econ_veto",
        ),
        decision="econ_veto",
        reason="price_bracket_veto (ask_yes=0.72 outside $0.20-$0.70 value zone)",
        db_path=db_path,
    )

    entered = _candidate("submitted", decision="entered")
    entered["result"].ask_yes = 0.66
    entered["snapshot"].yes_quote["ask"] = 0.66
    entered["snapshot"].yes_quote["bid"] = 0.60
    record_prediction(
        scan_id="scan-2",
        candidate=entered,
        decision="entered",
        reason="submitted",
        db_path=db_path,
    )

    summary = ot.get_yes_path_audit_summary(db_path=db_path, start_date="2026-07-23")

    assert summary["chosen_yes_count"] == 2
    assert summary["entered_yes_count"] == 1
    assert summary["blocked_yes_count"] == 1
    assert summary["top_blockers"][0]["reason"] == "price_bracket_veto"
    assert summary["top_blockers"][0]["count"] == 1
    assert summary["gate_flag_counts"]["price_bracket_high"] == 1
