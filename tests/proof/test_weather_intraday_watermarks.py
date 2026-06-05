from __future__ import annotations


def test_intraday_payload_tracks_daily_watermarks_by_city_day():
    import data.kalshi_weather_monitor as wm

    watermarks: dict[str, float] = {}

    payload_1 = wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "obs-1"},
        {"hrrr_high": 74.0, "hrrr_trend": "rising"},
        watermarks=watermarks,
    )
    payload_2 = wm._intraday_payload(
        "LAX",
        {"temp_f": 65.0, "raw": "obs-2"},
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
