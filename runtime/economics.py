"""
runtime/economics.py — Unified lane-aware economics/friction interface.

Each lane exposes: taker_fee_pct, min_viable_edge_pct, round_trip_cost_pct
Dashboard and validator can query per-lane economics status.
"""

import os
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@dataclass(frozen=True)
class LaneEconomics:
    lane_id: str
    taker_fee_pct: float
    maker_fee_pct: float
    round_trip_cost_pct: float
    min_viable_edge_pct: float
    spread_gate_bps: float
    notes: str

    @property
    def taker_fee_bps(self) -> float:
        return self.taker_fee_pct * 10_000

    @property
    def round_trip_bps(self) -> float:
        return self.round_trip_cost_pct * 10_000

    @property
    def min_edge_bps(self) -> float:
        return self.min_viable_edge_pct * 10_000


# Pre-populated lane economics
LANE_ECONOMICS: dict[str, LaneEconomics] = {
    "crypto": LaneEconomics(
        lane_id="crypto",
        taker_fee_pct=0.0003,       # 0.030% Coinbase taker
        maker_fee_pct=0.0000,       # 0.000% Coinbase maker
        round_trip_cost_pct=0.0006, # 0.060% round-trip (taker in + taker out)
        min_viable_edge_pct=0.008,  # 0.80% minimum edge after fees
        spread_gate_bps=25.0,       # 25 bps spread gate (economics_gate.py)
        notes="Coinbase nano futures; taker=0.03%, maker=0.00%; round-trip=0.06%",
    ),
    "forecast": LaneEconomics(
        lane_id="forecast",
        taker_fee_pct=0.0,
        maker_fee_pct=0.0,
        round_trip_cost_pct=0.0,
        min_viable_edge_pct=0.05,   # 5% min edge on binary yes/no
        spread_gate_bps=0.0,
        notes="ForecastEx binary options, zero commission; min edge = bid/ask spread quality",
    ),
    "mes_archived": LaneEconomics(
        lane_id="mes_archived",
        taker_fee_pct=0.0,
        maker_fee_pct=0.0,
        round_trip_cost_pct=0.0,
        min_viable_edge_pct=0.0,
        spread_gate_bps=0.0,
        notes="ARCHIVED — MES dormant; economics not applicable",
    ),
}


def get_lane_economics(lane_id: str) -> LaneEconomics:
    """
    Returns LaneEconomics for the given lane_id.
    Falls back to a zero-cost unknown entry if lane is not registered.
    """
    if lane_id in LANE_ECONOMICS:
        return LANE_ECONOMICS[lane_id]
    # Unknown lane — return a safe zero-cost sentinel
    return LaneEconomics(
        lane_id=lane_id,
        taker_fee_pct=0.0,
        maker_fee_pct=0.0,
        round_trip_cost_pct=0.0,
        min_viable_edge_pct=0.0,
        spread_gate_bps=0.0,
        notes=f"Unknown lane '{lane_id}' — economics not configured",
    )


def get_all_economics() -> dict:
    """Returns a copy of LANE_ECONOMICS dict."""
    return dict(LANE_ECONOMICS)


def is_trade_viable(lane_id: str, expected_edge_pct: float) -> bool:
    """
    Returns True if expected_edge_pct >= lane's min_viable_edge_pct.
    Always returns True for lanes with min_viable_edge_pct == 0.0
    (i.e. archived / unknown lanes where gate is not applicable).
    """
    econ = get_lane_economics(lane_id)
    if econ.min_viable_edge_pct == 0.0:
        return True
    return expected_edge_pct >= econ.min_viable_edge_pct
