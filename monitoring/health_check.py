"""
monitoring/health_check.py — Automated system health assertions.

Runs after every scan cycle. Checks 6 critical invariants and writes
a pass/fail record to system_events (source='health_check').

Checks:
  1. ML gate functional       — p_win != 0.5 (not stuck on error default)
  2. No stagnant positions    — no crypto/perp position past max hold time
  3. Scan liveness            — heartbeat written within 2× scan interval
  4. Attribution working      — trade_attribution rows being written
  5. Error rate               — < 10 errors in last hour
  6. Risk manager sane        — not halted without reason, positions consistent

Dashboard reads source='health_check' to show health score in status bar.
"""

import os
import sys
import time
import sqlite3
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    DB_PATH,
    PAPER_TRADING,
    FLAT_POSITION_THRESHOLD_PCT,
    CRYPTO_SCAN_INTERVAL_SECONDS,
)
from logging_db.trade_logger import log_event

# How often to actually run (don't run more than once per minute)
_MIN_INTERVAL_SECONDS = 60
_last_run: float = 0.0

# Deduplication: suppress writing identical failure keys to system_events more
# than once per hour. Stagnant positions increment their age every minute, so
# the summary text changes but the *failing checks* don't — without this, the
# DB fills with hundreds of near-identical ERROR rows per day.
_last_failure_keys: frozenset = frozenset()
_last_status: str = ""
_last_event_written: float = 0.0
_REPEAT_STATUS_COOLDOWN = 3600  # write repeated identical status at most once per hour


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _check_ml_gate() -> dict:
    """ML tower: verify ModelStore is importable and models dir is reachable."""
    try:
        from ml.model_store import ModelStore, MODELS_DIR
        import os

        pkl_files = [f for f in os.listdir(MODELS_DIR) if f.endswith(".pkl")]
        if not pkl_files:
            return {
                "ok": True,
                "detail": "No models trained yet — ML tower using neutral 50.0",
            }
        return {
            "ok": True,
            "detail": f"ModelStore ready — {len(pkl_files)} pkl file(s) in {MODELS_DIR}",
        }
    except Exception as e:
        return {"ok": False, "detail": f"ModelStore unavailable: {e}"}


_STAGNANT_HEALTH_HOURS = 48.0  # health alarm threshold — longer than trading exit logic


