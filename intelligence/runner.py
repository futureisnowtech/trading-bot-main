"""Bounded asynchronous orchestration for RBI 2.0 and Cerebro."""

from __future__ import annotations

import time
from typing import Any

from config import DB_PATH
from intelligence.cerebro import generate_insights
from intelligence.rbi2 import train_challenger

_LAST_RUN = 0.0
_COOLDOWN_SECONDS = 18 * 60 * 60


def run_intelligence_cycle(*, force: bool = False, db_path: str = DB_PATH) -> dict[str, Any]:
    global _LAST_RUN
    now = time.monotonic()
    if not force and now - _LAST_RUN < _COOLDOWN_SECONDS:
        return {"status": "cooldown"}
    _LAST_RUN = now
    return {
        "status": "complete",
        "rbi2": train_challenger(db_path=db_path),
        "cerebro": generate_insights(db_path=db_path),
    }
