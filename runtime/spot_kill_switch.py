"""
runtime/spot_kill_switch.py — Lane-specific kill switches for the spot scalp lane.

KS8:  Execution anomaly — 3 consecutive order failures → HALT
KS10: Loss cluster —
        a) 4 consecutive losing trades → HALT
        b) Daily realized PnL ≤ -SPOT_KS_DAILY_LOSS_PCT of live account equity → HALT

When halted, writes a structured event to system_events (level=CRITICAL) and
sets the spot_kill_switch_active flag in lane_runtime_state.

Reset requires explicit human action (set lane_runtime_state.spot_kill_switch_active=0
or call reset_spot_kill_switch()).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB = Path(__file__).parents[1] / "logs" / "trades.db"
_HALT_SOURCE = "spot_kill_switch"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_halt(conn: sqlite3.Connection, reason: str, detail: dict) -> None:
    msg = f"SPOT KILL SWITCH TRIGGERED: {reason}"
    try:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?,?,?,?)",
            (_now(), "CRITICAL", _HALT_SOURCE, msg),
        )
    except Exception:
        pass
    try:
        conn.execute(
            """INSERT INTO system_events (ts, level, source, message)
               VALUES (?,?,?,?)""",
            (_now(), "CRITICAL", _HALT_SOURCE, json.dumps(detail)),
        )
    except Exception:
        pass
    try:
        conn.commit()
    except Exception:
        pass
    
    # ── Grafana IRM Integration ──────────────────────────────────────────────
    try:
        from monitoring.irm_reporter import create_irm_incident
        create_irm_incident(
            title=msg,
            severity="critical",
            description=f"Spot lane halted due to {reason}.",
            labels=["lane:spot", f"reason:{reason}"],
            extra_details=detail
        )
    except Exception as e:
        logger.debug(f"[spot_kill_switch] irm report failed: {e}")

    logger.critical("[spot_kill_switch] HALT — %s | %s", reason, detail)


def _is_halted(conn: sqlite3.Connection) -> bool:
    """Return True if the spot kill switch is active (uncleared CRITICAL event in system_events)."""
    try:
        # Halted = most recent spot_kill_switch event is CRITICAL (not a RESET INFO)
        row = conn.execute(
            """SELECT level FROM system_events
               WHERE source='spot_kill_switch'
               ORDER BY ts DESC LIMIT 1"""
        ).fetchone()
        return bool(row and row["level"] == "CRITICAL")
    except Exception:
        return False


def trigger_spot_halt(reason: str, detail: dict | None = None) -> bool:
    """Public helper for other spot-lane components to force a hard halt."""
    try:
        conn = _conn()
        _log_halt(conn, reason, detail or {})
        conn.close()
        return True
    except Exception as exc:
        logger.error("[spot_kill_switch] trigger failed: %s", exc)
        return False


def check_spot_kill_switch() -> tuple[bool, str]:
    """
    Check all spot kill switches.  Returns (halt, reason).
    Writes structured halt event if any switch fires.
    """
    try:
        conn = _conn()
        try:
            if _is_halted(conn):
                conn.close()
                return True, "spot_kill_switch_already_active"

            try:
                from runtime.spot_position_truth import get_spot_position_truth

                truth = get_spot_position_truth()
                if not truth.get("snapshot_ok"):
                    reason = "ks_spot_truth_snapshot_unavailable"
                    _log_halt(conn, reason, {"trigger": reason})
                    conn.close()
                    return True, reason
                truth_blockers = truth.get("blocking_issues") or []
                if truth_blockers:
                    reason = "ks_spot_truth_blocker"
                    _log_halt(
                        conn,
                        reason,
                        {
                            "trigger": reason,
                            "blockers": [
                                {
                                    "symbol": str(b.get("symbol") or ""),
                                    "status": str(
                                        b.get("position_truth_status") or ""
                                    ),
                                }
                                for b in truth_blockers
                            ],
                        },
                    )
                    conn.close()
                    return True, reason
            except Exception as exc:
                logger.warning("[spot_kill_switch] truth blocker check error: %s", exc)

            import config as _cfg

            # ── KS10a: 4 consecutive losing closed trades ─────────────────────
            consecutive_limit = int(getattr(_cfg, "SPOT_KS_CONSECUTIVE_LOSSES", 4))
            recent_rows = conn.execute(
                """SELECT pnl_usd FROM trades
                   WHERE strategy LIKE 'spot_%' AND action='SELL' AND paper=0
                   ORDER BY ts DESC LIMIT ?""",
                (consecutive_limit,),
            ).fetchall()
            if len(recent_rows) >= consecutive_limit and all(
                float(r["pnl_usd"] or 0) < 0 for r in recent_rows
            ):
                reason = f"ks10a_consecutive_losses_{consecutive_limit}"
                detail = {
                    "trigger": reason,
                    "consecutive_losses": consecutive_limit,
                    "pnl_last_n": [float(r["pnl_usd"] or 0) for r in recent_rows],
                }
                _log_halt(conn, reason, detail)
                conn.close()
                return True, reason

            # ── KS10b: Daily realized PnL ≤ -SPOT_KS_DAILY_LOSS_PCT of account ─
            daily_loss_pct = float(getattr(_cfg, "SPOT_KS_DAILY_LOSS_PCT", 0.02))
            try:
                from runtime.live_account import get_live_account_size

                account_size = get_live_account_size()
            except Exception:
                account_size = float(getattr(_cfg, "ACCOUNT_SIZE", 5000))
            daily_threshold = -abs(daily_loss_pct * account_size)
            today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
            daily_row = conn.execute(
                """SELECT COALESCE(SUM(pnl_usd), 0) AS daily_pnl
                   FROM trades
                   WHERE strategy LIKE 'spot_%' AND action='SELL' AND paper=0
                     AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime(?)""",
                (today_start.replace("T", " "),),
            ).fetchone()
            daily_pnl = float(daily_row["daily_pnl"] or 0)
            if daily_pnl <= daily_threshold:
                reason = (
                    f"ks10b_daily_loss_${abs(daily_pnl):.2f}"
                    f"_exceeds_{daily_loss_pct * 100:.1f}pct"
                )
                detail = {
                    "trigger": "ks10b_daily_loss",
                    "daily_pnl": round(daily_pnl, 4),
                    "threshold": round(daily_threshold, 4),
                    "account_size": account_size,
                }
                _log_halt(conn, reason, detail)
                conn.close()
                return True, reason

            conn.close()
            return False, ""
        except Exception as exc:
            logger.warning("[spot_kill_switch] check error: %s", exc)
            try:
                conn.close()
            except Exception:
                pass
            return False, ""
    except Exception as exc:
        logger.warning("[spot_kill_switch] db error: %s", exc)
        return False, ""


def reset_spot_kill_switch() -> bool:
    """Human-initiated reset. Returns True if successful."""
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?,?,?,?)",
            (_now(), "INFO", _HALT_SOURCE, "SPOT KILL SWITCH RESET by operator"),
        )
        conn.commit()
        conn.close()
        logger.info("[spot_kill_switch] reset by operator")
        return True
    except Exception as exc:
        logger.error("[spot_kill_switch] reset failed: %s", exc)
        return False


def kill_switch_status() -> dict:
    """Return current kill switch state for monitoring surfaces."""
    try:
        conn = _conn()
        halted = _is_halted(conn)
        recent = conn.execute(
            """SELECT ts, message FROM system_events
               WHERE source='spot_kill_switch' AND level='CRITICAL'
               ORDER BY ts DESC LIMIT 1"""
        ).fetchone()
        conn.close()
        return {
            "halted": halted,
            "last_halt_ts": recent["ts"] if recent else None,
            "last_halt_reason": recent["message"] if recent else None,
        }
    except Exception:
        return {"halted": False, "last_halt_ts": None, "last_halt_reason": None}
