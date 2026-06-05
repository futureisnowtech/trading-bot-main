from datetime import datetime, timedelta, timezone


def _make_weather_contract() -> dict:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y%m%d %H:%M:%S"
    )
    return {
        "id": 1,
        "market_id": 1,
        "local_symbol": "KXHIGHNY-30JUN26-T75",
        "contract_name": "Will the high temp in NY be >75° on Jun 30, 2026?",
        "right": "C",
        "strike": 75.0,
        "last_trade_at": expiry,
    }


def _quote(mid: float, spread: float, ts: str) -> dict:
    return {
        "bid": round(mid - spread / 2.0, 4),
        "ask": round(mid + spread / 2.0, 4),
        "mid": round(mid, 4),
        "spread": round(spread, 4),
        "implied_prob": round(mid, 4),
        "ts": ts,
    }


def test_weather_continuous_sizing_stays_live_positive():
    from forecast.strategy_engine import calculate_continuous_sizing

    qty = calculate_continuous_sizing(
        market_price=0.40,
        ensemble_prob=0.70,
        capital_base=100.0,
        multiplier=1.0,
        cap_pct=0.05,
    )
    assert qty > 0


def test_weather_override_cannot_bypass_hard_spread_veto(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(
        se,
        "get_weather_data",
        lambda ticker: fresh_weather,
    )
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )

    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.30, 0.14, now_ts),
        no_quote=_quote(0.63, 0.14, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.econ_approved is False
    assert "spread" in result.veto_reason.lower()


def test_weather_override_can_clear_soft_low_conviction_gate(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(
        se,
        "get_weather_data",
        lambda ticker: fresh_weather,
    )
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )

    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.62, 0.02, now_ts),
        no_quote=_quote(0.38, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.econ_approved is True
    assert result.veto_reason == ""


def test_weather_override_uses_same_fee_buffered_ev_floor(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: fresh_weather)
    monkeypatch.setattr(se, "KALSHI_EXPENSIVE_YES_THRESHOLD", 0.95)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )

    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.83, 0.02, now_ts),
        no_quote=_quote(0.17, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.econ_approved is False
    assert result.veto_reason.startswith("fee_adjusted_ev_too_low")


def test_weather_pair_freshness_uses_staler_quote_leg(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: fresh_weather)
    monkeypatch.setattr(se, "get_contract_weather_data", lambda ticker, **kwargs: fresh_weather)

    fresh_ts = datetime.now(timezone.utc).isoformat()
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.62, 0.02, fresh_ts),
        no_quote=_quote(0.38, 0.02, stale_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.econ_approved is False
    assert result.veto_reason == "stale_market_data"


def test_weather_one_sided_no_book_can_still_trade(monkeypatch):
    import forecast.strategy_engine as se

    cold_weather = {
        "members_high": [60.0] * 31,
        "ecmwf": {"members_high": [60.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: cold_weather)
    monkeypatch.setattr(se, "get_contract_weather_data", lambda ticker, **kwargs: cold_weather)

    now_ts = datetime.now(timezone.utc).isoformat()
    yes_quote = {
        "bid": 0.25,
        "ask": None,
        "mid": 0.25,
        "spread": None,
        "implied_prob": 0.25,
        "ts": now_ts,
    }
    no_quote = _quote(0.74, 0.02, now_ts)

    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=yes_quote,
        no_quote=no_quote,
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "NO"
    assert result.econ_approved is True


def test_weather_strategy_can_use_adaptive_model_weights(monkeypatch):
    import forecast.strategy_engine as se

    divergent_weather = {
        "members_high": ([76.0] * 18) + ([75.0] * 13),
        "ecmwf": {"members_high": ([76.0] * 6) + ([75.0] * 25)},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: divergent_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: divergent_weather,
    )
    monkeypatch.setattr(
        se,
        "_get_adaptive_weather_model_blend",
        lambda mode: {
            "segment": "HIGH",
            "sample_size": 8,
            "gfs_weight": 0.2,
            "ecmwf_weight": 0.8,
            "effective_weight": 7.1,
            "shrinkage": 1.0,
        },
    )

    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.65, 0.02, now_ts),
        no_quote=_quote(0.35, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "NO"
    assert result.econ_approved is True


def test_expensive_yes_weather_requires_extra_headroom(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )

    passes, side, _prob, factors, _is_taker, _mult, _tier, _cap = se._strategy_weather_details(
        "KXHIGHNY-30JUN26-T75",
        ask_yes=0.81,
        ask_no=0.19,
        hours_to_res=24.0,
        contract_name="Will the high temp in NY be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert passes is False
    assert side == ""
    assert any("expensive_yes_headroom_veto" in factor for factor in factors)


def test_weather_divergence_is_softened_before_catastrophic_veto(monkeypatch):
    import forecast.strategy_engine as se

    weather = {
        "members_high": ([76.0] * 24) + ([75.0] * 7),
        "ecmwf": {"members_high": ([76.0] * 12) + ([75.0] * 19)},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: weather)
    monkeypatch.setattr(se, "get_contract_weather_data", lambda ticker, **kwargs: weather)

    passes, side, confidence, factors, *_ = se._strategy_weather_details(
        ticker="KXHIGHNY-30JUN26-T75",
        ask_yes=0.35,
        ask_no=0.65,
        hours_to_res=24.0,
        contract_name="Will the high temp in NY be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert passes is True
    assert side == "YES"
    assert confidence > 0.50
    assert not any("model_divergence_veto" in factor for factor in factors)


def test_weather_high_cloud_needs_low_solar_to_veto_high_temp(monkeypatch):
    import forecast.strategy_engine as se

    cloudy_but_hot_weather = {
        "members_high": [82.0] * 31,
        "ecmwf": {"members_high": [82.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 82.0,
        "peak_ssrd": 520.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: cloudy_but_hot_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: cloudy_but_hot_weather,
    )

    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.62, 0.02, now_ts),
        no_quote=_quote(0.38, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "YES"
    assert result.econ_approved is True


def test_blended_weather_yes_probability_neutralizes_catastrophic_divergence(monkeypatch):
    import forecast.strategy_engine as se

    monkeypatch.setattr(
        se,
        "_get_adaptive_weather_model_blend",
        lambda mode: {
            "segment": "GLOBAL",
            "sample_size": 20,
            "gfs_weight": 0.6,
            "ecmwf_weight": 0.4,
        },
    )

    prob = se.blended_weather_yes_probability(
        "KXHIGHNY-30JUN26-T75",
        {
            "members_high": [76.0] * 31,
            "ecmwf": {"members_high": [70.0] * 31},
        },
        contract_name="Will the high temp in NY be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert prob == 0.5


def test_ensure_weather_data_backfills_missing_series(monkeypatch):
    import data.kalshi_weather_monitor as wm

    wm._WEATHER_SHADOW_STATE.clear()

    async def fake_fetch_open_meteo_ensemble(city_key, lat, lon):
        return {
            "members_high": [76.0] * 31,
            "members_low": [60.0] * 31,
            "members_precip": [0.0] * 31,
            "sigma_high": 0.8,
            "sigma_low": 0.7,
            "peak_tcdc": 5.0,
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "ecmwf": {"members_high": [76.0] * 31},
            "aigefs": {"members_high": [76.0]},
        }

    async def fake_fetch_metar_observation(icao):
        return {"temp_f": 74.0, "raw": f"{icao} RAW"}

    async def fake_fetch_hrrr_forecast(city_key, lat, lon):
        return {"hrrr_high": 77.0, "hrrr_trend": "rising"}

    monkeypatch.setattr(wm, "fetch_open_meteo_ensemble", fake_fetch_open_meteo_ensemble)
    monkeypatch.setattr(wm, "fetch_metar_observation", fake_fetch_metar_observation)
    monkeypatch.setattr(wm, "fetch_hrrr_forecast", fake_fetch_hrrr_forecast)

    summary = wm.ensure_weather_data(["KXHIGHNY-30JUN26-T75"])

    assert summary["requested_series"] == 1
    assert summary["refreshed_series"] == 1
    hydrated = wm.get_weather_data("KXHIGHNY-30JUN26-T75")
    assert hydrated
    assert hydrated["members_high"]
    assert hydrated["intraday"]["metar_temp"] == 74.0


def test_active_weather_city_scope_tracks_live_contract_universe(monkeypatch):
    import data.kalshi_weather_monitor as wm

    wm._ACTIVE_CITY_SCOPE_CACHE["timestamp"] = 0.0
    wm._ACTIVE_CITY_SCOPE_CACHE["city_keys"] = []

    monkeypatch.setattr(
        "forecast.db.get_active_contracts",
        lambda: [
            {"local_symbol": "KXHIGHNY-30JUN26-T75"},
            {"local_symbol": "KXRAINLAX-30JUN26-T0.01"},
            {"local_symbol": "KXLOWCHI-30JUN26-T62"},
        ],
    )

    city_keys = wm._active_weather_city_keys()

    assert city_keys == ["CHI", "LAX", "NY"]


def test_open_meteo_429_cools_city_and_skips_repeat_fetch(monkeypatch):
    import asyncio
    import data.kalshi_weather_monitor as wm

    class _Resp:
        status_code = 429
        text = '{"reason":"Daily API request limit exceeded. Please try again tomorrow.","error":true}'

        def json(self):
            return {}

    calls = {"count": 0}

    wm._COORDINATE_CACHE.clear()
    wm._ENSEMBLE_FETCH_STATE.clear()
    wm._ENSEMBLE_GLOBAL_RATE_LIMIT["until"] = 0.0
    wm._ENSEMBLE_GLOBAL_RATE_LIMIT["reason"] = ""

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return _Resp()

    monkeypatch.setattr(wm.requests, "get", fake_get)
    monkeypatch.setattr(
        "logging_db.trade_logger.log_event",
        lambda *args, **kwargs: None,
    )

    first = asyncio.run(wm.fetch_open_meteo_ensemble("NY", 40.78, -73.97))
    second = asyncio.run(wm.fetch_open_meteo_ensemble("LAX", 33.94, -118.41))

    assert first == {}
    assert second == {}
    assert calls["count"] == 1
    assert wm._global_ensemble_rate_limit_active() is True


def test_contract_weather_projection_is_day_specific(monkeypatch):
    import data.kalshi_weather_monitor as wm

    wm._WEATHER_SHADOW_STATE.clear()
    wm._WEATHER_SHADOW_STATE["KXHIGHLAX"] = {
        "members_high": [70.0],
        "members_low": [60.0],
        "members_precip": [0.0],
        "sigma_high": 0.8,
        "sigma_low": 0.6,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "hourly_time": [
            "2026-06-05T00:00",
            "2026-06-05T12:00",
            "2026-06-06T00:00",
            "2026-06-06T12:00",
        ],
        "hourly_members_temp_f": {
            "member_00": [66.0, 70.0, 80.0, 84.0],
            "member_01": [65.0, 69.0, 79.0, 83.0],
        },
        "hourly_members_precip_in": {
            "member_00": [0.0, 0.0, 0.0, 0.0],
            "member_01": [0.0, 0.0, 0.0, 0.0],
        },
        "hourly_members_cloud": {
            "member_00": [10.0, 15.0, 20.0, 25.0],
            "member_01": [10.0, 12.0, 18.0, 22.0],
        },
        "hourly_members_ssrd": {
            "member_00": [100.0, 250.0, 500.0, 650.0],
            "member_01": [120.0, 260.0, 520.0, 670.0],
        },
        "intraday": {"metar_temp": 69.0, "daily_max": 70.0, "daily_min": 66.0},
        "ecmwf": None,
        "aigefs": None,
    }

    projected = wm.get_contract_weather_data(
        "KXHIGHLAX-26JUN06-B83.5",
        contract_name="Will the high temp in LA be 83-84° on Jun 6, 2026?",
        strike=83.5,
    )

    assert projected["target_local_date"] == "2026-06-06"
    assert projected["members_high"] == [84.0, 83.0]
    assert projected["members_low"] == [80.0, 79.0]
    assert round(projected["peak_ssrd"], 1) == 585.0
