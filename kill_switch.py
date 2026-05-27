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

class KillSwitch:
    """System-level trading halt manager."""

    def __init__(self, lane_id: str = "global"):
        self.lane_id = lane_id
        self.lock = threading.RLock()
        self.halted = False
        self.halt_reason = ""
        self.halt_ts = 0.0
        self.api_errors = deque(maxlen=100)
        self.last_latency_ms = 0.0
        self.live_baseline = 0.0
        logger.info(f"[kill_switch] Initialized lane '{lane_id}'")

    def is_halted(self) -> bool:
        with self.lock:
            return self.halted

    def get_halt_reason(self) -> str:
        with self.lock:
            return self.halt_reason

    def _trigger(self, reason: str, balance: float = 0.0):
        """Internal: activate kill switch."""
        with self.lock:
            if self.halted:
                return  # already halted
            self.halted = True
            self.halt_reason = reason
            self.halt_ts = time.time()

        logger.critical(f"[kill_switch] [{self.lane_id.upper()}] TRIGGERED: {reason}")
        
        # ── Grafana IRM Integration ──────────────────────────────────────────────
        try:
            from monitoring.irm_reporter import create_irm_incident
            create_irm_incident(
                title=f"KILL SWITCH [{self.lane_id.upper()}]: {reason}",
                severity="critical",
                description=f"Halt triggered for lane '{self.lane_id}': {reason}",
                labels=[f"scope:{self.lane_id}", f"trigger:{reason.split()[0].lower()}"],
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
                (id, ts, reason, balance, trigger_type, lane)
                VALUES (?, ?, ?, ?, 'trigger', ?)
                """,
                (f"{time.time():.3f}", _now_iso, reason, balance, self.lane_id),
            )
            db.conn.commit()
        except Exception as e:
            logger.debug(f"[kill_switch] DB log error: {e}")

        # Also write to system_events
        try:
            from logging_db.trade_logger import log_event
            log_event("CRITICAL", f"kill_switch_{self.lane_id}", f"TRIGGERED: {reason}")
        except Exception:
            pass

        # Notification
        try:
            from notifications.notification_engine import notify, Category, Severity
            notify(
                category=Category.RISK,
                severity=Severity.CRITICAL,
                title=f"KILL SWITCH [{self.lane_id.upper()}]",
                message=reason,
                data={"balance": balance, "ts": time.time(), "lane": self.lane_id},
            )
        except Exception:
            pass

    def set_live_baseline(self, amount: float) -> None:
        """Explicitly set the live funded account baseline."""
        with self.lock:
            if amount > 0:
                self.live_baseline = float(amount)
                logger.info(f"[kill_switch] [{self.lane_id}] live_baseline set to ${self.live_baseline:.2f}")

    def check_balance(self, current_balance: float, initial_balance: float | None = None):
        """Check if balance has fallen below kill threshold."""
        if initial_balance is None:
            try:
                from runtime.live_account import get_live_account_size
                initial_balance = float(get_live_account_size())
            except Exception:
                initial_balance = 5000.0

        if self.is_halted():
            return

        with self.lock:
            if self.live_baseline <= 0.0 and current_balance > 50.0:
                self.live_baseline = current_balance
                logger.info(f"[kill_switch] [{self.lane_id}] live_baseline auto-set to ${self.live_baseline:.2f}")
            baseline = self.live_baseline if self.live_baseline > 0.0 else float(initial_balance)

        if not _EQUITY_TRIPWIRE_ENABLED:
            return

        threshold = baseline * _LIVE_KILL_PCT
        baseline_desc = f"50% of live baseline ${baseline:.2f}"

        if current_balance > 0 and current_balance < threshold:
            self._trigger(
                f"Balance ${current_balance:.2f} below kill threshold ${threshold:.2f} ({baseline_desc})",
                balance=current_balance,
            )

    def record_api_error(self, error_msg: str = ""):
        """Record an API error. Triggers halt if 5+ errors in 10 minutes."""
        try:
            from monitoring.metrics import increment_api_errors
            increment_api_errors()
        except ImportError:
            pass

        now = time.time()
        with self.lock:
            self.api_errors.append(now)
            cutoff = now - _API_ERROR_WINDOW
            recent_errors = sum(1 for ts in self.api_errors if ts >= cutoff)

        if recent_errors >= _API_ERROR_THRESHOLD:
            self._trigger(f"{recent_errors} API errors in {_API_ERROR_WINDOW // 60} minutes: {error_msg}")

    def record_latency(self, latency_seconds: float):
        """Record order latency. Triggers halt if > 5 seconds."""
        with self.lock:
            self.last_latency_ms = latency_seconds * 1000

        try:
            from monitoring.metrics import update_latency
            update_latency(self.last_latency_ms)
        except ImportError:
            pass

        if latency_seconds > _LATENCY_THRESHOLD_S:
            self._trigger(f"Order latency {latency_seconds:.1f}s exceeds {_LATENCY_THRESHOLD_S}s threshold")

    def resume(self, reason: str = "manual"):
        """Resume trading after a kill switch halt."""
        with self.lock:
            if not self.halted:
                logger.info(f"[kill_switch] [{self.lane_id}] not halted — nothing to resume")
                return

            logger.warning(f"[kill_switch] [{self.lane_id}] RESUMED by: {reason}")
            self.halted = False
            self.halt_reason = ""
            self.halt_ts = 0.0

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
                WHERE trigger_type='trigger' AND resumed_at IS NULL AND lane=?
                """,
                (datetime.now(timezone.utc).isoformat(), self.lane_id),
            )
            db.conn.commit()
        except Exception:
            pass

        try:
            from logging_db.trade_logger import log_event
            log_event("INFO", f"kill_switch_{self.lane_id}", f"RESUMED: {reason}")
        except Exception:
            pass

        try:
            from notifications.notification_engine import notify, Category, Severity
            notify(
                category=Category.RISK,
                severity=Severity.WARNING,
                title=f"Kill Switch Resumed [{self.lane_id.upper()}]",
                message=f"Trading resumed: {reason}",
            )
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return kill switch status dict."""
        with self.lock:
            now = time.time()
            cutoff = now - _API_ERROR_WINDOW
            recent_errors = sum(1 for ts in self.api_errors if ts >= cutoff)
            return {
                "halted": self.halted,
                "halt_reason": self.halt_reason,
                "halt_ts": self.halt_ts,
                "halted_for_s": round(now - self.halt_ts, 0) if self.halt_ts > 0 else 0,
                "api_errors_10m": recent_errors,
                "last_latency_ms": round(self.last_latency_ms, 1),
                "live_baseline": self.live_baseline,
                "live_threshold": round(self.live_baseline * _LIVE_KILL_PCT, 2) if self.live_baseline > 0 else 0.0,
                "lane": self.lane_id
            }

# ── Multi-Instance Manager ───────────────────────────────────────────────────

_switches: Dict[str, KillSwitch] = {}

def get_switch(lane_id: str = "global") -> KillSwitch:
    """Singleton getter for lane-specific kill switches."""
    if lane_id not in _switches:
        _switches[lane_id] = KillSwitch(lane_id)
    return _switches[lane_id]

# ── Module-level Proxy Functions (Backward Compatibility) ─────────────────────

def is_halted() -> bool:
    return get_switch().is_halted()

def get_halt_reason() -> str:
    return get_switch().get_halt_reason()

def set_live_baseline(amount: float) -> None:
    get_switch().set_live_baseline(amount)

def check_balance(current_balance: float, initial_balance: float | None = None):
    get_switch().check_balance(current_balance, initial_balance)

def record_api_error(error_msg: str = ""):
    get_switch().record_api_error(error_msg)

def record_latency(latency_seconds: float):
    get_switch().record_latency(latency_seconds)

def resume(reason: str = "manual"):
    get_switch().resume(reason)

def get_status() -> dict:
    return get_switch().get_status()
