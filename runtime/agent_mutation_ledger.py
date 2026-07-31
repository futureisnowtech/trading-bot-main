"""
runtime/agent_mutation_ledger.py — Immutable Mutation Ledger & Audit Trail.

Tracks all agent-proposed parameter and code updates in an immutable SQLite table.
Stores reasoning traces, in-sample Brier deltas, out-of-sample PnL deltas, and rollback status.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

STATUS_PROPOSED = "PROPOSED"
STATUS_APPLIED = "APPLIED"
STATUS_SOAKING = "SOAKING"
STATUS_PASSED = "PASSED"
STATUS_REVERTED = "REVERTED"
STATUS_REJECTED = "REJECTED"

_DDL_MUTATION_LOG = """
CREATE TABLE IF NOT EXISTS agent_mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sha_before TEXT NOT NULL,
    sha_after TEXT,
    target_file TEXT NOT NULL,
    parameter_key TEXT NOT NULL,
    old_value_json TEXT NOT NULL,
    new_value_json TEXT NOT NULL,
    reasoning_trace TEXT NOT NULL,
    in_sample_brier_delta REAL,
    out_sample_pnl_delta REAL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    rollback_reason TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def init_mutation_ledger_db(db_path: str = DB_PATH) -> None:
    """Ensure the agent_mutation_log table exists."""
    try:
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(_DDL_MUTATION_LOG)
        conn.close()
    except Exception as exc:
        logger.error("Failed to initialize agent_mutation_log table: %s", exc)


def record_mutation_proposal(
    *,
    sha_before: str,
    target_file: str,
    parameter_key: str,
    old_value: Any,
    new_value: Any,
    reasoning_trace: str,
    in_sample_brier_delta: float = 0.0,
    out_sample_pnl_delta: float = 0.0,
    db_path: str = DB_PATH,
) -> int:
    """Record a new proposed parameter or code mutation."""
    init_mutation_ledger_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    old_json = json.dumps(old_value, default=str)
    new_json = json.dumps(new_value, default=str)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_mutation_log (
                ts, sha_before, sha_after, target_file, parameter_key,
                old_value_json, new_value_json, reasoning_trace,
                in_sample_brier_delta, out_sample_pnl_delta, status, rollback_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso,
                sha_before,
                "",
                target_file,
                parameter_key,
                old_json,
                new_json,
                reasoning_trace,
                in_sample_brier_delta,
                out_sample_pnl_delta,
                STATUS_PROPOSED,
                "",
                now_iso,
            ),
        )
        mutation_id = int(cursor.lastrowid)
        conn.commit()
        conn.close()
        return mutation_id
    except Exception as exc:
        logger.error("Failed to record mutation proposal: %s", exc)
        return -1


def update_mutation_status(
    mutation_id: int,
    status: str,
    *,
    sha_after: str = "",
    rollback_reason: str = "",
    db_path: str = DB_PATH,
) -> bool:
    """Update status of a mutation (e.g. APPLIED, SOAKING, PASSED, REVERTED, REJECTED)."""
    init_mutation_ledger_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(
                """
                UPDATE agent_mutation_log
                SET status = ?, sha_after = COALESCE(NULLIF(?, ''), sha_after),
                    rollback_reason = COALESCE(NULLIF(?, ''), rollback_reason), updated_at = ?
                WHERE id = ?
                """,
                (status, sha_after, rollback_reason, now_iso, mutation_id),
            )
        conn.close()
        return True
    except Exception as exc:
        logger.error("Failed to update mutation status for id %s: %s", mutation_id, exc)
        return False


def get_active_soak_mutations(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieve all mutations currently in SOAKING state."""
    init_mutation_ledger_db(db_path)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_mutation_log WHERE status = ? ORDER BY id DESC",
            (STATUS_SOAKING,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("Failed to fetch active soak mutations: %s", exc)
        return []


def get_mutation_history(limit: int = 20, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Fetch mutation history for diagnostics."""
    init_mutation_ledger_db(db_path)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_mutation_log ORDER BY id DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("Failed to fetch mutation history: %s", exc)
        return []
