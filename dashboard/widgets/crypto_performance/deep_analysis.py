"""
Widget: Crypto Performance Deep Analysis
Question: Full breakdown of how our crypto trades are performing over time.
Tab: CRYPTO PERFORMANCE
Refresh: 30s
Asset class: CRYPTO PERPS
"""

import streamlit as st

from db import _q, LAUNCH_DATE
from tooltips import TIPS
from formatters import _fmt_pnl, _time_ago, _parse_notes, _asset_badge
from data.account import (
    get_account,
    get_today_pnl,
    get_equity_curve,
    get_drawdown,
    get_trade_log,
)
from data.performance import (
    get_performance_stats,
    get_per_symbol_stats,
    get_signal_bayesian_stats,
)
from data.positions import get_open_positions, get_live_prices
from data.execution import get_execution_stats, get_failure_counts
from data.health import get_ml_status


@st.fragment(run_every=30)
def render_deep_analysis():
    import pandas as pd

    st.markdown(_asset_badge("crypto"), unsafe_allow_html=True)

    # ── Full Edge Quality ──────────────────────────────────────────────────────
    st.subheader("Edge Quality — Full Breakdown")
    stats = get_performance_stats()
    dd = get_drawdown()

    c1, c2, c3, c4, c5 = st.columns(5)
    pf = stats["profit_factor"]
    c1.metric(
        "Profit Factor",
        f"{pf:.2f}" if pf != float("inf") else "∞",
        delta="≥1.35 needed for live",
        delta_color="normal" if pf >= 1.35 else "inverse",
        help=TIPS.get("profit_factor"),
    )
    c2.metric(
        "Win Rate",
        f"{stats['win_rate']:.1f}%",
        delta=f"{stats['wins']}W / {stats['losses']}L",
        help=TIPS.get("win_rate"),
    )
    c3.metric(
        "EV / trade",
        _fmt_pnl(stats["total_pnl"] / stats["closes"]) if stats["closes"] else "$0",
        delta_color="normal",
        help=TIPS.get("ev_per_trade"),
    )
    c4.metric(
        "R:R Realized", f"{stats['rr_realized']:.2f}×", help=TIPS.get("rr_realized")
    )
    c5.metric(
        "Max Drawdown",
        f"${dd['max_dd_usd']:.2f}",
        delta=f"{dd['max_dd_pct']:.1f}%",
        delta_color="inverse",
        help=TIPS.get("max_drawdown"),
    )

    col_left, col_right = st.columns(2)

    with col_left:
        st.caption("**Performance by regime**")
        regime_data = _q(
            """
            SELECT regime,
                COUNT(*) AS trades,
                SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS wins,
                ROUND(100.0 * SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS wr_pct,
                ROUND(AVG(pnl_usd), 2) AS avg_pnl,
                ROUND(SUM(pnl_usd), 2) AS total_pnl
            FROM trade_attribution
            WHERE ts >= ? GROUP BY regime ORDER BY total_pnl DESC
        """,
            (LAUNCH_DATE,),
        )
        if regime_data:
            st.dataframe(
                pd.DataFrame(regime_data), use_container_width=True, hide_index=True
            )
        else:
            st.info("No regime attribution data yet.")

    with col_right:
        st.caption("**Performance by symbol**")
        sym = get_per_symbol_stats()
        if sym:
            st.dataframe(pd.DataFrame(sym), use_container_width=True, hide_index=True)
        else:
            st.info("No closed trades yet.")

    eq = get_equity_curve()
    if eq:
        df = pd.DataFrame(eq)
        df["ts"] = pd.to_datetime(df["ts"].str[:19])
        df = df.rename(columns={"cum_pnl": "Net P&L ($)"})
        st.line_chart(
            df.set_index("ts")[["Net P&L ($)"]], height=200, use_container_width=True
        )

    st.divider()

    # ── Full Execution Quality ─────────────────────────────────────────────────
    st.subheader("Execution Quality — Full Breakdown")
    ex = get_execution_stats()
    if ex["total"] > 0:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Entry Timing",
            f"{ex['entry_score']:.1f}/10",
            delta="higher = better entry price",
            help=TIPS.get("entry_score"),
        )
        c2.metric(
            "Exit Efficiency",
            f"{ex['exit_score']:.1f}/10",
            delta="higher = captured more MFE",
            help=TIPS.get("exit_score"),
        )
        c3.metric(
            "Fee Trap Rate",
            f"{ex['fee_trap_rate']:.1f}%",
            delta=f"{ex['fee_traps']} traps / {ex['total']} trades",
            delta_color="inverse" if ex["fee_trap_rate"] > 5 else "off",
            help=TIPS.get("fee_trap"),
        )
        c4.metric(
            "Avg MAE",
            f"{ex['avg_mae_pct']:.3f}%",
            delta="adverse move before recovery",
            help=TIPS.get("mae"),
        )

        attr = _q(
            """
            SELECT symbol, direction, ROUND(mae_pct*100,3) AS mae_pct, ROUND(mfe_pct*100,3) AS mfe_pct,
                   exit_type, hold_minutes, is_fee_trap, won
            FROM trade_attribution WHERE ts >= ?
            ORDER BY entry_ts DESC LIMIT 30
        """,
            (LAUNCH_DATE,),
        )
        if attr:
            st.caption("Last 30 trade attributions")
            st.dataframe(pd.DataFrame(attr), use_container_width=True, hide_index=True)
    else:
        st.info("trade_attribution table is empty — populates as trades close.")

    st.divider()

    # ── Signal Attribution (Bayesian Learning) ────────────────────────────────
    st.subheader("Signal Attribution (Bayesian Learning)")
    with st.expander("What is this?", expanded=False):
        st.markdown(
            "Every time a trade closes, the system figures out which signals fired at entry and "
            "whether the trade won or lost. It then updates the win rate for each signal using "
            "Bayesian statistics — signals that keep firing on winning trades get more weight; "
            "those on losing trades get less. "
            "This table shows the current state of those weights. "
            "**pts_drift** = how much the signal's score contribution has moved from its starting value. "
            "Positive = the signal is outperforming expectations; negative = it's underperforming."
        )
    bay_stats = get_signal_bayesian_stats()
    if bay_stats:
        df_bay = pd.DataFrame(bay_stats)
        st.dataframe(df_bay, use_container_width=True, hide_index=True)
    else:
        st.info("No Bayesian signal data yet — accumulates with live trades.")

    st.divider()

    # ── Learning / Intelligence ────────────────────────────────────────────────
    st.subheader("Learning & Intelligence")

    ml = get_ml_status()
    col1, col2 = st.columns(2)
    with col1:
        snap = ml["snapshots"]
        needed = ml["min_needed"]
        status = (
            "ACTIVE" if snap >= needed else f"ACCUMULATING — {snap}/{needed} snapshots"
        )
        st.metric(
            "ML Snapshots",
            f"{snap} / {needed}",
            delta=status,
            delta_color="normal" if snap >= needed else "off",
            help=TIPS.get("ml_gate"),
        )
        st.progress(
            min(snap / needed, 1.0),
            text=f"{'Ready' if snap >= needed else 'Needs ' + str(needed - snap) + ' more'}",
        )
        st.caption(
            "XGBoost 60% + LightGBM 40% · walk-forward 60d/10d · WR≥54%, PF≥1.35, Sharpe≥0.8"
        )

    with col2:
        try:
            from learning.dynamic_weights import get_learning_summary

            summary = get_learning_summary()
            st.metric("Attributed Trades", str(summary.get("attributed_trades", 0)))
            st.metric("Signals Tracked", str(summary.get("signals_tracked", 0)))
            drift = summary.get("weights_diverged", 0)
            st.metric(
                "Weights Diverged",
                str(drift),
                delta=f"signals with |Δ| > 1.0pts",
                delta_color="off",
            )
        except Exception as e:
            st.info(f"Dynamic weights: {e}")

    st.divider()

    # ── Failure Mode Analysis ─────────────────────────────────────────────────
    st.subheader("Failure Mode Analysis (7 days)")
    with st.expander("What is this?", expanded=False):
        st.markdown(
            "Categorized breakdown of things that went wrong in the last 7 days. "
            "**Fee trap** = trade won but fees ate most of the profit — the system is trading too small or on moves that are too tiny. "
            "**Quick stop** = stop-loss hit within 30 minutes — usually bad entry timing or a stop-hunt wick. "
            "**Economics veto** = the system automatically blocked a trade because expected profit was too close to fees — this is *healthy* behavior, not a failure. "
            "**Scan dropout** = scanner found zero candidates — possible connectivity or market-hours issue."
        )
    failures = get_failure_counts()
    df_fail = pd.DataFrame(failures)
    st.dataframe(df_fail, use_container_width=True, hide_index=True)

    st.divider()

    # ── Full Trade Log ────────────────────────────────────────────────────────
    st.subheader("Trade History (last 100)")
    trades = get_trade_log(100)
    if trades:
        rows = []
        for t in trades:
            notes = _parse_notes(t.get("notes", ""))
            action = t.get("action", "")
            direction = (
                "LONG" if action == "SELL" else ("SHORT" if action == "BUY" else action)
            )
            pnl = t.get("pnl_usd") or 0
            fee = t.get("fee_usd") or 0
            rows.append(
                {
                    "Time": _time_ago(t.get("ts", "")),
                    "Symbol": t.get("symbol", ""),
                    "Direction": direction,
                    "Score": notes.get("score", ""),
                    "Regime": notes.get("regime", ""),
                    "Setup": notes.get("setup", notes.get("reason", ""))[:20],
                    "Price": t.get("price") or 0,
                    "P&L": _fmt_pnl(pnl),
                    "Fee": _fmt_pnl(-fee),
                    "Net": _fmt_pnl(pnl - fee),
                    "Result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ── Risk & Exposure ───────────────────────────────────────────────────────
    st.subheader("Risk & Exposure")

    open_p = get_open_positions()
    balance, _, base = get_account()
    today_pnl = get_today_pnl()

    if open_p:
        live_prices = get_live_prices([p.get("symbol", "") for p in open_p])
        total_deployed = sum(
            float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in open_p
        )
        total_unrealized = sum(
            (
                (live_prices.get(p["symbol"], p["entry"]) - p["entry"]) * p["qty"]
                if p["direction"] == "LONG"
                else (p["entry"] - live_prices.get(p["symbol"], p["entry"])) * p["qty"]
            )
            for p in open_p
        )
        deployed_pct = total_deployed / balance * 100 if balance else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Deployed Capital",
            f"${total_deployed:,.0f}",
            delta=f"{deployed_pct:.1f}% of account",
        )
        c2.metric(
            "Unrealized P&L",
            _fmt_pnl(total_unrealized),
            delta_color="normal" if total_unrealized >= 0 else "inverse",
        )
        try:
            from config import MAX_DAILY_LOSS_PCT, ACCOUNT_SIZE

            daily_limit = float(ACCOUNT_SIZE) * MAX_DAILY_LOSS_PCT
            c3.metric(
                "Daily P&L",
                _fmt_pnl(today_pnl),
                delta=f"limit: -${daily_limit:.0f}",
                delta_color="normal" if today_pnl >= -daily_limit else "inverse",
            )
        except Exception:
            c3.metric("Daily P&L", _fmt_pnl(today_pnl))
        c4.metric(
            "Kill Switch",
            f"${base * 0.75:,.0f}",
            delta=f"balance < 75% of ${base:,.0f}",
            delta_color="off",
        )

        rows = []
        for p in open_p:
            entry = float(p.get("entry") or 0)
            stop = float(p.get("stop") or 0)
            qty = float(p.get("qty") or 0)
            direction = p.get("direction", "LONG")
            now = live_prices.get(p.get("symbol", ""), entry) or entry
            stop_dist = abs(entry - stop) / entry * 100 if entry else 0
            if direction == "LONG":
                unreal = (now - entry) * qty
            else:
                unreal = (entry - now) * qty
            rows.append(
                {
                    "Symbol": p.get("symbol", ""),
                    "Direction": direction,
                    "Entry $": f"{entry:.5g}",
                    "Now $": f"{now:.5g}" if now != entry else "–",
                    "Unrealized": _fmt_pnl(unreal),
                    "Stop $": f"{stop:.5g}",
                    "Stop %": f"-{stop_dist:.2f}%",
                    "Age": _time_ago(p.get("ts_entry", "")),
                    "Setup": (p.get("entry_reason") or "")[:22],
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")
