"""
Widget: Open Positions
Question: What crypto trades are currently open and how are they doing?
Tab: MISSION CONTROL
Refresh: 10s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from formatters import _time_ago, _fmt_pnl, _asset_badge
from data.positions import get_open_positions, get_live_prices


@st.fragment(run_every=10)
def render_positions_compact():
    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Open Positions</div>', unsafe_allow_html=True)

    open_p = get_open_positions()
    n = len(open_p)

    if not open_p:
        st.info("No open positions.")
        return

    symbols = [p.get("symbol", "") for p in open_p]
    live_prices = get_live_prices(symbols)
    total_deployed = 0.0
    total_unrealized = 0.0

    rows_html = ""
    for p in open_p:
        symbol = p.get("symbol", "")
        direction = p.get("direction", "LONG")
        entry = float(p.get("entry") or 0)
        qty = float(p.get("qty") or 0)
        stop = float(p.get("stop") or 0)
        now = live_prices.get(symbol, 0) or entry
        deployed = qty * entry
        if direction == "LONG":
            unreal = (now - entry) * qty
            stop_pct = (entry - stop) / entry * 100 if entry else 0
        else:
            unreal = (entry - now) * qty
            stop_pct = (stop - entry) / entry * 100 if entry else 0
        total_deployed += deployed
        total_unrealized += unreal
        pnl_color = "#4ade80" if unreal >= 0 else "#f87171"
        dir_arrow = "▲" if direction == "LONG" else "▼"
        dir_color = "#4ade80" if direction == "LONG" else "#f87171"
        age = _time_ago(p.get("ts_entry", ""))
        rows_html += (
            f'<div style="display:flex; justify-content:space-between; font-size:0.8em; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<span><span style="color:{dir_color}; font-weight:700">{dir_arrow}</span> <span style="color:#e2e8f0; font-weight:600">{symbol}</span>'
            f' <span style="color:#64748b">{age}</span></span>'
            f'<span style="color:{pnl_color}; font-weight:700">{_fmt_pnl(unreal)}</span>'
            f"</div>"
        )

    st.markdown(rows_html, unsafe_allow_html=True)
    pnl_color_overall = "#4ade80" if total_unrealized >= 0 else "#f87171"
    st.markdown(
        f'<div style="display:flex; justify-content:space-between; margin-top:6px; font-size:0.82em">'
        f'<span style="color:#94a3b8">{n} positions · ${total_deployed:,.0f} deployed</span>'
        f'<span style="color:{pnl_color_overall}; font-weight:700">Unrealized {_fmt_pnl(total_unrealized)}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
