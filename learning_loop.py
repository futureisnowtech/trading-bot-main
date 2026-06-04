"""Lean learning-loop compatibility hooks used by proof tests."""

from __future__ import annotations

import sqlite3


def _db_path() -> str:
    from config import DB_PATH

    return DB_PATH


def _ensure_tables(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT '',
                symbol TEXT DEFAULT '',
                won INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()


def maybe_trigger_retrains(**kwargs):
    return []


def run_nightly_rbi(symbol: str = "BTCUSDT", **kwargs):
    return {"promoted": 0, "passed": 0}
