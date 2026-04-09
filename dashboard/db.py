"""
dashboard/db.py — Database primitives shared across all dashboard modules.
"""

import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "logs", "trades.db")
LOG_PATH = os.path.join(_ROOT, "logs", "bot.log")
LAUNCH_DATE = "2026-04-02"


def _q(sql, params=()):
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _q1(sql, params=()):
    rows = _q(sql, params)
    return rows[0] if rows else {}


def _tail_log(n=800):
    try:
        with open(LOG_PATH, "r") as f:
            return f.readlines()[-n:]
    except Exception:
        return []
