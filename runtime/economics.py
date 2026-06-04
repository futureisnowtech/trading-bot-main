"""Forecast-lane economics helpers used by runtime proof tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LaneEconomics:
    lane_id: str
    taker_fee_pct: float
    maker_fee_pct: float
    round_trip_cost_pct: float

    @property
    def min_viable_edge_pct(self) -> float:
        return self.round_trip_cost_pct


_LANE_ECONOMICS = {
    "forecast": LaneEconomics(
        lane_id="forecast",
        taker_fee_pct=0.0,
        maker_fee_pct=0.0,
        round_trip_cost_pct=0.0,
    ),
}


def get_lane_economics(lane_id: str) -> LaneEconomics:
    return _LANE_ECONOMICS.get(
        lane_id,
        LaneEconomics(
            lane_id=lane_id,
            taker_fee_pct=0.0,
            maker_fee_pct=0.0,
            round_trip_cost_pct=0.0,
        ),
    )


def is_trade_viable(lane_id: str, expected_edge_pct: float) -> bool:
    return float(expected_edge_pct) >= get_lane_economics(lane_id).round_trip_cost_pct
