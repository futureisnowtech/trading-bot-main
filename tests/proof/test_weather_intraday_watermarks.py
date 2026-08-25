from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_intraday_payload_tracks_daily_watermarks_by_city_day(proof_runtime, monkeypatch):
    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(
        wm.time,
        "time",
        lambda: datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc).timestamp(),
    )

    watermarks: dict[str, float] = {}

    payload_1 = wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "KLAX 251200Z 25006KT 10SM FEW012 18/14 A2992 RMK AO2 P0000"},
        {"hrrr_high": 74.0, "hrrr_trend": "rising"},
        watermarks=watermarks,
    )
    payload_2 = wm._intraday_payload(
        "LAX",
        {"temp_f": 65.0, "raw": "KLAX 251300Z 25008KT 10SM FEW012 16/13 A2991 RMK AO2 P0012"},
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


def test_intraday_payload_deduplicates_metar_hourly_precip_by_observation_time(
    proof_runtime,
    monkeypatch,
):
    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(
        wm.time,
        "time",
        lambda: datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc).timestamp(),
    )

    watermarks: dict[str, float] = {}
    metar = {
        "temp_f": 67.0,
        "raw": "KLAX 251300Z 25008KT 10SM FEW012 16/13 A2991 RMK AO2 P0012",
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


def test_intraday_payload_expires_old_metar_temperature_derivative(proof_runtime, monkeypatch):
    import data.kalshi_weather_monitor as wm

    now = {
        "value": datetime(
            2026, 8, 25, 13, 0, tzinfo=timezone.utc
        ).timestamp()
    }
    monkeypatch.setattr(wm.time, "time", lambda: now["value"])
    monkeypatch.setattr(wm, "METAR_TREND_MAX_AGE_SEC", 90 * 60)
    watermarks: dict[str, float] = {}

    wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "KLAX 251200Z 25006KT 10SM CLR 18/14 A2992"},
        {},
        watermarks=watermarks,
    )
    fresh = wm._intraday_payload(
        "LAX",
        {"temp_f": 68.0, "raw": "KLAX 251300Z 25006KT 10SM CLR 17/14 A2992"},
        {},
        watermarks=watermarks,
    )
    assert fresh["metar_temp_trend_f_per_hr"] == pytest.approx(-2.0)
    assert fresh["metar_temp_trend_age_seconds"] == 0.0

    now["value"] += 90 * 60 + 1
    stale = wm._intraday_payload(
        "LAX",
        {"temp_f": 68.0, "raw": "KLAX 251300Z 25006KT 10SM CLR 17/14 A2992"},
        {},
        watermarks=watermarks,
    )
    assert stale["metar_temp_trend_f_per_hr"] is None
    assert stale["metar_temp_trend_age_seconds"] > 90 * 60


def test_intraday_derivative_orders_observations_across_utc_month_rollover(
    proof_runtime, monkeypatch
):
    import data.kalshi_weather_monitor as wm

    now = datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(wm.time, "time", lambda: now)
    watermarks: dict[str, float] = {}

    wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "KLAX 312300Z 25006KT 10SM CLR 18/14 A2992"},
        {},
        watermarks=watermarks,
    )
    payload = wm._intraday_payload(
        "LAX",
        {"temp_f": 68.0, "raw": "KLAX 010000Z 25006KT 10SM CLR 17/14 A2992"},
        {},
        watermarks=watermarks,
    )

    assert payload["metar_temp_trend_f_per_hr"] == pytest.approx(-2.0)
    assert payload["metar_temp_trend_age_seconds"] == pytest.approx(5 * 60)


def test_stale_metar_fetch_cannot_reset_derivative_freshness(
    proof_runtime, monkeypatch
):
    import data.kalshi_weather_monitor as wm

    now = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(wm.time, "time", lambda: now)
    monkeypatch.setattr(wm, "METAR_TREND_MAX_AGE_SEC", 90 * 60)
    watermarks: dict[str, float] = {}

    wm._intraday_payload(
        "LAX",
        {"temp_f": 70.0, "raw": "KLAX 251200Z 25006KT 10SM CLR 18/14 A2992"},
        {},
        watermarks=watermarks,
    )
    payload = wm._intraday_payload(
        "LAX",
        {"temp_f": 68.0, "raw": "KLAX 251300Z 25006KT 10SM CLR 17/14 A2992"},
        {},
        watermarks=watermarks,
    )

    assert payload["metar_temp_trend_f_per_hr"] is None
    assert payload["metar_temp_trend_age_seconds"] == pytest.approx(2 * 3600)


def test_metar_observation_day_far_from_fetch_time_is_rejected(proof_runtime):
    import data.kalshi_weather_monitor as wm

    reference = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc).timestamp()
    assert wm._parse_metar_observation_key(
        "KLAX 101300Z 25006KT 10SM CLR 17/14 A2992",
        reference_ts=reference,
    ) is None
