from __future__ import annotations

import json
from types import SimpleNamespace


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
