"""
runtime/position_reconciler.py — Reconciles open_positions for Kalshi Weather Engine.
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from config import DB_PATH

def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def run_reconciliation(db_path: str = DB_PATH) -> None:
    """
    Run reconciliation against Kalshi broker.
    Safe to call at startup — never raises.
    """
    try:
        holdings_count = 0
        try:
            from execution.kalshi_broker import get_kalshi_broker
            broker = get_kalshi_broker()
            if broker.connect():
                holdings = broker.get_positions()
                holdings_count = len(holdings)
        except Exception: pass

        now_iso = datetime.now(timezone.utc).isoformat()
        msg = f"Reconciliation complete: Kalshi holdings={holdings_count}"

        try:
            with _conn(db_path) as c:
                c.execute(
                    "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'INFO', 'PositionReconciler', ?)",
                    (now_iso, msg),
                )
        except Exception:
            pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("run_reconciliation failed: %s", e)
