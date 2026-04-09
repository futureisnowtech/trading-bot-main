"""
Widget: Scanner Funnel
Question: Is the scanner finding trade candidates? How many passed each filter?
Tab: MISSION CONTROL
Refresh: 15s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from formatters import _status_dot, _age_color, _asset_badge
from data.scanner_data import get_scan_status


@st.fragment(run_every=15)
def render_scanner_funnel():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Scanner Funnel</div>', unsafe_allow_html=True)

    scan = get_scan_status()
    age = scan["age_s"]
    age_str = f"{age}s ago" if age < 9999 else "no data"
    age_color = _age_color(age, warn=120, crit=360)

    st.markdown(
        f'{_status_dot(age_color)} <span style="font-size:0.85em; color:#94a3b8">Last scan: <span style="color:#e2e8f0; font-weight:600">{age_str}</span></span>  '
        f'<span style="font-size:0.85em; color:#94a3b8">&nbsp;·&nbsp; {scan["count"]} candidates &nbsp;·&nbsp; {scan["duration_s"]:.1f}s</span>',
        unsafe_allow_html=True,
    )

    if scan["steps"]:
        steps_html = ""
        for s in scan["steps"]:
            drop_pct = s["dropped"] / s["in"] * 100 if s["in"] else 0
            bar_w = max(4, int((s["out"] / max(s["in"], 1)) * 100))
            steps_html += (
                f'<div style="font-size:0.76em; margin:2px 0; display:flex; align-items:center; gap:6px">'
                f'<span style="color:#64748b; min-width:24px">S{s["step"]}</span>'
                f'<div style="flex:1; background:rgba(255,255,255,0.06); border-radius:3px; height:10px">'
                f'<div style="width:{bar_w}%; background:#3b82f6; border-radius:3px; height:10px"></div></div>'
                f'<span style="color:#e2e8f0; min-width:30px">{s["out"]}</span>'
                f'<span style="color:#f87171; font-size:0.85em">-{s["dropped"]}</span>'
                f"</div>"
            )
        st.markdown(steps_html, unsafe_allow_html=True)
    elif scan["candidates"]:
        st.caption(f"{len(scan['candidates'])} candidates passed all filters")
    else:
        st.caption("Waiting for scan data…")

    if scan["balance"]:
        st.caption(
            f"Balance ${scan['balance']:,.0f} · Deployed ${scan['deployed']:,.0f}"
        )
