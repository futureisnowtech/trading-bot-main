"""
Widget: Edge Quality — Is the strategy profitable after fees?
Refresh: 30s
"""

import streamlit as st

import ui
from db import get_effective_launch_date
from formatters import _fmt_pnl
from data.performance import get_performance_stats, get_rolling_pf


@st.fragment(run_every=30)
def render_edge_quality():
    st.markdown(
        ui.section_header(
            "TRADE QUALITY",
            "Is the strategy profitable enough to beat fees?",
        ),
        unsafe_allow_html=True,
    )

    stats = get_performance_stats()
    rolling7 = get_rolling_pf(days=7)
    rolling1 = get_rolling_pf(days=1)
    closes = stats["closes"]

    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
    ev = stats["total_pnl"] / closes if closes else 0.0

    if pf >= 1.35:
        pf_color = ui.C_GREEN
        chip_s, chip_l = "good", "Good  ≥ 1.35"
    elif pf >= 1.0:
        pf_color = ui.C_AMBER
        chip_s, chip_l = "watch", "Marginal  ≥ 1.0"
    else:
        pf_color = ui.C_RED
        chip_s, chip_l = "problem", "Problem  < 1.0"

    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px;">'
        f'<div style="font-size:2.0em;font-weight:800;color:{pf_color};">PF&nbsp;{pf_str}</div>'
        f"<div>{ui.chip(chip_l, chip_s)}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    r7_pf = rolling7["profit_factor"]
    r7_str = f"{r7_pf:.2f}" if r7_pf != float("inf") else "∞"
    r1_pf = rolling1["profit_factor"]
    r1_str = f"{r1_pf:.2f}" if r1_pf != float("inf") else "∞"

    rows = (
        ui.metric_row(
            "Win rate",
            f"{stats['win_rate']:.1f}%  ({stats['wins']}W / {stats['losses']}L)",
        )
        + ui.metric_row(
            "EV / trade (after fees)",
            _fmt_pnl(ev),
            value_color=ui.C_GREEN if ev > 0 else ui.C_RED,
        )
        + ui.metric_row(
            "Avg win / avg loss",
            f"{_fmt_pnl(stats['avg_win'])} / {_fmt_pnl(-stats['avg_loss'])}",
        )
        + ui.metric_row("R:R realized", f"{stats['rr_realized']:.2f}×")
        + ui.metric_row(
            "Total fees paid", _fmt_pnl(-stats["total_fees"]), value_color=ui.C_AMBER
        )
        + ui.metric_row(
            f"7-day PF  ({rolling7['closes']} trades)",
            r7_str,
            value_color=ui.C_GREEN if r7_pf >= 1.2 else ui.C_AMBER,
        )
        + ui.metric_row(f"24-hour PF  ({rolling1['closes']} trades)", r1_str)
    )
    st.markdown(
        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:12px 14px;">{rows}</div>',
        unsafe_allow_html=True,
    )
    st.caption(f"{closes} clean trades since {get_effective_launch_date()}")
