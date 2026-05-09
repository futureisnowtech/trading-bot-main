"""
runtime/position_reconciler.py — Reconciles open_positions against trades ledger.

Run at startup and periodically. Fixes scale_33_done/scale_66_done flags
when trade ledger shows partial closes that positions table missed.
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
from runtime.spot_position_truth import get_spot_position_truth


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def reconcile_position_flags(db_path: str = DB_PATH) -> list:
    """
    For each open position, check the trades ledger for partial closes
    since ts_entry and repair scale_33_done / scale_66_done flags.

    Returns list of repair dicts:
      {symbol, flags_set, partial_close_count, closed_qty, original_qty}
    """
    repairs = []

    try:
        with _conn(db_path) as c:
            # Fetch all open positions
            positions = c.execute(
                "SELECT symbol, qty, ts_entry, scale_33_done, scale_66_done "
                "FROM open_positions WHERE paper=0"
            ).fetchall()

            if not positions:
                return []

            now_iso = datetime.now(timezone.utc).isoformat()

            for pos in positions:
                symbol = pos["symbol"]
                original_qty = pos["qty"] or 0
                ts_entry = pos["ts_entry"] or "1970-01-01T00:00:00"
                scale_33 = bool(pos["scale_33_done"])
                scale_66 = bool(pos["scale_66_done"])

                # Count partial closes in trades table since entry
                result = c.execute(
                    """
                    SELECT COUNT(*) as cnt, COALESCE(SUM(qty), 0) as closed_qty
                    FROM trades
                    WHERE symbol=? AND ts>=? AND paper=0
                      AND broker LIKE '%coinbase%'
                      AND (
                          action IN ('SELL', 'CLOSE')
                          OR notes LIKE '%scale_out%'
                          OR notes LIKE '%partial%'
                      )
                    """,
                    (symbol, ts_entry),
                ).fetchone()

                partial_count = result["cnt"] or 0
                closed_qty = result["closed_qty"] or 0.0

                flags_set = []

                if partial_count > 0:
                    # Any partial close → set scale_33_done
                    if not scale_33:
                        c.execute(
                            "UPDATE open_positions SET scale_33_done=1 WHERE symbol=? AND paper=0""",
                            (symbol,),
                        )
                        flags_set.append("scale_33_done")

                    # If closed_qty >= 50% of original → set scale_66_done
                    if (
                        original_qty > 0
                        and closed_qty >= 0.50 * original_qty
                        and not scale_66
                    ):
                        c.execute(
                            "UPDATE open_positions SET scale_66_done=1 WHERE symbol=? AND paper=0",
                            (symbol,),
                        )
                        flags_set.append("scale_66_done")


                if flags_set:
                    repair = {
                        "symbol": symbol,
                        "flags_set": flags_set,
                        "partial_close_count": partial_count,
                        "closed_qty": closed_qty,
                        "original_qty": original_qty,
                    }
                    repairs.append(repair)

                    # Log to system_events
                    msg = f"Repaired {symbol}: set {', '.join(flags_set)} (partial_closes={partial_count}, closed_qty={closed_qty:.4f})"
                    try:
                        c.execute(
                            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'INFO', 'PositionReconciler', ?)",
                            (now_iso, msg),
                        )
                    except Exception:
                        pass

    except Exception as e:
        # Don't crash startup — log to stderr and return empty
        import logging
        logging.getLogger(__name__).warning("PositionReconciler: %s", e)

    return repairs


def run_reconciliation(db_path: str = DB_PATH) -> None:
    """
    Run reconcile_position_flags and log a summary to system_events.
    Safe to call at startup — never raises.
    """
    try:
        repairs = reconcile_position_flags(db_path=db_path)
        spot_truth = get_spot_position_truth(db_path=db_path)
        truth_issues = spot_truth.get("issues") or []
        now_iso = datetime.now(timezone.utc).isoformat()
        if repairs:
            msg = f"Reconciliation complete: {len(repairs)} position(s) repaired"
        else:
            msg = "Reconciliation complete: no repairs needed"
        if truth_issues:
            issue_summary = ", ".join(
                f"{i.get('symbol') or 'GLOBAL'}:{i.get('position_truth_status')}"
                for i in truth_issues
            )
            msg += f" | spot_truth={issue_summary}"

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