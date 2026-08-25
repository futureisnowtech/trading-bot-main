#!/usr/bin/env python3
"""Exercise the deterministic production weather probability path."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.kalshi_weather_monitor import (  # noqa: E402
    STATIONS,
    fetch_deterministic_weather_models,
    _project_contract_record,
)
from forecast.pricing_engine import calculate_pricing  # noqa: E402
from forecast.db import init_forecast_db  # noqa: E402
from forecast.strategy_engine import _convergence_guardrail  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="CHI", help="Versioned station key (default: CHI)")
    return parser.parse_args()


def _complete_forecast_date(record: dict) -> date:
    dates = sorted(
        {
            datetime.fromisoformat(str(raw_time).replace("Z", "+00:00")).date()
            for raw_time in record.get("hourly_time", [])
        }
    )
    if len(dates) < 2:
        raise RuntimeError("forecast response did not include a complete future day")
    return dates[1]


def run_probe(city: str) -> dict:
    city_key = str(city or "").strip().upper()
    station = STATIONS.get(city_key)
    if not station:
        raise RuntimeError(f"unknown city key {city_key!r}")

    combined = asyncio.run(
        fetch_deterministic_weather_models(
            city_key,
            float(station["lat"]),
            float(station["lon"]),
        )
    )
    if not combined:
        raise RuntimeError("deterministic provider returned no production record")

    target_date = _complete_forecast_date(combined)
    projected = _project_contract_record(
        combined,
        target_date,
        timezone_name=str(station["tz"]),
    )
    if not projected:
        raise RuntimeError("contract-date projection returned no record")

    model_records = {
        "gfs": projected,
        "ecmwf": projected.get("ecmwf") or {},
        "aigefs": projected.get("aigefs") or {},
    }
    observed_counts = {
        model_key: len(record.get("members_high") or [])
        for model_key, record in model_records.items()
    }
    if observed_counts != {"gfs": 1, "ecmwf": 1, "aigefs": 1}:
        raise RuntimeError(f"deterministic-model count mismatch: {observed_counts}")

    physical_members = (
        list(model_records["gfs"]["members_high"])
        + list(model_records["ecmwf"]["members_high"])
    )
    strike = round(float(statistics.median(physical_members)), 1)
    with tempfile.TemporaryDirectory(prefix="kalshi-probability-probe-") as temp_dir:
        probe_db_path = str(Path(temp_dir) / "probe.db")
        init_forecast_db(probe_db_path)
        pricing = calculate_pricing(
            f"KXHIGH{city_key}-PROBE-T{strike:g}",
            projected,
            24.0,
            contract_name=f"Will the high temperature in {station['name']} be above {strike:g}°?",
            strike=strike,
            db_path=probe_db_path,
        )

    guardrail = _convergence_guardrail(
        pricing.get("q_gfs"),
        pricing.get("q_ecmwf"),
    )
    weights = {
        model: float(pricing[f"{model}_weight"])
        for model in ("gfs", "ecmwf", "hrrr")
    }
    required_probabilities = {
        model: pricing.get(f"q_{model}")
        for model in ("gfs", "ecmwf", "aigfs")
    }
    if any(value is None for value in required_probabilities.values()):
        raise RuntimeError(f"missing model probability: {required_probabilities}")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise RuntimeError(f"blend weights do not sum to one: {weights}")

    return {
        "status": "PASS",
        "trading_enabled": False,
        "provider_authority": "keyless_deterministic_production",
        "city": city_key,
        "target_local_date": target_date.isoformat(),
        "member_counts": observed_counts,
        "strike_f": strike,
        "probabilities": required_probabilities,
        "q_hat_raw": pricing["q_hat_raw"],
        "q_hat": pricing["q_hat"],
        "weights": weights,
        "aigfs_lambda": pricing["lambda_scaler"],
        "predictive_sigma_f": {
            "gfs": projected.get("sigma_high"),
            "ecmwf": (projected.get("ecmwf") or {}).get("sigma_high"),
        },
        "physics_adjustment_f": {
            "gfs": (pricing.get("physics_gfs") or {}).get("adjustment_f"),
            "ecmwf": (pricing.get("physics_ecmwf") or {}).get("adjustment_f"),
        },
        "convergence_guardrail": guardrail,
        "model_path": pricing["model_path"],
    }


def main() -> int:
    args = _parse_args()
    try:
        result = run_probe(args.city)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "trading_enabled": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
