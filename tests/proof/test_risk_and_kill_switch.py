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
            paper=0,
        )

    risk_engine.reset_daily(5_000.0)
    risk_engine.update_var_from_db()
    report = risk_engine.get_risk_report()

    assert report["var_95"] > 0
    assert report["cvar_95"] >= report["var_95"]


def test_risk_engine_blocks_new_positions_when_margin_is_too_high():
    import risk_engine

    risk_engine.reset_daily(5_000.0)
    risk_engine.update_balances(
        current_balance=5_000.0, deployed_usd=2_000.0, margin_usd=3_500.0
    )

    allowed, reason = risk_engine.can_open_new_position()

    assert allowed is False
    assert "Margin utilization" in reason


def test_risk_engine_does_not_call_kill_switch_directly():
    """
    After the live-mode false-trigger fix, risk_engine.update_balances() must NOT
    call kill_switch.check_balance().  The v10_runner.kill_switch_monitor() handles
    that with the correct paper/live flag.

    Regression guard: calling update_balances with a live-scale balance (1966)
    must not trigger the kill switch even though 1966 < 0.75*10000 = 7500.
    """
    import kill_switch
    import risk_engine

    # Ensure kill switch is clear
    with kill_switch._lock:
        kill_switch._halted = False
        kill_switch._halt_reason = ""

    # Simulate a live balance that is smaller than the old $7,500 hardcode
    risk_engine.update_balances(
        current_balance=1_966.0, deployed_usd=0.0, margin_usd=0.0
    )

    assert kill_switch.is_halted() is False, (
        "risk_engine.update_balances() must not trigger kill_switch; "
        "kill_switch_monitor() in v10_runner handles that with the correct paper flag."
    )


# ── kill_switch live-mode policy tests ───────────────────────────────────────


def test_live_kill_switch_uses_50_pct_of_live_baseline():
    """
    THE KEY INVARIANT: paper=False mode uses 50% of live_baseline, not $7,500.
    With live_baseline=1966, threshold=983.  Balance 1966 > 983 → no trigger.
    """
    import kill_switch

    kill_switch._live_baseline = 0.0  # reset so auto-set fires
    kill_switch.check_balance(1_966.0, initial_balance=5_000.0)

    # Baseline should be auto-set to first valid balance
    assert kill_switch._live_baseline == 1_966.0
    # Should NOT have triggered (1966 > 983)
    assert kill_switch.is_halted() is False


def test_live_baseline_1966_gives_threshold_983():
    """Verify the exact threshold arithmetic for the live account."""
    import kill_switch

    kill_switch.set_live_baseline(1_966.0)
    status = kill_switch.get_status()

    assert abs(status["live_baseline"] - 1_966.0) < 0.01
    assert abs(status["live_threshold"] - 983.0) < 0.01


def test_live_kill_switch_triggers_below_50pct_of_baseline(proof_runtime, monkeypatch):
    """Balance below 50% of live baseline must trigger the kill switch.
    v18.19.2: equity tripwire is opt-in; enable via env-equivalent module attr."""
    import kill_switch

    monkeypatch.setattr(kill_switch, "_EQUITY_TRIPWIRE_ENABLED", True)
    kill_switch.set_live_baseline(1_966.0)
    # Balance at 40% of baseline → below threshold
    kill_switch.check_balance(786.0, initial_balance=5_000.0)

    assert kill_switch.is_halted() is True
    reason = kill_switch.get_halt_reason()
    assert (
        "983" in reason or "50%" in reason.lower() or "live baseline" in reason.lower()
    ), f"Halt reason must report baseline and threshold. Got: {reason}"


