"""
runtime/runtime_state.py — Persistent system + lane runtime truth.

Two tables in logs/trades.db:
  system_runtime_state  — one row, system-level truth
  lane_runtime_state    — one row per lane_id

Written by main.py on startup and heartbeat.
Read by dashboard, validator, live audit hooks.
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional
from logging_db.trade_logger import _conn as _db_conn

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL_SYSTEM = """
CREATE TABLE IF NOT EXISTS system_runtime_state (
    id                      INTEGER PRIMARY KEY DEFAULT 1,
    process_mode            TEXT,
    startup_ts              TEXT,
    account_size_live       REAL,
    process_alive           INTEGER DEFAULT 0,
    active_lanes            TEXT DEFAULT '[]',
    global_status           TEXT DEFAULT 'OK',
    last_global_heartbeat_at TEXT,
    launch_readiness_state  TEXT DEFAULT 'NOT_READY',
    updated_at              TEXT
)
"""

_DDL_LANE = """
CREATE TABLE IF NOT EXISTS lane_runtime_state (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    lane_id              TEXT UNIQUE NOT NULL,
    lane_role            TEXT DEFAULT 'secondary',
    enabled              INTEGER DEFAULT 0,
    active               INTEGER DEFAULT 0,
    configured           INTEGER DEFAULT 0,
    dashboard_visible    INTEGER DEFAULT 1,
    autonomous_enabled   INTEGER DEFAULT 0,
    manual_allowed       INTEGER DEFAULT 0,
    mode                 TEXT DEFAULT 'disabled',
    connected            INTEGER DEFAULT 0,
    discovering          INTEGER DEFAULT 0,
    quoting              INTEGER DEFAULT 0,
    tradable             INTEGER DEFAULT 0,
    health               TEXT DEFAULT 'UNKNOWN',
    blocked_reason       TEXT DEFAULT '',
    action_needed        TEXT DEFAULT '',
    last_success_at      TEXT,
    last_error_at        TEXT,
    last_heartbeat_at    TEXT,
    positions_open       INTEGER DEFAULT 0,
    capital_deployed_usd REAL DEFAULT 0.0,
    buying_power_usd     REAL DEFAULT 0.0,
    issue_count          INTEGER DEFAULT 0,
    readiness_state      TEXT DEFAULT 'UNKNOWN',
    promotion_condition  TEXT DEFAULT '',
    snapshot_json        TEXT,
    updated_at           TEXT
)
"""


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    # Use the hardened, WAL-enabled singleton connection
    return _db_conn()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


# ── Table init ────────────────────────────────────────────────────────────────

def init_runtime_tables(db_path: str = DB_PATH) -> None:
    """CREATE TABLE IF NOT EXISTS for both runtime tables."""
    with _conn(db_path) as c:
        c.execute(_DDL_SYSTEM)
        c.execute(_DDL_LANE)
        _sys_cols = {
            r["name"]
            for r in c.execute("PRAGMA table_info(system_runtime_state)").fetchall()
        }
        if "account_size_live" not in _sys_cols:
            c.execute("ALTER TABLE system_runtime_state ADD COLUMN account_size_live REAL")
        _lane_cols = {
            r["name"] for r in c.execute("PRAGMA table_info(lane_runtime_state)").fetchall()
        }
        if "lane_role" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN lane_role TEXT DEFAULT 'secondary'")
        if "dashboard_visible" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN dashboard_visible INTEGER DEFAULT 1")
        if "autonomous_enabled" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN autonomous_enabled INTEGER DEFAULT 0")
        if "manual_allowed" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN manual_allowed INTEGER DEFAULT 0")
        if "promotion_condition" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN promotion_condition TEXT DEFAULT ''")
        if "snapshot_json" not in _lane_cols:
            c.execute("ALTER TABLE lane_runtime_state ADD COLUMN snapshot_json TEXT")


# ── System-level state ────────────────────────────────────────────────────────

def upsert_system_state(db_path: str = DB_PATH, **kwargs) -> None:
    """
    Upsert the single system_runtime_state row (id=1).
    Any keyword argument that matches a column name will be set.
    updated_at is always set automatically.
    """
    kwargs["updated_at"] = _now_iso()
    kwargs["id"] = 1

    # Always ensure the row exists first
    with _conn(db_path) as c:
        # Build SET clause from kwargs (excluding 'id')
        set_cols = {k: v for k, v in kwargs.items() if k != "id"}
        if not set_cols:
            return

        cols = list(set_cols.keys()) + ["id"]
        vals = list(set_cols.values()) + [1]
        placeholders = ", ".join("?" * len(vals))
        col_names = ", ".join(cols)
        update_pairs = ", ".join(f"{k}=excluded.{k}" for k in set_cols)

        c.execute(
            f"INSERT INTO system_runtime_state ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {update_pairs}",
            vals,
        )


def get_system_state(db_path: str = DB_PATH) -> dict:
    """Returns the system_runtime_state row as a dict, or empty dict if none."""
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT * FROM system_runtime_state WHERE id=1"
            ).fetchone()
            return _row_to_dict(row) or {}
    except Exception:
        return {}


# ── Lane-level state ──────────────────────────────────────────────────────────

def upsert_lane_state(lane_id: str, db_path: str = DB_PATH, **kwargs) -> None:
    """
    Upsert a lane_runtime_state row identified by lane_id.
    Any keyword argument matching a column will be set.
    updated_at is always set automatically.
    """
    kwargs["updated_at"] = _now_iso()
    kwargs["lane_id"] = lane_id

    with _conn(db_path) as c:
        set_cols = {k: v for k, v in kwargs.items() if k not in ("lane_id",)}
        if not set_cols:
            return

        all_cols = ["lane_id"] + list(set_cols.keys())
        all_vals = [lane_id] + list(set_cols.values())
        placeholders = ", ".join("?" * len(all_vals))
        col_names = ", ".join(all_cols)
        update_pairs = ", ".join(f"{k}=excluded.{k}" for k in set_cols)

        c.execute(
            f"INSERT INTO lane_runtime_state ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(lane_id) DO UPDATE SET {update_pairs}",
            all_vals,
        )


def get_lane_state(lane_id: str, db_path: str = DB_PATH) -> dict:
    """Returns the lane_runtime_state row for lane_id as a dict, or empty dict."""
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT * FROM lane_runtime_state WHERE lane_id=?", (lane_id,)
            ).fetchone()
            return _row_to_dict(row) or {}
    except Exception:
        return {}


def get_all_lane_states(db_path: str = DB_PATH) -> list:
    """Returns all lane_runtime_state rows as list of dicts."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT * FROM lane_runtime_state ORDER BY lane_id"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


# ── Heartbeats ────────────────────────────────────────────────────────────────

def write_system_heartbeat(db_path: str = DB_PATH) -> None:
    """Update last_global_heartbeat_at and set process_alive=1."""
    upsert_system_state(
        db_path=db_path,
        last_global_heartbeat_at=_now_iso(),
        process_alive=1,
    )


def mark_lane_heartbeat(lane_id: str, db_path: str = DB_PATH) -> None:
    """Update last_heartbeat_at for the given lane."""
    upsert_lane_state(lane_id, db_path=db_path, last_heartbeat_at=_now_iso())
