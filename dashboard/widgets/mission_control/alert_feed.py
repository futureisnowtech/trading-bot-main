"""
Widget: Alert Feed — Active warnings and critical alerts.
Refresh: 10s
"""

import streamlit as st

import ui
from formatters import _time_ago
from data.notifications import get_notification_feed, get_notification_counts
from data.execution import get_recent_events


@st.fragment(run_every=10)
def render_alert_feed():
    st.markdown(
        ui.section_header(
            "ALERTS",
            "Critical issues first — grouped by severity",
        ),
        unsafe_allow_html=True,
    )

    counts = get_notification_counts()
    crit = counts["critical"]
    warn = counts["warning"]
    last_ts = counts["last_ts"]

    # Summary bar
    c_color = ui.C_RED if crit > 0 else ui._TEXT_CAP
    w_color = ui.C_AMBER if warn > 0 else ui._TEXT_CAP
    st.markdown(
        f'<div style="display:flex;gap:20px;margin-bottom:10px;'
        f'padding-bottom:8px;border-bottom:1px solid {ui._BORDER};">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:1.5em;font-weight:800;color:{c_color};">{crit}</div>'
        f'<div style="font-size:0.68em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.07em;">Critical</div>'
        f"</div>"
        f'<div style="text-align:center;">'
        f'<div style="font-size:1.5em;font-weight:800;color:{w_color};">{warn}</div>'
        f'<div style="font-size:0.68em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.07em;">Warning</div>'
        f"</div>"
        f'<div style="margin-left:auto;font-size:0.75em;color:{ui._TEXT_CAP};'
        f'padding-top:4px;">Last: {_time_ago(last_ts) if last_ts else "–"}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    feed = get_notification_feed(limit=6)

    if not feed:
        # Fall back to raw system_events
        events = get_recent_events(6)
        if not events:
            st.markdown(
                ui.info_callout("No alerts — system is quiet.", "good"),
                unsafe_allow_html=True,
            )
            return
        rows_html = ""
        for e in events:
            level = e.get("level", "INFO")
            color = (
                ui.C_RED
                if level == "ERROR"
                else ui.C_AMBER
                if level == "WARNING"
                else ui._TEXT_CAP
            )
            msg = e.get("message", "")[:70]
            ts = _time_ago(e.get("ts", ""))
            rows_html += (
                f'<div style="padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:flex-start;gap:6px;">'
                f'<span style="color:{color};font-weight:700;font-size:0.74em;'
                f'flex-shrink:0;">[{level}]</span>'
                f'<span style="color:{ui._TEXT_PRI};font-size:0.78em;flex:1;">{msg}</span>'
                f"</div>"
                f'<div style="color:{ui._TEXT_CAP};font-size:0.70em;margin-top:2px;">{ts}</div>'
                f"</div>"
            )
        st.markdown(rows_html, unsafe_allow_html=True)
        return

    rows_html = ""
    for n in feed:
        sev = n.get("severity", "INFO")
        color = ui.C_RED if sev == "CRITICAL" else ui.C_AMBER
        title = n.get("title", "")[:60]
        ts = _time_ago(n.get("ts", ""))
        rows_html += (
            f'<div style="padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<div style="display:flex;justify-content:space-between;gap:6px;">'
            f'<span style="color:{color};font-size:0.70em;font-weight:700;flex-shrink:0;">'
            f"[{sev}]</span>"
            f'<span style="color:{ui._TEXT_PRI};font-size:0.80em;flex:1;">{title}</span>'
            f"</div>"
            f'<div style="color:{ui._TEXT_CAP};font-size:0.70em;margin-top:2px;">{ts}</div>'
            f"</div>"
        )
    st.markdown(rows_html, unsafe_allow_html=True)
