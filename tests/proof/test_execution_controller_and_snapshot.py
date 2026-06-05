from __future__ import annotations

from types import SimpleNamespace


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
                "yes_ask": 0.61,
                "yes_ask_size": 4.9,
            }

    result = SimpleNamespace(
        position_contracts=12,
        side="YES",
        is_taker_override=False,
        strategy_family="weather_ensemble",
        ev=0.12,
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


def test_execution_controller_retries_smaller_after_depth_loss():
    from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent

    class BrokerStub:
        def __init__(self):
            self.quote_calls = 0
            self.orders: list[tuple[int, float, str]] = []

        def get_quote(self, _ticker):
            self.quote_calls += 1
            if self.quote_calls == 1:
                return {"yes_ask": 0.62, "yes_ask_size": 4}
            return {"yes_ask": 0.62, "yes_ask_size": 2}

        def place_buy_order(self, contract_dict, qty, limit_price, **kwargs):
            self.orders.append((qty, limit_price, kwargs.get("reason", "")))
            if len(self.orders) == 1:
                return {
                    "order_id": "ERR",
                    "status": "fill_or_kill_insufficient_resting_volume",
                }
            return {
                "order_id": "ORD-2",
                "status": "executed",
                "qty": qty,
                "price": limit_price,
            }

    result = SimpleNamespace(
        position_contracts=4,
        side="YES",
        is_taker_override=True,
        strategy_family="weather_ensemble",
        ev=0.18,
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
    assert execution["status"] == "executed"
    assert execution["qty"] == 2
    assert execution["execution_reason"] == "retried_smaller_after_depth_loss"
    assert len(broker.orders) == 2


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
