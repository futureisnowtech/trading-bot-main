"""
Widget: Activity Feed
Plain-English answer to: what has the bot been doing?

Converts raw log messages into human-readable events with icons.
Refresh: 15s
"""

import re

import streamlit as st

from data.scanner_data import get_smart_log_summary
from db import _q1
from formatters import _asset_badge


def _bot_is_alive() -> bool:
    """Return True if recent DB activity suggests the bot is running, even if log parsing fails."""
    try:
        # Heartbeat in last 10 minutes
        r = _q1(
            "SELECT COUNT(*) AS n FROM system_events "
            "WHERE source='heartbeat' AND ts >= datetime('now','-10 minutes')"
        )
        if r.get("n", 0) > 0:
            return True
        # Any system_events in last 30 minutes
        r = _q1(
            "SELECT COUNT(*) AS n FROM system_events "
            "WHERE ts >= datetime('now','-30 minutes')"
        )
        if r.get("n", 0) > 0:
            return True
        # Any trades in last 24h
        r = _q1(
            "SELECT COUNT(*) AS n FROM trades "
            "WHERE ts >= datetime('now','-1 day')"
        )
        if r.get("n", 0) > 0:
            return True
    except Exception:
        pass
    return False


# ── plain-English translators ─────────────────────────────────────────────────


def _plain(kind: str, msg: str) -> str:
    """Translate a raw log message into a human-readable string."""

    if kind == "ENTERED":
        m = re.search(r"ENTERED\s+(\S+)\s+(LONG|SHORT)", msg, re.I)
        if m:
            sym, direction = m.group(1), m.group(2)
            size_m = re.search(r"size=([\d.]+)", msg)
            lev_m = re.search(r"lev=(\d+)x", msg)
            parts = [f"Opened {sym} {direction}"]
            if size_m:
                parts.append(f"${float(size_m.group(1)):.0f}")
            if lev_m:
                parts.append(f"at {lev_m.group(1)}× leverage")
            return "  ·  ".join(parts)
        return msg[:80]

    if kind == "CLOSE":
        m = re.search(r"CLOSE\s+(\S+)\s+(LONG|SHORT)", msg, re.I)
        if m:
            sym, direction = m.group(1), m.group(2)
            pnl_m = re.search(r"pnl=([+-]?[\d.]+)", msg)
            if pnl_m:
                pnl = float(pnl_m.group(1))
                sign = "+" if pnl >= 0 else ""
                return f"Closed {sym} {direction}  →  {sign}${abs(pnl):.2f}"
            return f"Closed {sym} {direction}"
        return msg[:80]

    if kind == "VETO":
        m_sym = re.search(r"VETO\s+(\S+)\s+(LONG|SHORT)", msg, re.I)
        m_reason = re.search(r"reason=(\S+)", msg)
        sym = (m_sym.group(1).upper() + " " + m_sym.group(2)) if m_sym else "trade"
        raw = m_reason.group(1) if m_reason else ""
        reasons = {
            "ev_below_floor": "fees would eat the profit",
            "spread_too_wide": "bid-ask spread too wide",
            "volume_too_low": "not enough trading volume",
            "rr_below_min": "risk/reward ratio too low",
            "depth_too_thin": "order book too thin",
        }
        reason = reasons.get(
            raw, raw.replace("_", " ") if raw else "economics check failed"
        )
        return f"Skipped {sym}  —  {reason}"

    if kind == "SCAN":
        m = re.search(r"Complete:\s*(\d+)\s*candidates", msg)
        if m:
            return f"Scanned {int(m.group(1)):,} pairs across 3 exchanges"
        return "Scan completed"

    if kind == "ERROR":
        clean = re.sub(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\S+\s+\S+\s+", "", msg
        )
        return clean[:90]

    return msg[:80]


_ICON = {
    "ENTERED": ("✅", "#4ade80"),
    "CLOSE": ("💰", "#60a5fa"),
    "VETO": ("🚫", "#f97316"),
    "SCAN": ("🔍", "#94a3b8"),
    "ERROR": ("⚠️", "#f87171"),
    "ML": ("🧠", "#a78bfa"),
}

_ORDER = ["ENTERED", "CLOSE", "ERROR", "VETO", "SCAN", "ML"]


@st.fragment(run_every=15)
def render_smart_logs():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">What Just Happened</div>', unsafe_allow_html=True
    )

    summary = get_smart_log_summary(300)
    err_1h = summary["error_count_1h"]
    veto_1h = summary["veto_count_1h"]
    entry_1h = summary["entry_count_1h"]
    buckets = summary["buckets"]

    err_col = "#f87171" if err_1h > 0 else "#64748b"
    st.markdown(
        f'<div style="font-size:0.78em; color:#64748b; margin-bottom:10px;">'
        f"Last hour:&nbsp; "
        f'<span style="color:#4ade80">{entry_1h} entered</span>'
        f" &nbsp;·&nbsp; "
        f'<span style="color:#f97316">{veto_1h} skipped</span>'
        f" &nbsp;·&nbsp; "
        f'<span style="color:{err_col}">{err_1h} error{"s" if err_1h != 1 else ""}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    rows: list[tuple] = []
    for kind in _ORDER:
        events = buckets.get(kind, [])
        if not events:
            continue
        icon, color = _ICON.get(kind, ("·", "#94a3b8"))
        for ev in events[:2]:
            text = _plain(kind, ev["msg"])
            rows.append((icon, color, text, ev.get("ts", "")))

    if rows:
        html = ""
        for icon, color, text, ts in rows:
            html += (
                f'<div style="display:flex; gap:8px; padding:5px 0;'
                f' border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'  <span style="font-size:0.85em; flex-shrink:0; padding-top:1px;">'
                f"{icon}</span>"
                f'  <div style="flex:1; min-width:0;">'
                f'    <span style="color:#e2e8f0; font-size:0.82em;">{text}</span>'
                f'    <span style="color:#475569; font-size:0.75em; margin-left:6px;">'
                f"{ts}</span>"
                f"  </div>"
                f"</div>"
            )
        st.markdown(html, unsafe_allow_html=True)
    elif _bot_is_alive():
        # Bot is running (heartbeat/events in DB) but log parser found nothing displayable
        st.markdown(
            '<p style="color:#475569; font-size:0.82em; margin-top:8px;">'
            "System alive — no recent log activity to display"
            "</p>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<p style="color:#475569; font-size:0.82em; margin-top:8px;">'
            "No recent activity — start the bot with:"
            " <code>python3 main.py --mode paper</code>"
            "</p>",
            unsafe_allow_html=True,
        )
