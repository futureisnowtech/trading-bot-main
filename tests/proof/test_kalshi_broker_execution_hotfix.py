from execution.kalshi_broker import KalshiBroker


def _connected_broker() -> KalshiBroker:
    broker = KalshiBroker()
    broker._connected = True
    broker._private_key = object()
    return broker


def test_marketable_yes_buy_uses_price_field_and_cost_cap(monkeypatch):
    broker = _connected_broker()
    captured = {}

    def fake_request(method, path, params=None, body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"order": {"status": "resting", "order_id": "ORD-1"}}

    monkeypatch.setattr(broker, "_request", fake_request)

    result = broker.place_buy_order(
        {"local_symbol": "KXHIGHLAX-26JUN05-B69.5", "right": "C"},
        qty=3,
        limit_price=0.67,
        type="market",
    )

    assert result["status"] == "resting"
    assert captured["method"] == "POST"
    assert captured["path"] == "/trade-api/v2/portfolio/orders"
    assert captured["body"]["yes_price"] == 99
    assert captured["body"]["buy_max_cost"] == 204
    assert captured["body"]["time_in_force"] == "fill_or_kill"
    assert "type" not in captured["body"]


def test_marketable_no_buy_uses_no_leg_price_field(monkeypatch):
    broker = _connected_broker()
    captured = {}

    def fake_request(method, path, params=None, body=None):
        captured["body"] = body
        return {"order": {"status": "resting", "order_id": "ORD-2"}}

    monkeypatch.setattr(broker, "_request", fake_request)

    result = broker.place_buy_order(
        {"local_symbol": "KXLOWTPHX-26JUN05-T80", "right": "P"},
        qty=2,
        limit_price=0.54,
        type="market",
    )

    assert result["status"] == "resting"
    assert captured["body"]["no_price"] == 99
    assert captured["body"]["buy_max_cost"] == 110
    assert "yes_price" not in captured["body"]


def test_broker_surfaces_rate_limit_status(monkeypatch):
    broker = _connected_broker()

    monkeypatch.setattr(
        broker,
        "_request",
        lambda *args, **kwargs: {
            "error": {"code": "too_many_requests", "message": "too many requests"}
        },
    )

    result = broker.place_buy_order(
        {"local_symbol": "KXHIGHAUS-26JUN05-B83.5", "right": "C"},
        qty=1,
        limit_price=0.69,
        type="limit",
    )

    assert result["status"] == "too_many_requests"


def test_fill_price_falls_back_to_total_cost_per_share():
    broker = _connected_broker()

    fill_price = broker._extract_average_fill_price(
        {
            "fill_count_fp": "43.00",
            "taker_fill_cost_dollars": "6.880000",
        }
    )

    assert fill_price == 0.16


def test_sync_positions_preserves_cost_basis(monkeypatch):
    broker = _connected_broker()

    monkeypatch.setattr(
        broker,
        "_request",
        lambda method, path, params=None, body=None: {
            "market_positions": [
                {
                    "ticker": "KXHIGHLAX-26JUN05-B69.5",
                    "position_fp": "43.00",
                    "total_traded_dollars": "6.880000",
                }
            ]
        },
    )

    broker.sync_positions()
    pos = broker.get_position("KXHIGHLAX-26JUN05-B69.5", "C")

    assert pos is not None
    assert pos["qty"] == 43.0
    assert pos["entry_price"] == 0.16


def test_sync_positions_restores_weather_observation_fields(monkeypatch):
    broker = _connected_broker()

    monkeypatch.setattr(
        broker,
        "_request",
        lambda method, path, params=None, body=None: {
            "market_positions": [
                {
                    "ticker": "KXHIGHLAX-26JUN05-B69.5",
                    "position_fp": "5.00",
                    "total_traded_dollars": "3.250000",
                }
            ]
        },
    )
    monkeypatch.setattr(
        broker,
        "_load_latest_entry_context",
        lambda ticker, side: {
            "entry_price": None,
            "forecast_yes_prob": 0.81,
            "model_prob_gfs": 0.77,
            "model_prob_ecmwf": 0.84,
            "weather_mode": "HIGH",
            "forecast_hours_to_resolution": 19.5,
            "entered_at": "2026-06-06T05:00:00+00:00",
        },
    )

    broker.sync_positions()
    pos = broker.get_position("KXHIGHLAX-26JUN05-B69.5", "C")

    assert pos is not None
    assert pos["forecast_yes_prob"] == 0.81
    assert pos["model_prob_gfs"] == 0.77
    assert pos["model_prob_ecmwf"] == 0.84
    assert pos["weather_mode"] == "HIGH"
    assert pos["forecast_hours_to_resolution"] == 19.5
    assert pos["entered_at"] == "2026-06-06T05:00:00+00:00"


