"""
Widget: Open Positions
Question: What trades are currently open and how are they doing?
Tab: MISSION CONTROL (left column), CRYPTO
Refresh: 10s
"""

import streamlit as st

import ui
from formatters import _time_ago, _fmt_pnl, _parse_notes
from data.positions import get_open_positions, get_live_prices
from tooltips import TIPS


@st.fragment(run_every=10)
def render_positions_compact():
    st.markdown(
        ui.section_header(
            "OPEN POSITIONS",
            "Active trades right now — live P&L updates every 10 seconds",
        ),
        unsafe_allow_html=True,
    )

    open_p = get_open_positions()

    if not open_p:
        st.markdown(
            ui.empty_state(
                "No open positions",
                "The bot is watching markets for the next setup. "
                "Positions appear here the moment a trade is entered.",
            ),
            unsafe_allow_html=True,
        )
        return

    symbols = [p.get("symbol", "") for p in open_p]
    live_prices = get_live_prices(symbols)
    total_deployed = 0.0
    total_unrealized = 0.0

    cards_html = ""
    for p in open_p:
        symbol = p.get("symbol", "")
        direction = p.get("direction", "LONG")
        entry = float(p.get("entry") or 0)
        qty = float(p.get("qty") or 0)
        stop = float(p.get("stop") or 0)
        target = float(p.get("target") or entry * 1.02)
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

        notes = p.get("notes", "") or ""
        meta = _parse_notes(notes)
        setup = meta.get("setup", "") or meta.get("regime", "")
        lev = meta.get("lev", "")
        setup_str = f"{setup} · {lev}x lev" if lev else setup

        is_live = not bool(p.get("paper", 1))
        risk_note = f"Stop at ${stop:,.4g} — {stop_pct:.1f}% away" if stop else ""
        if is_live:
            risk_note = "LIVE " + risk_note if risk_note else "LIVE POSITION"

        age = _time_ago(p.get("ts_entry", ""))
        cards_html += ui.position_card(
            symbol=symbol,
            direction=direction,
            pnl=unreal,
            entry=entry,
            current=now,
            stop_pct=stop_pct,
            setup=setup_str,
            risk_note=risk_note,
            age=age,
        )

        # risk bar for each position
        if stop and target:
            cards_html += ui.risk_bar(symbol, entry, stop, target)

    st.markdown(cards_html, unsafe_allow_html=True)

    n = len(open_p)
    pnl_color = ui.C_GREEN if total_unrealized >= 0 else ui.C_RED
    pnl_sign = "+" if total_unrealized >= 0 else ""
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;margin-top:6px;'
        f'padding-top:8px;border-top:1px solid rgba(255,255,255,0.07);font-size:0.80em;">'
        f'<span style="color:{ui._TEXT_SEC};">'
        f"{n} position{'s' if n != 1 else ''} · ${total_deployed:,.0f} deployed</span>"
        f'<span style="color:{pnl_color};font-weight:700;">'
        f"Unrealized {pnl_sign}${abs(total_unrealized):.2f}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
