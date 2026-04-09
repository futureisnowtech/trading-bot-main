"""
Widget: Failure Modes
Question: What's been going wrong in the last 7 days?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from formatters import _asset_badge
from data.execution import get_failure_counts


@st.fragment(run_every=30)
def render_failures_compact():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">Failure Modes (7d)</div>', unsafe_allow_html=True
    )

    failures = get_failure_counts()
    active = sorted(
        [f for f in failures if f["Count (7d)"] > 0], key=lambda x: -x["Count (7d)"]
    )
    show = active[:5] if active else failures[:5]

    if not show:
        st.markdown(
            '<span style="color:#4ade80; font-size:0.85em">✓ No failures detected in last 7 days</span>',
            unsafe_allow_html=True,
        )
        return

    for f in show:
        sev = f["Severity"]
        color = (
            "#f87171" if sev == "CRIT" else ("#facc15" if sev == "WARN" else "#94a3b8")
        )
        count = f["Count (7d)"]
        cat = f["Category"]
        desc = f["Description"][:60]
        last = f["Last"]
        st.markdown(
            f'<div style="display:flex; justify-content:space-between; font-size:0.8em; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<span><span style="color:{color}; font-weight:700">{cat}</span>'
            f'<br><span style="color:#64748b; font-size:0.85em">{desc}</span></span>'
            f'<span style="text-align:right"><span style="color:{color}; font-weight:700">{count}</span>'
            f'<br><span style="color:#64748b; font-size:0.85em">{last}</span></span>'
            f"</div>",
            unsafe_allow_html=True,
        )
