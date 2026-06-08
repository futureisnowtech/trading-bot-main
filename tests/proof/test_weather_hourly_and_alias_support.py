from __future__ import annotations

import time


def test_hourly_weather_mode_detection_recognizes_hour_stamped_tickers():
    from forecast.weather_contracts import (
        is_hourly_weather_contract,
        is_live_entry_weather_contract,
        live_entry_scope,
        weather_mode_for_ticker,
    )

    assert weather_mode_for_ticker("KXTEMPNYCH-24JAN0122-T75.99") == "TEMP"
    assert weather_mode_for_ticker("KXHIGHNYD-24JAN0122-T75.99") == "TEMP"
    assert live_entry_scope() == "TRUE_HOURLY_ONLY"
    assert is_hourly_weather_contract("KXTEMPNYCH-24JAN0122-T75.99")
    assert not is_hourly_weather_contract("KXLOWTNYC-26JUN09-T52")
    assert is_live_entry_weather_contract("KXTEMPNYCH-24JAN0122-T75.99")
    assert not is_live_entry_weather_contract(
        "KXLOWTNYC-26JUN09-T52",
        contract_name="Will the minimum temperature be <52° on Jun 9, 2026?",
    )


def test_only_true_hourly_contracts_use_intraday_lane_gates():
    import forecast.strategy_engine as se

    ok, reason = se._weather_market_gate(
        ask_yes=0.20,
        ask_no=0.80,
        spread=0.18,
        hours_to_resolution=0.5,
        mode="TEMP",
        ticker="KXTEMPCHIH-24JAN0122-T75.99",
        contract_name="Will the temperature in Chicago be above 75.99° at 10pm local time?",
    )
    assert ok is True
    assert reason == ""

    ok, reason = se._weather_market_gate(
        ask_yes=0.20,
        ask_no=0.80,
        spread=0.18,
        hours_to_resolution=0.5,
        mode="LOW",
        ticker="KXLOWTNYC-26JUN09-T52",
        contract_name="Will the minimum temperature be <52° on Jun 9, 2026?",
    )
    assert ok is False
    assert reason == "RESOLUTION_HORIZON_TOO_SHORT"


def test_hourly_weather_contract_preserves_decimal_threshold():
    from forecast.weather_contracts import resolve_weather_contract

    semantics = resolve_weather_contract(
        "KXTEMPNYCH-24JAN0122-T75.99",
        contract_name="Will the temp in New York City be above 75.99° on Jan 1, 2024 at 10pm EST?",
        strike=75.99,
    )

    assert semantics is not None
    assert semantics.mode == "TEMP"
    assert semantics.threshold == 75.99


def test_hourly_series_aliases_resolve_for_rain_and_temp():
    import data.kalshi_weather_monitor as wm

    assert wm._resolve_weather_series("KXRAINNYC-26JUN07-T0") == "KXRAINNYC"
    assert wm._resolve_weather_series("KXTEMPNYCH-24JAN0122-T75.99") == "KXTEMPNYCH"


def test_verified_hourly_resolver_covers_exchange_verified_cities():
    import data.kalshi_weather_monitor as wm

    summary = wm.get_hourly_city_support_summary()
    assert summary["exchange_verified_city_count"] == 6
    assert summary["exchange_verified_cities"] == ["BOS", "CHI", "DC", "LAX", "MIA", "NY"]

    expected = {
        "KXTEMPBOSH-24JAN0122-T75.99": "KXTEMPBOSH",
        "KXTEMPCHIH-24JAN0122-T75.99": "KXTEMPCHIH",
        "KXTEMPDCH-24JAN0122-T75.99": "KXTEMPDCH",
        "KXTEMPLAXH-24JAN0122-T75.99": "KXTEMPLAXH",
        "KXTEMPMIAH-24JAN0122-T75.99": "KXTEMPMIAH",
        "KXTEMPNYCH-24JAN0122-T75.99": "KXTEMPNYCH",
    }
    for ticker, series in expected.items():
        assert wm._resolve_weather_series(ticker) == series


