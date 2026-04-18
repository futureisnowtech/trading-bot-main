"""
Widget: Decision Quality
Question: Are we picking good trades? Which signals are winning or losing?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from db import _q1, get_effective_launch_date, _runtime_paper_flag
from formatters import _asset_badge
from data.performance import get_performance_stats, get_signal_bayesian_stats


@st.fragment(run_every=30)
def render_decision_quality():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">Decision Quality</div>', unsafe_allow_html=True
    )

    stats = get_performance_stats()
    closes = stats["closes"]
    if closes == 0:
        st.info("No closed trades yet.")
        return

    wins = stats["wins"]
    losses = stats["losses"]
    win_pct = stats["win_rate"]

    r = _q1(
        """
        SELECT
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS good_outcome,
            SUM(CASE WHEN won=0 AND exit_type='stop_hit' THEN 1 ELSE 0 END) AS stopped_out,
            SUM(CASE WHEN won=0 AND exit_type='thesis_exit' THEN 1 ELSE 0 END) AS thesis_failed,
            SUM(CASE WHEN won=1 AND exit_type='target_hit' THEN 1 ELSE 0 END) AS full_target,
            COUNT(*) AS total
        FROM trade_attribution
        WHERE COALESCE(created_at, entry_ts, '') >= ?
          AND paper=?
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10')
    """,
        (get_effective_launch_date(), _runtime_paper_flag()),
    )
    attr_total = r.get("total") or 0

    rows_html = ""
    if attr_total > 0:
        good = r.get("good_outcome") or 0
        stopped = r.get("stopped_out") or 0
        thesis_fail = r.get("thesis_failed") or 0
        full_target = r.get("full_target") or 0
        metrics = [
            (
                "Good outcome (won)",
                f"{good}/{attr_total} = {good / attr_total * 100:.0f}%",
            ),
            (
                "Stopped out (bad timing/signal)",
                f"{stopped}/{attr_total} = {stopped / attr_total * 100:.0f}%",
            ),
            (
                "Thesis invalidation exit",
                f"{thesis_fail}/{attr_total} = {thesis_fail / attr_total * 100:.0f}%",
            ),
            (
                "Full target hit",
                f"{full_target}/{attr_total} = {full_target / attr_total * 100:.0f}%",
            ),
        ]
    else:
        metrics = [
            ("Overall win rate", f"{win_pct:.1f}%  ({wins}W / {losses}L)"),
            ("Attribution rows", "0 — populates after trade closes"),
        ]

    bay = get_signal_bayesian_stats()
    if bay:
        improving = sorted(
            [b for b in bay if b.get("pts_drift", 0) > 0], key=lambda x: -x["pts_drift"]
        )[:2]
        degrading = sorted(
            [b for b in bay if b.get("pts_drift", 0) < 0], key=lambda x: x["pts_drift"]
        )[:2]
        if improving:
            metrics.append(
                (
                    "Top signal ↑",
                    f"{improving[0]['signal_name']} (+{improving[0]['pts_drift']:.1f}pts, {improving[0]['win_rate_pct']:.0f}%WR)",
                )
            )
        if degrading:
            metrics.append(
                (
                    "Worst signal ↓",
                    f"{degrading[0]['signal_name']} ({degrading[0]['pts_drift']:.1f}pts, {degrading[0]['win_rate_pct']:.0f}%WR)",
                )
            )

    for label, val in metrics:
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
