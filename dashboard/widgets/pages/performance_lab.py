"""
dashboard/widgets/pages/performance_lab.py — PERFORMANCE LAB page.

Sections:
  - Performance summary strip (equity, P&L, win rate)
  - Full deep analysis widget
"""

import os
import sys

_PAGES_DIR = os.path.dirname(os.path.abspath(__file__))
_WIDGETS_DIR = os.path.dirname(_PAGES_DIR)
_DASH_DIR = os.path.dirname(_WIDGETS_DIR)
_ROOT = os.path.dirname(_DASH_DIR)

for _p in (_DASH_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st


def render_performance_lab():
    st.caption(
        "How is the strategy doing over time? "
        "Start with the summary strip — expand any section for the full breakdown."
    )

    # ── Performance summary strip ─────────────────────────────────────────────
    try:
        from data.account import get_account, get_today_pnl
        from data.performance import get_performance_stats

        equity, paper, base = get_account()
        daily_pnl = get_today_pnl()
        stats = get_performance_stats()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Equity", f"${equity:,.2f}")
        c2.metric("Base", f"${base:,.0f}")
        c3.metric("Daily P&L", f"${daily_pnl:+.2f}")

        win_rate = stats.get("win_rate", 0.0)
        c4.metric("Win rate", f"{win_rate:.1f}%")

        closes = stats.get("closes", 0)
        c5.metric("Closed trades", closes)

    except Exception as e:
        st.caption(f"Performance summary unavailable: {e}")

    st.divider()

    # ── Deep analysis widget ──────────────────────────────────────────────────
    try:
        from widgets.crypto_performance.deep_analysis import render_deep_analysis

        render_deep_analysis()
    except Exception as e:
        st.error(f"Deep analysis widget unavailable: {e}")
