"""
runtime/incident_tracker.py — Groups repeated errors into incidents for Kalshi Weather Engine.
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH

_DDL_INCIDENTS = """
CREATE TABLE IF NOT EXISTS incidents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lane_id        TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    fingerprint    TEXT NOT NULL,
    sample_message TEXT NOT NULL DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    severity       TEXT NOT NULL DEFAULT 'INFO',
    state          TEXT NOT NULL DEFAULT 'open',
    alerted_at     TEXT,
    last_alerted_count INTEGER NOT NULL DEFAULT 0,
    last_alerted_severity TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL,
    UNIQUE(lane_id, fingerprint)
)
"""

def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _lane_from_source(source: str) -> str:
    """Map a system_events source name to a lane_id."""
    src = (source or "").lower()
    if any(x in src for x in ("forecastrunner", "forecast", "forecastex", "kalshi", "weather")):
        return "forecast"
    return "system"

def _severity_from_level(level: str) -> str:
    lvl = (level or "INFO").upper()
    if lvl in ("CRITICAL", "FATAL"): return "CRITICAL"
    if lvl == "ERROR": return "ERROR"
    if lvl in ("WARNING", "WARN"): return "WARNING"
    return "INFO"


def _severity_rank(level: str) -> int:
    severity = _severity_from_level(level)
    return {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}.get(severity, 0)


def _ensure_incident_columns(c: sqlite3.Connection) -> None:
    cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(incidents)").fetchall()}
    for name, ddl in (
        ("sample_message", "sample_message TEXT NOT NULL DEFAULT ''"),
        ("alerted_at", "alerted_at TEXT"),
        ("last_alerted_count", "last_alerted_count INTEGER NOT NULL DEFAULT 0"),
        ("last_alerted_severity", "last_alerted_severity TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in cols:
            c.execute(f"ALTER TABLE incidents ADD COLUMN {ddl}")

def init_incident_table(db_path: str = DB_PATH) -> None:
    with _conn(db_path) as c:
        c.execute(_DDL_INCIDENTS)
        _ensure_incident_columns(c)

def ingest_system_events(lookback_minutes: int = 60, db_path: str = DB_PATH) -> int:
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat()
    try:
        with _conn(db_path) as c:
            rows = c.execute("SELECT source, level, message, ts FROM system_events WHERE ts >= ? ORDER BY ts", (cutoff_iso,)).fetchall()
    except Exception: return 0

    groups: dict[tuple, dict] = {}
    for row in rows:
        source = row["source"] or ""
        level = row["level"] or "INFO"
        message = row["message"] or ""
        ts = row["ts"] or _now_iso()

        lane_id = _lane_from_source(source)
        if (level or "INFO").upper() == "INFO": continue

        fp = f"{source}::{message[:80].lower().strip()}"
        key = (lane_id, fp)
        if key not in groups:
            groups[key] = {
                "lane_id": lane_id,
                "source": source,
                "fingerprint": fp,
                "sample_message": message[:240],
                "first_seen_at": ts,
                "last_seen_at": ts,
                "count": 1,
                "severity": _severity_from_level(level),
            }
        else:
            groups[key]["count"] += 1
            groups[key]["last_seen_at"] = max(groups[key]["last_seen_at"], ts)
            new_sev = _severity_from_level(level)
            if _severity_rank(new_sev) > _severity_rank(groups[key]["severity"]):
                groups[key]["severity"] = new_sev
            groups[key]["sample_message"] = message[:240]

    if not groups: return 0

    now = _now_iso()
    upserted = 0
    try:
        with _conn(db_path) as c:
            _ensure_incident_columns(c)
            for key, g in groups.items():
                c.execute("""
                    INSERT INTO incidents (
                        lane_id, source, fingerprint, sample_message,
                        first_seen_at, last_seen_at, count, severity, state, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    ON CONFLICT(lane_id, fingerprint) DO UPDATE SET
                        sample_message = excluded.sample_message,
                        last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                        count        = count + excluded.count,
                        severity     = CASE
                            WHEN excluded.severity='CRITICAL' OR severity='CRITICAL' THEN 'CRITICAL'
                            WHEN excluded.severity='ERROR' OR severity='ERROR' THEN 'ERROR'
                            WHEN excluded.severity='WARNING' OR severity='WARNING' THEN 'WARNING'
                            ELSE 'INFO' END,
                        state        = CASE WHEN state='resolved' THEN 'open' ELSE state END,
                        updated_at   = excluded.updated_at
                """, (
                    g["lane_id"],
                    g["source"],
                    g["fingerprint"],
                    g["sample_message"],
                    g["first_seen_at"],
                    g["last_seen_at"],
                    g["count"],
                    g["severity"],
                    now,
                ))
                upserted += 1
    except Exception: pass
    return upserted

def get_open_incidents(db_path: str = DB_PATH) -> list:
    try:
        with _conn(db_path) as c:
            rows = c.execute("SELECT * FROM incidents WHERE state='open' ORDER BY last_seen_at DESC").fetchall()
            return [dict(r) for r in rows]
    except Exception: return []

def get_incident_summary(db_path: str = DB_PATH) -> dict:
    try:
        with _conn(db_path) as c:
            rows = c.execute("SELECT lane_id, severity, COUNT(*) as cnt FROM incidents WHERE state='open' GROUP BY lane_id, severity").fetchall()
        by_lane, by_severity, total = {}, {}, 0
        for r in rows:
            lane, sev, cnt = r["lane_id"], r["severity"], r["cnt"]
            total += cnt
            by_lane[lane] = by_lane.get(lane, 0) + cnt
            by_severity[sev] = by_severity.get(sev, 0) + cnt
        return {"total_open": total, "by_lane": by_lane, "by_severity": by_severity}
    except Exception: return {"total_open": 0, "by_lane": {}, "by_severity": {}}


def _incident_text(row: dict | sqlite3.Row) -> str:
    record = dict(row)
    return str(record.get("sample_message") or record.get("fingerprint") or "").lower()


def _is_operator_alert_incident(row: dict | sqlite3.Row) -> bool:
    text = _incident_text(row)
    if not text:
        return False
    if " vetoed: " in text or "execution_blocked:" in text:
        return False
    if "depth_slipped_after_submission" in text or "insufficient_resting_volume" in text:
        return False

    alert_tokens = (
        "too_many_requests",
        "rate limit",
        "429",
        "connection failed",
        "auth verification failed",
        "position sync error",
        "sync_positions_failed",
        "get_account_balance_failed",
        "get_positions_failed",
        "get_quote error",
        "broker bootstrap failed",
        "stale_runtime_heartbeat",
        "open-meteo 429",
        "http_4",
        "http_5",
        "json_decode_failed",
    )
    return any(token in text for token in alert_tokens)


def sync_incidents_and_notify(
    *,
    lookback_minutes: int = 60,
    db_path: str = DB_PATH,
) -> dict:
    from notifications.notification_engine import notify_system

    init_incident_table(db_path=db_path)
    upserted = ingest_system_events(lookback_minutes=lookback_minutes, db_path=db_path)
    alerted = 0
    now = _now_iso()

    try:
        with _conn(db_path) as c:
            _ensure_incident_columns(c)
            rows = c.execute(
                "SELECT * FROM incidents WHERE state='open' ORDER BY last_seen_at DESC"
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                if not _is_operator_alert_incident(row):
                    continue

                severity = _severity_from_level(str(row.get("severity") or "INFO"))
                count = int(row.get("count") or 0)
                last_alerted_count = int(row.get("last_alerted_count") or 0)
                last_alerted_severity = str(row.get("last_alerted_severity") or "INFO")
                should_alert = not row.get("alerted_at")
                if not should_alert and _severity_rank(severity) > _severity_rank(last_alerted_severity):
                    should_alert = True
                if not should_alert and count >= max(3, last_alerted_count + 5):
                    should_alert = True
                if not should_alert:
                    continue

                notify_system(
                    title=f"{row.get('source') or row.get('lane_id')}: {severity}",
                    detail=(
                        f"{row.get('sample_message') or row.get('fingerprint')}\n"
                        f"Count={count}  Lane={row.get('lane_id')}  "
                        f"LastSeen={row.get('last_seen_at')}"
                    ),
                    severity="CRITICAL" if severity in {"ERROR", "CRITICAL"} else "WARNING",
                    telegram=True,
                    data={
                        "incident_id": row.get("id"),
                        "lane_id": row.get("lane_id"),
                        "source": row.get("source"),
                        "count": count,
                    },
                )
                c.execute(
                    """
                    UPDATE incidents
                    SET alerted_at=?, last_alerted_count=?, last_alerted_severity=?, updated_at=?
                    WHERE id=?
                    """,
                    (now, count, severity, now, row["id"]),
                )
                alerted += 1
    except Exception:
        pass

    return {"upserted": upserted, "alerted": alerted}
