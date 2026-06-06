from __future__ import annotations


def test_intraday_payload_tracks_daily_watermarks_by_city_day():
    import data.kalshi_weather_monitor as wm

    watermarks: dict[str, float] = {}

    payload_1 = wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "KLAX 061200Z 25006KT 10SM FEW012 18/14 A2992 RMK AO2 P0000"},
        {"hrrr_high": 74.0, "hrrr_trend": "rising"},
        watermarks=watermarks,
    )
    payload_2 = wm._intraday_payload(
        "LAX",
        {"temp_f": 65.0, "raw": "KLAX 061300Z 25008KT 10SM FEW012 16/13 A2991 RMK AO2 P0012"},
        {"hrrr_high": 72.0, "hrrr_trend": "falling"},
        watermarks=watermarks,
    )

    city_day = wm._station_local_day("LAX")
    assert watermarks[f"LAX|{city_day}|max"] == 70.0
    assert watermarks[f"LAX|{city_day}|min"] == 65.0
    assert payload_1["daily_max"] == 70.0
    assert payload_1["daily_min"] == 70.0
    assert payload_2["daily_max"] == 70.0
    assert payload_2["daily_min"] == 65.0
    assert payload_1["daily_precip"] == 0.0
    assert payload_2["daily_precip"] == 0.12
    assert watermarks[f"LAX|{city_day}|precip_total"] == 0.12


def test_intraday_payload_deduplicates_metar_hourly_precip_by_observation_time():
    import data.kalshi_weather_monitor as wm

    watermarks: dict[str, float] = {}
    metar = {
        "temp_f": 67.0,
        "raw": "KLAX 061300Z 25008KT 10SM FEW012 16/13 A2991 RMK AO2 P0012",
    }

    payload_1 = wm._intraday_payload(
        "LAX",
        metar,
        {"hrrr_high": 72.0, "hrrr_trend": "falling"},
        watermarks=watermarks,
    )
    payload_2 = wm._intraday_payload(
        "LAX",
        metar,
        {"hrrr_high": 72.0, "hrrr_trend": "falling"},
        watermarks=watermarks,
    )

    assert payload_1["daily_precip"] == 0.12
    assert payload_2["daily_precip"] == 0.12
