"""
Widget: Decision Quality — Are we picking good trades?
Refresh: 30s
"""

import streamlit as st

import ui
from db import _q1, get_effective_launch_date, _runtime_paper_flag
from data.performance import get_performance_stats, get_signal_bayesian_stats


@st.fragment(run_every=30)
def render_decision_quality():
    st.markdown(
        ui.section_header(
            "DECISION QUALITY",
            "Are we choosing strong trades? Which signals are working?",
        ),
        unsafe_allow_html=True,
    )

    stats = get_performance_stats()
    closes = stats["closes"]

    if closes == 0:
        st.markdown(
            ui.info_callout("No closed trades yet.", "info"),
            unsafe_allow_html=True,
        )
        return

    r = _q1(
        """
        SELECT
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END)                          AS good_outcome,
            SUM(CASE WHEN won=0 AND exit_type='stop_hit' THEN 1 ELSE 0 END) AS stopped_out,
            SUM(CASE WHEN won=0 AND exit_type='thesis_exit' THEN 1 ELSE 0 END) AS thesis_failed,
            SUM(CASE WHEN won=1 AND exit_type='target_hit' THEN 1 ELSE 0 END)  AS full_target,
            COUNT(*) AS total
        FROM trade_attribution
        WHERE COALESCE(created_at, entry_ts, '') >= ?
          AND paper=?
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10')
        """,
        (get_effective_launch_date(), _runtime_paper_flag()),
    )
    attr_total = r.get("total") or 0

    if attr_total > 0:
        good = r.get("good_outcome") or 0
        stopped = r.get("stopped_out") or 0
        thesis_fail = r.get("thesis_failed") or 0
        full_target = r.get("full_target") or 0

        g_pct = good / attr_total * 100
        s_pct = stopped / attr_total * 100
        tf_pct = thesis_fail / attr_total * 100
        ft_pct = full_target / attr_total * 100

        rows = (
            ui.metric_row(
                "Won (trade worked)",
                f"{good}/{attr_total}  = {g_pct:.0f}%",
                value_color=ui.C_GREEN if g_pct >= 50 else ui.C_AMBER,
            )
            + ui.metric_row(
                "Stopped out (bad timing / weak signal)",
                f"{stopped}/{attr_total}  = {s_pct:.0f}%",
                value_color=ui.C_RED if s_pct > 40 else ui._TEXT_PRI,
            )
            + ui.metric_row(
                "Thesis invalidated (signal failed mid-trade)",
                f"{thesis_fail}/{attr_total}  = {tf_pct:.0f}%",
            )
            + ui.metric_row(
                "Hit full target",
                f"{full_target}/{attr_total}  = {ft_pct:.0f}%",
                value_color=ui.C_GREEN if ft_pct >= 30 else ui._TEXT_PRI,
            )
        )
    else:
        rows = ui.metric_row(
            "Overall win rate",
            f"{stats['win_rate']:.1f}%  ({stats['wins']}W / {stats['losses']}L)",
        ) + ui.metric_row("Attribution rows", "0 — populates after trade closes")

    # Bayesian signal insights
    bay = get_signal_bayesian_stats()
    if bay:
        improving = sorted(
            [b for b in bay if b.get("pts_drift", 0) > 0],
            key=lambda x: -x["pts_drift"],
        )[:1]
        degrading = sorted(
            [b for b in bay if b.get("pts_drift", 0) < 0],
            key=lambda x: x["pts_drift"],
        )[:1]
        if improving:
            sig = improving[0]
            rows += ui.metric_row(
                "Best signal",
                f"{sig['signal_name']}  +{sig['pts_drift']:.1f}pts  {sig['win_rate_pct']:.0f}%WR",
                value_color=ui.C_GREEN,
            )
        if degrading:
            sig = degrading[0]
            rows += ui.metric_row(
                "Weakest signal",
                f"{sig['signal_name']}  {sig['pts_drift']:.1f}pts  {sig['win_rate_pct']:.0f}%WR",
                value_color=ui.C_RED,
            )

    st.markdown(
        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:12px 14px;">{rows}</div>',
        unsafe_allow_html=True,
    )
