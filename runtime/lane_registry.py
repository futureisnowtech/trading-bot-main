"""
runtime/lane_registry.py — Control plane for all trading lanes.

Single source of truth for what lanes exist, their activation flags,
and their startup callbacks.
"""

import os
import sys
from typing import Callable, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class _Lane:
    __slots__ = ("lane_id", "enabled_flag", "start_fn", "mode_fn", "_started")

    def __init__(
        self,
        lane_id: str,
        enabled_flag: bool,
        start_fn: Optional[Callable] = None,
        mode_fn: Optional[Callable] = None,
    ):
        self.lane_id = lane_id
        self.enabled_flag = enabled_flag
        self.start_fn = start_fn
        self.mode_fn = mode_fn
        self._started = False


class LaneRegistry:
    """
    Registry of all known trading lanes.

    Usage:
        registry = LaneRegistry()
        registry.start_active_lanes()
    """

    def __init__(self):
        self._lanes: dict[str, _Lane] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Pre-register all known lanes based on current config."""
        from config import (COINBASE_CDP_KEY_NAME,
            FUTURES_LANE_ACTIVE,
            FORECAST_LANE_ACTIVE,
            STOCKS_LANE_ACTIVE,
        )

        # crypto: enabled when paper mode OR Coinbase credentials present
        # v18.18: Use process_mode from system_runtime_state if available
        paper_active = False
        try:
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, timeout=1) as conn:
                row = conn.execute("SELECT process_mode FROM system_runtime_state LIMIT 1").fetchone()
                if row and row[0] == 'paper':
                    paper_active = True
        except Exception:
            pass

        crypto_enabled = paper_active or bool(COINBASE_CDP_KEY_NAME)
        self.register("crypto", enabled_flag=crypto_enabled)

        # forecast: enabled when FORECAST_LANE_ACTIVE=True
        self.register("forecast", enabled_flag=bool(FORECAST_LANE_ACTIVE))

        # mes_archived: enabled when FUTURES_LANE_ACTIVE=True (default False)
        self.register("mes_archived", enabled_flag=bool(FUTURES_LANE_ACTIVE))

        # stocks: enabled when STOCKS_LANE_ACTIVE=True
        self.register("stocks", enabled_flag=bool(STOCKS_LANE_ACTIVE))

    def register(
        self,
        lane_id: str,
        enabled_flag: bool,
        start_fn: Optional[Callable] = None,
        mode_fn: Optional[Callable] = None,
    ) -> None:
        """Register or update a lane definition."""
        self._lanes[lane_id] = _Lane(
            lane_id=lane_id,
            enabled_flag=enabled_flag,
            start_fn=start_fn,
            mode_fn=mode_fn,
        )

    def start_active_lanes(self) -> list:
        """
        Start all lanes where enabled_flag=True and start_fn is provided.
        Returns list of lane_ids that were started.
        """
        started = []
        for lane_id, lane in self._lanes.items():
            if lane.enabled_flag and lane.start_fn and not lane._started:
                try:
                    lane.start_fn()
                    lane._started = True
                    started.append(lane_id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        "LaneRegistry: failed to start lane %s: %s", lane_id, e
                    )
        return started

    def get_lane_ids(self) -> list:
        """Return all registered lane IDs."""
        return list(self._lanes.keys())

    def get_active_lane_ids(self) -> list:
        """Return lane IDs where enabled_flag=True."""
        return [lid for lid, lane in self._lanes.items() if lane.enabled_flag]

    def is_enabled(self, lane_id: str) -> bool:
        """True if lane is registered and enabled_flag=True."""
        lane = self._lanes.get(lane_id)
        return lane is not None and lane.enabled_flag