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


def _persist_last_known_good(value: float, db_path: Optional[str] = None) -> None:
    """Best-effort cache of the last healthy broker reading."""
    try:
        with sqlite3.connect(db_path or _db_path(), timeout=3, check_same_thread=False) as conn:
            cur = conn.execute(
                "UPDATE system_runtime_state SET account_size_live=? WHERE id=1",
                (float(value),),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO system_runtime_state (id, account_size_live) VALUES (1, ?)",
                    (float(value),),
                )
    except Exception:
        pass


def _last_known_good(db_path: Optional[str] = None) -> float:
    try:
        with sqlite3.connect(db_path or _db_path(), timeout=3, check_same_thread=False) as conn:
            row = conn.execute(
                "SELECT account_size_live FROM system_runtime_state WHERE id=1"
            ).fetchone()
            if row and row[0]:
                value = float(row[0])
                if value > 0:
                    return value
    except Exception:
        pass
    return 0.0


def resolve_live_bankroll(*, db_path: Optional[str] = None, broker=None) -> float:
    """Canonical bankroll denominator, sourced from Kalshi itself.

    Resolution order:
      1. Live broker cash balance, when it reads positive.
      2. Last known good reading, persisted in system_runtime_state.
      3. config.ACCOUNT_SIZE, as a final floor.

    A sizing denominator that transiently reads zero is dangerous -- it either
    halts trading or produces nonsense position sizes -- so an unreachable or
    non-positive broker value never propagates. The last healthy reading
    carries instead.

    This is the cash balance Kalshi reports, not cash plus open position
    value. Cash is the conservative choice and is what the exchange calls the
    account balance, but it does mean the denominator tapers as capital gets
    deployed, tightening sizing as the book fills.
    """
    balance = 0.0
    try:
        if broker is None:
            from execution.kalshi_broker import get_kalshi_broker

            broker = get_kalshi_broker()
        balance = float(broker.get_account_balance() or 0.0)
    except Exception:
        balance = 0.0

    if balance > 0:
        _persist_last_known_good(balance, db_path)
        return balance

    cached = _last_known_good(db_path)
    if cached > 0:
        return cached

    return _config_account_size()


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
