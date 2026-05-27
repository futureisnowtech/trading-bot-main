"""
runtime/spot_session.py — Shared spot-session timing rules for autonomous entries.
"""

from __future__ import annotations

import datetime as _dt

import pytz


def _parse_hhmm(value: str, fallback: str) -> tuple[int, int]:
    raw = str(value or fallback).strip()
    try:
        hh, mm = [int(x) for x in raw.split(":", 1)]
        return hh, mm
    except Exception:
        hh, mm = [int(x) for x in fallback.split(":", 1)]
        return hh, mm


def is_spot_entry_session_open(now: _dt.datetime | None = None) -> bool:
    """
    Canonical session gate for autonomous spot entries.

    Defaults to a weekday-only U.S. session to match the day-trading posture:
    no weekend entries and no late-day entries after the recycle window closes.
    """
    try:
        from config import (
            SPOT_WEEKDAYS_ONLY,
            SPOT_ENTRY_START_TIME,
            SPOT_ENTRY_END_TIME,
        )

        weekdays_only = bool(SPOT_WEEKDAYS_ONLY)
        start_raw = str(SPOT_ENTRY_START_TIME)
        end_raw = str(SPOT_ENTRY_END_TIME)
    except Exception:
        weekdays_only = True
        start_raw = "09:35"
        end_raw = "15:15"

    tz = pytz.timezone("America/New_York")
    current = now or _dt.datetime.now(tz)
    if current.tzinfo is None:
        current = tz.localize(current)
    else:
        current = current.astimezone(tz)

    if weekdays_only and current.weekday() >= 5:
        return False

    start_h, start_m = _parse_hhmm(start_raw, "09:35")
    end_h, end_m = _parse_hhmm(end_raw, "15:15")
    start = current.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = current.replace(hour=end_h, minute=end_m, second=59, microsecond=999999)
    return start <= current <= end

