"""
dashboard/db.py — Database primitives for Kalshi Weather Engine.
"""

import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from config import BOT_LOG_PATH as LOG_PATH
from config import DB_PATH

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

def init_db():
    """Ensure basic tables exist for the dashboard if not already there."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lane_runtime_state (
                lane_id TEXT PRIMARY KEY,
                snapshot_json TEXT,
                readiness_state TEXT,
                ts TEXT
            )
        """)
        # SRE FIX: Weather-Native Schema Enforcement
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forecast_positions (
                ticker TEXT PRIMARY KEY,
                qty INTEGER,            -- Kalshi operates in whole contracts
                entry REAL,             -- Replaces 'entry_price'
                side TEXT,
                unrealized_pnl REAL
            )
        """)
        conn.commit()
