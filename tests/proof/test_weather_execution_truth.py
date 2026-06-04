from datetime import datetime, timedelta, timezone


def _make_weather_contract() -> dict:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y%m%d %H:%M:%S"
    )
    return {
        "id": 1,
        "market_id": 1,
        "local_symbol": "KXHIGHNY-30JUN26-T75",
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
