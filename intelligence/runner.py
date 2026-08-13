"""Bounded asynchronous orchestration for RBI 2.0 and Cerebro."""

from __future__ import annotations

import time
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH
from intelligence.cerebro import generate_insights
from intelligence.rbi2 import train_challenger
from intelligence.schema import connect, init_intelligence_db

_LAST_RUN = 0.0
_COOLDOWN_SECONDS = 18 * 60 * 60


def run_intelligence_cycle(*, force: bool = False, db_path: str = DB_PATH) -> dict[str, Any]:
    global _LAST_RUN
    now = time.monotonic()
    if not force and now - _LAST_RUN < _COOLDOWN_SECONDS:
        return {"status": "cooldown"}
    _LAST_RUN = now
    init_intelligence_db(db_path)
    run_id = f"ir-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO intelligence_runs (run_id, component, started_at, status) VALUES (?, 'rbi2_cerebro', ?, 'RUNNING')",
            (run_id, started_at),
        )
        conn.commit()
    try:
        result = {
            "status": "complete",
            "run_id": run_id,
            "rbi2": train_challenger(db_path=db_path),
            "cerebro": generate_insights(db_path=db_path),
        }
        cutoff = str((result["rbi2"] or {}).get("data_cutoff") or "")
        status = "COMPLETE"
    except Exception as exc:
        result = {"status": "failed", "run_id": run_id, "error": str(exc)}
        cutoff = ""
        status = "FAILED"
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE intelligence_runs
               SET completed_at=?, status=?, data_cutoff=?, summary_json=?
               WHERE run_id=?""",
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                cutoff,
                json.dumps(result, sort_keys=True, default=str),
                run_id,
            ),
        )
        conn.commit()
    return result
