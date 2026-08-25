"""Proof that every fetched weather model shapes the production decision path."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import json
import sqlite3

import pytest

from data import kalshi_weather_monitor as wm
from forecast import pricing_engine as pe
from forecast import strategy_engine as se
from intelligence.schema import connect, init_intelligence_db


def _high_weather(*, aigfs: float | None = 79.5) -> dict:
    weather = {
        "members_high": [79.0, 79.5, 80.0] * 11,
        "ecmwf": {"members_high": [78.5, 79.0, 79.5] * 17},
    }
    if aigfs is not None:
        weather["aigefs"] = {"members_high": [aigfs]}
    return weather


def _price(weather: dict, tmp_path) -> dict:
    return pe.calculate_pricing(
        "KXHIGHCHI-26AUG25-T75",
        weather,
        24.0,
        contract_name="Will the high temperature in Chicago be above 75°?",
        strike=75.0,
        db_path=str(tmp_path / "no_history.db"),
    )


@pytest.mark.parametrize(
    ("model_name", "expected_key", "member_count"),
    [
        ("gfs_seamless", "gfs", 31),
        ("ecmwf_ifs025", "ecmwf", 51),
        ("ncep_aigfs025", "aigefs", 1),
    ],
)
def test_provider_keys_and_member_counts_are_real(model_name, expected_key, member_count):
    assert wm._weather_model_key(model_name) == expected_key
    hourly = {"time": ["2026-08-25T12:00"]}
    for index in range(member_count):
        suffix = "" if index == 0 else f"_member{index:02d}"
        hourly[f"temperature_2m{suffix}"] = [20.0 + index / 100.0]

    record = wm._build_weather_record_from_hourly(
        hourly,
        model_name,
        deterministic=False,
        forecast_source="proof",
    )
    assert len(record["members_high"]) == member_count


def test_provider_parser_preserves_time_alignment_across_null_hours():
    hourly = {
        "time": ["2026-08-25T00:00", "2026-08-25T01:00", "2026-08-25T02:00"],
        "temperature_2m": [None, 20.0, 21.0],
        "precipitation": [None, 0.0, 1.0],
        "cloud_cover": [None, 10.0, 20.0],
        "shortwave_radiation": [None, 0.0, 100.0],
        "wind_speed_10m": [None, 5.0, 10.0],
    }
    record = wm._build_weather_record_from_hourly(
        hourly,
        "ncep_aigfs025",
        deterministic=False,
        forecast_source="proof",
    )
    assert record["hourly_members_temp_f"]["member_00"][0] is None
    projected = wm._project_contract_record(
        record,
        date(2026, 8, 25),
        timezone_name="America/Chicago",
    )
    assert len(projected["members_high"]) == 1


def test_contract_projection_purges_stale_commercial_icon():
    def hourly_record(model_name: str) -> dict:
        return {
            "model_name": model_name,
            "provider_mode": "ensemble_members",
            "timestamp": 1.0,
            "hourly_time": ["2026-08-25T12:00"],
            "hourly_members_temp_f": {"member_00": [80.0]},
            "hourly_members_precip_in": {"member_00": [0.0]},
            "hourly_members_cloud": {"member_00": [0.0]},
            "hourly_members_ssrd": {"member_00": [500.0]},
            "hourly_members_wind": {"member_00": [5.0]},
        }

    record = hourly_record("gfs_seamless")
    record["icon"] = hourly_record("icon_seamless")
    projected = wm._project_contract_record(
        record,
        date(2026, 8, 25),
        target_hour=12,
        timezone_name="America/Chicago",
    )
    assert projected["icon"] is None


def test_stale_icon_payload_cannot_change_production_probability(tmp_path):
    baseline_weather = _high_weather(aigfs=None)
    stale_icon_weather = dict(baseline_weather)
    stale_icon_weather["icon"] = {"members_high": [50.0] * 40}

    baseline = _price(baseline_weather, tmp_path)
    with_stale_icon = _price(stale_icon_weather, tmp_path)

    assert "icon_weight" not in with_stale_icon
    assert "q_icon" not in with_stale_icon
    assert with_stale_icon["q_hat"] == pytest.approx(baseline["q_hat"])
    assert with_stale_icon["model_path"] == "deterministic_gfs_ecmwf_aigfs_hrrr_physics"
    assert with_stale_icon["physics_method"] == "bounded_heuristic_v1"
    assert with_stale_icon["physics_validation_status"] == "learning_epoch_pending_outcomes"


def test_aigfs_actual_forecast_controls_uncertainty(tmp_path):
    agreement = _price(_high_weather(aigfs=79.5), tmp_path)
    disagreement = _price(_high_weather(aigfs=65.0), tmp_path)

    assert agreement["q_aigfs"] is not None
    assert agreement["lambda_scaler"] < 1.0
    assert disagreement["lambda_scaler"] > 1.0
    assert disagreement["lambda_scaler"] > agreement["lambda_scaler"]


def test_temperature_physics_operates_in_forecast_space_and_is_mode_safe(monkeypatch):
    monkeypatch.setattr(pe, "PHYSICS_DELTA_ENABLED", True)
    forcing = {
        "mean_precip": 0.35,
        "mean_wind": 18.0,
        "peak_tcdc": 90.0,
        "peak_ssrd": 120.0,
    }

    high_members, high = pe.apply_temperature_physics([80.0], forcing, "HIGH", 24.0)
    low_members, low = pe.apply_temperature_physics([60.0], forcing, "LOW", 24.0)
    rain_members, rain = pe.apply_temperature_physics([0.35], forcing, "RAIN", 24.0)

    assert -2.5 <= high["adjustment_f"] < 0.0
    assert high_members[0] == pytest.approx(80.0 + high["adjustment_f"])
    assert 0.0 < low["adjustment_f"] <= 2.5
    assert low_members[0] == pytest.approx(60.0 + low["adjustment_f"])
    assert rain["adjustment_f"] == 0.0
    assert rain_members == [0.35]


def test_single_member_probability_uses_projected_predictive_sigma():
    semantics = SimpleNamespace(
        comparator="gt",
        threshold=75.0,
        lower_bound=None,
        upper_bound=None,
        display_high=None,
        display_low=None,
    )
    narrow = pe.kernel_smoothed_probability([76.0], semantics, predictive_sigma=0.5)
    wide = pe.kernel_smoothed_probability([76.0], semantics, predictive_sigma=3.0)

    assert narrow > wide > 0.5


def test_nested_hrrr_forecast_reaches_near_term_blend(tmp_path):
    weather = _high_weather(aigfs=79.5)
    weather["intraday"] = {"hrrr_high": 82.0, "hrrr_trend": "rising"}
    priced = pe.calculate_pricing(
        "KXHIGHCHI-26AUG25-T75",
        weather,
        6.0,
        contract_name="Will the high temperature in Chicago be above 75°?",
        strike=75.0,
        db_path=str(tmp_path / "no_history.db"),
    )

    assert priced["q_hrrr"] is not None
    assert priced["hrrr_weight"] > 0.0


def test_convergence_and_uncertainty_multipliers_reach_contract_quantity():
    kwargs = {
        "market_price": 0.40,
        "model_prob": 0.55,
        "capital_base": 50.0,
        "cap_pct": 0.12,
        "position_cap_usd": 100.0,
    }
    uncertain = se.calculate_continuous_sizing(multiplier=0.60, **kwargs)
    neutral = se.calculate_continuous_sizing(multiplier=1.00, **kwargs)
    converged = se.calculate_continuous_sizing(multiplier=1.50, **kwargs)

    assert 0 < uncertain < neutral < converged
    assert se.estimate_kalshi_order_cost_usd(converged, 0.40, maker=False) <= 6.0


def test_promoted_rbi_weights_reach_pricing_engine(tmp_path):
    db_path = str(tmp_path / "rbi.db")
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE intelligence_model_artifacts SET weights_json=? WHERE status='champion'",
            (json.dumps({"GLOBAL": {"gfs": 0.30, "ecmwf": 0.70}}),),
        )
        conn.commit()

    assert pe.calculate_brier_weights("HIGH", 2, db_path) == {
        "gfs": pytest.approx(0.30),
        "ecmwf": pytest.approx(0.70),
    }


def test_dynamic_sizing_override_reads_sqlite_instead_of_silently_falling_back(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dynamic.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE dynamic_system_config (param_key TEXT PRIMARY KEY, param_value REAL)"
        )
        conn.execute(
            "INSERT INTO dynamic_system_config (param_key, param_value) VALUES ('KELLY_FRACTION', 0.10)"
        )
        conn.commit()

    monkeypatch.setattr(se, "DB_PATH", db_path)
    assert se.get_dynamic_param("KELLY_FRACTION", 0.25) == pytest.approx(0.10)


def test_convergence_guardrail_covers_both_physical_models():
    converged = se._convergence_guardrail(0.82, 0.84)
    assert converged["convergence_multiplier"] == 1.5
    assert converged["divergence_size_multiplier"] == 1.0
    assert converged["catastrophic_divergence"] is False

    disagreement = se._convergence_guardrail(0.90, 0.61)
    assert disagreement["convergence_multiplier"] == 1.0
    assert 0.60 <= disagreement["divergence_size_multiplier"] < 1.0
    assert 0.55 <= disagreement["confidence_scale"] < 1.0

    catastrophic = se._convergence_guardrail(0.99, 0.01)
    assert catastrophic["catastrophic_divergence"] is True


def test_threshold_probability_path_survives_dead_helper_removal():
    gt = SimpleNamespace(comparator="gt", threshold=75.0, lower_bound=None, upper_bound=None)
    lt = SimpleNamespace(comparator="lt", threshold=75.0, lower_bound=None, upper_bound=None)
    assert se._probability_from_estimate(77.0, 1.0, gt) > 0.95
    assert se._probability_from_estimate(73.0, 1.0, lt) > 0.95


def test_production_pricing_branch_enforces_catastrophic_veto(monkeypatch):
    weather = {"members_high": [80.0], "ecmwf": {"members_high": [70.0]}}
    monkeypatch.setattr(se, "get_contract_weather_data", lambda *args, **kwargs: weather)

    def fake_pricing(*args, **kwargs):
        return {
            "q_hat": 0.75,
            "q_gfs": 0.99,
            "q_ecmwf": 0.01,
            "q_aigfs": 0.60,
            "q_hrrr": None,
            "lambda_scaler": 1.0,
            "gfs_weight": 0.51,
            "ecmwf_weight": 0.34,
            "hrrr_weight": 0.0,
        }

    monkeypatch.setattr(pe, "calculate_pricing", fake_pricing)
    details = se._strategy_weather_details(
        ticker="KXHIGHCHI-26AUG25-T75",
        ask_yes=0.40,
        ask_no=0.60,
        hours_to_res=24.0,
        contract_name="Will the high temperature in Chicago be above 75°?",
        strike=75.0,
    )
    passes, _, _, factors, *_ = details
    assert passes is False
    assert factors == ["catastrophic_divergence_veto (gap=98.00%)"]
    # Catastrophic disagreement is still a hard trade veto, but its complete
    # priced opportunity must remain available to the governed RBI review.
    assert details[8]["q_gfs"] == pytest.approx(0.99)
    assert details[8]["q_ecmwf"] == pytest.approx(0.01)
    assert details[8]["q_decision_guarded"] == pytest.approx(0.6375)
