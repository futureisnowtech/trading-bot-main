"""
Widget: Scanner Funnel — Where are trade ideas getting filtered out?
Refresh: 15s
"""

import streamlit as st

import ui
from formatters import _age_color
from data.scanner_data import get_scan_status


@st.fragment(run_every=15)
def render_scanner_funnel():
    st.markdown(
        ui.section_header(
            "SCANNER FUNNEL",
            "The scanner checks 200+ symbols and filters them step-by-step — "
            "only the strongest setups reach entry.",
        ),
        unsafe_allow_html=True,
    )

    scan = get_scan_status()
    age = scan["age_s"]
    age_str = f"{age}s ago" if age < 9999 else "no data yet"
    age_color = _age_color(age, warn=120, crit=360)

    age_css = (
        ui.C_GREEN
        if age_color == "green"
        else ui.C_AMBER
        if age_color == "yellow"
        else ui.C_RED
    )
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:0.80em;margin-bottom:10px;">'
        f'<span style="color:{ui._TEXT_SEC};">'
        f'Last scan: <strong style="color:{age_css};">{age_str}</strong></span>'
        f'<span style="color:{ui._TEXT_CAP};">'
        f"{scan['count']} candidates · {scan['duration_s']:.1f}s</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if scan["steps"]:
        max_in = scan["steps"][0]["in"] if scan["steps"] else 1
        max_val = max(max_in, 1)
        bars_html = ""
        _step_colors = [
            "#8b949e",
            "#d29922",
            "#bc8cff",
            "#58a6ff",
            "#a78bfa",
            "#38bdf8",
            "#3fb950",
            "#86efac",
        ]
        for i, s in enumerate(scan["steps"]):
            color = _step_colors[i % len(_step_colors)]
            drop_pct = s["dropped"] / s["in"] * 100 if s["in"] else 0
            bars_html += (
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'margin-bottom:4px;font-size:0.76em;">'
                f'<span style="color:{ui._TEXT_CAP};min-width:22px;">S{s["step"]}</span>'
                f'<div style="flex:1;background:rgba(255,255,255,0.06);'
                f'border-radius:3px;height:8px;overflow:hidden;">'
                f'<div style="width:{max(2, int(s["out"] / max_val * 100))}%;'
                f'background:{color};height:100%;border-radius:3px;"></div>'
                f"</div>"
                f'<span style="color:{ui._TEXT_PRI};min-width:28px;">{s["out"]}</span>'
                f'<span style="color:{ui.C_RED};font-size:0.85em;">−{s["dropped"]}</span>'
                f"</div>"
            )
        st.markdown(bars_html, unsafe_allow_html=True)
    elif scan["candidates"]:
        st.caption(f"{len(scan['candidates'])} candidates passed all filters")
    else:
        st.markdown(
            ui.info_callout("Waiting for first scan data…", "info"),
            unsafe_allow_html=True,
        )

    if scan["balance"]:
        st.caption(
            f"Balance ${scan['balance']:,.0f} · Deployed ${scan['deployed']:,.0f}"
        )
