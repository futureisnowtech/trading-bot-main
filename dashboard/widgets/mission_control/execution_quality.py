"""
Widget: Execution Quality
Question: Are we entering at good prices and exiting efficiently?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from db import LAUNCH_DATE
from formatters import _status_dot, _asset_badge
from data.execution import get_execution_stats


@st.fragment(run_every=30)
def render_execution_quality():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">Execution Quality</div>', unsafe_allow_html=True
    )

    ex = get_execution_stats()
    total = ex["total"]

    if total == 0:
        st.info("No trade attribution data yet — populates after first closed trade.")
        return

    rows_html = ""
    metrics = [
        (
            "Entry timing score",
            f"{ex['entry_score']:.1f} / 10",
            "green"
            if ex["entry_score"] >= 6
            else "yellow"
            if ex["entry_score"] >= 4
            else "red",
        ),
        (
            "Exit efficiency score",
            f"{ex['exit_score']:.1f} / 10",
            "green"
            if ex["exit_score"] >= 6
            else "yellow"
            if ex["exit_score"] >= 4
            else "red",
        ),
        (
            "Avg MAE (adverse move)",
            f"{ex['avg_mae_pct']:.3f}%",
            "green" if ex["avg_mae_pct"] < 0.5 else "yellow",
        ),
        ("Avg MFE (best possible)", f"{ex['avg_mfe_pct']:.3f}%", "gray"),
        (
            "Fee trap rate",
            f"{ex['fee_trap_rate']:.1f}%  ({ex['fee_traps']}/{total})",
            "green"
            if ex["fee_trap_rate"] < 5
            else "yellow"
            if ex["fee_trap_rate"] < 15
            else "red",
        ),
        (
            "Avg hold — wins",
            f"{ex['avg_hold_win_min']:.0f}m" if ex["avg_hold_win_min"] else "n/a",
            "gray",
        ),
        (
            "Avg hold — losses",
            f"{ex['avg_hold_loss_min']:.0f}m" if ex["avg_hold_loss_min"] else "n/a",
            "gray",
        ),
        ("Slippage", "N/A — not yet instrumented", "gray"),
    ]
    for label, val, color in metrics:
        dot = _status_dot(color)
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{dot} {label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"Based on {total} attributed trades since {LAUNCH_DATE}")
