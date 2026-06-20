from execution.kalshi_broker import KalshiBroker


def _connected_broker() -> KalshiBroker:
    broker = KalshiBroker()
    broker._connected = True
    broker._private_key = object()
    return broker


def test_marketable_yes_buy_uses_event_order_v2_shape(monkeypatch):
    broker = _connected_broker()
    calls = []

    def fake_request(method, path, params=None, body=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
            }
        )
        return {"order": {"status": "resting", "order_id": "ORD-1"}}

    monkeypatch.setattr(broker, "_request", fake_request)

    result = broker.place_buy_order(
        {"local_symbol": "KXHIGHLAX-26JUN05-B69.5", "right": "C"},
        qty=3,
        limit_price=0.67,
        type="market",
    )

    assert result["status"] == "resting"
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/trade-api/v2/portfolio/events/orders"
    assert calls[0]["body"]["side"] == "bid"
    assert calls[0]["body"]["count"] == "3.00"
    assert calls[0]["body"]["price"] == "0.6700"
    assert calls[0]["body"]["time_in_force"] == "immediate_or_cancel"
    assert calls[0]["body"]["reduce_only"] is False
    assert any(call["method"] == "GET" for call in calls[1:])


def test_marketable_no_buy_maps_to_yes_leg_ask(monkeypatch):
    broker = _connected_broker()
    calls = []

    def fake_request(method, path, params=None, body=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
            }
        )
        return {"order": {"status": "resting", "order_id": "ORD-2"}}

    monkeypatch.setattr(broker, "_request", fake_request)

    result = broker.place_buy_order(
        {"local_symbol": "KXLOWTPHX-26JUN05-T80", "right": "P"},
        qty=2,
        limit_price=0.54,
        type="market",
    )

    assert result["status"] == "resting"
    assert calls[0]["path"] == "/trade-api/v2/portfolio/events/orders"
    assert calls[0]["body"]["side"] == "ask"
    assert calls[0]["body"]["price"] == "0.4600"
    assert calls[0]["body"]["reduce_only"] is False
    assert any(call["method"] == "GET" for call in calls[1:])


def test_broker_surfaces_rate_limit_status(monkeypatch):
    broker = _connected_broker()
    calls = []

    def fake_request(*args, **kwargs):
        calls.append(kwargs.get("body") or {})
        return {
            "error": {"code": "too_many_requests", "message": "too many requests"}
        }

    monkeypatch.setattr(broker, "_request", fake_request)

    result = broker.place_buy_order(
        {"local_symbol": "KXHIGHAUS-26JUN05-B83.5", "right": "C"},
        qty=1,
        limit_price=0.69,
        type="limit",
    )

    assert result["status"] == "too_many_requests"
    assert calls[0]["time_in_force"] == "immediate_or_cancel"


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


def test_no_side_exit_pnl_uses_complement_math(monkeypatch):
    broker = _connected_broker()
    broker._open_positions["KXLOWTPHX-26JUN05-T80_P"] = {
        "qty": 5,
        "side": "NO",
        "local_symbol": "KXLOWTPHX-26JUN05-T80",
        "right": "P",
        "entry": 0.41,
        "entry_price": 0.41,
    }
    captured = {}

    monkeypatch.setattr(
        broker,
        "_request",
        lambda *args, **kwargs: {"order": {"status": "executed", "order_id": "ORD-NO-EXIT"}},
    )
    monkeypatch.setattr(
        broker,
        "_hydrate_order_details",
        lambda order: {
            **order,
            "fill_count_fp": "5.00",
            "taker_fill_cost_dollars": "3.000000",
        },
    )
    monkeypatch.setattr(broker, "_extract_total_fees", lambda *_args, **_kwargs: 0.15)
    monkeypatch.setattr("execution.kalshi_broker.log_trade", lambda **kwargs: captured.update(kwargs))

    result = broker.place_sell_order(
        {"local_symbol": "KXLOWTPHX-26JUN05-T80"},
        qty=5,
        limit_price=0.60,
        side="no",
        type="limit",
    )

    assert result["status"] == "executed"
    assert result["exit_price"] == 0.6
    assert round(result["pnl_usd"], 4) == -1.1
    assert round(captured["pnl_usd"], 4) == -1.1


