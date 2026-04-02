"""
kill_switch.py — System-level trading halt with strict tripwires.

Tripwires (any one triggers full halt):
  1. Balance < $7,500 (75% of $10K peak — for paper use <75% of starting $5K)
  2. 5+ API errors in 10 minutes
  3. Latency > 5 seconds (measured from order send to fill ack)

On trigger:
  - Sets _halted = True (blocks all new entries in perps_engine / signal_engine)
  - Logs to kill_switch_log table in SQLite
  - Queues notification via notification_engine

Resume: manual only via resume() call with reason.
"""

import logging
import time
import threading
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_halted: bool = False
_halt_reason: str = ''
_halt_ts: float = 0.0

# API error tracking: timestamp deque
_api_errors: deque = deque(maxlen=100)
_API_ERROR_WINDOW = 600      # 10 minutes
_API_ERROR_THRESHOLD = 5     # 5 errors in window

# Latency tracking
_last_latency_ms: float = 0.0
_LATENCY_THRESHOLD_S = 5.0   # 5 seconds

# Balance thresholds
_KILL_BALANCE_USD = 7500.0   # $10K account: 75% peak
_PAPER_KILL_PCT   = 0.75     # paper: 75% of initial balance


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
            return   # already halted
        _halted = True
        _halt_reason = reason
        _halt_ts = time.time()

    logger.critical(f'[kill_switch] TRIGGERED: {reason}')

    # Log to DB
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            INSERT OR IGNORE INTO kill_switch_log
            (ts, reason, balance_at_trigger, resolved)
            VALUES (?, ?, ?, 0)
        """, (time.time(), reason, balance))
        db.conn.commit()
    except Exception as e:
        logger.debug(f'[kill_switch] DB log error: {e}')

    # Notification
    try:
        from notifications.notification_engine import notify, Category, Severity
        notify(
            category=Category.RISK,
            severity=Severity.CRITICAL,
            title='KILL SWITCH TRIGGERED',
            message=reason,
            data={'balance': balance, 'ts': time.time()},
        )
    except Exception:
        pass


def check_balance(current_balance: float, initial_balance: float = 10000.0,
                   paper: bool = True):
    """
    Check if balance has fallen below kill threshold.
    Call this on every P&L update.
    """
    if is_halted():
        return

    if paper:
        threshold = initial_balance * _PAPER_KILL_PCT
    else:
        threshold = _KILL_BALANCE_USD

    if current_balance < threshold:
        _trigger(
            f'Balance ${current_balance:.0f} below kill threshold ${threshold:.0f} '
            f'({_PAPER_KILL_PCT:.0%} of ${initial_balance:.0f})',
            balance=current_balance,
        )


def record_api_error(error_msg: str = ''):
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
            f'{recent_errors} API errors in {_API_ERROR_WINDOW//60} minutes: {error_msg}',
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
            f'Order latency {latency_seconds:.1f}s exceeds {_LATENCY_THRESHOLD_S}s threshold',
        )


def resume(reason: str = 'manual'):
    """
    Resume trading after a kill switch halt.
    Should only be called manually after investigating the trigger.
    """
    global _halted, _halt_reason, _halt_ts

    with _lock:
        if not _halted:
            logger.info('[kill_switch] System not halted — nothing to resume')
            return

        logger.warning(f'[kill_switch] RESUMED by: {reason}')
        _halted = False
        _halt_reason = ''
        _halt_ts = 0.0

    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            UPDATE kill_switch_log SET resolved=1, resolved_ts=?, resolved_reason=?
            WHERE resolved=0
        """, (time.time(), reason))
        db.conn.commit()
    except Exception:
        pass

    try:
        from notifications.notification_engine import notify, Category, Severity
        notify(
            category=Category.RISK,
            severity=Severity.WARNING,
            title='Kill Switch Resumed',
            message=f'Trading resumed: {reason}',
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
            'halted': _halted,
            'halt_reason': _halt_reason,
            'halt_ts': _halt_ts,
            'halted_for_s': round(now - _halt_ts, 0) if _halt_ts > 0 else 0,
            'api_errors_10m': recent_errors,
            'last_latency_ms': round(_last_latency_ms, 1),
        }
