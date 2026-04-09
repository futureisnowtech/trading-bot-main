"""
Widget: Equity Curve
Question: Is the account value growing over time? What's the current drawdown?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from tooltips import TIPS
from formatters import _fmt_pnl, _asset_badge
from data.account import get_equity_curve, get_drawdown, get_today_pnl
from data.performance import get_rolling_pf


@st.fragment(run_every=30)
def render_equity_curve_compact():
    import pandas as pd

    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)

    eq = get_equity_curve()
    dd = get_drawdown()
    today_pnl = get_today_pnl()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Today's P&L",
        _fmt_pnl(today_pnl),
        delta_color="normal" if today_pnl >= 0 else "inverse",
    )
    col2.metric(
        "Max Drawdown",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}% of account",
        delta_color="inverse" if dd["max_dd_usd"] > 0 else "off",
        help=TIPS.get("max_drawdown"),
    )
    col3.metric(
        "Current DD",
        f"${dd['current_dd_usd']:.2f}",
        delta=f"{dd['current_dd_pct']:.1f}%",
        delta_color="inverse" if dd["current_dd_usd"] > 0 else "off",
        help=TIPS.get("current_dd"),
    )

    rolling = get_rolling_pf(days=7)
    col4.metric(
        "7d Trades",
        str(rolling["closes"]),
        delta=f"{rolling['win_rate']:.0f}% WR",
        delta_color="normal" if rolling["win_rate"] >= 52 else "inverse",
    )

    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]], height=160, use_container_width=True
        )
    else:
        st.info("Equity curve appears after first closed trade.")
