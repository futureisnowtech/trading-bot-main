#!/usr/bin/env python3
"""Non-trading provider -> probability -> sizing -> order-plan proof."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, time as clock_time
from pathlib import Path

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data import kalshi_weather_monitor as weather_monitor  # noqa: E402
from execution.kalshi_execution_controller import (  # noqa: E402
    KalshiExecutionController,
    TradeIntent,
)
from forecast.pricing_engine import calculate_pricing  # noqa: E402
from forecast.strategy_engine import evaluate_contract  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="CHI")
    parser.add_argument("--bankroll", type=float, default=100.0)
    return parser.parse_args()


class _ReadOnlyBroker:
    def __init__(self, quote: dict) -> None:
        self._quote = quote

    def get_quote(self, _ticker: str) -> dict:
        return dict(self._quote)

    def place_buy_order(self, *args, **kwargs):  # pragma: no cover - safety tripwire
        raise AssertionError("non-trading proof must never submit an order")


def run_proof(city: str, bankroll: float) -> dict:
    city_key = str(city or "").strip().upper()
    station = weather_monitor.STATIONS.get(city_key)
    if not station:
        raise RuntimeError(f"unknown city key {city_key!r}")

    raw = asyncio.run(
        weather_monitor.fetch_deterministic_weather_models(
            city_key,
            float(station["lat"]),
            float(station["lon"]),
        )
    )
    if not raw:
        raise RuntimeError("deterministic provider returned no GFS-led bundle")

    target_date = sorted(
        {
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
            for value in raw.get("hourly_time", [])
        }
    )[1]
    projected = weather_monitor._project_contract_record(
        raw,
        target_date,
        timezone_name=str(station["tz"]),
    )
    physical_values = list(projected.get("members_high") or []) + list(
        (projected.get("ecmwf") or {}).get("members_high") or []
    )
    if len(physical_values) != 2:
        raise RuntimeError(f"expected one GFS and one ECMWF value, got {physical_values}")
    # The proof quote must exercise an actually admissible physical edge, not a
    # coin-flip threshold. Put the HIGH strike safely below deterministic
    # consensus so the production 2°F headroom rail is expected to pass.
    strike = round((sum(physical_values) / len(physical_values)) - 4.0, 1)
    ticker = f"KXHIGH{city_key}-{target_date.strftime('%y%b%d').upper()}-T{strike:g}"
    contract_name = (
        f"Will the high temperature in {station['name']} be above {strike:g}° "
        f"on {target_date.strftime('%b %d, %Y')}?"
    )
    local_tz = pytz.timezone(str(station["tz"]))
    expiry_local = local_tz.localize(datetime.combine(target_date, clock_time(23, 59)))
    expiry_utc = expiry_local.astimezone(pytz.UTC).isoformat()

    pricing = calculate_pricing(
        ticker,
        projected,
        max(0.0, (expiry_local.astimezone(pytz.UTC) - datetime.now(pytz.UTC)).total_seconds() / 3600.0),
        contract_name=contract_name,
        strike=strike,
    )
    q_hat = float(pricing["q_hat"])
    if q_hat >= 0.50:
        ask_yes, ask_no = 0.35, 0.65
    else:
        ask_yes, ask_no = 0.65, 0.35
    now = datetime.now(pytz.UTC).isoformat()
    yes_quote = {
        "bid": ask_yes - 0.02,
        "ask": ask_yes,
        "mid": ask_yes - 0.01,
        "spread": 0.02,
        "ask_size": 25,
        "ts": now,
    }
    no_quote = {
        "bid": ask_no - 0.02,
        "ask": ask_no,
        "mid": ask_no - 0.01,
        "spread": 0.02,
        "ask_size": 25,
        "ts": now,
    }

    # Process-local hydration only. This proof never writes the shared snapshot.
    for series in station.get("series", []):
        weather_monitor._WEATHER_SHADOW_STATE[str(series)] = raw
    result = evaluate_contract(
        contract={
            "id": 1,
            "market_id": 1,
            "local_symbol": ticker,
            "contract_name": contract_name,
            "right": "C",
            "strike": strike,
            "last_trade_at": expiry_utc,
            "resolution_at": expiry_utc,
        },
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
        yes_quote=yes_quote,
        no_quote=no_quote,
        bankroll=float(bankroll),
    )
    if result is None or not result.econ_approved or result.position_contracts <= 0:
        raise RuntimeError(
            f"strategy failed proof quote: {getattr(result, 'veto_reason', 'no_result')}"
        )

    chosen_right = "C" if result.side == "YES" else "P"
    live_quote = {
        "yes_bid": ask_yes - 0.02,
        "yes_ask": ask_yes,
        "yes_ask_size": 25,
        "yes_bid_size": 25,
        "no_bid": ask_no - 0.02,
        "no_ask": ask_no,
        "no_ask_size": 25,
        "no_bid_size": 25,
    }
    plan = KalshiExecutionController(_ReadOnlyBroker(live_quote)).plan_entry(
        TradeIntent(
            contract={
                "local_symbol": ticker,
                "right": chosen_right,
                "strike": strike,
                "last_trade_at": expiry_utc,
            },
            result=result,
            bankroll=float(bankroll),
            buying_power_usd=float(bankroll),
        )
    )
    if plan.status != "ready" or plan.executable_qty <= 0:
        raise RuntimeError(f"order-plan proof failed: {plan.reason or plan.status}")

    return {
        "status": "PASS",
        "trading_enabled": False,
        "provider_mode": raw.get("provider_mode"),
        "model_path": pricing.get("model_path"),
        "ticker": ticker,
        "q_hat": q_hat,
        "strategy_q_hat": result.q_hat,
        "chosen_side_probability": result.confidence,
        "chosen_side": result.side,
        "strategy_family": result.strategy_family,
        "sized_contracts": result.position_contracts,
        "fee_inclusive_position_fraction": result.position_fraction,
        "order_plan_status": plan.status,
        "order_plan_qty": plan.executable_qty,
        "order_plan_price": plan.limit_price,
        "top_factors": result.top_factors,
    }


def main() -> int:
    args = _args()
    try:
        payload = run_proof(args.city, args.bankroll)
    except Exception as exc:
        payload = {"status": "FAIL", "trading_enabled": False, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