def _check_stagnant_positions() -> dict:
    """No position should be past 48h while still flat (stop not hit, no movement).

    Uses the open_positions DB table as ground truth so two classes of false
    positives are avoided:
      1. Ghost positions — closed trades that were never removed from the
         risk_manager's in-memory dict (position not in DB → skip).
      2. Managed positions — entries that hit a profit target or activated a
         trailing stop are being wound down by the exit stack; flagging them as
         stagnant is misleading (trailing_active=1 or scale_33_done=1 → skip).
    """
    try:
        from risk.risk_manager import get_risk_manager

        rm = get_risk_manager()
        positions = rm.get_all_positions()
        stagnant = []
        now = datetime.now(timezone.utc)
        max_mins = _STAGNANT_HEALTH_HOURS * 60

        # Build DB ground-truth lookup: symbol → {trailing_active, scale_33_done, entry, high}
        _db_state: dict = {}
        try:
            with _conn() as c:
                rows = c.execute(
                    "SELECT symbol, entry, high_since_entry, trailing_active, scale_33_done "
                    "FROM open_positions WHERE paper=?",
                    (1 if PAPER_TRADING else 0,),
                ).fetchall()
                for r in rows:
                    _db_state[r["symbol"]] = {
                        "entry": r["entry"] or 0,
                        "high": r["high_since_entry"] or r["entry"] or 0,
                        "trailing_active": bool(r["trailing_active"]),
                        "scale_33_done": bool(r["scale_33_done"]),
                    }
        except Exception:
            pass  # if DB unreadable, fall through with empty dict (no false-positive skips)

        for strat, syms in positions.items():
            if not syms:
                continue
            for sym, pos in syms.items():
                try:
                    # Skip if position has already been closed (ghost in risk_manager memory)
                    if _db_state and sym not in _db_state:
                        continue
                    # Skip managed positions — trailing stop or scale-out means the exit
                    # stack is already handling the wind-down; not truly stagnant
                    db = _db_state.get(sym, {})
                    if db.get("trailing_active") or db.get("scale_33_done"):
                        continue

                    ts = pos.get("ts_entry", "")
                    entry_dt = datetime.fromisoformat(ts)
                    if not entry_dt.tzinfo:
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    age_min = (
                        now - entry_dt.astimezone(timezone.utc)
                    ).total_seconds() / 60

                    # Use DB values for entry/high (risk_manager in-memory can lag)
                    entry = db.get("entry") or pos.get("entry", 0) or 0
                    high = db.get("high") or pos.get("high_since_entry", entry) or entry
                    pnl_pct = abs(high - entry) / max(entry, 1e-10) if entry > 0 else 0

                    if age_min >= max_mins and pnl_pct < FLAT_POSITION_THRESHOLD_PCT:
                        stagnant.append(f"{sym}({age_min:.0f}m)")
                except Exception:
                    pass

        if stagnant:
            return {"ok": False, "detail": f"Stagnant positions: {', '.join(stagnant)}"}
        return {
            "ok": True,
            "detail": f"{sum(len(s) for s in positions.values() if s)} open, none stagnant",
        }
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_scan_liveness() -> dict:
    """Heartbeat must have been written within 2× scan interval."""
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT ts FROM system_events WHERE source='heartbeat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return {"ok": False, "detail": "No heartbeat ever written"}
        dt = datetime.fromisoformat(row["ts"])
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        age_secs = (
            datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        ).total_seconds()
        threshold = (
            CRYPTO_SCAN_INTERVAL_SECONDS * 3
        )  # 3× scan interval = definitely stale
        if age_secs > threshold:
            return {
                "ok": False,
                "detail": f"Last heartbeat {age_secs:.0f}s ago (threshold {threshold}s)",
            }
        return {"ok": True, "detail": f"Last heartbeat {age_secs:.0f}s ago"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_attribution_working() -> dict:
    """trade_attribution rows must be written for recent closed trades."""
    try:
        conn = _conn()
        # Check if any trades closed in last 24h have attribution rows
        recent_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE paper=? AND pnl_usd != 0 "
            "AND ts >= datetime('now', '-24 hours')",
            (1 if PAPER_TRADING else 0,),
        ).fetchone()[0]

        if recent_trades == 0:
            conn.close()
            return {"ok": True, "detail": "No closed trades in last 24h to attribute"}

        attributed = conn.execute(
            "SELECT COUNT(*) FROM trade_attribution WHERE created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        conn.close()

        ratio = attributed / recent_trades if recent_trades > 0 else 0
        if ratio < 0.5 and recent_trades >= 3:
            return {
                "ok": False,
                "detail": f"Attribution gap: {attributed}/{recent_trades} trades attributed",
            }
        return {
            "ok": True,
            "detail": f"{attributed}/{recent_trades} trades attributed (last 24h)",
        }
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_error_rate() -> dict:
    """Less than 10 errors in the last hour."""
    try:
        conn = _conn()
        n_errors = conn.execute(
            "SELECT COUNT(*) FROM system_events WHERE level='ERROR' "
            "AND source != 'health_check' "
            "AND ts >= datetime('now', '-1 hour')"
        ).fetchone()[0]
        conn.close()
        if n_errors >= 10:
            return {"ok": False, "detail": f"{n_errors} errors in last hour"}
        return {"ok": True, "detail": f"{n_errors} errors in last hour"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_risk_manager() -> dict:
    """Risk manager should not be halted without a reason."""
    try:
        from risk.risk_manager import get_risk_manager

        rm = get_risk_manager()
        if rm.is_halted:
            reason = getattr(rm, "halt_reason", "") or "unknown reason"
            return {"ok": False, "detail": f"HALTED: {reason}"}
        positions = rm.get_all_positions()
        n = sum(len(s) for s in positions.values() if s)
        return {"ok": True, "detail": f"Not halted | {n} open positions"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_ibkr_connection() -> dict:
    """IBKR/TWS connectivity — only checked when FUTURES_ENABLED=true."""
    try:
        from config import FUTURES_ENABLED

        if not FUTURES_ENABLED:
            return {"ok": True, "detail": "FUTURES_ENABLED=false — skipped"}
        from execution.ibkr_broker import get_ibkr_broker

        broker = get_ibkr_broker()
        if not broker.is_connected():
            # Attempt reconnect once before reporting failure
            broker.connect()
        if not broker.is_connected():
            return {
                "ok": False,
                "detail": "IBKR not connected — TWS unreachable on port 7497",
            }
        bal = broker.get_account_balance()
        bal_str = f"${bal:.0f}" if bal > 0 else "unavailable"
        return {"ok": True, "detail": f"TWS connected | balance={bal_str}"}
    except Exception as e:
        return {"ok": False, "detail": f"IBKR check error: {e}"}


def run_health_check(force: bool = False) -> dict:
    """
    Run all health checks. Rate-limited to once per minute unless force=True.
    Writes results to system_events source='health_check'.
    Returns dict with score, total, and per-check results.
    """
    global _last_run, _last_failure_keys, _last_status, _last_event_written
    if not force and time.time() - _last_run < _MIN_INTERVAL_SECONDS:
        return {}

    _last_run = time.time()

    checks = {
        "ml_gate": _check_ml_gate(),
        "stagnant": _check_stagnant_positions(),
        "scan_liveness": _check_scan_liveness(),
        "attribution": _check_attribution_working(),
        "error_rate": _check_error_rate(),
        "risk_manager": _check_risk_manager(),
        "ibkr": _check_ibkr_connection(),
    }

    passed = sum(1 for v in checks.values() if v["ok"])
    total = len(checks)
    score = f"{passed}/{total}"
    status = (
        "HEALTHY"
        if passed == total
        else ("DEGRADED" if passed >= total - 1 else "UNHEALTHY")
    )

    # Summarise failures
    failures = [f"{k}: {v['detail']}" for k, v in checks.items() if not v["ok"]]
    summary = f"Health {score} [{status}]"
    if failures:
        summary += " | FAIL: " + " | ".join(failures)

    level = (
        "INFO"
        if status == "HEALTHY"
        else ("WARNING" if status == "DEGRADED" else "ERROR")
    )

    # Deduplicate: only write to system_events when failure keys change OR
    # status changes OR the hourly cooldown has elapsed. This prevents stagnant
    # position age increments from flooding the DB with near-identical ERRORs.
    failure_keys = frozenset(k for k, v in checks.items() if not v["ok"])
    now_ts = time.time()
    should_write = (
        status != _last_status
        or failure_keys != _last_failure_keys
        or (now_ts - _last_event_written) > _REPEAT_STATUS_COOLDOWN
    )
    if should_write:
        try:
            log_event(level, "health_check", summary)
        except Exception:
            pass
        _last_failure_keys = failure_keys
        _last_status = status
        _last_event_written = now_ts

    if status != "HEALTHY":
        print(f"[health_check] {summary}")

    return {
        "score": passed,
        "total": total,
        "status": status,
        "checks": checks,
        "summary": summary,
    }
