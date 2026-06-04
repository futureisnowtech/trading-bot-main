"""Cadence guards for learning-loop proof tests."""

from __future__ import annotations

import sqlite3
import time

import config

_last_ml_retrain_ts = 0.0
_last_ml_retrain_snapshot_count = 0
_last_rbi_run_ts = 0.0
_last_rbi_snapshot_count = 0


def _import_learning_loop():
    import learning_loop

    return learning_loop


def _import_notification_engine():
    return None


def _learning_snapshot_count() -> int:
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            row = conn.execute("SELECT COUNT(*) FROM learning_snapshots").fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


def ml_retrain_check():
    global _last_ml_retrain_ts, _last_ml_retrain_snapshot_count

    min_hours = float(getattr(config, "ML_RETRAIN_MIN_HOURS", 24))
    min_new = int(getattr(config, "ML_RETRAIN_MIN_NEW_CLEAN_TRADES", 20))
    now = time.time()
    current_snapshots = _learning_snapshot_count()
    elapsed_hours = (now - _last_ml_retrain_ts) / 3600.0 if _last_ml_retrain_ts else float("inf")

    if elapsed_hours < min_hours:
        return None
    if current_snapshots - _last_ml_retrain_snapshot_count < min_new:
        return None

    result = _import_learning_loop().maybe_trigger_retrains()
    _last_ml_retrain_ts = now
    _last_ml_retrain_snapshot_count = current_snapshots
    return result


def rbi_nightly():
    global _last_rbi_run_ts, _last_rbi_snapshot_count

    min_days = float(getattr(config, "RBI_MIN_DAYS", 7))
    min_new = int(getattr(config, "RBI_MIN_NEW_CLEAN_TRADES", 20))
    now = time.time()
    current_snapshots = _learning_snapshot_count()
    elapsed_days = (now - _last_rbi_run_ts) / 86400.0 if _last_rbi_run_ts else float("inf")

    if elapsed_days < min_days:
        return None
    if current_snapshots - _last_rbi_snapshot_count < min_new:
        return None

    result = _import_learning_loop().run_nightly_rbi(symbol="BTCUSDT")
    _last_rbi_run_ts = now
    _last_rbi_snapshot_count = current_snapshots

    notifier = _import_notification_engine()
    if notifier and hasattr(notifier, "send_message"):
        try:
            notifier.send_message(f"Nightly RBI complete: {result}")
        except Exception:
            pass
    return result
