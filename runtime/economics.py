"""Forecast-lane economics helpers backed by the live Kalshi fee model."""

from __future__ import annotations

from dataclasses import dataclass

from config import (
    KALSHI_MAKER_FEE_RATE,
    KALSHI_TAKER_FEE_RATE,
    estimate_kalshi_fee_per_contract,
)


@dataclass(frozen=True)
class LaneEconomics:
    lane_id: str
    taker_fee_pct: float
    maker_fee_pct: float
    round_trip_cost_pct: float

    @property
    def min_viable_edge_pct(self) -> float:
        return self.round_trip_cost_pct


def _forecast_lane_economics() -> LaneEconomics:
    # Use a midpoint contract as the canonical fee reference for runtime summaries.
    midpoint_price = 0.50
    round_trip_cost_pct = 2.0 * estimate_kalshi_fee_per_contract(
        midpoint_price,
        rounded=False,
    )
    return LaneEconomics(
        lane_id="forecast",
        taker_fee_pct=float(KALSHI_TAKER_FEE_RATE),
        maker_fee_pct=float(KALSHI_MAKER_FEE_RATE),
        round_trip_cost_pct=float(round_trip_cost_pct),
    )


def get_lane_economics(lane_id: str) -> LaneEconomics:
    if str(lane_id or "").lower() == "forecast":
        return _forecast_lane_economics()
    return LaneEconomics(
        lane_id=lane_id,
        taker_fee_pct=float(KALSHI_TAKER_FEE_RATE),
        maker_fee_pct=float(KALSHI_MAKER_FEE_RATE),
        round_trip_cost_pct=0.0,
    )


def is_trade_viable(lane_id: str, expected_edge_pct: float) -> bool:
    return float(expected_edge_pct) >= get_lane_economics(lane_id).round_trip_cost_pct
