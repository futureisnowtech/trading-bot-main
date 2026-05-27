"""
dashboard/data/notifications.py — Notification feed and counts.
"""

from datetime import datetime

from db import _q, _q1


def get_notification_feed(limit=20) -> list:
    """Read from notifications table (WARNING/CRITICAL only)."""
    return _q(
        """SELECT ts, category, severity, title, message FROM notifications
           WHERE severity IN ('WARNING','CRITICAL')
           ORDER BY rowid DESC LIMIT ?""",
        (limit,),
    )


def get_notification_counts() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        """SELECT
            SUM(CASE WHEN severity='CRITICAL' THEN 1 ELSE 0 END) AS critical,
            SUM(CASE WHEN severity='WARNING'  THEN 1 ELSE 0 END) AS warning,
            MAX(ts) AS last_ts
           FROM notifications WHERE ts >= ?""",
        (today,),
    )
    return {
        "critical": r.get("critical") or 0,
        "warning": r.get("warning") or 0,
        "last_ts": r.get("last_ts") or "",
    }