def test_verified_hourly_resolver_uses_known_weather_suffixes_only():
    import data.kalshi_weather_monitor as wm

    assert wm._resolve_weather_series("KXTEMPATL-24JAN0122-T75.99") is None
    assert wm._resolve_weather_series("KXTEMPSFO-24JAN0122-T75.99") is None
    assert wm._resolve_weather_series("KXTEMPDCA-24JAN0122-T75.99") is None


def test_hourly_city_resolution_prefers_contract_title_when_series_label_is_messy():
    import data.kalshi_weather_monitor as wm

    assert (
        wm.resolve_weather_city_key(
            "KXTEMPLAXH-26APR1619-T76.99",
            contract_name="Will the temp in NYC be above 76.99° on Apr 16, 2026 at 7pm EDT?",
        )
        == "NY"
    )
    assert (
        wm.resolve_weather_city_key(
            "KXTEMPLAXH-26APR1620-T66.99",
            contract_name="Will the temp in Los Angeles be above 66.99° on Apr 16, 2026 at 8pm EDT?",
        )
        == "LAX"
    )


def test_hourly_contract_projection_uses_exact_hour_members(monkeypatch):
    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(wm, "_load_weather_snapshot", lambda *args, **kwargs: {"loaded_series": 0})
    monkeypatch.setattr(
        wm,
        "_WEATHER_SHADOW_STATE",
        {
            "KXTEMPNYCH": {
                "timestamp": time.time(),
                "provider_mode": "ensemble_members",
                "forecast_source": "test",
                "hourly_time": [
                    "2024-01-01T21:00",
                    "2024-01-01T22:00",
                    "2024-01-01T23:00",
                ],
                "hourly_members_temp_f": {
                    "m1": [70.0, 76.0, 72.0],
                    "m2": [71.0, 78.0, 73.0],
                },
                "hourly_members_precip_in": {"m1": [0.0, 0.0, 0.0], "m2": [0.0, 0.0, 0.0]},
                "hourly_members_cloud": {"m1": [20.0, 10.0, 30.0], "m2": [15.0, 12.0, 32.0]},
                "hourly_members_ssrd": {"m1": [0.0, 0.0, 0.0], "m2": [0.0, 0.0, 0.0]},
            }
        },
        raising=False,
    )

    projected = wm.get_contract_weather_data(
        "KXTEMPNYCH-24JAN0122-T75.99",
        contract_name="Will the temp in New York City be above 75.99° on Jan 1, 2024 at 10pm EST?",
        strike=75.99,
    )

    assert projected["target_local_hour"] == 22
    assert projected["members_temp"] == [76.0, 78.0]
    assert projected["mean_temp"] == 77.0


def test_hourly_observed_truth_uses_archive_fetch(monkeypatch):
    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(wm, "_load_weather_snapshot", lambda *args, **kwargs: {"loaded_series": 0})
    monkeypatch.setattr(
        wm,
        "_WEATHER_SHADOW_STATE",
        {"KXTEMPNYCH": {"timestamp": time.time(), "intraday": {}}},
        raising=False,
    )
    monkeypatch.setattr(
        wm,
        "_fetch_open_meteo_archive_hourly_temp",
        lambda *args, **kwargs: {
            "city_key": "NY",
            "target_local_date": "2024-01-01",
            "target_local_hour": 22,
            "observed_temp": 73.5,
            "source": "open_meteo_archive_hourly",
        },
        raising=False,
    )

    observed = wm.get_contract_observed_weather_data(
        "KXTEMPNYCH-24JAN0122-T75.99",
        contract_name="Will the temp in New York City be above 75.99° on Jan 1, 2024 at 10pm EST?",
        strike=75.99,
    )

    assert observed["observed_temp"] == 73.5
    assert observed["source"] == "open_meteo_archive_hourly"


def test_hourly_resolution_supports_temp_contracts():
    from forecast.resolution_sync import determine_weather_resolution

    resolution = determine_weather_resolution(
        ticker="KXTEMPNYCH-24JAN0122-T75.99",
        observed_high=None,
        observed_low=None,
        observed_precip=None,
        observed_temp=77.0,
        contract_name="Will the temp in New York City be above 75.99° on Jan 1, 2024 at 10pm EST?",
        strike=75.99,
    )

    assert resolution is not None
    assert resolution[0] == "YES"
