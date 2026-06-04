"""Kalshi-only health checks for the lean runtime."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _check_sqlite() -> dict:
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with _connect() as conn:
            conn.execute("SELECT 1")
        return {"name": "sqlite", "ok": True, "detail": "reachable"}
    except Exception as exc:
        return {"name": "sqlite", "ok": False, "detail": str(exc)}


def _check_kalshi_credentials() -> dict:
    key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
    ok = bool(key_id and key_path)
    detail = "present"
    if not ok:
        detail = "missing key id or private key path"
    elif not Path(key_path).expanduser().exists():
        ok = False
        detail = f"private key path missing: {key_path}"
    return {"name": "kalshi_credentials", "ok": ok, "detail": detail}


def _check_telegram() -> dict:
    ok = bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    )
    return {
        "name": "telegram",
        "ok": ok,
        "detail": "present" if ok else "missing bot token or chat id",
    }


def _check_error_rate(lookback_minutes: int = 60, unhealthy_threshold: int = 10) -> dict:
    try:
        with _connect() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_events'"
            ).fetchone()
            if not table:
                return {
                    "name": "recent_errors",
                    "ok": True,
                    "detail": "system_events unavailable",
                }
            rows = conn.execute(
                """
                SELECT source, level, message
                FROM system_events
                WHERE level IN ('ERROR', 'CRITICAL')
                  AND datetime(replace(substr(ts,1,19),'T',' '))
                      >= datetime('now', ?)
                """,
                (f"-{int(lookback_minutes)} minutes",),
            ).fetchall()
    except Exception as exc:
        return {"name": "recent_errors", "ok": False, "detail": str(exc)}

    count = len(rows)
    return {
        "name": "recent_errors",
        "ok": count < unhealthy_threshold,
        "detail": f"{count} recent errors",
    }


def run_health_check(force: bool = False) -> dict:
    checks = [
        _check_sqlite(),
        _check_error_rate(),
        _check_kalshi_credentials(),
        _check_telegram(),
    ]

    healthy = all(check["ok"] for check in checks)
    return {
        "healthy": healthy,
        "status": "HEALTHY" if healthy else "DEGRADED",
        "checks": checks,
    }
