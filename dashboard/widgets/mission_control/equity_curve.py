"""
Widget: Equity Curve — Is the account growing? What's the current drawdown?
Refresh: 30s
"""

import streamlit as st

import ui
from data.account import get_drawdown, get_equity_curve, get_today_pnl
from data.performance import get_rolling_pf
from formatters import _fmt_pnl
from tooltips import TIPS


@st.fragment(run_every=30)
def render_equity_curve_compact():
    import pandas as pd

    eq = get_equity_curve()
    dd = get_drawdown()
    today = get_today_pnl()
    rolling = get_rolling_pf(days=7)

    today_c = ui.C_GREEN if today >= 0 else ui.C_RED
    today_s = "+" if today >= 0 else ""

    # ── 4-metric strip ─────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Today's P&L",
        f"{today_s}${abs(today):.2f}",
        help="Profit or loss since midnight today (closed trades only)",
    )
    col2.metric(
        "Worst losing stretch",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}% of account",
        delta_color="inverse" if dd["max_dd_usd"] > 0 else "off",
        help=TIPS.get("max_drawdown"),
    )
    col3.metric(
        "Currently below peak",
        f"${dd['current_dd_usd']:.2f}",
        delta=f"{dd['current_dd_pct']:.1f}%",
        delta_color="inverse" if dd["current_dd_usd"] > 0 else "off",
        help=TIPS.get("current_dd"),
    )
    col4.metric(
        "Last 7 days",
        f"{rolling['closes']} trades",
        delta=f"{rolling['win_rate']:.0f}% winning",
        delta_color="normal" if rolling["win_rate"] >= 52 else "inverse",
    )

    # ── Chart ──────────────────────────────────────────────────────────────────
    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]],
            height=160,
            use_container_width=True,
        )

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
                trend_col = ui.C_GREEN
            elif recent >= 0:
                trend_msg = (
                    f"Profitable overall, but ${peak - recent:.2f} below the peak."
                )
                trend_col = ui.C_AMBER
            else:
                trend_msg = f"Currently underwater by ${abs(recent):.2f} from the starting point."
                trend_col = ui.C_RED
            st.markdown(
                f'<p style="color:{trend_col};font-size:0.78em;margin-top:2px;">'
                f"{trend_msg}</p>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            ui.info_callout(
                "Equity curve appears after the first closed trade. "
                "Every completed trade adds a data point.",
                "info",
            ),
            unsafe_allow_html=True,
        )
