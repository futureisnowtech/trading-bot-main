"""
runtime/allocator.py — Cross-lane capital allocation substrate.

SCAFFOLD: Interface defined, full logic TBD in v16.0.
Provides clean interface for opportunity ranking across lanes.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@dataclass
class LaneBudget:
    lane_id: str
    max_deployed_usd: float
    current_deployed_usd: float
    max_concurrent_positions: int
    current_positions: int
    buying_power_usd: float = field(default=0.0)

    @property
    def available_usd(self) -> float:
        return max(0.0, self.max_deployed_usd - self.current_deployed_usd)

    @property
    def position_slots_available(self) -> int:
        return max(0, self.max_concurrent_positions - self.current_positions)


class GlobalAllocator:
    """
    Cross-lane capital allocator.

    SCAFFOLD — full cross-lane logic planned for v16.0.
    Currently provides per-lane budget tracking and a
    stub opportunity ranker (sorts by 'ev' field descending).
    """

    def __init__(self):
        self._budgets: dict[str, LaneBudget] = {}

    def register_lane_budget(
        self,
        lane_id: str,
        max_deployed_usd: float,
        max_concurrent_positions: int,
    ) -> None:
        """Register or reset a lane budget."""
        existing = self._budgets.get(lane_id)
        if existing is not None:
            existing.max_deployed_usd = max_deployed_usd
            existing.max_concurrent_positions = max_concurrent_positions
        else:
            self._budgets[lane_id] = LaneBudget(
                lane_id=lane_id,
                max_deployed_usd=max_deployed_usd,
                current_deployed_usd=0.0,
                max_concurrent_positions=max_concurrent_positions,
                current_positions=0,
                buying_power_usd=max_deployed_usd,
            )

    def update_lane_deployed(
        self,
        lane_id: str,
        deployed_usd: float,
        positions: int,
    ) -> None:
        """Update a lane's current deployed capital and position count."""
        budget = self._budgets.get(lane_id)
        if budget is None:
            return
        budget.current_deployed_usd = max(0.0, deployed_usd)
        budget.current_positions = max(0, positions)
        budget.buying_power_usd = budget.available_usd

    def get_available_capital(self, lane_id: str) -> float:
        """Returns available capital (max - current) for the given lane."""
        budget = self._budgets.get(lane_id)
        if budget is None:
            return 0.0
        return budget.available_usd

    def rank_opportunities(self, opportunities: list) -> list:
        """
        STUB — returns input sorted by 'ev' field descending.
        Full cross-lane ranking logic TBD in v16.0.
        """
        try:
            return sorted(opportunities, key=lambda x: float(x.get("ev", 0.0)), reverse=True)
        except Exception:
            return list(opportunities)

    def get_allocation_summary(self) -> dict:
        """Returns summary of all lane budgets."""
        summary = {}
        for lane_id, budget in self._budgets.items():
            summary[lane_id] = {
                "max_deployed_usd": budget.max_deployed_usd,
                "current_deployed_usd": budget.current_deployed_usd,
                "available_usd": budget.available_usd,
                "max_concurrent_positions": budget.max_concurrent_positions,
                "current_positions": budget.current_positions,
                "position_slots_available": budget.position_slots_available,
                "buying_power_usd": budget.buying_power_usd,
            }
        return summary
