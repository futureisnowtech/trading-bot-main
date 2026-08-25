from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_build_market_snapshots_collapses_yes_no_rows_into_one_market():
    from forecast.market_snapshot import build_market_snapshots

    active_contracts = [
        {
            "id": 1,
            "market_id": 7,
            "local_symbol": "KXLOWNY-26JUN06-T70",
            "contract_name": "NY Low",
            "right": "C",
            "strike": 70.0,
            "last_trade_at": "20260606",
            "resolution_at": "2026-06-06T04:59:00Z",
        },
        {
            "id": 2,
            "market_id": 7,
            "local_symbol": "KXLOWNY-26JUN06-T70",
            "contract_name": "NY Low",
            "right": "P",
            "strike": 70.0,
            "last_trade_at": "20260606",
            "resolution_at": "2026-06-06T04:59:00Z",
        },
    ]

    snapshots = build_market_snapshots(
        active_contracts,
        get_bars_fn=lambda *_args, **_kwargs: [{"c": 0.42}],
        get_quotes_fn=lambda *_args, **_kwargs: {
            "yes_quote": {"ask": 0.41, "mid": 0.40},
            "no_quote": {"ask": 0.59, "mid": 0.60},
        },
    )

    assert len(snapshots) == 1
    assert snapshots[0].ticker == "KXLOWNY-26JUN06-T70"
    assert snapshots[0].yes_contract["right"] == "C"
    assert snapshots[0].no_contract["right"] == "P"


def test_execution_controller_caps_qty_to_visible_depth():
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def get_quote(self, _ticker):
                return {
                    "yes_bid": 0.58,
                    "yes_ask": 0.61,
                "yes_ask_size": 4.9,
            }

    result = SimpleNamespace(
        position_contracts=12,
        side="YES",
        is_taker_override=False,
        strategy_family="weather_ensemble",
        ev=0.12,
        confidence=0.90,
        ask_yes=0.61,
    )
    intent = TradeIntent(
        contract={"local_symbol": "KXLOWTPHX-26JUN05-T80", "right": "C"},
        result=result,
        bankroll=200.0,
        buying_power_usd=200.0,
    )

    plan = KalshiExecutionController(BrokerStub()).plan_entry(intent)

    assert plan.status == "ready"
    assert plan.requested_qty == 12
    assert plan.visible_qty == 4
    assert plan.executable_qty == 4
    assert plan.depth_capped is True


