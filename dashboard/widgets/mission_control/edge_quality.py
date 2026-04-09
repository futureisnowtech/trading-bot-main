"""
Widget: Edge Quality
Question: Is our crypto trading strategy making money?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from db import LAUNCH_DATE
from formatters import _badge, _fmt_pnl, _asset_badge
from data.performance import get_performance_stats, get_rolling_pf


@st.fragment(run_every=30)
def render_edge_quality():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Edge Quality</div>', unsafe_allow_html=True)

    stats = get_performance_stats()
    rolling_7d = get_rolling_pf(days=7)
    rolling_1d = get_rolling_pf(days=1)
    closes = stats["closes"]

    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
    pf_color = "green" if pf >= 1.35 else ("yellow" if pf >= 1.0 else "red")
    badge_kind = "pass" if pf >= 1.35 else ("warn" if pf >= 1.0 else "fail")

    wr = stats["win_rate"]
    ev_per_trade = stats["total_pnl"] / closes if closes else 0.0

    st.markdown(
        f'<div style="font-size:2em; font-weight:800; color: {"#4ade80" if pf_color == "green" else "#facc15" if pf_color == "yellow" else "#f87171"}">'
        f"PF {pf_str}</div>"
        f'<div style="font-size:0.75em; color:#94a3b8; margin-top:-4px">'
        f"{_badge('PASS ≥1.35' if pf >= 1.35 else 'WARN ≥1.0' if pf >= 1.0 else 'FAIL <1.0', badge_kind)}"
        f"</div>",
        unsafe_allow_html=True,
    )

    rows_html = ""
    metrics = [
        ("Win Rate", f"{wr:.1f}%  ({stats['wins']}W/{stats['losses']}L)"),
        ("EV / trade (net)", _fmt_pnl(ev_per_trade)),
        (
            "Avg Win / Avg Loss",
            f"{_fmt_pnl(stats['avg_win'])} / {_fmt_pnl(-stats['avg_loss'])}",
        ),
        ("R:R realized", f"{stats['rr_realized']:.2f}×"),
        ("Total fees", _fmt_pnl(-stats["total_fees"])),
        (
            "7d PF",
            f"{rolling_7d['profit_factor']:.2f}"
            if rolling_7d["profit_factor"] != float("inf")
            else "∞" + f"  ({rolling_7d['closes']} trades)",
        ),
        (
            "24h PF",
            f"{rolling_1d['profit_factor']:.2f}"
            if rolling_1d["profit_factor"] != float("inf")
            else "∞" + f"  ({rolling_1d['closes']} trades)",
        ),
    ]
    for label, val in metrics:
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"{closes} clean trades since {LAUNCH_DATE}")
