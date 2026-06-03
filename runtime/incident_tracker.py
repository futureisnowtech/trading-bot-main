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
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    severity       TEXT NOT NULL DEFAULT 'INFO',
    state          TEXT NOT NULL DEFAULT 'open',
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
    if any(x in src for x in ("forecastrunner", "forecast", "forecastex", "kalshi")):
        return "forecast"
    return "system"

def _severity_from_level(level: str) -> str:
    lvl = (level or "INFO").upper()
    if lvl in ("CRITICAL", "FATAL"): return "CRITICAL"
    if lvl == "ERROR": return "ERROR"
    if lvl == "WARNING": return "WARNING"
    return "INFO"

def init_incident_table(db_path: str = DB_PATH) -> None:
    with _conn(db_path) as c:
        c.execute(_DDL_INCIDENTS)

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
                "first_seen_at": ts,
                "last_seen_at": ts,
                "count": 1,
                "severity": _severity_from_level(level),
            }
        else:
            groups[key]["count"] += 1
            groups[key]["last_seen_at"] = max(groups[key]["last_seen_at"], ts)
            sev_order = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}
            new_sev = _severity_from_level(level)
            if sev_order.get(new_sev, 0) > sev_order.get(groups[key]["severity"], 0):
                groups[key]["severity"] = new_sev

    if not groups: return 0

    now = _now_iso()
    upserted = 0
    try:
        with _conn(db_path) as c:
            for key, g in groups.items():
                c.execute("""
                    INSERT INTO incidents (lane_id, source, fingerprint, first_seen_at, last_seen_at, count, severity, state, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    ON CONFLICT(lane_id, fingerprint) DO UPDATE SET
                        last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                        count        = count + excluded.count,
                        severity     = CASE
                            WHEN excluded.severity='CRITICAL' OR severity='CRITICAL' THEN 'CRITICAL'
                            WHEN excluded.severity='ERROR' OR severity='ERROR' THEN 'ERROR'
                            WHEN excluded.severity='WARNING' OR severity='WARNING' THEN 'WARNING'
                            ELSE 'INFO' END,
                        state        = CASE WHEN state='resolved' THEN 'open' ELSE state END,
                        updated_at   = excluded.updated_at
                """, (g["lane_id"], g["source"], g["fingerprint"], g["first_seen_at"], g["last_seen_at"], g["count"], g["severity"], now))
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
