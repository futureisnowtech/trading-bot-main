"""Top-level dashboard DB shim used by proof tests."""

from __future__ import annotations

import sqlite3

from config import DB_PATH as _CFG_DB_PATH

DB_PATH = _CFG_DB_PATH
LOG_PATH = ""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def q(sql: str, params: tuple = ()) -> list[dict]:
    try:
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def q1(sql: str, params: tuple = ()) -> dict:
    rows = q(sql, params)
    return rows[0] if rows else {}


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lane_runtime_state (
                lane_id TEXT PRIMARY KEY,
                snapshot_json TEXT,
                readiness_state TEXT,
                ts TEXT
            )
            """
        )
        conn.commit()
