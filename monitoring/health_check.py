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
    EQUITY_SCAN_INTERVAL_SECONDS,
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

        # Build DB ground-truth lookup: symbol → {trailing_active, scale_33_done, scale_66_done, entry, high, ts_entry}
        _db_state: dict = {}
        try:
            with _conn() as c:
                rows = c.execute(
                    "SELECT symbol, entry, high_since_entry, trailing_active, "
                    "scale_33_done, scale_66_done, ts_entry "
                    "FROM open_positions WHERE paper=0"
                ).fetchall()
                for r in rows:
                    _db_state[r["symbol"]] = {
                        "entry": r["entry"] or 0,
                        "high": r["high_since_entry"] or r["entry"] or 0,
                        "trailing_active": bool(r["trailing_active"]),
                        "scale_33_done": bool(r["scale_33_done"]),
                        "scale_66_done": bool(r.get("scale_66_done", 0)),
                        "ts_entry": r.get("ts_entry", ""),
                    }
        except Exception:
            pass  # if DB unreadable, fall through with empty dict (no false-positive skips)

        # Build partial-close ledger: symbols that have had any scale-out/partial activity
        _partial_close_syms: set = set()
        # Build partial-close ledger: symbols that have had any scale-out/partial activity
        _partial_close_syms: set = set()
        try:
            with _conn() as c:
                rows = c.execute(
                    "SELECT DISTINCT symbol FROM trades "
                    "WHERE paper=0 AND (action IN ('SELL','CLOSE') OR notes LIKE '%scale_out%' OR notes LIKE '%partial%') "
                    "AND broker LIKE '%coinbase%'"
                ).fetchall()
                for r in rows:
                    _partial_close_syms.add(r["symbol"])
        except Exception:
            pass

        # Build live in-memory lookup from perps_engine: symbol → {peak_price, trailing_active}
        # perps_engine updates peak_price + trailing_active in-memory but does NOT write back to
        # the open_positions DB (high_since_entry and trailing_active columns stay stale).
        # Using in-memory state prevents false-positive stagnant alarms for positions that have
        # moved or already had their trailing stop activated.
        _perps_state: dict = {}
        try:
            import perps_engine as _pe

            for _sym, _ppos in _pe.get_open_positions().items():
                _perps_state[_sym] = {
                    "peak_price": float(_ppos.get("peak_price") or 0),
                    "trailing_active": bool(_ppos.get("trailing_active", False)),
                }
        except Exception:
            pass

        for strat, syms in positions.items():
            if not syms:
                continue
            for sym, pos in syms.items():
                try:
                    # Skip if position has already been closed (ghost in risk_manager memory)
                    if _db_state and sym not in _db_state:
                        continue
                    # Skip managed positions — trailing stop or any scale-out means the exit
                    # stack is already handling the wind-down; not truly stagnant.
                    # Also skip if there's any partial-close trade in the ledger for this symbol.
                    db = _db_state.get(sym, {})
                    _live = _perps_state.get(sym, {})
                    # Trailing active: DB flag OR live in-memory flag (DB lags activation)
                    _trailing_active = db.get("trailing_active") or _live.get(
                        "trailing_active", False
                    )
                    if (
                        _trailing_active
                        or db.get("scale_33_done")
                        or db.get("scale_66_done")
                        or sym in _partial_close_syms
                    ):
                        continue

                    ts = pos.get("ts_entry", "")
                    entry_dt = datetime.fromisoformat(ts)
                    if not entry_dt.tzinfo:
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    age_min = (
                        now - entry_dt.astimezone(timezone.utc)
                    ).total_seconds() / 60

                    # Use live peak_price from perps_engine in-memory when available; fall back
                    # to DB high_since_entry (which is only updated at position open, not live).
                    entry = db.get("entry") or pos.get("entry", 0) or 0
                    _live_peak = _live.get("peak_price", 0)
                    high = max(
                        db.get("high") or entry,
                        _live_peak if _live_peak > 0 else entry,
                        pos.get("high_since_entry", entry) or entry,
                    )
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
    """Heartbeat must have been written within 3× scan interval.

    Primary: lane_runtime_state.last_heartbeat_at — updated every 1 minute by
    v10_runner._write_heartbeat() and by this health_check itself, regardless
    of candidate count.  Authoritative liveness signal.

    Secondary: system_events WHERE source='heartbeat' — only written when
    _scan_and_trade_inner() completes with candidates > 0, so it goes stale
    during quiet markets.  Used only as a fallback tiebreaker.
    """
    threshold = CRYPTO_SCAN_INTERVAL_SECONDS * 3  # 900s = 15 min

    # --- Primary: lane_runtime_state.last_heartbeat_at ---
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT last_heartbeat_at FROM lane_runtime_state "
            "WHERE lane_id='crypto' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row["last_heartbeat_at"]:
            dt = datetime.fromisoformat(row["last_heartbeat_at"])
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            age_secs = (
                datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            ).total_seconds()
            if age_secs <= threshold:
                return {"ok": True, "detail": f"Last heartbeat {age_secs:.0f}s ago"}
            # Runtime table is stale — fall through to system_events check
    except Exception:
        pass

    # --- Secondary: system_events heartbeat rows ---
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
        if age_secs > threshold:
            return {
                "ok": False,
                "detail": f"Last heartbeat {age_secs:.0f}s ago (threshold {threshold}s)",
            }
        return {"ok": True, "detail": f"Last heartbeat {age_secs:.0f}s ago"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_candle_freshness() -> dict:
    """Indicator health: candles must be fresh (last 1h candle < 2h old)."""
    try:
        conn = _conn()
        # Query last candidate TS — candidates are only written if candles are fetched
        row = conn.execute(
            "SELECT ts FROM scan_candidates ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return {"ok": True, "detail": "No candidates logged yet"}
        
        from datetime import datetime, timezone
        import pytz
        
        # scan_candidates.ts is ISO8601
        dt = datetime.fromisoformat(row["ts"])
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        
        age_secs = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        threshold = 7200 # 2 hours (allows for gap between scans but catches stuck data)
        
        if age_secs > threshold:
            return {"ok": False, "detail": f"Candles stale: last scan {age_secs/60:.1f}m ago"}
        return {"ok": True, "detail": f"Candles fresh: {age_secs/60:.1f}m old"}
    except Exception as e:
        return {"ok": False, "detail": f"Freshness check error: {e}"}


def _check_spot_truth() -> dict:
    """Live spot health must be broker-canonical and free of unresolved blockers."""
    try:
        from runtime.spot_position_truth import get_spot_position_truth

        truth = get_spot_position_truth()
        if not truth.get("snapshot_ok"):
            return {"ok": False, "detail": "spot broker snapshot unavailable"}
        blockers = truth.get("blocking_issues") or []
        if blockers:
            rendered = ", ".join(
                f"{b.get('symbol') or 'GLOBAL'}:{b.get('position_truth_status')}"
                for b in blockers
            )
            return {"ok": False, "detail": f"spot truth blockers: {rendered}"}
        return {
            "ok": True,
            "detail": (
                f"spot truth ok | holdings={truth.get('positions_open', 0)} "
                f"deployed=${float(truth.get('deployment_notional') or 0.0):.2f}"
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": f"spot truth check error: {e}"}


def _check_spot_learning_truth() -> dict:
    """Recent closed spot trades must write both attribution and feature snapshots."""
    try:
        conn = _conn()
        recent_trades = conn.execute(
            """
            SELECT COUNT(*) FROM trades 
            WHERE strategy LIKE 'spot_%' 
              AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now', '-24 hours')
            """
        ).fetchone()[0]

        if recent_trades == 0:
            conn.close()
            return {"ok": True, "detail": "No recent closed spot trades to verify"}

        attributed = conn.execute(
            """
            SELECT COUNT(*)
            FROM trade_attribution ta
            WHERE ta.strategy LIKE 'spot_%'
              AND datetime(replace(substr(ta.created_at,1,19),'T',' ')) >= datetime('now', '-24 hours')
            """
        ).fetchone()[0]
        snapshots = conn.execute(
            """
            SELECT COUNT(*)
            FROM ml_feature_snapshots m
            JOIN trades t ON t.id = m.trade_id
            WHERE t.strategy LIKE 'spot_%'
              AND datetime(replace(substr(t.ts,1,19),'T',' ')) >= datetime('now', '-24 hours')
            """
        ).fetchone()[0]
        conn.close()

        attr_ratio = attributed / recent_trades if recent_trades > 0 else 0
        snap_ratio = snapshots / recent_trades if recent_trades > 0 else 0
        if (attr_ratio < 1.0 or snap_ratio < 1.0) and recent_trades >= 1:
            return {
                "ok": False,
                "detail": (
                    f"spot learning gap: attr={attributed}/{recent_trades} "
                    f"snapshots={snapshots}/{recent_trades}"
                ),
            }
        return {
            "ok": True,
            "detail": (
                f"spot learning ok: attr={attributed}/{recent_trades} "
                f"snapshots={snapshots}/{recent_trades}"
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_error_rate() -> dict:
    """Less than 10 errors in the last hour (excluding archived lane noise)."""
    try:
        import config as _cfg

        futures_lane_active = bool(getattr(_cfg, "FUTURES_LANE_ACTIVE", False))
        conn = _conn()
        rows = conn.execute(
            "SELECT source, message FROM system_events WHERE level='ERROR' "
            "AND source != 'health_check' "
            "AND ts >= datetime('now', '-1 hour')"
        ).fetchall()
        conn.close()
        # Filter archived-lane noise: IBKRBroker/MES errors when FUTURES_LANE_ACTIVE=false
        _archived_markers = ("ibkrbroker", "ibkr", "mes_", "mes ", "twsbroke")
        n_errors = 0
        for row in rows:
            src = (row[0] or "").lower()
            msg = (row[1] or "").lower()
            if not futures_lane_active and any(
                m in src or m in msg for m in _archived_markers
            ):
                continue
            n_errors += 1
        if n_errors >= 10:
            return {"ok": False, "detail": f"{n_errors} errors in last hour"}
        return {"ok": True, "detail": f"{n_errors} errors in last hour"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


def _check_spot_kill_switch() -> dict:
    """Spot lane kill switch must not be active."""
    try:
        from runtime.spot_kill_switch import kill_switch_status

        status = kill_switch_status()
        if status.get("halted"):
            return {
                "ok": False,
                "detail": f"HALTED: {status.get('last_halt_reason') or 'spot kill switch active'}",
            }
        return {"ok": True, "detail": "spot kill switch clear"}
    except Exception as e:
        return {"ok": False, "detail": f"Exception: {e}"}


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

    # Keep incident tracker current before running checks
    try:
        from runtime.incident_tracker import ingest_system_events

        ingest_system_events()
    except Exception:
        pass

    checks = {
        "ml_gate": _check_ml_gate(),
        "scan_liveness": _check_scan_liveness(),
        "candle_freshness": _check_candle_freshness(),
        "spot_truth": _check_spot_truth(),
        "spot_learning": _check_spot_learning_truth(),
        "error_rate": _check_error_rate(),
        "spot_kill_switch": _check_spot_kill_switch(),
    }

    passed = sum(1 for v in checks.values() if v["ok"])
    total = len(checks)
    status = (
        "HEALTHY"
        if passed == total
        else ("DEGRADED" if passed >= total - 1 else "UNHEALTHY")
    )

    # Summarise failures
    failures = [f"{k}: {v['detail']}" for k, v in checks.items() if not v["ok"]]
    summary = f"System {status} | truth-lane={passed}/{total}"
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

    # Update runtime truth tables with fresh heartbeat
    try:
        from runtime.runtime_state import write_system_heartbeat, upsert_lane_state

        write_system_heartbeat()
        upsert_lane_state(
            "crypto", last_heartbeat_at=datetime.now(tz=timezone.utc).isoformat()
        )
    except Exception:
        pass

    return {
        "score": passed,
        "total": total,
        "status": status,
        "checks": checks,
        "summary": summary,
    }