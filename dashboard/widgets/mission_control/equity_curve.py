"""
Widget: Equity Curve
Question: Is the account value growing over time? What's the current drawdown?
Tab: MISSION CONTROL
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from data.account import get_drawdown, get_equity_curve, get_today_pnl
from data.performance import get_rolling_pf
from formatters import _asset_badge, _fmt_pnl
from tooltips import TIPS


@st.fragment(run_every=30)
def render_equity_curve_compact():
    import pandas as pd

    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)

    eq = get_equity_curve()
    dd = get_drawdown()
    today = get_today_pnl()
    rolling = get_rolling_pf(days=7)

    col1, col2, col3, col4 = st.columns(4)

    # Today's P&L — plain label
    col1.metric(
        "Today's P&L",
        _fmt_pnl(today),
        delta_color="normal" if today >= 0 else "inverse",
    )

    # Max drawdown — translated label + sub-text
    col2.metric(
        "Biggest losing streak ever",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}% of account",
        delta_color="inverse" if dd["max_dd_usd"] > 0 else "off",
        help=TIPS.get("max_drawdown"),
    )

    # Current DD — translated
    col3.metric(
        "Currently down from peak",
        f"${dd['current_dd_usd']:.2f}",
        delta=f"{dd['current_dd_pct']:.1f}%",
        delta_color="inverse" if dd["current_dd_usd"] > 0 else "off",
        help=TIPS.get("current_dd"),
    )

    # Last 7 days
    col4.metric(
        "Last 7 days",
        f"{rolling['closes']} trades",
        delta=f"{rolling['win_rate']:.0f}% winning",
        delta_color="normal" if rolling["win_rate"] >= 52 else "inverse",
    )

    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]], height=160, use_container_width=True
        )
        # Plain-English trend interpretation
        pnls = [
            r["Net P&L ($)"]
            for r in df.to_dict("records")
            if r["Net P&L ($)"] is not None
        ]
        if len(pnls) >= 2:
            recent = pnls[-1]
            peak = max(pnls)
            if recent == peak:
                trend_msg = "At an all-time high — the strategy is working."
                trend_col = "#4ade80"
            elif recent >= 0:
                trend_msg = (
                    f"Profitable overall, but ${peak - recent:.2f} below the peak."
                )
                trend_col = "#facc15"
            else:
                trend_msg = f"Currently underwater by ${abs(recent):.2f} from the starting point."
                trend_col = "#f87171"
            st.markdown(
                f'<p style="color:{trend_col}; font-size:0.8em; margin-top:2px;">'
                f"{trend_msg}</p>",
                unsafe_allow_html=True,
            )
    else:
        st.info("Equity curve appears after the first closed trade.")
