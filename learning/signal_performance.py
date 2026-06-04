"""Minimal learning-table bootstrap for proof/runtime compatibility."""

from __future__ import annotations

import sqlite3

from config import DB_PATH as _CFG_DB_PATH

DB_PATH = _CFG_DB_PATH


def init_learning_tables(db_path: str | None = None) -> None:
    path = db_path or DB_PATH
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name TEXT NOT NULL,
                regime TEXT DEFAULT 'any',
                source TEXT DEFAULT '',
                fires INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                bayesian_pts REAL DEFAULT 0,
                prior_pts REAL DEFAULT 0,
                last_updated TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_attribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_ref TEXT,
                symbol TEXT,
                strategy TEXT,
                regime TEXT,
                source TEXT,
                entry_ts TEXT,
                exit_ts TEXT,
                entry_price REAL DEFAULT 0,
                exit_price REAL DEFAULT 0,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                fee_usd REAL DEFAULT 0,
                won INTEGER DEFAULT 0,
                signals_json TEXT DEFAULT '{}',
                conviction REAL DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                hold_minutes REAL DEFAULT 0,
                paper INTEGER DEFAULT 1,
                lesson TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                mae_pct REAL DEFAULT 0,
                mfe_pct REAL DEFAULT 0,
                exit_type TEXT DEFAULT 'unknown',
                is_fee_trap INTEGER DEFAULT 0,
                ml_p_win REAL DEFAULT 0,
                super_score REAL DEFAULT 0,
                composite_score REAL DEFAULT 0
            )
            """
        )
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
