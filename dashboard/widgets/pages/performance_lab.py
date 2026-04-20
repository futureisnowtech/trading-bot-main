"""
dashboard/widgets/pages/performance_lab.py — PERFORMANCE LAB page.

Premium design: 5-metric hero strip, then 6 internal subtabs covering
strategy performance, trade history, learning signals, and risk attribution.
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

import ui
from formatters import _plain_pf


def render_performance_lab():
    # ── Top metric strip ───────────────────────────────────────────────────────
    try:
        from data.account import get_today_pnl, get_drawdown
        from data.performance import get_performance_stats

        stats = get_performance_stats()
        dd = get_drawdown()
        daily_pnl = get_today_pnl()

        pf = stats.get("profit_factor", 0.0)
        wr = stats.get("win_rate", 0.0)
        rr = stats.get("rr_realized", 0.0)
        avg_win = stats.get("avg_win", 0.0)
        max_dd_pct = dd.get("max_dd_pct", 0.0)
        closes = stats.get("closes", 0)
        total_fees = stats.get("total_fees", 0.0)

        pf_status = "good" if pf >= 1.4 else "watch" if pf >= 1.0 else "problem"
        wr_status = "good" if wr >= 55 else "watch" if wr >= 45 else "problem"
        rr_status = "good" if rr >= 1.5 else "watch" if rr >= 1.0 else "problem"
        dd_status = (
            "good" if max_dd_pct < 5 else "watch" if max_dd_pct < 12 else "problem"
        )
        daily_status = (
            "good" if daily_pnl >= 0 else "watch" if daily_pnl >= -50 else "problem"
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(
                ui.summary_card(
                    "Profit Factor",
                    _plain_pf(pf),
                    "Strong" if pf >= 1.4 else "Marginal" if pf >= 1.0 else "Below 1",
                    pf_status,
                    f"Gross wins ÷ gross losses · {closes} closed trades · "
                    "Above 1.3 = good, below 1.0 = losing money overall",
                ),
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                ui.summary_card(
                    "Win Rate",
                    f"{wr:.1f}%",
                    "Good" if wr >= 55 else "Watch" if wr >= 45 else "Low",
                    wr_status,
                    "Percentage of closed trades that made money. "
                    "55%+ is strong; needs to pair with good R:R",
                ),
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                ui.summary_card(
                    "Avg Win : Avg Loss",
                    f"{rr:.2f}×" if rr > 0 else "—",
                    "Good" if rr >= 1.5 else "Marginal" if rr >= 1.0 else "Low",
                    rr_status,
                    "How much you win on winners vs lose on losers. "
                    "1.5× means average wins are 50% bigger than average losses",
                ),
                unsafe_allow_html=True,
            )
        with c4:
            st.markdown(
                ui.summary_card(
                    "Avg Win Per Trade",
                    f"${avg_win:.2f}" if avg_win > 0 else "—",
                    "Fees OK"
                    if avg_win > total_fees / max(closes, 1) * 2
                    else "Watch fees",
                    "good" if avg_win > 2.0 else "watch",
                    f"Expected dollar value of a winning trade · "
                    f"Total fees paid: ${total_fees:.2f}",
                ),
                unsafe_allow_html=True,
            )
        with c5:
            st.markdown(
                ui.summary_card(
                    "Worst Losing Stretch",
                    f"{max_dd_pct:.1f}%",
                    "Under control"
                    if max_dd_pct < 5
                    else "Watch"
                    if max_dd_pct < 12
                    else "High",
                    dd_status,
                    "Largest peak-to-trough drawdown since clean data started. "
                    "Kill switch fires at 25% (paper) / 50% (live)",
                ),
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.error(f"Performance summary unavailable: {e}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Internal subtabs ───────────────────────────────────────────────────────
    (
        tab_overview,
        tab_winners,
        tab_fees,
        tab_learning,
        tab_risk,
        tab_history,
    ) = st.tabs(
        [
            "Overview",
            "Winners & Losers",
            "Fees & Execution",
            "Learning & Attribution",
            "Risk & Exposure",
            "Trade History",
        ]
    )

    # ── TAB 1: Overview ────────────────────────────────────────────────────────
    with tab_overview:
        st.markdown(
            ui.info_callout(
                "High-level strategy health — equity growth, rolling performance, "
                "and the most recent closed trades.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        ov_left, ov_right = st.columns([1.4, 1])

        with ov_left:
            try:
                from widgets.mission_control.equity_curve import (
                    render_equity_curve_compact,
                )

                render_equity_curve_compact()
            except Exception as e:
                st.caption(f"Equity curve unavailable: {e}")

        with ov_right:
            try:
                from data.performance import get_rolling_pf, get_per_symbol_stats

                rolling7 = get_rolling_pf(days=7)
                rolling30 = get_rolling_pf(days=30)

                body = ""
                body += ui.metric_row(
                    "Last 7 days — profit factor",
                    _plain_pf(rolling7.get("profit_factor", 0)),
                    ui.C_GREEN
                    if rolling7.get("profit_factor", 0) >= 1.2
                    else ui.C_AMBER,
                )
                body += ui.metric_row(
                    "Last 7 days — trades",
                    f"{rolling7.get('closes', 0)} trades, {rolling7.get('win_rate', 0):.0f}% win",
                )
                body += ui.metric_row(
                    "Last 30 days — profit factor",
                    _plain_pf(rolling30.get("profit_factor", 0)),
                    ui.C_GREEN
                    if rolling30.get("profit_factor", 0) >= 1.2
                    else ui.C_AMBER,
                )
                body += ui.metric_row(
                    "Last 30 days — trades",
                    f"{rolling30.get('closes', 0)} trades, {rolling30.get('win_rate', 0):.0f}% win",
                )

                st.markdown(
                    ui.detail_card(
                        "Rolling Performance",
                        "How has the last 7 and 30 days gone?",
                        body,
                    ),
                    unsafe_allow_html=True,
                )
            except Exception as e:
                st.caption(f"Rolling stats unavailable: {e}")

        try:
            from widgets.mission_control.scanner_funnel import render_scanner_funnel

            render_scanner_funnel()
        except Exception as e:
            st.caption(f"Scanner funnel unavailable: {e}")

    # ── TAB 2: Winners & Losers ────────────────────────────────────────────────
    with tab_winners:
        st.markdown(
            ui.info_callout(
                "Which symbols and setups are making money, and which are dragging performance. "
                "Helps identify where the edge actually lives.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from data.performance import get_per_symbol_stats

            sym_stats = get_per_symbol_stats()

            if sym_stats:
                winners = sorted(
                    [s for s in sym_stats if (s.get("net_pnl") or 0) > 0],
                    key=lambda s: s.get("net_pnl") or 0,
                    reverse=True,
                )
                losers = sorted(
                    [s for s in sym_stats if (s.get("net_pnl") or 0) <= 0],
                    key=lambda s: s.get("net_pnl") or 0,
                )

                wl_left, wl_right = st.columns(2)

                with wl_left:
                    body_w = ""
                    for s in winners[:8]:
                        sym_name = s.get("symbol") or s.get("underlying") or "?"
                        pnl_w = s.get("net_pnl") or 0
                        ct_w = s.get("closes") or 0
                        wr_w = s.get("win_rate") or 0
                        body_w += ui.metric_row(
                            f"{sym_name} · {ct_w} trades · {wr_w:.0f}% WR",
                            f"+${pnl_w:.2f}",
                            ui.C_GREEN,
                        )
                    if not body_w:
                        body_w = ui.info_callout("No profitable symbols yet.", "info")
                    st.markdown(
                        ui.detail_card(
                            "Top Winners", "Symbols generating the most profit", body_w
                        ),
                        unsafe_allow_html=True,
                    )

                with wl_right:
                    body_l = ""
                    for s in losers[:8]:
                        sym_name = s.get("symbol") or s.get("underlying") or "?"
                        pnl_l = s.get("net_pnl") or 0
                        ct_l = s.get("closes") or 0
                        wr_l = s.get("win_rate") or 0
                        body_l += ui.metric_row(
                            f"{sym_name} · {ct_l} trades · {wr_l:.0f}% WR",
                            f"−${abs(pnl_l):.2f}",
                            ui.C_RED,
                        )
                    if not body_l:
                        body_l = ui.info_callout(
                            "No losing symbols — great start.", "good"
                        )
                    st.markdown(
                        ui.detail_card(
                            "Worst Performers", "Symbols dragging overall P&L", body_l
                        ),
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    ui.empty_state(
                        "No closed trades yet",
                        "Winners & Losers appears after the first closed trade.",
                    ),
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.caption(f"Symbol stats unavailable: {e}")

    # ── TAB 3: Fees & Execution ────────────────────────────────────────────────
    with tab_fees:
        st.markdown(
            ui.info_callout(
                "Are trading costs eating the edge? Entry/exit quality shows whether the bot "
                "is getting filled at good prices relative to what the signal predicted.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        fe_left, fe_right = st.columns(2)
        with fe_left:
            try:
                from widgets.mission_control.execution_quality import (
                    render_execution_quality,
                )

                render_execution_quality()
            except Exception as e:
                st.caption(f"Execution quality unavailable: {e}")

        with fe_right:
            try:
                from data.performance import get_performance_stats

                stats_fe = get_performance_stats()
                total_pnl = stats_fe.get("total_pnl") or 0.0
                total_fees_fe = stats_fe.get("total_fees") or 0.0
                closes_fe = stats_fe.get("closes") or 0
                fee_per_trade = total_fees_fe / closes_fe if closes_fe else 0.0
                fee_drag_pct = (
                    abs(total_fees_fe / (total_pnl + total_fees_fe)) * 100
                    if (total_pnl + total_fees_fe) != 0
                    else 0.0
                )

                body_fee = ""
                body_fee += ui.metric_row(
                    "Total fees paid", f"${total_fees_fe:.2f}", ui.C_AMBER
                )
                body_fee += ui.metric_row(
                    "Avg fee per trade",
                    f"${fee_per_trade:.3f}",
                    ui.C_AMBER if fee_per_trade > 0.5 else ui._TEXT_PRI,
                )
                body_fee += ui.metric_row(
                    "Fee drag on gross P&L",
                    f"{fee_drag_pct:.1f}%",
                    ui.C_RED
                    if fee_drag_pct > 20
                    else ui.C_AMBER
                    if fee_drag_pct > 10
                    else ui.C_GREEN,
                )
                body_fee += ui.metric_row(
                    "Coinbase taker rate",
                    "0.030% per side (0.060% round-trip)",
                )
                body_fee += ui.metric_row(
                    "Economics gate",
                    "Blocks trades where fees eat projected edge",
                )

                st.markdown(
                    ui.detail_card(
                        "Fee Analysis",
                        "Coinbase 0.030% taker — economics gate screens every entry",
                        body_fee,
                    ),
                    unsafe_allow_html=True,
                )
            except Exception as e:
                st.caption(f"Fee analysis unavailable: {e}")

    # ── TAB 4: Learning & Attribution ─────────────────────────────────────────
    with tab_learning:
        st.markdown(
            ui.info_callout(
                "Which signals are actually working? The Bayesian engine updates win rates for "
                "every signal after every trade. High-performing signals get more weight.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        la_left, la_right = st.columns(2)
        with la_left:
            try:
                from widgets.mission_control.decision_quality import (
                    render_decision_quality,
                )

                render_decision_quality()
            except Exception as e:
                st.caption(f"Decision quality unavailable: {e}")

        with la_right:
            try:
                from widgets.mission_control.edge_quality import render_edge_quality

                render_edge_quality()
            except Exception as e:
                st.caption(f"Edge quality unavailable: {e}")

        try:
            from widgets.crypto_performance.deep_analysis import render_deep_analysis

            with st.expander("Full attribution breakdown", expanded=False):
                render_deep_analysis()
        except Exception as e:
            st.caption(f"Deep analysis unavailable: {e}")

    # ── TAB 5: Risk & Exposure ─────────────────────────────────────────────────
    with tab_risk:
        st.markdown(
            ui.info_callout(
                "Drawdown history, current exposure, and kill-switch status. "
                "The kill switch fires at 25% drawdown (paper) or 50% of live baseline.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        try:
            from data.account import get_drawdown, get_account

            dd_risk = get_drawdown()
            equity, paper_flag, base = get_account()

            rk_left, rk_right = st.columns(2)

            with rk_left:
                body_dd = ""
                body_dd += ui.metric_row(
                    "Max drawdown (USD)",
                    f"${dd_risk.get('max_dd_usd', 0):.2f}",
                    ui.C_RED if dd_risk.get("max_dd_pct", 0) > 8 else ui.C_AMBER,
                )
                body_dd += ui.metric_row(
                    "Max drawdown (%)",
                    f"{dd_risk.get('max_dd_pct', 0):.2f}%",
                    ui.C_RED if dd_risk.get("max_dd_pct", 0) > 8 else ui.C_AMBER,
                )
                body_dd += ui.metric_row(
                    "Current below peak (USD)",
                    f"${dd_risk.get('current_dd_usd', 0):.2f}",
                    ui.C_RED if dd_risk.get("current_dd_pct", 0) > 3 else ui._TEXT_PRI,
                )
                body_dd += ui.metric_row(
                    "Current below peak (%)",
                    f"{dd_risk.get('current_dd_pct', 0):.2f}%",
                )
                st.markdown(
                    ui.detail_card(
                        "Drawdown", "Peak-to-trough account decline", body_dd
                    ),
                    unsafe_allow_html=True,
                )

            with rk_right:
                body_acc = ""
                body_acc += ui.metric_row("Current equity", f"${equity:,.2f}")
                body_acc += ui.metric_row("Account base", f"${base:,.0f}")
                body_acc += ui.metric_row(
                    "Net P&L from base",
                    f"${equity - base:+,.2f}",
                    ui.C_GREEN if equity >= base else ui.C_RED,
                )
                body_acc += ui.metric_row("Max risk per trade", "1% of account")
                body_acc += ui.metric_row(
                    "Max daily loss limit", "4% → halt all trading"
                )
                body_acc += ui.metric_row(
                    "Kill switch threshold", "75% of account (paper)"
                )
                st.markdown(
                    ui.detail_card(
                        "Account & Risk Rules",
                        "Current state vs. risk limits",
                        body_acc,
                    ),
                    unsafe_allow_html=True,
                )

        except Exception as e:
            st.caption(f"Risk data unavailable: {e}")

    # ── TAB 6: Trade History ───────────────────────────────────────────────────
    with tab_history:
        st.markdown(
            ui.info_callout(
                "Full log of closed trades. Each row shows the trade, the outcome, "
                "and the fee impact.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        try:
            from db import _q, LAUNCH_DATE, _runtime_paper_flag
            import pandas as pd

            paper = _runtime_paper_flag()
            trades = _q(
                """SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd,
                          (pnl_usd - fee_usd) AS net_pnl, strategy, broker, notes
                   FROM trades
                   WHERE ts >= ? AND paper=?
                     AND won IS NOT NULL
                     AND broker NOT LIKE '%bybit%'
                     AND (source IS NULL OR source NOT IN
                          ('backtest','pre_v10_contaminated','bybit_paper'))
                   ORDER BY ts DESC
                   LIMIT 200""",
                (LAUNCH_DATE, paper),
            )

            if trades:
                df = pd.DataFrame(trades)
                df["ts"] = df["ts"].str[:16]
                df["net_pnl"] = df["net_pnl"].round(2)
                df["fee_usd"] = df["fee_usd"].round(3)
                df["price"] = df["price"].round(4)

                st.dataframe(
                    df[
                        [
                            "ts",
                            "symbol",
                            "action",
                            "qty",
                            "price",
                            "pnl_usd",
                            "fee_usd",
                            "net_pnl",
                            "strategy",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                st.caption(
                    f"Showing up to 200 most recent closed trades · {len(trades)} loaded"
                )
            else:
                st.markdown(
                    ui.empty_state(
                        "No closed trades yet",
                        "Trade history fills in after the first completed trade.",
                    ),
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.caption(f"Trade history unavailable: {e}")
