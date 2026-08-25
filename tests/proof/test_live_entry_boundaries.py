from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest


def test_live_risk_book_requires_successful_fresh_broker_sync():
    from forecast.runner import _refresh_entry_risk_book

    positions = [{"local_symbol": "KXLOWTOKC-26AUG25-T76", "qty": 1}]
    broker = SimpleNamespace(
        sync_positions=lambda: True,
        has_fresh_position_snapshot=lambda: True,
        get_positions=lambda: positions,
    )
    assert _refresh_entry_risk_book(broker) == positions

    stale = SimpleNamespace(
        sync_positions=lambda: True,
        has_fresh_position_snapshot=lambda: False,
        position_snapshot_status=lambda: {"age_seconds": 61},
        get_positions=lambda: positions,
    )
    with pytest.raises(RuntimeError, match="position_snapshot_unavailable"):
        _refresh_entry_risk_book(stale)


def test_live_buying_power_refresh_accepts_zero_and_rejects_invalid_values():
    from forecast.runner import _refresh_entry_buying_power

    assert _refresh_entry_buying_power(
        SimpleNamespace(get_account_balance=lambda: 0.0)
    ) == 0.0
    with pytest.raises(RuntimeError, match="buying_power_invalid"):
        _refresh_entry_buying_power(
            SimpleNamespace(get_account_balance=lambda: float("nan"))
        )


def test_covariance_shrunk_size_uses_fee_inclusive_controller_cap():
    from config import estimate_kalshi_order_cost_usd
    from forecast.runner import _set_fee_inclusive_risk_size

    result = SimpleNamespace(position_contracts=15, position_fraction=1.0)
    _set_fee_inclusive_risk_size(result, 1, 0.77, 58.15)

    expected_cost = estimate_kalshi_order_cost_usd(1, 0.77, maker=False)
    assert expected_cost > 0.77
    assert result.position_contracts == 1
    assert result.position_fraction * 58.15 == pytest.approx(expected_cost)


def test_fee_inclusive_covariance_size_survives_the_real_controller_budget_boundary():
    from execution.kalshi_execution_controller import (
        KalshiExecutionController,
        TradeIntent,
    )
    from forecast.runner import _set_fee_inclusive_risk_size

    result = SimpleNamespace(
        position_contracts=1,
        position_fraction=0.0,
        side="YES",
        confidence=0.95,
        ask_yes=0.77,
        is_taker_override=True,
        weather_mode="TEMP",
    )
    _set_fee_inclusive_risk_size(result, 1, 0.77, 58.15)
    max_capital = result.position_fraction * 58.15
    broker = SimpleNamespace(
        get_quote=lambda _ticker: {
            "yes_bid": 0.75,
            "yes_ask": 0.77,
            "yes_ask_size": 1,
        }
    )
    plan = KalshiExecutionController(broker).plan_entry(
        TradeIntent(
            contract={"local_symbol": "KXHIGHCHI-26AUG26-T80", "right": "C"},
            result=result,
            bankroll=58.15,
            buying_power_usd=58.15,
            max_capital_usd=max_capital,
        )
    )

    assert max_capital > 0.77
    assert plan.status == "ready"
    assert plan.affordable_qty == 1
    assert plan.executable_qty == 1


def test_partial_swap_or_concurrent_book_change_cannot_open_a_new_slot():
    from forecast.runner import _swap_exit_freed_capacity

    freed, remaining = _swap_exit_freed_capacity(
        [{"local_symbol": "OLD", "qty": 1}],
        "OLD",
        2,
    )
    assert freed is False
    assert remaining == 1.0

    freed, remaining = _swap_exit_freed_capacity(
        [
            {"local_symbol": "OTHER1", "qty": 1},
            {"local_symbol": "OTHER2", "qty": 1},
        ],
        "OLD",
        2,
    )
    assert freed is False
    assert remaining == 0.0

    freed, remaining = _swap_exit_freed_capacity(
        [{"local_symbol": "OTHER1", "qty": 1}],
        "OLD",
        2,
    )
    assert freed is True
    assert remaining == 0.0


def test_entry_stall_clock_starts_when_release_becomes_continuously_allowed(
    tmp_path,
    monkeypatch,
):
    import config
    import runtime.sentinel as sentinel

    db_path = tmp_path / "trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trades (ts TEXT, action TEXT, broker TEXT)")
        conn.execute(
            "INSERT INTO trades VALUES (?, 'BUY', 'kalshi')",
            ("2026-08-01T00:00:00+00:00",),
        )
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True},
    )

    now_ts = time.time()
    _key, bad, _message = sentinel._check_entry_stall(
        stall_hours=3.0,
        allowed_since_ts=now_ts - 3600,
    )
    assert bad is False

    _key, bad, message = sentinel._check_entry_stall(
        stall_hours=3.0,
        allowed_since_ts=now_ts - 4 * 3600,
    )
    assert bad is True
    assert "while entries are allowed" in message


def test_sentinel_sends_recovery_and_prunes_retired_state(tmp_path, monkeypatch):
    import runtime.sentinel as sentinel

    state_path = tmp_path / "sentinel.json"
    state_path.write_text(
        json.dumps(
            {
                "rbi_evidence": {"bad": True, "last_sent_ts": 1},
                "maker_fill_rate": {"bad": True, "last_sent_ts": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sentinel, "_state_path", lambda: state_path)
    monkeypatch.setattr(
        sentinel,
        "_CHECKS",
        [lambda: ("rbi_evidence", False, "")],
    )
    sent = []
    monkeypatch.setattr(sentinel, "_send", lambda message: sent.append(message) or True)

    sentinel.check_and_alert()

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "maker_fill_rate" not in saved
    assert saved["rbi_evidence"]["bad"] is False
    assert sent == ["Recovered: rbi evidence is healthy again."]
