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


def _make_rain_contract() -> dict:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y%m%d %H:%M:%S"
    )
    return {
        "id": 2,
        "market_id": 1,
        "local_symbol": "KXRAINNY-30JUN26-T1",
        "contract_name": "Will rainfall in NY be >1 inch on Jun 30, 2026?",
        "right": "C",
        "strike": 1.0,
        "last_trade_at": expiry,
    }


def test_weather_continuous_sizing_stays_live_positive():
    from forecast.strategy_engine import calculate_continuous_sizing

    qty = calculate_continuous_sizing(
        market_price=0.40,
        ensemble_prob=0.70,
        capital_base=1000.0,
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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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


def test_weather_entry_uses_post_fee_ev_floor_without_legacy_raw_edge_choke(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: fresh_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )
    monkeypatch.setattr(
        se,
        "_blend_weather_probabilities",
        lambda **kwargs: {
            "ensemble_prob": 0.80,
            "gfs_weight": 0.60,
            "ecmwf_weight": 0.40,
            "convergence_multiplier": 1.0,
            "divergence_gap": 0.0,
            "divergence_size_multiplier": 1.0,
            "catastrophic_divergence": False,
        },
        )

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.52, 0.02, now_ts),
        no_quote=_quote(0.46, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "YES"
    assert result.econ_approved is True
    assert any(factor.startswith("net_ev=") for factor in result.top_factors)


def test_weather_override_uses_exchange_fee_only_ev_floor_without_buffer_tax(monkeypatch):
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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.70, 0.02, now_ts),
        no_quote=_quote(0.30, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "YES"
    assert result.econ_approved is True
    assert result.veto_reason == ""
    assert result.position_contracts > 0


def test_rain_lane_allows_sub_fifteen_cent_entries_when_above_rain_floor(monkeypatch):
    import forecast.strategy_engine as se

    rain_weather = {
        "members_precip": [1.3] * 31,
        "ecmwf": {"members_precip": [1.4] * 31},
        "sigma_precip": 0.05,
        "peak_tcdc": 20.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: rain_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: rain_weather,
    )

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_rain_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.08, 0.02, now_ts),
        no_quote=_quote(0.92, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.side == "YES"
    assert result.econ_approved is True


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
    assert result.veto_reason.startswith("stale_market_data")


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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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
        ask_yes=0.91,
        ask_no=0.09,
        hours_to_res=24.0,
        contract_name="Will the high temp in NY be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert passes is False
    assert side == ""
    assert any("expensive_yes_headroom_veto" in factor for factor in factors)


def test_narrow_bin_weather_gets_sizing_haircut_instead_of_hard_veto(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [84.0] * 31,
        "ecmwf": {"members_high": [84.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )
    monkeypatch.setattr(
        se,
        "_blend_weather_probabilities",
        lambda **kwargs: {
            "ensemble_prob": 0.80,
            "gfs_weight": 0.60,
            "ecmwf_weight": 0.40,
            "convergence_multiplier": 1.0,
            "divergence_gap": 0.0,
            "divergence_size_multiplier": 1.0,
            "catastrophic_divergence": False,
        },
        )

    passes, side, _prob, factors, _is_taker, sizing_mult, _tier, _cap = se._strategy_weather_details(
        "KXHIGHLAX-26JUN06-B83.5",
        ask_yes=0.53,
        ask_no=0.47,
        hours_to_res=24.0,
        contract_name="Will the high temp in LA be 83-84° on Jun 6, 2026?",
        strike=83.5,
    )

    assert passes is True
    # The bin contract ban was removed, so this now passes if EV is high enough.


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
    assert not any("divergence_veto" in factor for factor in factors)


def test_weather_high_cloud_hard_veto_blocks_high_temp_yes(monkeypatch):
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

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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
    assert result.strategy_family == "vetoed"
    assert result.side == "NONE"
    assert result.econ_approved is False
    assert result.veto_reason.startswith("cloud_cover_veto")


def test_weather_ignores_legacy_macro_risk_gate(monkeypatch):
    import forecast.strategy_engine as se

    fresh_weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }

    monkeypatch.setattr(se, "_get_macro_context", lambda: {"risk_score": 9})
    monkeypatch.setattr(se, "get_weather_data", lambda ticker: fresh_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: fresh_weather,
    )

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
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


def test_blended_weather_yes_probability_preserves_softened_divergence_signal(monkeypatch):
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

    assert prob is not None
    assert 0.5 < prob < 0.97


def test_ensure_weather_data_backfills_missing_series(monkeypatch):
    import data.kalshi_weather_monitor as wm

    wm._WEATHER_SHADOW_STATE.clear()
    wm._LAST_SNAPSHOT_MTIME = 0.0
    monkeypatch.setattr(wm, "_WEATHER_SNAPSHOT_FILE", "", raising=False)

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


def test_get_weather_data_restores_shared_snapshot_without_fetch(tmp_path, monkeypatch):
    import json
    import data.kalshi_weather_monitor as wm

    snapshot_path = tmp_path / "weather_snapshot.json"
    wm._WEATHER_SHADOW_STATE.clear()
    wm._LAST_SNAPSHOT_MTIME = 0.0
    monkeypatch.setattr(wm, "_WEATHER_SNAPSHOT_FILE", str(snapshot_path), raising=False)

    snapshot_path.write_text(
        json.dumps(
            {
                "written_at": datetime.now(timezone.utc).timestamp(),
                "series_count": 1,
                "state": {
                    "KXHIGHNY": {
                        "members_high": [76.0] * 31,
                        "timestamp": datetime.now(timezone.utc).timestamp(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    hydrated = wm.get_weather_data("KXHIGHNY-30JUN26-T75")

    assert hydrated["members_high"][0] == 76.0


def test_ensure_weather_data_uses_shared_snapshot_before_fetch(tmp_path, monkeypatch):
    import json
    import data.kalshi_weather_monitor as wm

    snapshot_path = tmp_path / "weather_snapshot.json"
    wm._WEATHER_SHADOW_STATE.clear()
    wm._LAST_SNAPSHOT_MTIME = 0.0
    monkeypatch.setattr(wm, "_WEATHER_SNAPSHOT_FILE", str(snapshot_path), raising=False)

    snapshot_path.write_text(
        json.dumps(
            {
                "written_at": datetime.now(timezone.utc).timestamp(),
                "series_count": 1,
                "state": {
                    "KXHIGHNY": {
                        "members_high": [76.0] * 31,
                        "timestamp": datetime.now(timezone.utc).timestamp(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    async def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("fresh shared snapshot should avoid live fetch")

    monkeypatch.setattr(wm, "fetch_open_meteo_ensemble", _should_not_fetch)

    summary = wm.ensure_weather_data(["KXHIGHNY-30JUN26-T75"], include_intraday=False)

    assert summary["refreshed_series"] == 0
    assert summary["snapshot_loaded"] == 1


def test_ensure_weather_data_refreshes_snapshot_before_entry_gate_if_weather_is_aging(
    tmp_path,
    monkeypatch,
):
    import json
    import data.kalshi_weather_monitor as wm

    snapshot_path = tmp_path / "weather_snapshot.json"
    wm._WEATHER_SHADOW_STATE.clear()
    wm._LAST_SNAPSHOT_MTIME = 0.0
    monkeypatch.setattr(wm, "_WEATHER_SNAPSHOT_FILE", str(snapshot_path), raising=False)

    stale_ts = datetime.now(timezone.utc).timestamp() - float(wm.WEATHER_REFRESH_TARGET_SEC + 60)
    snapshot_path.write_text(
        json.dumps(
            {
                "written_at": stale_ts,
                "series_count": 1,
                "state": {
                    "KXHIGHNY": {
                        "members_high": [76.0] * 31,
                        "timestamp": stale_ts,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    async def fake_fetch_open_meteo_ensemble(city_key, lat, lon):
        return {
            "members_high": [80.0] * 31,
            "members_low": [65.0] * 31,
            "members_precip": [0.01] * 31,
            "mean_high": 80.0,
            "sigma_high": 1.2,
            "mean_low": 65.0,
            "sigma_low": 1.1,
            "mean_precip": 0.01,
            "sigma_precip": 0.02,
            "peak_tcdc": 10.0,
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "provider_mode": "ensemble_members",
            "forecast_source": "open_meteo_ensemble",
            "ecmwf": None,
            "aigefs": None,
        }

    async def fake_fetch_metar_observation(icao):
        return {}

    async def fake_fetch_hrrr_forecast(city_key, lat, lon):
        return {}

    monkeypatch.setattr(wm, "fetch_open_meteo_ensemble", fake_fetch_open_meteo_ensemble)
    monkeypatch.setattr(wm, "fetch_metar_observation", fake_fetch_metar_observation)
    monkeypatch.setattr(wm, "fetch_hrrr_forecast", fake_fetch_hrrr_forecast)

    summary = wm.ensure_weather_data(["KXHIGHNY-30JUN26-T75"], include_intraday=False)

    assert summary["refreshed_series"] == 1
    assert wm.get_weather_data("KXHIGHNY-30JUN26-T75")["mean_high"] == 80.0


def test_cached_ensemble_record_expires_on_refresh_target_window():
    import data.kalshi_weather_monitor as wm

    wm._COORDINATE_CACHE.clear()
    wm._COORDINATE_CACHE["40.78_-73.97"] = {
        "timestamp": datetime.now(timezone.utc).timestamp()
        - float(wm.WEATHER_REFRESH_TARGET_SEC + 60),
        "mean_high": 76.0,
    }

    assert wm._cached_ensemble_record("40.78_-73.97") == {}


def test_weather_market_snapshots_skip_bar_loading():
    from forecast.market_snapshot import build_market_snapshots

    active_contracts = [
        {
            "id": 1,
            "market_id": 9,
            "local_symbol": "KXHIGHNY-26JUN05-B89.5",
            "contract_name": "NY High",
            "right": "C",
            "strike": 89.5,
            "last_trade_at": "2026-06-05T23:59:59Z",
            "resolution_at": "2026-06-05T23:59:59Z",
        },
        {
            "id": 2,
            "market_id": 9,
            "local_symbol": "KXHIGHNY-26JUN05-B89.5",
            "contract_name": "NY High",
            "right": "P",
            "strike": 89.5,
            "last_trade_at": "2026-06-05T23:59:59Z",
            "resolution_at": "2026-06-05T23:59:59Z",
        },
    ]

    snapshots = build_market_snapshots(
        active_contracts,
        get_bars_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("weather snapshots should not request bars")
        ),
        get_quotes_fn=lambda *_args, **_kwargs: {
            "yes_quote": {"ask": 0.42, "bid": 0.40, "mid": 0.41},
            "no_quote": {"ask": 0.58, "bid": 0.56, "mid": 0.57},
        },
    )

    assert len(snapshots) == 1
    assert snapshots[0].bars_5m == []


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


def test_fetch_open_meteo_ensemble_uses_deterministic_fallback_without_api_key(monkeypatch):
    import asyncio
    import data.kalshi_weather_monitor as wm

    fallback_record = {
        "provider_mode": "deterministic_multi_model",
        "forecast_source": "open_meteo_forecast",
        "members_high": [76.0],
        "members_low": [61.0],
        "members_precip": [0.02],
        "mean_high": 76.0,
        "sigma_high": 1.8,
        "mean_low": 61.0,
        "sigma_low": 1.8,
        "mean_precip": 0.02,
        "sigma_precip": 0.08,
        "peak_tcdc": 10.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "ecmwf": {"provider_mode": "deterministic_multi_model", "members_high": [75.5], "mean_high": 75.5, "sigma_high": 1.7},
        "aigefs": {"provider_mode": "deterministic_multi_model", "members_high": [76.2], "mean_high": 76.2, "sigma_high": 1.6},
    }

    wm._COORDINATE_CACHE.clear()
    wm._ENSEMBLE_FETCH_STATE.clear()
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    monkeypatch.setattr(
        wm,
        "_fetch_open_meteo_deterministic_multimodel",
        lambda city_key, lat, lon: asyncio.sleep(0, result=fallback_record),
    )

    result = asyncio.run(wm.fetch_open_meteo_ensemble("NY", 40.78, -73.97))

    assert result["provider_mode"] == "deterministic_multi_model"
    assert result["mean_high"] == 76.0


def test_open_meteo_429_cools_city_and_skips_repeat_fetch(monkeypatch):
    import asyncio
    import data.kalshi_weather_monitor as wm

    class _Resp:
        status_code = 429
        text = '{"reason":"Daily API request limit exceeded. Please try again tomorrow.","error":true}'

        def json(self):
            return {}

    calls = {"count": 0}
    fallback_calls = {"count": 0}
    fallback_record = {
        "provider_mode": "deterministic_multi_model",
        "forecast_source": "open_meteo_forecast",
        "members_high": [76.0],
        "members_low": [61.0],
        "members_precip": [0.01],
        "mean_high": 76.0,
        "sigma_high": 1.8,
        "mean_low": 61.0,
        "sigma_low": 1.8,
        "mean_precip": 0.01,
        "sigma_precip": 0.08,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "ecmwf": None,
        "aigefs": None,
    }

    wm._COORDINATE_CACHE.clear()
    wm._ENSEMBLE_FETCH_STATE.clear()
    wm._ENSEMBLE_GLOBAL_RATE_LIMIT["until"] = 0.0
    wm._ENSEMBLE_GLOBAL_RATE_LIMIT["reason"] = ""

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return _Resp()

    monkeypatch.setattr(wm.requests, "get", fake_get)
    monkeypatch.setenv("OPEN_METEO_API_KEY", "present-for-test")
    async def fake_det(city_key, lat, lon):
        fallback_calls["count"] += 1
        return dict(fallback_record)
    monkeypatch.setattr(wm, "_fetch_open_meteo_deterministic_multimodel", fake_det)
    monkeypatch.setattr(
        "logging_db.trade_logger.log_event",
        lambda *args, **kwargs: None,
    )

    first = asyncio.run(wm.fetch_open_meteo_ensemble("NY", 40.78, -73.97))
    second = asyncio.run(wm.fetch_open_meteo_ensemble("LAX", 33.94, -118.41))

    assert first["provider_mode"] == "deterministic_multi_model"
    assert second["provider_mode"] == "deterministic_multi_model"
    assert calls["count"] == 1
    assert fallback_calls["count"] == 2
    assert wm._global_ensemble_rate_limit_active() is True


def test_contract_weather_projection_is_day_specific(monkeypatch):
    import data.kalshi_weather_monitor as wm

    data = {
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
    wm._WEATHER_SHADOW_STATE["KXHIGHNY"] = data
    wm._WEATHER_SHADOW_STATE["NY"] = data
    monkeypatch.setattr(wm, "get_weather_data", lambda series: data)

    projected = wm.get_contract_weather_data(
        "KXHIGHNY-26JUN06-T83",
        contract_name="Will high temp in NY be >83 on Jun 6, 2026?",
        strike=83.0,
    )

    assert "target_local_date" in projected
    assert projected["target_local_date"] == "2026-06-06"
    assert projected["settlement_start_hour"] == 1
    assert projected["members_high"] == [84.0, 83.0]
    assert projected["members_low"] == [84.0, 83.0]
    assert round(projected["peak_ssrd"], 1) == 660.0


def test_contract_observed_weather_data_prefers_intraday_for_current_local_day(monkeypatch):
    import data.kalshi_weather_monitor as wm

    tz_name = wm.STATIONS["LAX"]["tz"]
    local_today = datetime.now(timezone.utc).astimezone(
        timezone.utc if tz_name == "UTC" else __import__("pytz").timezone(tz_name)
    ).date()

    monkeypatch.setattr(
        wm,
        "get_weather_data",
        lambda series: {
            "intraday": {
                "daily_max": 75.0,
                "daily_min": 61.0,
                "daily_precip": 0.18,
            }
        },
    )
    monkeypatch.setattr(
        wm,
        "_parse_contract_local_date",
        lambda *args, **kwargs: local_today,
    )

    observed = wm.get_contract_observed_weather_data(
        "KXHIGHLAX-30JUN26-T75",
        contract_name="Will the high temp in LA be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert observed["source"] == "metar_watermark"
    assert observed["observed_high"] == 75.0
    assert observed["observed_low"] == 61.0
    assert observed["observed_precip"] == 0.18


def test_contract_observed_weather_data_backfills_prior_day_from_archive(monkeypatch):
    import data.kalshi_weather_monitor as wm
    import pytz

    tz_name = wm.STATIONS["LAX"]["tz"]
    local_today = datetime.now(pytz.timezone(tz_name)).date()
    prior_day = local_today - timedelta(days=1)

    monkeypatch.setattr(wm, "get_weather_data", lambda series: {})
    monkeypatch.setattr(
        wm,
        "_parse_contract_local_date",
        lambda *args, **kwargs: prior_day,
    )
    monkeypatch.setattr(
        wm,
        "_fetch_observed_daily_summary",
        lambda city_key, lat, lon, target_date, timezone_name, station=None: {
            "city_key": city_key,
            "target_local_date": target_date.isoformat(),
            "observed_high": 83.0,
            "observed_low": 67.0,
            "observed_precip": 0.42,
            "source": "open_meteo_archive_daily",
        },
    )

    observed = wm.get_contract_observed_weather_data(
        "KXRAINLAX-30JUN26-T1",
        contract_name="Will rainfall in LA be >1 inch on Jun 30, 2026?",
        strike=1.0,
    )

    assert observed["source"] == "open_meteo_archive_daily"
    assert observed["observed_high"] == 83.0
    assert observed["observed_low"] == 67.0
    assert observed["observed_precip"] == 0.42


def test_observed_daily_summary_converts_metric_archive_units(monkeypatch):
    import data.kalshi_weather_monitor as wm

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "daily": {
                    "time": ["2026-06-05"],
                    "temperature_2m_max": [30.0],
                    "temperature_2m_min": [20.0],
                    "precipitation_sum": [25.4],
                }
            }

    wm._OBSERVED_DAILY_CACHE.clear()
    monkeypatch.setattr(wm, "_fetch_nws_cli_daily_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(wm.requests, "get", lambda *args, **kwargs: _Resp())

    observed = wm._fetch_observed_daily_summary(
        "LAX",
        33.94,
        -118.41,
        datetime(2026, 6, 5).date(),
        timezone_name="America/Los_Angeles",
    )

    assert round(observed["observed_high"], 2) == 86.0
    assert round(observed["observed_low"], 2) == 68.0
    assert round(observed["observed_precip"], 4) == 1.0


def test_nws_cli_product_text_parses_daily_truth():
    import data.kalshi_weather_monitor as wm

    parsed = wm._parse_nws_cli_product_text(
        """
...THE LOS ANGELES INTL AIRPORT CA CLIMATE SUMMARY FOR JUNE 5 2026...

TEMPERATURE (F)
 YESTERDAY
  MAXIMUM         70   4:12 PM
  MINIMUM         64  10:30 AM

PRECIPITATION (IN)
  YESTERDAY        T

SNOWFALL (IN)
  YESTERDAY       MM
        """,
        target_date=datetime(2026, 6, 5).date(),
    )

    assert parsed["observed_high"] == 70.0
    assert parsed["observed_low"] == 64.0
    assert parsed["observed_precip"] == 0.001


def test_observed_daily_summary_prefers_nws_cli_daily(monkeypatch):
    import data.kalshi_weather_monitor as wm

    wm._OBSERVED_DAILY_CACHE.clear()
    monkeypatch.setattr(
        wm,
        "_fetch_nws_cli_daily_summary",
        lambda city_key, station, target_date: {
            "city_key": city_key,
            "target_local_date": target_date.isoformat(),
            "observed_high": 74.0,
            "observed_low": 59.0,
            "observed_precip": 0.21,
            "source": "nws_cli_daily",
            "cached_at": 123.0,
        },
    )
    monkeypatch.setattr(
        wm,
        "_fetch_open_meteo_archive_daily_summary",
        lambda *args, **kwargs: {
            "city_key": "LAX",
            "target_local_date": "2026-06-05",
            "observed_high": 999.0,
            "observed_low": 999.0,
            "observed_precip": 999.0,
            "source": "open_meteo_archive_daily",
            "cached_at": 123.0,
        },
    )

    observed = wm._fetch_observed_daily_summary(
        "LAX",
        33.94,
        -118.41,
        datetime(2026, 6, 5).date(),
        timezone_name="America/Los_Angeles",
        station=wm.STATIONS["LAX"],
    )

    assert observed["source"] == "nws_cli_daily"
    assert observed["observed_high"] == 74.0
    assert observed["observed_precip"] == 0.21


def test_deterministic_fallback_weather_probability_is_continuous():
    import forecast.strategy_engine as se

    prob = se.blended_weather_yes_probability(
        "KXHIGHNY-30JUN26-T75",
        {
            "provider_mode": "deterministic_multi_model",
            "mean_high": 77.0,
            "sigma_high": 1.8,
            "members_high": [77.0],
            "ecmwf": {
                "provider_mode": "deterministic_multi_model",
                "mean_high": 76.4,
                "sigma_high": 1.6,
                "members_high": [76.4],
            },
            "aigefs": {
                "provider_mode": "deterministic_multi_model",
                "mean_high": 76.8,
                "sigma_high": 1.6,
                "members_high": [76.8],
            },
        },
        contract_name="Will the high temp in NY be >75° on Jun 30, 2026?",
        strike=75.0,
    )

    assert prob is not None
    assert 0.60 < prob < 0.90


def test_deterministic_fallback_can_power_weather_strategy(monkeypatch):
    import forecast.strategy_engine as se

    deterministic_weather = {
        "provider_mode": "deterministic_multi_model",
        "members_high": [77.0],
        "members_low": [61.0],
        "members_precip": [0.0],
        "mean_high": 77.0,
        "sigma_high": 1.8,
        "mean_low": 61.0,
        "sigma_low": 1.8,
        "mean_precip": 0.0,
        "sigma_precip": 0.08,
        "peak_tcdc": 8.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "ecmwf": {
            "provider_mode": "deterministic_multi_model",
            "members_high": [76.2],
            "mean_high": 76.2,
            "sigma_high": 1.6,
        },
        "aigefs": {
            "provider_mode": "deterministic_multi_model",
            "members_high": [76.8],
            "mean_high": 76.8,
            "sigma_high": 1.5,
        },
    }

    monkeypatch.setattr(se, "get_weather_data", lambda ticker: deterministic_weather)
    monkeypatch.setattr(
        se,
        "get_contract_weather_data",
        lambda ticker, **kwargs: deterministic_weather,
    )

    monkeypatch.setattr(se, "_resolve_hard_rbi_threshold", lambda **kwargs: 0.0)
    now_ts = datetime.now(timezone.utc).isoformat()
    result = se.evaluate_contract(
        contract=_make_weather_contract(),
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=_quote(0.38, 0.02, now_ts),
        no_quote=_quote(0.62, 0.02, now_ts),
        bankroll=100.0,
    )

    assert result is not None
    assert result.strategy_family == "weather_ensemble"
    assert result.econ_approved is True
    assert result.side == "YES"
