"""
dashboard/formatters.py — Pure formatting helpers (no Streamlit, no DB calls).
"""

import re
from datetime import datetime


def _fmt_pnl(v):
    s = "+" if v > 0 else ""
    return f"{s}${v:,.2f}"


def _parse_ts(ts_str) -> datetime:
    """
    Parse a timestamp string into a datetime object.
    Handles both ISO-8601 strings (with T or space separator, with/without timezone)
    and Unix epoch floats stored as strings (e.g. '1775843246.789').
    """
    if not ts_str:
        raise ValueError("empty ts")
    s = str(ts_str).strip()
    # Detect epoch: digits with optional dot/decimal, no letters except maybe 'e'
    if s.replace(".", "", 1).lstrip("-").replace("e", "", 1).isdigit():
        return datetime.fromtimestamp(float(s))
    # ISO / datetime string — strip sub-seconds and timezone offset
    s = s.replace("T", " ").split(".")[0]
    # Remove timezone suffix (+HH:MM or -HH:MM at end)
    if len(s) > 19 and (s[19] in ("+", "-")):
        s = s[:19]
    s = s[:19]
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _time_ago(ts_str):
    try:
        dt = _parse_ts(ts_str)
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s ago"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return str(ts_str)[:16] if ts_str else "–"


def _ts_age_s(ts_str) -> int:
    try:
        dt = _parse_ts(ts_str)
        return max(0, int((datetime.now() - dt).total_seconds()))
    except Exception:
        return 9999


def _parse_notes(notes):
    if not notes:
        return {}
    r = {}
    for pattern, key in [
        (r"score=([\d.]+)", "score"),
        (r"regime=(\w+)", "regime"),
        (r"setup=(\S+)", "setup"),
        (r"lev=(\d+)x", "lev"),
        (r"reason=(\S+)", "reason"),
    ]:
        m = re.search(pattern, notes)
        if m:
            r[key] = m.group(1)
    return r


def _badge(text, kind="pass"):
    return f'<span class="badge-{kind}">{text}</span>'


def _status_dot(color):
    colors = {
        "green": "#4ade80",
        "yellow": "#facc15",
        "red": "#f87171",
        "gray": "#64748b",
    }
    c = colors.get(color, "#94a3b8")
    return f'<span style="color:{c}; font-size:1.1em;">●</span>'


def _age_color(age_s, warn=120, crit=300):
    if age_s >= 9999:
        return "red"
    if age_s > crit:
        return "red"
    if age_s > warn:
        return "yellow"
    return "green"


def _asset_badge(kind: str) -> str:
    """Return an HTML badge marking the asset class of a widget."""
    if kind == "crypto":
        return '<span class="badge-crypto">CRYPTO PERPS</span>'
    return '<span class="badge-futures">S&P FUTURES · MES</span>'


def _plain_pf(pf: float) -> str:
    """Plain-English profit factor interpretation."""
    if pf == float("inf"):
        return "No losing trades yet — keep watching"
    if pf >= 1.5:
        return f"Making ${pf:.2f} for every $1 lost — strong edge"
    if pf >= 1.2:
        return f"Making ${pf:.2f} for every $1 lost — solid"
    if pf >= 1.0:
        return f"Making ${pf:.2f} for every $1 lost — barely profitable"
    return f"Only making ${pf:.2f} for every $1 lost — losing money overall"


def _verdict(
    value: float,
    good_threshold: float,
    warn_threshold: float,
    higher_is_better: bool = True,
) -> tuple:
    """Return (chip_status, chip_label) for a scalar metric."""
    if higher_is_better:
        if value >= good_threshold:
            return "good", "Good"
        if value >= warn_threshold:
            return "watch", "Watch"
        return "problem", "Problem"
    else:
        if value <= good_threshold:
            return "good", "Good"
        if value <= warn_threshold:
            return "watch", "Watch"
        return "problem", "Problem"


def _pct_bar(pct: float, color: str = "#58a6ff", height: int = 4) -> str:
    """Return an HTML percentage progress bar."""
    w = max(0, min(100, int(pct)))
    return (
        f'<div style="background:rgba(255,255,255,0.06);border-radius:3px;'
        f'height:{height}px;margin:4px 0;">'
        f'<div style="width:{w}%;background:{color};border-radius:3px;height:100%;'
        f'opacity:0.8;"></div>'
        f"</div>"
    )
