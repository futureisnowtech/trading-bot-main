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


def run_reconciliation(db_path: str = DB_PATH) -> dict:
    """
    Run reconciliation against Kalshi broker.
    Raises when broker truth is unavailable so startup cannot treat unknown as flat.
    """
    summary = {
        "holdings_count": 0,
        "adopted": 0,
        "refreshed": 0,
        "closed": 0,
        "connected": False,
    }
    from execution.kalshi_broker import get_kalshi_broker
    from forecast.db import init_forecast_db, reconcile_forecast_positions

    broker = get_kalshi_broker()
    if not broker.is_connected() and not broker.connect():
        raise RuntimeError("reconciliation_broker_unavailable")
    if not broker.sync_positions() or not broker.has_fresh_position_snapshot():
        raise RuntimeError(
            f"reconciliation_position_snapshot_unavailable:{broker.position_snapshot_status()}"
        )
    holdings = broker.get_positions()
    init_forecast_db(db_path=db_path)
    recon = reconcile_forecast_positions(holdings, broker=broker, db_path=db_path)
    summary.update(
        {
            "holdings_count": len(holdings),
            "connected": True,
            "adopted": int(recon.get("adopted") or 0),
            "refreshed": int(recon.get("refreshed") or 0),
            "closed": int(recon.get("closed") or 0),
        }
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    msg = (
        "Reconciliation complete: "
        f"connected={summary['connected']} "
        f"Kalshi holdings={summary['holdings_count']} "
        f"adopted={summary['adopted']} "
        f"refreshed={summary['refreshed']} "
        f"closed={summary['closed']}"
    )

    try:
        with _conn(db_path) as c:
            c.execute(
                "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'INFO', 'PositionReconciler', ?)",
                (now_iso, msg),
            )
    except Exception:
        pass
    return summary
