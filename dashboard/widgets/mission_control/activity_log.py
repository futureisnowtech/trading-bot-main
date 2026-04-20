"""
Widget: Activity Feed — Plain-English answer to: what has the bot been doing?
Refresh: 15s
"""

import re

import streamlit as st

import ui
from data.scanner_data import get_smart_log_summary
from db import _q1


def _bot_is_alive() -> bool:
    try:
        r = _q1(
            "SELECT COUNT(*) AS n FROM system_events "
            "WHERE source='heartbeat' AND ts >= datetime('now','-10 minutes')"
        )
        if r.get("n", 0) > 0:
            return True
        r = _q1(
            "SELECT COUNT(*) AS n FROM system_events "
            "WHERE ts >= datetime('now','-30 minutes')"
        )
        if r.get("n", 0) > 0:
            return True
        r = _q1("SELECT COUNT(*) AS n FROM trades WHERE ts >= datetime('now','-1 day')")
        if r.get("n", 0) > 0:
            return True
    except Exception:
        pass
    return False


def _plain(kind: str, msg: str) -> str:
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
    "ENTERED": ("✅", ui.C_GREEN),
    "CLOSE": ("💰", ui.C_CYAN),
    "VETO": ("🚫", ui.C_AMBER),
    "SCAN": ("🔍", ui._TEXT_CAP),
    "ERROR": ("⚠️", ui.C_RED),
    "ML": ("🧠", ui.C_MAG),
}
_ORDER = ["ENTERED", "CLOSE", "ERROR", "VETO", "SCAN", "ML"]


@st.fragment(run_every=15)
def render_smart_logs():
    st.markdown(
        ui.section_header("RECENT ACTIVITY", "What the bot has been doing"),
        unsafe_allow_html=True,
    )

    summary = get_smart_log_summary(300)
    err_1h = summary["error_count_1h"]
    veto_1h = summary["veto_count_1h"]
    entry_1h = summary["entry_count_1h"]
    buckets = summary["buckets"]

    err_color = ui.C_RED if err_1h > 0 else ui._TEXT_CAP
    st.markdown(
        f'<div style="font-size:0.76em;color:{ui._TEXT_CAP};margin-bottom:8px;">'
        f"Last hour: "
        f'<span style="color:{ui.C_GREEN};">{entry_1h} entered</span>'
        f' · <span style="color:{ui.C_AMBER};">{veto_1h} skipped</span>'
        f' · <span style="color:{err_color};">'
        f"{err_1h} error{'s' if err_1h != 1 else ''}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    rows: list[tuple] = []
    for kind in _ORDER:
        events = buckets.get(kind, [])
        if not events:
            continue
        icon, color = _ICON.get(kind, ("·", ui._TEXT_CAP))
        for ev in events[:2]:
            text = _plain(kind, ev["msg"])
            rows.append((icon, color, text, ev.get("ts", "")))

    if rows:
        html = ""
        for icon, color, text, ts in rows:
            html += (
                f'<div style="display:flex;gap:8px;padding:5px 0;'
                f'border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'<span style="font-size:0.85em;flex-shrink:0;padding-top:1px;">{icon}</span>'
                f'<div style="flex:1;min-width:0;">'
                f'<span style="color:{ui._TEXT_PRI};font-size:0.80em;">{text}</span>'
                f'<span style="color:{ui._TEXT_CAP};font-size:0.72em;margin-left:6px;">{ts}</span>'
                f"</div>"
                f"</div>"
            )
        st.markdown(html, unsafe_allow_html=True)
    elif _bot_is_alive():
        st.markdown(
            f'<p style="color:{ui._TEXT_CAP};font-size:0.80em;margin-top:6px;">'
            f"System alive — no recent log activity to display</p>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<p style="color:{ui._TEXT_CAP};font-size:0.80em;margin-top:6px;">'
            f"No recent activity — start the bot with: "
            f"<code>python3 main.py --mode paper</code></p>",
            unsafe_allow_html=True,
        )
