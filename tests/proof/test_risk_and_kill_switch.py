from __future__ import annotations

import sqlite3

from tests.proof.support import insert_trade


def test_risk_engine_updates_var_from_trade_history(proof_runtime):
    import risk_engine

    for idx, pnl in enumerate([-8.0, -6.0, -4.0, -2.0, -1.0, 2.0, 3.0, 4.0, 5.0, 7.0]):
        insert_trade(
            proof_runtime.db_path,
            ts=f"2026-04-10 0{idx}:00:00",
            pnl_usd=pnl,
            fee_usd=0.25,
            won=1 if pnl > 0 else 0,
        )

    risk_engine.reset_daily(5_000.0)
    risk_engine.update_var_from_db(paper=True)
    report = risk_engine.get_risk_report()

    assert report["var_95"] > 0
    assert report["cvar_95"] >= report["var_95"]


def test_risk_engine_blocks_new_positions_when_margin_is_too_high():
    import risk_engine

    risk_engine.reset_daily(5_000.0)
    risk_engine.update_balances(current_balance=5_000.0, deployed_usd=2_000.0, margin_usd=3_500.0)

    allowed, reason = risk_engine.can_open_new_position()

    assert allowed is False
    assert "Margin utilization" in reason


def test_kill_switch_triggers_and_resolves_with_db_audit_trail(proof_runtime):
    import kill_switch

    for _ in range(5):
        kill_switch.record_api_error("proof failure")

    assert kill_switch.is_halted() is True
    assert "API errors" in kill_switch.get_halt_reason()

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT reason, resolved FROM kill_switch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[1] == 0

    kill_switch.resume("proof reset")
    status = kill_switch.get_status()

    assert status["halted"] is False
    with sqlite3.connect(proof_runtime.db_path) as conn:
        resolved = conn.execute(
            "SELECT resolved, resolved_reason FROM kill_switch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert resolved == (1, "proof reset")
