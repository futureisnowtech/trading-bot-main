"""
kill_switch.py — System-level trading halt with strict tripwires.

Tripwires (any one triggers full halt):
  1. Balance falls below kill threshold (see below)
  2. 5+ API errors in 10 minutes
  3. Latency > 5 seconds (measured from order send to fill ack)

Balance kill-threshold policy (operator-approved 2026-04-15):
  PAPER mode : balance < 75% of initial balance (ACCOUNT_SIZE)
  LIVE mode  : balance < 50% of live_baseline
               live_baseline = first valid live balance seen at runtime
               (~$1,966 live funded account → threshold ~$983)

On trigger:
  - Sets _halted = True (blocks all new entries in perps_engine / signal_engine)
  - Logs to kill_switch_log table in SQLite
  - Writes CRITICAL event to system_events so dashboard can see it
  - Queues notification via notification_engine

Resume: manual only via resume() call with reason.
"""

import logging
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

# Balance thresholds — operator-approved live policy (2026-04-15)
_LIVE_KILL_PCT = 0.50  # live: 50% of live_baseline (loose, protects funded account)
_PAPER_KILL_PCT = 0.75  # paper: 75% of initial balance (unchanged)

# Live baseline — set on first valid live check_balance call.
# Prevents false triggers from the stale ACCOUNT_SIZE config value.
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

    _now_iso = datetime.now(timezone.utc).isoformat()

    # Log to kill_switch_log (schema: id TEXT PK, ts TEXT, reason TEXT, balance REAL,
    # peak_balance REAL, positions_closed INT, resumed_at TEXT, trigger_type TEXT)
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

    # Also write to system_events so dashboard can see the trigger without
    # needing to query kill_switch_log separately.
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
    If not called, the baseline is set automatically on the first valid
    check_balance(paper=False) call with current_balance > 50.
    """
    global _live_baseline
    with _lock:
        if amount > 0:
            _live_baseline = float(amount)
            logger.info(f"[kill_switch] live_baseline set to ${_live_baseline:.2f}")


def check_balance(
    current_balance: float, initial_balance: float | None = None, paper: bool = True
):
    """
    Check if balance has fallen below kill threshold.
    Call this on every P&L update.

    Kill-threshold policy (operator-approved 2026-04-15):
      paper=True  → threshold = initial_balance * 0.75
      paper=False → threshold = live_baseline * 0.50
                    live_baseline set on first valid call (auto-established from
                    first broker-reported balance; ~$1,966 → threshold ~$983)
    """
    global _live_baseline

    if initial_balance is None:
        try:
            from runtime.live_account import get_live_account_size

            initial_balance = float(get_live_account_size(paper=paper))
        except Exception:
            initial_balance = 5000.0

    if is_halted():
        return

    if paper:
        threshold = initial_balance * _PAPER_KILL_PCT
        baseline_desc = f"75% of initial ${initial_balance:.0f}"
    else:
        # Live mode: establish baseline from first valid balance if not yet set
        with _lock:
            if _live_baseline <= 0.0 and current_balance > 50.0:
                _live_baseline = current_balance
                logger.info(
                    f"[kill_switch] live_baseline auto-set to ${_live_baseline:.2f}"
                )
            baseline = _live_baseline if _live_baseline > 0.0 else float(initial_balance)
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
    Call from perps_engine / binance_broker on any OrderRejected / timeout.
    """
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
    Call after each order fill ack with (ack_ts - send_ts).
    """
    global _last_latency_ms
    _last_latency_ms = latency_seconds * 1000

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

    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()
        # Schema: id TEXT PK, ts TEXT, reason TEXT, balance REAL, peak_balance REAL,
        #         positions_closed INT, resumed_at TEXT, trigger_type TEXT
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