def test_sync_positions_prefers_recorded_buy_entry_price(monkeypatch):
    broker = _connected_broker()

    monkeypatch.setattr(
        broker,
        "_request",
        lambda method, path, params=None, body=None: {
            "market_positions": [
                {
                    "ticker": "KXLOWTPHX-26JUN05-T80",
                    "position_fp": "-8.00",
                    "total_traded_dollars": "8.800000",
                }
            ]
        },
    )
    monkeypatch.setattr(
        broker,
        "_load_latest_entry_context",
        lambda ticker, side: {
            "entry_price": 0.41,
            "forecast_yes_prob": 0.26,
            "model_prob_gfs": 0.22,
            "model_prob_ecmwf": 0.28,
            "weather_mode": "LOW",
            "forecast_hours_to_resolution": 21.5,
            "entered_at": "2026-06-06T05:00:00+00:00",
        },
    )

    broker.sync_positions()
    pos = broker.get_position("KXLOWTPHX-26JUN05-T80", "P")

    assert pos is not None
    assert pos["entry_price"] == 0.41
    assert pos["qty"] == 8.0


def test_executed_buy_logs_weather_observation_fields(monkeypatch):
    broker = _connected_broker()
    captured = {}

    monkeypatch.setattr(
        broker,
        "_request",
        lambda *args, **kwargs: {"order": {"status": "executed", "order_id": "ORD-9"}},
    )
    monkeypatch.setattr(
        broker,
        "_hydrate_order_details",
        lambda order: {
            **order,
            "fill_count_fp": "2.00",
            "taker_fill_cost_dollars": "1.240000",
        },
    )
    monkeypatch.setattr(broker, "_extract_total_fees", lambda *_args, **_kwargs: 0.14)

    def fake_log_trade(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr("execution.kalshi_broker.log_trade", fake_log_trade)

    result = broker.place_buy_order(
        {"local_symbol": "KXLOWTPHX-26JUN05-T80", "right": "C"},
        qty=2,
        limit_price=0.62,
        type="limit",
        forecast_yes_prob=0.74,
        model_prob_gfs=0.71,
        model_prob_ecmwf=0.79,
        weather_mode="LOW",
        forecast_hours_to_resolution=21.5,
    )

    assert result["status"] == "executed"
    assert captured["forecast_yes_prob"] == 0.74
    assert captured["model_prob_gfs"] == 0.71
    assert captured["model_prob_ecmwf"] == 0.79
    assert captured["weather_mode"] == "LOW"
    assert captured["forecast_hours_to_resolution"] == 21.5


def test_partial_sell_preserves_remaining_position(monkeypatch):
    broker = _connected_broker()
    broker._open_positions["KXLOWTPHX-26JUN05-T80_C"] = {
        "qty": 10,
        "side": "YES",
        "local_symbol": "KXLOWTPHX-26JUN05-T80",
        "right": "C",
        "entry": 0.40,
        "entry_price": 0.40,
        "forecast_yes_prob": 0.74,
        "model_prob_gfs": 0.71,
        "model_prob_ecmwf": 0.79,
        "weather_mode": "LOW",
        "forecast_hours_to_resolution": 21.5,
    }
    captured = {}

    monkeypatch.setattr(
        broker,
        "_request",
        lambda *args, **kwargs: {"order": {"status": "executed", "order_id": "ORD-SELL"}},
    )
    monkeypatch.setattr(
        broker,
        "_hydrate_order_details",
        lambda order: {
            **order,
            "fill_count_fp": "4.00",
            "taker_fill_cost_dollars": "2.000000",
        },
    )
    monkeypatch.setattr(broker, "_extract_total_fees", lambda *_args, **_kwargs: 0.28)
    monkeypatch.setattr("execution.kalshi_broker.log_trade", lambda **kwargs: captured.update(kwargs))

    result = broker.place_sell_order(
        {"local_symbol": "KXLOWTPHX-26JUN05-T80"},
        qty=4,
        limit_price=0.50,
        side="yes",
        type="limit",
    )

    pos = broker.get_position("KXLOWTPHX-26JUN05-T80", "C")
    assert result["status"] == "executed"
    assert result["filled_qty"] == 4.0
    assert result["remaining_position_qty"] == 6.0
    assert pos is not None
    assert pos["qty"] == 6.0
    assert captured["qty"] == 4.0
    assert captured["forecast_yes_prob"] == 0.74
    assert captured["model_prob_gfs"] == 0.71
    assert captured["model_prob_ecmwf"] == 0.79
    assert captured["weather_mode"] == "LOW"
    assert captured["forecast_hours_to_resolution"] == 21.5
