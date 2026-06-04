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
