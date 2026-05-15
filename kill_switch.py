"""
kill_switch.py — System-level trading halt with strict tripwires.

Tripwires (any one triggers full halt):
  1. Balance falls below kill threshold — RETIRED in v18.19.2.
     The equity floor is fully covered by spot KS10a (4 consecutive losses),
     KS10b (-2% daily realized PnL), KS10b rolling (3 of last 10 losses).
     Stacking a 50%-of-baseline gate on top caused premature halts after
     normal drawdowns. Re-enable via env EQUITY_KILL_SWITCH_ENABLED=true.
  2. 5+ API errors in 10 minutes — KEPT (different failure mode).
  3. Latency > 5 seconds — KEPT (different failure mode).

On trigger:
  - Sets _halted = True (blocks all new entries in perps_engine / signal_engine)
  - Logs to kill_switch_log table in SQLite
  - Writes CRITICAL event to system_events so dashboard can see it
  - Queues notification via notification_engine

Resume: manual only via resume() call with reason.
"""

import logging
import os
import time
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_halted: bool = False
_halt_reason: str = ""
_halt_ts: float = 0.0

# API error tracking: timestamp deque
_api_errors: deque = deque(maxlen=100)
_API_ERROR_WINDOW = 600  # 10 minutes
_API_ERROR_THRESHOLD = 5  # 5 errors in window

# Latency tracking
_last_latency_ms: float = 0.0
_LATENCY_THRESHOLD_S = 5.0  # 5 seconds

# Balance thresholds — 50% of live_baseline. Disabled by default in v18.19.2.
_LIVE_KILL_PCT = 0.50
_EQUITY_TRIPWIRE_ENABLED: bool = (
    os.getenv("EQUITY_KILL_SWITCH_ENABLED", "false").strip().lower() == "true"
)

# Live baseline — set on first valid live check_balance call.
_live_baseline: float = 0.0


def is_halted() -> bool:
    with _lock:
        return _halted


def get_halt_reason() -> str:
    with _lock:
        return _halt_reason


def _trigger(reason: str, balance: float = 0.0):
    """Internal: activate kill switch."""
    global _halted, _halt_reason, _halt_ts

    with _lock:
        if _halted:
            return  # already halted
        _halted = True
        _halt_reason = reason
        _halt_ts = time.time()

    logger.critical(f"[kill_switch] TRIGGERED: {reason}")
    
    # ── Grafana IRM Integration ──────────────────────────────────────────────
    try:
        from monitoring.irm_reporter import create_irm_incident
        create_irm_incident(
            title=f"GLOBAL KILL SWITCH: {reason}",
            severity="critical",
            description=f"System-level halt triggered: {reason}",
            labels=["scope:global", f"trigger:{reason.split()[0].lower()}"],
            extra_details={"balance": balance, "timestamp": time.time()}
        )
    except Exception as e:
        logger.debug(f"[kill_switch] irm report failed: {e}")

    # 📊 Metrics
    try:
        from monitoring.metrics import update_kill_switch
        update_kill_switch(True)
    except ImportError:
        pass

    _now_iso = datetime.now(timezone.utc).isoformat()

    # Log to kill_switch_log
    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()
        db.conn.execute(
            """
            INSERT OR IGNORE INTO kill_switch_log
            (id, ts, reason, balance, trigger_type)
            VALUES (?, ?, ?, ?, 'trigger')
            """,
            (f"{time.time():.3f}", _now_iso, reason, balance),
        )
        db.conn.commit()
    except Exception as e:
        logger.debug(f"[kill_switch] DB log error: {e}")

    # Also write to system_events
    try:
        from logging_db.trade_logger import log_event

        log_event("CRITICAL", "kill_switch", f"TRIGGERED: {reason}")
    except Exception:
        pass

    # Notification
    try:
        from notifications.notification_engine import notify, Category, Severity

        notify(
            category=Category.RISK,
            severity=Severity.CRITICAL,
            title="KILL SWITCH TRIGGERED",
            message=reason,
            data={"balance": balance, "ts": time.time()},
        )
    except Exception:
        pass


def set_live_baseline(amount: float) -> None:
    """
    Explicitly set the live funded account baseline.
    Call once at live startup with the actual Coinbase balance.
    """
    global _live_baseline
    with _lock:
        if amount > 0:
            _live_baseline = float(amount)
            logger.info(f"[kill_switch] live_baseline set to ${_live_baseline:.2f}")


