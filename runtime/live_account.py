"""
runtime/live_account.py — Canonical live account size helper.

Paper mode continues to use config.ACCOUNT_SIZE.
Live mode reads the persisted runtime truth in system_runtime_state.account_size_live
and only falls back to config.ACCOUNT_SIZE if that runtime field is unavailable.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional


def _db_path() -> str:
    try:
        from config import DB_PATH

        return DB_PATH
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "logs", "trades.db")


def _config_account_size() -> float:
    try:
        from config import ACCOUNT_SIZE

        return float(ACCOUNT_SIZE)
    except Exception:
        return 5000.0


def _runtime_mode() -> str:
    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT process_mode FROM system_runtime_state WHERE id=1"
            ).fetchone()
            return str(row[0] or "") if row else ""
    except Exception:
        return ""


def get_live_account_size(*, paper: Optional[bool] = None) -> float:
    """
    Return the canonical account-size denominator.

    - paper=True  -> config.ACCOUNT_SIZE
    - paper=False -> system_runtime_state.account_size_live when present,
                     else config.ACCOUNT_SIZE fallback
    - paper=None  -> infer from system_runtime_state.process_mode first
    """
    if paper is None:
        paper = _runtime_mode() != "live"

    if paper:
        return _config_account_size()

    try:
        with sqlite3.connect(_db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT account_size_live FROM system_runtime_state WHERE id=1"
            ).fetchone()
            if row and row[0]:
                value = float(row[0])
                if value > 0:
                    return value
    except Exception:
        pass

    return _config_account_size()
