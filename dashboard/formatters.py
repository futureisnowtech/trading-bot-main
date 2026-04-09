"""
dashboard/formatters.py — Pure formatting helpers (no Streamlit, no DB calls).
"""

import re
from datetime import datetime


def _fmt_pnl(v):
    s = "+" if v > 0 else ""
    return f"{s}${v:,.2f}"


def _time_ago(ts_str):
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0][:19]
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s ago"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str[:16] if ts_str else "–"


def _ts_age_s(ts_str) -> int:
    try:
        ts_str = ts_str.replace("T", " ").split(".")[0].split("+")[0][:19]
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
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