def test_execution_controller_does_not_chase_after_depth_loss():
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.orders: list[tuple[int, float, str]] = []

        def get_quote(self, _ticker):
            return {"yes_bid": 0.59, "yes_ask": 0.62, "yes_ask_size": 4}

        def place_buy_order(self, contract_dict, qty, limit_price, **kwargs):
            self.orders.append((qty, limit_price, kwargs.get("reason", "")))
            return {
                "order_id": "ERR",
                "status": "fill_or_kill_insufficient_resting_volume",
            }

    result = SimpleNamespace(
        position_contracts=4,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_ensemble",
        ev=0.18,
        confidence=0.90,
        ask_yes=0.62,
    )
    intent = TradeIntent(
        contract={
            "local_symbol": "KXHIGHLAX-26JUN05-B69.5",
            "right": "C",
            "strike": 69.5,
            "last_trade_at": "20260605",
        },
        result=result,
        bankroll=200.0,
        buying_power_usd=200.0,
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    plan = controller.plan_entry(intent)
    execution = controller.execute_plan(plan, forecast_yes_prob=0.74)

    assert plan.executable_qty == 4
    assert execution["status"] == "fill_or_kill_insufficient_resting_volume"
    assert execution["execution_reason"] == "depth_slipped_after_submission"
    assert len(broker.orders) == 1


def test_execution_controller_passes_weather_observation_fields_to_broker():
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.kwargs = None

        def get_quote(self, _ticker):
            return {"yes_bid": 0.59, "yes_ask": 0.62, "yes_ask_size": 4}

        def place_buy_order(self, contract_dict, qty, limit_price, **kwargs):
            self.kwargs = kwargs
            return {
                "order_id": "ORD-1",
                "status": "executed",
                "price": limit_price,
                "qty": qty,
            }

    result = SimpleNamespace(
        position_contracts=4,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_ensemble",
        ev=0.18,
        confidence=0.90,
        ask_yes=0.62,
    )
    intent = TradeIntent(
        contract={
            "local_symbol": "KXHIGHLAX-26JUN05-B69.5",
            "right": "C",
            "strike": 69.5,
            "last_trade_at": "20260605",
        },
        result=result,
        bankroll=200.0,
        buying_power_usd=200.0,
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    plan = controller.plan_entry(intent)
    controller.execute_plan(
        plan,
        forecast_yes_prob=0.74,
        model_prob_gfs=0.71,
        model_prob_ecmwf=0.79,
        weather_mode="HIGH",
        forecast_hours_to_resolution=21.5,
    )

    assert broker.kwargs is not None
    assert broker.kwargs["forecast_yes_prob"] == 0.74
    assert broker.kwargs["model_prob_gfs"] == 0.71
    assert broker.kwargs["model_prob_ecmwf"] == 0.79
    assert broker.kwargs["weather_mode"] == "HIGH"
    assert broker.kwargs["forecast_hours_to_resolution"] == 21.5


def test_held_mark_from_quote_uses_no_side_prices():
    from forecast.runner import _held_mark_from_quote

    position = {"side": "NO"}
    quote = {
        "yes_bid": 0.18,
        "yes_ask": 0.22,
        "no_bid": 0.78,
        "no_ask": 0.82,
    }

    assert _held_mark_from_quote(position, quote) == 0.8


def test_execution_controller_reprices_at_final_post_boundary():
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.quote_calls = 0
            self.orders = []

        def get_quote(self, _ticker):
            self.quote_calls += 1
            ask = 0.60 if self.quote_calls == 1 else 0.64
            return {"yes_bid": ask - 0.02, "yes_ask": ask, "yes_ask_size": 10}

        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    result = SimpleNamespace(
        position_contracts=3,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_physics",
        ev=0.20,
        confidence=0.90,
        ask_yes=0.60,
        weather_mode="TEMP",
    )
    intent = TradeIntent(
        contract={"local_symbol": "KXTEMPNYCH-1-T70", "right": "C"},
        result=result,
        bankroll=200.0,
        buying_power_usd=200.0,
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)

    execution = controller.execute_plan(
        controller.plan_entry(intent), forecast_yes_prob=0.90
    )

    assert execution["status"] == "blocked"
    assert "live_slippage_veto" in execution["execution_reason"]
    assert broker.orders == []


def test_execution_controller_rechecks_release_gate_after_final_replan(monkeypatch):
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.orders = []

        def position_snapshot_status(self):
            return {"fresh": True}

        def has_fresh_position_snapshot(self):
            return True

        def sync_positions(self):
            return True

        def get_positions(self):
            return []

        def get_account_balance(self):
            return 58.15

        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}

        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    releases = iter(
        [
            {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
            {"entries_allowed": False, "current_release_verdict": "BLOCKED"},
        ]
    )
    monkeypatch.setattr(
        "forecast.firewall.check_entry_firewall",
        lambda ticker, bankroll: (True, ""),
    )
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: next(releases),
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )

    result = SimpleNamespace(
        position_contracts=2,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_physics",
        ev=0.20,
        confidence=0.80,
        ask_yes=0.50,
    )
    intent = TradeIntent(
        contract={"local_symbol": "KXHIGHCHI-26AUG26-T80", "right": "C"},
        result=result,
        bankroll=58.15,
        buying_power_usd=58.15,
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)

    execution = controller.execute_plan(
        controller.plan_entry(intent),
        forecast_yes_prob=0.80,
    )

    assert execution["status"] == "release_gate_blocked"
    assert execution["execution_reason"] == "BLOCKED"
    assert broker.orders == []


def test_execution_controller_refreshes_stale_planning_snapshot_before_post(monkeypatch):
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.fresh = False
            self.orders = []
            self.positions = [{"local_symbol": "OPEN", "side": "YES", "qty": 1, "market_exposure_usd": 0.5}]

        def position_snapshot_status(self):
            return {"fresh": self.fresh}

        def has_fresh_position_snapshot(self):
            return self.fresh

        def sync_positions(self):
            self.fresh = True
            return True

        def get_positions(self):
            return self.positions

        def get_account_balance(self):
            return 58.15

        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}

        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    monkeypatch.setattr("forecast.firewall.check_entry_firewall", lambda *_: (True, ""))
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    result = SimpleNamespace(
        position_contracts=1, side="YES", is_taker_override=True,
        strategy_family="weather_physics", ev=0.2, confidence=0.8, ask_yes=0.5,
    )
    broker = BrokerStub()
    fingerprint = KalshiExecutionController.position_book_fingerprint(broker.positions)
    intent = TradeIntent(
        contract={"local_symbol": "CANDIDATE", "right": "C"}, result=result,
        bankroll=58.15, buying_power_usd=58.15,
        risk_book_fingerprint=fingerprint,
    )
    execution = KalshiExecutionController(broker).execute_plan(
        KalshiExecutionController(broker).plan_entry(intent),
        forecast_yes_prob=0.8,
    )

    assert execution["status"] == "executed"
    assert len(broker.orders) == 1


