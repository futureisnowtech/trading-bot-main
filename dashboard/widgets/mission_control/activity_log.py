"""
Widget: Activity Log
Question: What has the bot been doing in the last 15 minutes?
Tab: MISSION CONTROL
Refresh: 15s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from formatters import _asset_badge
from data.scanner_data import get_smart_log_summary


@st.fragment(run_every=15)
def render_smart_logs():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">Activity (last 15m)</div>', unsafe_allow_html=True
    )

    summary = get_smart_log_summary(200)
    error_1h = summary["error_count_1h"]
    veto_1h = summary["veto_count_1h"]
    entry_1h = summary["entry_count_1h"]
    buckets = summary["buckets"]

    err_color = "#f87171" if error_1h > 0 else "#4ade80"
    st.markdown(
        f'<div style="font-size:0.82em; color:#94a3b8; margin-bottom:6px">'
        f"Last 1h: "
        f'<span style="color:#4ade80">{entry_1h} entries</span> · '
        f'<span style="color:#facc15">{veto_1h} vetoes</span> · '
        f'<span style="color:{err_color}; font-weight:700">{error_1h} errors</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    KIND_COLOR = {
        "ENTERED": "#4ade80",
        "CLOSE": "#60a5fa",
        "VETO": "#f97316",
        "SCAN": "#94a3b8",
        "ERROR": "#f87171",
        "ML": "#a78bfa",
        "HEALTH": "#64748b",
    }
    for kind, events in buckets.items():
        if not events:
            continue
        color = KIND_COLOR.get(kind, "#94a3b8")
        latest = events[0]
        st.markdown(
            f'<div style="font-size:0.78em; border-left:2px solid {color}; padding-left:6px; margin:2px 0">'
            f'<span style="color:{color}; font-weight:700">[{kind}]</span> '
            f'<span style="color:#e2e8f0">{latest["msg"][:100]}</span> '
            f'<span style="color:#64748b">{latest["ts"]}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