def test_live_halt_reason_reports_baseline_and_threshold(proof_runtime, monkeypatch):
    """Halt reason string must include the computed threshold and baseline.
    v18.19.2: equity tripwire is opt-in; enable via env-equivalent module attr."""
    import kill_switch

    monkeypatch.setattr(kill_switch, "_EQUITY_TRIPWIRE_ENABLED", True)
    kill_switch.set_live_baseline(2_000.0)
    kill_switch.check_balance(900.0)

    reason = kill_switch.get_halt_reason()
    # Must mention the live baseline concept
    assert "live baseline" in reason.lower() or "50%" in reason.lower(), (
        f"Halt reason must mention live baseline policy. Got: {reason}"
    )
    # Must mention the balance value
    assert "900" in reason, f"Halt reason must include current balance. Got: {reason}"


def test_paper_kill_switch_still_uses_50_pct(proof_runtime, monkeypatch):
    """Regression guard: v18.18 uses 50% of initial balance/baseline.
    v18.19.2: equity tripwire is opt-in; enable via env-equivalent module attr."""
    import kill_switch

    monkeypatch.setattr(kill_switch, "_EQUITY_TRIPWIRE_ENABLED", True)
    # 1. Establish baseline at 5000
    kill_switch.check_balance(5000.0)

    # 2. Drop balance to 2000 (40% of baseline) → should trigger
    kill_switch.check_balance(2000.0)

    assert kill_switch.is_halted() is True
    reason = kill_switch.get_halt_reason()
    assert "2000" in reason or "2500" in reason, (
        f"Halt reason should mention balance/threshold. Got: {reason}"
    )


def test_live_position_manager_does_not_use_account_size_kill_floor(monkeypatch):
    """
    position_manager.check_exits() must honor the live kill-switch baseline
    instead of force-exiting at 75% of config ACCOUNT_SIZE.
    """
    import kill_switch
    import position_manager as pm

    kill_switch.set_live_baseline(1_966.0)  # live threshold = 983

    pos = {
        "paper": False,
        "entry_price": 2_260.0,
        "direction": "SHORT",
        "atr_at_entry": 20.0,
        "stop_price": 2_500.0,
        "peak_price": 2_200.0,
        "entry_composite_score": 65.0,
        "regime": "UNKNOWN",
    }

    decision = pm.check_exits(
        position=pos,
        current_price=2_255.0,
        current_features=None,
        account_balance=1_926.0,
        total_deployed_usd=0.0,
        margin_utilization_pct=0.0,
        drawdown_pct=0.0,
        kill_switch_triggered=False,
    )

    assert decision.should_exit is False, (
        "Live positions must not risk-force exit at 75% of ACCOUNT_SIZE once "
        "kill_switch live_baseline policy is in effect."
    )


def test_live_kill_switch_db_log_uses_correct_schema(proof_runtime, monkeypatch):
    """
    After the schema-mismatch fix, triggering the kill switch must write a row
    to kill_switch_log using the actual column names (balance, not balance_at_trigger).
    v18.19.2: equity tripwire is opt-in; enable via env-equivalent module attr.
    """
    import kill_switch

    monkeypatch.setattr(kill_switch, "_EQUITY_TRIPWIRE_ENABLED", True)
    kill_switch.set_live_baseline(1_000.0)
    kill_switch.check_balance(400.0)

    assert kill_switch.is_halted() is True

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT reason, balance, resumed_at, trigger_type FROM kill_switch_log "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "kill_switch_log row must be written on trigger"
    assert "400" in row[0] or "500" in row[0], "Reason must include balance"
    assert row[2] is None, "resumed_at must be NULL (not yet resumed)"
    assert row[3] == "trigger"


def test_kill_switch_resume_clears_db_log(proof_runtime):
    """Resume must set resumed_at in kill_switch_log using the correct schema."""
    import kill_switch

    # Trigger first
    for _ in range(5):
        kill_switch.record_api_error("proof failure")
    assert kill_switch.is_halted() is True

    kill_switch.resume("proof reset")
    assert kill_switch.is_halted() is False

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT resumed_at FROM kill_switch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    # resumed_at should be set (not NULL) after resume
    assert row is not None
    assert row[0] is not None, "resumed_at must be set after resume()"
