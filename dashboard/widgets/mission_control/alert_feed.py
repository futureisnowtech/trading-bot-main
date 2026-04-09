"""
Widget: Alert Feed
Question: Are there any active warnings or critical alerts right now?
Tab: MISSION CONTROL
Refresh: 10s
Asset class: BOTH
"""

import streamlit as st

from formatters import _time_ago
from data.notifications import get_notification_feed, get_notification_counts
from data.execution import get_recent_events


@st.fragment(run_every=10)
def render_alert_feed():
    st.markdown('<div class="panel-title">Alert Feed</div>', unsafe_allow_html=True)

    counts = get_notification_counts()
    crit = counts["critical"]
    warn = counts["warning"]
    last_ts = counts["last_ts"]

    crit_color = "#f87171" if crit > 0 else "#94a3b8"
    warn_color = "#facc15" if warn > 0 else "#94a3b8"

    st.markdown(
        f'<div style="display:flex; gap:16px; margin-bottom:8px">'
        f'<div style="text-align:center"><div style="font-size:1.6em; font-weight:800; color:{crit_color}">{crit}</div><div style="font-size:0.72em; color:#94a3b8">CRITICAL</div></div>'
        f'<div style="text-align:center"><div style="font-size:1.6em; font-weight:800; color:{warn_color}">{warn}</div><div style="font-size:0.72em; color:#94a3b8">WARNING</div></div>'
        f'<div style="text-align:center"><div style="font-size:0.8em; color:#94a3b8; margin-top:8px">Last:<br>{_time_ago(last_ts) if last_ts else "–"}</div></div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    feed = get_notification_feed(limit=6)
    if not feed:
        events = get_recent_events(5)
        for e in events:
            level = e.get("level", "INFO")
            color = (
                "#f87171"
                if level == "ERROR"
                else ("#facc15" if level == "WARNING" else "#94a3b8")
            )
            msg = e.get("message", "")[:60]
            ts = _time_ago(e.get("ts", ""))
            st.markdown(
                f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:3px 0; color:#e2e8f0">'
                f'<span style="color:{color}; font-weight:700">[{level}]</span> {msg}<br>'
                f'<span style="color:#64748b">{ts}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        for n in feed:
            sev = n.get("severity", "INFO")
            color = "#f87171" if sev == "CRITICAL" else "#facc15"
            title = n.get("title", "")[:40]
            ts = _time_ago(n.get("ts", ""))
            st.markdown(
                f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:3px 0; color:#e2e8f0">'
                f'<span style="color:{color}; font-weight:700">[{sev}]</span> {title}<br>'
                f'<span style="color:#64748b">{ts}</span></div>',
                unsafe_allow_html=True,
            )