def test_execution_controller_vetoes_changed_book_at_final_refresh(monkeypatch):
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.orders = []

        def position_snapshot_status(self):
            return {"fresh": True}

        def has_fresh_position_snapshot(self):
            return True

        def sync_positions(self):
            return True

        def get_positions(self):
            return [{"local_symbol": "OPEN", "side": "YES", "qty": 2, "market_exposure_usd": 1.0}]

        def get_account_balance(self):
            return 58.15

        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}

        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    monkeypatch.setattr("forecast.firewall.check_entry_firewall", lambda *_: (True, ""))
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    result = SimpleNamespace(
        position_contracts=1, side="YES", is_taker_override=True,
        strategy_family="weather_physics", ev=0.2, confidence=0.8, ask_yes=0.5,
    )
    admitted = [{"local_symbol": "OPEN", "side": "YES", "qty": 1, "market_exposure_usd": 0.5}]
    intent = TradeIntent(
        contract={"local_symbol": "CANDIDATE", "right": "C"}, result=result,
        bankroll=58.15, buying_power_usd=58.15,
        risk_book_fingerprint=KalshiExecutionController.position_book_fingerprint(admitted),
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    execution = controller.execute_plan(controller.plan_entry(intent), forecast_yes_prob=0.8)

    assert execution["status"] == "position_book_changed"
    assert broker.orders == []


def test_execution_controller_vetoes_final_cash_drop(monkeypatch):
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        orders = []

        def position_snapshot_status(self): return {"fresh": True}
        def has_fresh_position_snapshot(self): return True
        def sync_positions(self): return True
        def get_positions(self): return []
        def get_account_balance(self): return 57.0
        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}
        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    monkeypatch.setattr("forecast.firewall.check_entry_firewall", lambda *_: (True, ""))
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    result = SimpleNamespace(
        position_contracts=1, side="YES", is_taker_override=True,
        strategy_family="weather_physics", ev=0.2, confidence=0.8, ask_yes=0.5,
    )
    intent = TradeIntent(
        contract={"local_symbol": "CANDIDATE", "right": "C"}, result=result,
        bankroll=58.15, buying_power_usd=58.15, risk_book_fingerprint=(),
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    execution = controller.execute_plan(controller.plan_entry(intent), forecast_yes_prob=0.8)

    assert execution["status"] == "buying_power_changed"
    assert broker.orders == []


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("sync_false", "position_snapshot_unavailable"),
        ("sync_exception", "position_snapshot_unavailable"),
        ("not_fresh", "position_snapshot_unavailable"),
        ("expires_after_sync", "position_snapshot_unavailable"),
        ("balance_exception", "position_snapshot_unavailable"),
        ("balance_nan", "position_snapshot_unavailable"),
        ("balance_negative", "position_snapshot_unavailable"),
        ("fee_insufficient", "insufficient_buying_power"),
    ],
)
def test_execution_controller_final_refresh_failures_never_order(
    monkeypatch, failure, expected_status
):
    import config
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    if failure == "fee_insufficient":
        monkeypatch.setattr(config, "KALSHI_KELLY_CAP", 10.0)

    class BrokerStub:
        def __init__(self):
            self.orders = []
            self.fresh_checks = 0

        def position_snapshot_status(self):
            return {"fresh": False, "failure": failure}

        def has_fresh_position_snapshot(self):
            self.fresh_checks += 1
            if failure == "not_fresh":
                return False
            if failure == "expires_after_sync" and self.fresh_checks >= 2:
                return False
            return True

        def sync_positions(self):
            if failure == "sync_false":
                return False
            if failure == "sync_exception":
                raise RuntimeError("sync exploded")
            return True

        def get_positions(self):
            return []

        def get_account_balance(self):
            if failure == "balance_exception":
                raise RuntimeError("balance exploded")
            if failure == "balance_nan":
                return float("nan")
            if failure == "balance_negative":
                return -1.0
            if failure == "fee_insufficient":
                return 0.30
            return 58.15

        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}

        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    monkeypatch.setattr("forecast.firewall.check_entry_firewall", lambda *_: (True, ""))
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    result = SimpleNamespace(
        position_contracts=1, side="YES", is_taker_override=True,
        strategy_family="weather_physics", ev=0.2, confidence=0.8, ask_yes=0.5,
    )
    admitted_cash = 0.20 if failure == "fee_insufficient" else 58.15
    intent = TradeIntent(
        contract={"local_symbol": "CANDIDATE", "right": "C"}, result=result,
        bankroll=admitted_cash, buying_power_usd=58.15, risk_book_fingerprint=(),
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    execution = controller.execute_plan(controller.plan_entry(intent), forecast_yes_prob=0.8)

    assert execution["status"] == expected_status
    assert broker.orders == []


def test_execution_controller_dynamic_release_block_precedes_final_broker_refresh(monkeypatch):
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.orders = []
            self.sync_calls = 0
        def position_snapshot_status(self): return {"fresh": True}
        def has_fresh_position_snapshot(self): return True
        def sync_positions(self):
            self.sync_calls += 1
            return True
        def get_positions(self): return []
        def get_account_balance(self): return 58.15
        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 10}
        def place_buy_order(self, *args, **kwargs):
            self.orders.append((args, kwargs))
            return {"status": "executed", "filled_qty": 1}

    monkeypatch.setattr("forecast.firewall.check_entry_firewall", lambda *_: (True, ""))
    monkeypatch.setattr(
        "runtime.release_gate.get_entry_submission_gate_status",
        lambda: {"entries_allowed": True, "current_release_verdict": "READY_FOR_LIVE"},
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda: {"entries_allowed": False, "blockers": ["stale_runtime_heartbeat"]},
    )
    result = SimpleNamespace(
        position_contracts=1, side="YES", is_taker_override=True,
        strategy_family="weather_physics", ev=0.2, confidence=0.8, ask_yes=0.5,
    )
    intent = TradeIntent(
        contract={"local_symbol": "CANDIDATE", "right": "C"}, result=result,
        bankroll=58.15, buying_power_usd=58.15, risk_book_fingerprint=(),
    )
    broker = BrokerStub()
    controller = KalshiExecutionController(broker)
    execution = controller.execute_plan(controller.plan_entry(intent), forecast_yes_prob=0.8)

    assert execution["status"] == "release_gate_blocked"
    assert "stale_runtime_heartbeat" in execution["execution_reason"]
    assert broker.sync_calls == 0
    assert broker.orders == []


def test_execution_controller_cannot_override_position_or_kelly_cap(monkeypatch):
    import config
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    monkeypatch.setattr(config, "KALSHI_MAX_USD_PER_POSITION", 40.0)
    monkeypatch.setattr(config, "KALSHI_KELLY_CAP", 0.10)

    class BrokerStub:
        def get_quote(self, _ticker):
            return {"yes_bid": 0.48, "yes_ask": 0.50, "yes_ask_size": 100}

    result = SimpleNamespace(
        position_contracts=100,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_physics",
        ev=0.30,
        confidence=0.90,
        ask_yes=0.50,
        weather_mode="TEMP",
    )
    intent = TradeIntent(
        contract={"local_symbol": "KXTEMPNYCH-1-T70", "right": "C"},
        result=result,
        bankroll=100.0,
        buying_power_usd=100.0,
        max_capital_usd=100.0,
    )

    plan = KalshiExecutionController(BrokerStub()).plan_entry(intent)

    assert plan.status == "ready"
    assert plan.affordable_qty == config.max_kalshi_contracts_for_budget(0.50, 10.0)
    assert plan.executable_qty == plan.affordable_qty