def test_resting_partial_buy_books_filled_contracts(monkeypatch):
    broker = _connected_broker()
    captured = {}

    monkeypatch.setattr(
        broker,
        "_request",
        lambda *args, **kwargs: {"order": {"status": "resting", "order_id": "ORD-REST"}},
    )
    monkeypatch.setattr(
        broker,
        "_hydrate_order_details",
        lambda order: {
            **order,
            "fill_count_fp": "2.00",
            "remaining_count": "3.00",
            "taker_fill_cost_dollars": "1.240000",
        },
    )
    monkeypatch.setattr(broker, "_extract_total_fees", lambda *_args, **_kwargs: 0.14)
    monkeypatch.setattr("execution.kalshi_broker.log_trade", lambda **kwargs: captured.update(kwargs))

    result = broker.place_buy_order(
        {"local_symbol": "KXHIGHLAX-26JUN05-B69.5", "right": "C"},
        qty=5,
        limit_price=0.62,
        type="limit",
        forecast_yes_prob=0.74,
        model_prob_gfs=0.71,
        model_prob_ecmwf=0.79,
        weather_mode="HIGH",
        forecast_hours_to_resolution=21.5,
    )

    pos = broker.get_position("KXHIGHLAX-26JUN05-B69.5", "C")
    assert result["status"] == "resting"
    assert result["filled_qty"] == 2.0
    assert result["remaining_order_qty"] == 3.0
    assert pos is not None
    assert pos["qty"] == 2.0
    assert pos["resting_remaining_qty"] == 3.0
    assert captured["qty"] == 2.0


def test_discover_markets_uses_series_catalog_and_keeps_partial_truth(monkeypatch):
    broker = _connected_broker()
    calls = []
    monkeypatch.setattr(
        "execution.kalshi_broker._WEATHER_SERIES_CACHE",
        {"expires_at": 0.0, "series_meta": {}},
    )

    def fake_request(method, path, params=None, body=None):
        calls.append((method, path, params))
        params = params or {}
        if path == "/trade-api/v2/series":
            return {
                "series": [
                    {
                        "ticker": "KXHIGHNY",
                        "title": "Highest temperature in NYC",
                        "category": "Climate and Weather",
                    },
                    {
                        "ticker": "KXTEMPNYCH",
                        "title": "Hourly Directional NYC Temperature",
                        "category": "Climate and Weather",
                    },
                    {
                        "ticker": "KXTEMPDCH",
                        "title": "Hourly Directional DC Temperature",
                        "category": "Climate and Weather",
                    },
                ]
            }
        if path == "/trade-api/v2/events" and params.get("series_ticker") == "KXHIGHNY":
            if params.get("status") == "open":
                return {
                    "events": [
                        {
                            "event_ticker": "KXHIGHNY-26JUN09",
                            "title": "Highest temperature in NYC on Jun 9, 2026?",
                            "category": "Climate and Weather",
                            "markets": [
                                {
                                    "ticker": "KXHIGHNY-26JUN09-T85",
                                    "status": "active",
                                    "title": "Will the **high temp in NYC** be >85° on Jun 9, 2026?",
                                    "close_time": "2026-06-10T04:59:00Z",
                                }
                            ],
                        }
                    ]
                }
            return {
                "events": [
                    {
                        "event_ticker": "KXHIGHNY-26JUN10",
                        "title": "Highest temperature in NYC on Jun 10, 2026?",
                        "category": "Climate and Weather",
                        "markets": [
                            {
                                "ticker": "KXHIGHNY-26JUN10-T85",
                                "status": "initialized",
                                "title": "Will the **high temp in NYC** be >85° on Jun 10, 2026?",
                                "close_time": "2026-06-11T04:59:00Z",
                            }
                        ],
                    }
                ]
            }
        if path == "/trade-api/v2/events" and params.get("series_ticker") == "KXTEMPDCH":
            if params.get("status") == "open":
                return {"events": []}
            return {
                "events": [
                    {
                        "event_ticker": "KXTEMPDCH-26JUN1019",
                        "title": "Hourly temperature in DC at 7pm on Jun 10, 2026?",
                        "category": "Climate and Weather",
                        "markets": [
                            {
                                "ticker": "KXTEMPDCH-26JUN1019-T77.99",
                                "status": "initialized",
                                "title": "Will the temp in Washington DC be above 77.99° on Jun 10, 2026 at 7pm EDT?",
                                "close_time": "2026-06-10T23:00:00Z",
                            }
                        ],
                    }
                ]
            }
        if path == "/trade-api/v2/events" and params.get("series_ticker") == "KXTEMPNYCH":
            return {"error": {"code": "too_many_requests", "message": "slow down"}}
        return {"events": []}

    monkeypatch.setattr(broker, "_request", fake_request)

    results = broker.discover_markets()

    active_rows = [row for row in results if row.get("local_symbol") == "KXHIGHNY-26JUN09-T85"]
    assert len(active_rows) == 2
    assert {row["side"] for row in active_rows} == {"YES", "NO"}
    assert any(
        row.get("stub_only") and row.get("underlier") == "KXTEMPDCH-26JUN1019"
        for row in results
    )
    assert any(path == "/trade-api/v2/series" for _method, path, _params in calls)
