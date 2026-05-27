"""
Widget: Execution Quality — Are entries and exits clean?
Refresh: 30s
"""

import streamlit as st

import ui
from db import get_effective_launch_date
from formatters import _status_dot
from data.execution import get_execution_stats


@st.fragment(run_every=30)
def render_execution_quality():
    st.markdown(
        ui.section_header(
            "EXECUTION QUALITY",
            "Are we entering at good prices and exiting efficiently?",
        ),
        unsafe_allow_html=True,
    )

    ex = get_execution_stats()
    total = ex["total"]

    if total == 0:
        st.markdown(
            ui.info_callout(
                "No trade attribution data yet — populates after the first closed trade.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        return

    def _score_color(v, good=6, ok=4):
        if v >= good:
            return ui.C_GREEN
        if v >= ok:
            return ui.C_AMBER
        return ui.C_RED

    def _rate_color(v, good=5, ok=15):
        if v <= good:
            return ui.C_GREEN
        if v <= ok:
            return ui.C_AMBER
        return ui.C_RED

    rows = (
        ui.metric_row(
            "Entry timing  (10 = perfect)",
            f"{ex['entry_score']:.1f} / 10",
            value_color=_score_color(ex["entry_score"]),
        )
        + ui.metric_row(
            "Exit efficiency  (10 = captured all profit)",
            f"{ex['exit_score']:.1f} / 10",
            value_color=_score_color(ex["exit_score"]),
        )
        + ui.metric_row(
            "Avg adverse move (MAE)",
            f"{ex['avg_mae_pct']:.3f}%",
            value_color=ui.C_GREEN if ex["avg_mae_pct"] < 0.5 else ui.C_AMBER,
        )
        + ui.metric_row("Avg best potential (MFE)", f"{ex['avg_mfe_pct']:.3f}%")
        + ui.metric_row(
            "Fee trap rate  (fees > 50% of gross profit)",
            f"{ex['fee_trap_rate']:.1f}%  ({ex['fee_traps']}/{total})",
            value_color=_rate_color(ex["fee_trap_rate"]),
        )
        + ui.metric_row(
            "Avg hold — wins",
            f"{ex['avg_hold_win_min']:.0f}m" if ex["avg_hold_win_min"] else "n/a",
        )
        + ui.metric_row(
            "Avg hold — losses",
            f"{ex['avg_hold_loss_min']:.0f}m" if ex["avg_hold_loss_min"] else "n/a",
        )
        + ui.metric_row(
            "Slippage",
            "Not yet instrumented",
            value_color=ui._TEXT_CAP,
        )
    )

    st.markdown(
        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:12px 14px;">{rows}</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Based on {total} attributed trades since {get_effective_launch_date()}"
    )