def check_balance(
    current_balance: float, initial_balance: float | None = None
):
    """
    Check if balance has fallen below kill threshold.
    Call this on every P&L update.

    Kill-threshold policy:
      threshold = live_baseline * 0.50
    Disabled by default in v18.19.2 — opt-in via EQUITY_KILL_SWITCH_ENABLED=true.
    """
    global _live_baseline

    if initial_balance is None:
        try:
            from runtime.live_account import get_live_account_size

            initial_balance = float(get_live_account_size())
        except Exception:
            initial_balance = 5000.0

    if is_halted():
        return

    # Establish baseline from first valid balance if not yet set.
    # Baseline auto-set stays live even when the tripwire is disabled,
    # so dashboards / get_status() keep reporting the running baseline.
    with _lock:
        if _live_baseline <= 0.0 and current_balance > 50.0:
            _live_baseline = current_balance
            logger.info(
                f"[kill_switch] live_baseline auto-set to ${_live_baseline:.2f}"
            )
        baseline = _live_baseline if _live_baseline > 0.0 else float(initial_balance)

    if not _EQUITY_TRIPWIRE_ENABLED:
        return

    threshold = baseline * _LIVE_KILL_PCT
    baseline_desc = f"50% of live baseline ${baseline:.2f}"

    if current_balance > 0 and current_balance < threshold:
        _trigger(
            f"Balance ${current_balance:.2f} below kill threshold ${threshold:.2f} "
            f"({baseline_desc})",
            balance=current_balance,
        )


def record_api_error(error_msg: str = ""):
    """
    Record an API error. Triggers halt if 5+ errors in 10 minutes.
    """
    # 📊 Metrics
    try:
        from monitoring.metrics import increment_api_errors
        increment_api_errors()
    except ImportError:
        pass

    now = time.time()
    with _lock:
        _api_errors.append(now)
        # Count errors in window
        cutoff = now - _API_ERROR_WINDOW
        recent_errors = sum(1 for ts in _api_errors if ts >= cutoff)

    if recent_errors >= _API_ERROR_THRESHOLD:
        _trigger(
            f"{recent_errors} API errors in {_API_ERROR_WINDOW // 60} minutes: {error_msg}",
        )


def record_latency(latency_seconds: float):
    """
    Record order latency. Triggers halt if > 5 seconds.
    """
    global _last_latency_ms
    _last_latency_ms = latency_seconds * 1000

    # 📊 Metrics
    try:
        from monitoring.metrics import update_latency
        update_latency(_last_latency_ms)
    except ImportError:
        pass

    if latency_seconds > _LATENCY_THRESHOLD_S:
        _trigger(
            f"Order latency {latency_seconds:.1f}s exceeds {_LATENCY_THRESHOLD_S}s threshold",
        )


def resume(reason: str = "manual"):
    """
    Resume trading after a kill switch halt.
    Should only be called manually after investigating the trigger.
    """
    global _halted, _halt_reason, _halt_ts

    with _lock:
        if not _halted:
            logger.info("[kill_switch] System not halted — nothing to resume")
            return

        logger.warning(f"[kill_switch] RESUMED by: {reason}")
        _halted = False
        _halt_reason = ""
        _halt_ts = 0.0

    # 📊 Metrics
    try:
        from monitoring.metrics import update_kill_switch
        update_kill_switch(False)
    except ImportError:
        pass

    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()
        db.conn.execute(
            """
            UPDATE kill_switch_log SET resumed_at=?
            WHERE trigger_type='trigger' AND resumed_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        db.conn.commit()
    except Exception:
        pass

    try:
        from logging_db.trade_logger import log_event

        log_event("INFO", "kill_switch", f"RESUMED: {reason}")
    except Exception:
        pass

    try:
        from notifications.notification_engine import notify, Category, Severity

        notify(
            category=Category.RISK,
            severity=Severity.WARNING,
            title="Kill Switch Resumed",
            message=f"Trading resumed: {reason}",
        )
    except Exception:
        pass


def get_status() -> dict:
    """Return kill switch status dict for dashboard."""
    with _lock:
        now = time.time()
        cutoff = now - _API_ERROR_WINDOW
        recent_errors = sum(1 for ts in _api_errors if ts >= cutoff)
        return {
            "halted": _halted,
            "halt_reason": _halt_reason,
            "halt_ts": _halt_ts,
            "halted_for_s": round(now - _halt_ts, 0) if _halt_ts > 0 else 0,
            "api_errors_10m": recent_errors,
            "last_latency_ms": round(_last_latency_ms, 1),
            "live_baseline": _live_baseline,
            "live_threshold": round(_live_baseline * _LIVE_KILL_PCT, 2)
            if _live_baseline > 0
            else 0.0,
        }
