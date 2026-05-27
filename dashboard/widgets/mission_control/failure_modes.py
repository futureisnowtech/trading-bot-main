"""
Widget: Failure Modes — What has been going wrong in the last 7 days?
Refresh: 30s
"""

import streamlit as st

import ui
from data.execution import get_failure_counts


@st.fragment(run_every=30)
def render_failures_compact():
    st.markdown(
        ui.section_header(
            "FAILURE MODES",
            "Recurring problems in the last 7 days — zero is the goal",
        ),
        unsafe_allow_html=True,
    )

    failures = get_failure_counts()
    active = sorted(
        [f for f in failures if f["Count (7d)"] > 0],
        key=lambda x: -x["Count (7d)"],
    )
    show = active[:6] if active else []

    if not show:
        st.markdown(
            ui.info_callout("No failures detected in the last 7 days.", "good"),
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for f in show:
        sev = f["Severity"]
        color = (
            ui.C_RED if sev == "CRIT" else ui.C_AMBER if sev == "WARN" else ui._TEXT_CAP
        )
        count = f["Count (7d)"]
        cat = f["Category"]
        desc = f["Description"][:55]
        last = f["Last"]
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="color:{color};font-weight:700;font-size:0.80em;">{cat}</div>'
            f'<div style="color:{ui._TEXT_CAP};font-size:0.73em;margin-top:1px;">{desc}</div>'
            f"</div>"
            f'<div style="text-align:right;flex-shrink:0;margin-left:10px;">'
            f'<div style="color:{color};font-weight:700;font-size:0.88em;">{count}×</div>'
            f'<div style="color:{ui._TEXT_CAP};font-size:0.70em;">{last}</div>'
            f"</div>"
            f"</div>"
        )

    st.markdown(
        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:12px 14px;">{rows_html}</div>',
        unsafe_allow_html=True,
    )
