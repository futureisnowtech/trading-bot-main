"""
dashboard/widgets/stocks/stocks_dashboard.py — STOCKS page widget.

4 subtabs: Overview, Opportunity Board, Trade Console, Stats.
Mirrors crypto_page.py design language and sys.path pattern.
Refreshes every 30s via @st.fragment(run_every=30).
"""

import os
import sys

_STOCKS_DIR = os.path.dirname(os.path.abspath(__file__))
_WIDGETS_DIR = os.path.dirname(_STOCKS_DIR)
_DASH_DIR = os.path.dirname(_WIDGETS_DIR)
_ROOT = os.path.dirname(_DASH_DIR)

for _p in (_DASH_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

import ui
from formatters import _time_ago, _fmt_pnl
from data.stocks import (
    get_stock_header,
    get_stock_positions,
    get_stock_trades_today,
    get_stock_all_time_stats,
    get_stock_daily_pnl,
    get_stock_candidates,
    get_stock_recent_trades,
)

try:
    from config import STOCK_UNIVERSE
except ImportError:
    STOCK_UNIVERSE = [
        "AMD",
        "GOOGL",
        "AAPL",
        "AMZN",
        "TSLA",
        "COIN",
        "IWM",
        "XLF",
        "XLE",
        "XLK",
        "NFLX",
        "UBER",
    ]

# ── Signal label map ──────────────────────────────────────────────────────────

_SIGNAL_LABELS = {
    "ema_aligned": "EMA 9/21/50 Aligned",
    "supertrend_bullish": "SuperTrend Bullish",
    "macd_rising": "MACD Histogram Rising",
    "rsi_healthy": "RSI 45-70 (Healthy Zone)",
    "vol_surge": "Volume Surge (1.5x avg)",
    "above_vwap": "Price Above VWAP",
    "low_chop": "Low Choppiness (<50)",
    "above_sma20": "Price Above SMA20",
}

_DECISION_LABELS = {
    "entered": "Entered",
    "below_threshold": "Score too low",
    "data_unavailable": "No data",
    "sizing_zero": "Size = 0",
    "execution_failed": "Broker rejected",
    "dual_exposure_block": "Already holding",
}


def _decision_label(raw: str) -> str:
    return _DECISION_LABELS.get(raw, raw.replace("_", " ").title())


def _candidate_card_stocks(row: dict) -> str:
    """HTML candidate card styled like crypto_page._candidate_card."""
    sym = row.get("symbol", "?")
    score = float(row.get("score") or 0)
    decision = row.get("decision", "")
    notes = row.get("notes", "")
    ts = row.get("ts", "")

    if decision == "entered":
        s_color, s_label = ui.C_GREEN, "ENTERED"
    elif decision == "below_threshold":
        s_color, s_label = ui.C_AMBER, "BELOW THRESHOLD"
    elif decision in ("execution_failed",):
        s_color, s_label = ui.C_RED, "FAILED"
    elif decision in ("data_unavailable",):
        s_color, s_label = ui.C_NEUTRAL, "NO DATA"
    elif decision == "dual_exposure_block":
        s_color, s_label = ui.C_CYAN, "ALREADY HELD"
    else:
        s_color, s_label = ui.C_NEUTRAL, decision.upper().replace("_", " ")

    sc_color = ui.C_GREEN if score >= 65 else (ui.C_AMBER if score >= 50 else ui.C_RED)

    # Parse signals from notes field
    signals_fired = []
    for key, label in _SIGNAL_LABELS.items():
        if key in notes:
            signals_fired.append(label)

    why_appeared = f"Source: Stock Scanner<br>Seen: {_time_ago(ts)}"
    why_works = (
        f"Direction: <strong style='color:{ui.C_GREEN};'>▲ LONG</strong><br>"
        f"Score: <strong style='color:{sc_color};'>{score:.0f}/100</strong><br>"
        f"Signals: <strong>{len(signals_fired)}</strong> fired"
    )
    if signals_fired:
        why_works += "<br>" + ", ".join(signals_fired[:3])

    what_kills = ""
    if decision == "entered":
        # Parse price/stop/target from notes
        price_note = ""
        for part in notes.split():
            if part.startswith("price="):
                price_note = part.replace("price=", "$")
            elif part.startswith("stop="):
                price_note += f" stop={part.replace('stop=', '$')}"
        what_kills = f'<span style="color:{ui.C_GREEN};">Executed — {price_note}</span>'
    elif decision == "below_threshold":
        what_kills = f'<span style="color:{ui.C_AMBER};">Score {score:.0f} &lt; 60 threshold</span>'
    elif decision == "execution_failed":
        what_kills = f'<span style="color:{ui.C_RED};">Broker returned None</span>'
    elif decision == "data_unavailable":
        what_kills = (
            f'<span style="color:{ui._TEXT_CAP};">No OHLCV bars from yfinance</span>'
        )
    elif decision == "sizing_zero":
        what_kills = (
            f'<span style="color:{ui.C_AMBER};">ATR sizing returned 0 shares</span>'
        )
    else:
        what_kills = (
            f'<span style="color:{ui._TEXT_CAP};">{_decision_label(decision)}</span>'
        )

    top_border = f"border-top:2px solid {s_color};"
    return (
        f'<div style="background:{ui._BG_CARD};{top_border}border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:14px 16px;margin-bottom:8px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin-bottom:10px;flex-wrap:wrap;gap:6px;">'
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<span style="font-weight:800;color:{ui._TEXT_PRI};font-size:1.1em;">{sym}</span>'
        f'<span style="color:{ui.C_GREEN};font-weight:700;font-size:0.80em;">▲ LONG</span>'
        f"</div>"
        f"<div>"
        f'<span style="color:{s_color};font-size:0.70em;font-weight:700;'
        f'padding:2px 8px;background:{s_color}1a;border-radius:100px;">{s_label}</span>'
        f"</div>"
        f"</div>"
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;'
        f'border-top:1px solid {ui._BORDER};padding-top:10px;">'
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">Why it appeared</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{why_appeared}</div>'
        f"</div>"
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">Why it might work</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{why_works}</div>'
        f"</div>"
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">What could stop it</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{what_kills}</div>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


def _position_row(pos: dict) -> str:
    """HTML row card for an open stock position."""
    sym = pos.get("symbol", "?")
    qty = int(pos.get("qty") or 0)
    entry = float(pos.get("entry") or 0)
    stop = float(pos.get("stop") or 0)
    target = float(pos.get("target") or 0)
    ts_entry = pos.get("ts_entry", "")

    stop_pct = abs((entry - stop) / entry * 100) if entry else 0.0
    target_pct = abs((target - entry) / entry * 100) if entry else 0.0
    age = _time_ago(ts_entry) if ts_entry else "–"

    return (
        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
        f"border-left:3px solid {ui.C_GREEN};border-radius:{ui._RADIUS_SM};"
        f'padding:14px 16px;margin-bottom:8px;">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
        f"<div>"
        f'<span style="font-weight:800;color:{ui._TEXT_PRI};font-size:1.05em;">{sym}</span>'
        f'&nbsp;<span style="color:{ui.C_GREEN};font-size:0.75em;font-weight:700;">▲ LONG</span>'
        f'&nbsp;&nbsp;<span style="font-size:0.70em;color:{ui._TEXT_CAP};">{age}</span>'
        f"</div>"
        f'<div style="text-align:right;">'
        f'<div style="font-size:0.78em;color:{ui._TEXT_SEC};">{qty} shares</div>'
        f"</div>"
        f"</div>"
        f'<div style="display:flex;gap:16px;font-size:0.74em;color:{ui._TEXT_SEC};">'
        f'<span>Entry <strong style="color:{ui._TEXT_PRI};">${entry:,.2f}</strong></span>'
        f'<span>Stop <strong style="color:{ui.C_RED};">-{stop_pct:.1f}%</strong></span>'
        f'<span>Target <strong style="color:{ui.C_GREEN};">+{target_pct:.1f}%</strong></span>'
        f"</div>"
        f"</div>"
    )


@st.fragment(run_every=30)
def render_stocks():
    """Main STOCKS page — 4 subtabs, refreshes every 30s."""

    hdr = get_stock_header()
    connected = hdr.get("connected", False)
    account_value = float(hdr.get("account_value") or 0.0)
    open_count = int(hdr.get("open_count") or 0)
    mode_label = hdr.get("mode_label", "UNKNOWN")
    pdt_count = int(hdr.get("pdt_count") or 0)

    # ── Summary cards ─────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        conn_status = "good" if connected else "watch"
        conn_label = "Connected" if connected else "Disconnected"
        st.markdown(
            ui.summary_card(
                "Stocks Lane",
                "IBKR US Equities",
                conn_label,
                conn_status,
                f"Mode: {mode_label} · clientId=4 · port 7496 (live TWS) · "
                f"Swing trades only — no day trading",
            ),
            unsafe_allow_html=True,
        )

    with c2:
        av_label = f"${account_value:,.0f}" if account_value > 0 else "–"
        av_status = "good" if account_value > 1000 else "watch"
        st.markdown(
            ui.summary_card(
                "Account Value",
                av_label,
                "NetLiquidation" if account_value > 0 else "Unavailable",
                av_status,
                "IBKR NetLiquidation from account summary. Live when TWS is connected.",
            ),
            unsafe_allow_html=True,
        )

    with c3:
        try:
            from config import STOCKS_MAX_POSITIONS
        except ImportError:
            STOCKS_MAX_POSITIONS = 3
        pos_status = (
            "neutral"
            if open_count == 0
            else "good"
            if open_count < STOCKS_MAX_POSITIONS
            else "watch"
        )
        st.markdown(
            ui.summary_card(
                "Open Positions",
                str(open_count),
                "Flat" if open_count == 0 else f"{open_count}/{STOCKS_MAX_POSITIONS}",
                pos_status,
                f"Max {STOCKS_MAX_POSITIONS} concurrent swing positions · "
                "Bracket orders placed on entry (server-side stop + target)",
            ),
            unsafe_allow_html=True,
        )

    with c4:
        daily_pnl = get_stock_daily_pnl()
        pnl_status = (
            "good" if daily_pnl > 0 else "watch" if daily_pnl == 0 else "problem"
        )
        pnl_label = _fmt_pnl(daily_pnl) if daily_pnl != 0 else "$0.00"
        st.markdown(
            ui.summary_card(
                "Today's P&L",
                pnl_label,
                f"PDT count: {pdt_count}",
                pnl_status,
                f"Realized P&L from closed trades today · "
                f"PDT advisory: {pdt_count}/3 day trades in rolling 5 days",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Subtabs ───────────────────────────────────────────────────────────────
    tab_overview, tab_board, tab_console, tab_stats = st.tabs(
        ["Overview", "Opportunity Board", "Trade Console", "Stats"]
    )

    # ── TAB 1: Overview ───────────────────────────────────────────────────────
    with tab_overview:
        st.markdown(
            ui.info_callout(
                "Live snapshot of the US equity swing-trading lane. "
                "Scans every 30 minutes during market hours (9:30–16:00 ET). "
                "Bracket orders placed server-side — stop and target fire automatically.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        col_left, col_right = st.columns(2)

        with col_left:
            positions = get_stock_positions()
            st.markdown(
                ui.section_header(
                    "Open Positions",
                    f"{len(positions)} position{'s' if len(positions) != 1 else ''} open",
                ),
                unsafe_allow_html=True,
            )
            if positions:
                for pos in positions:
                    st.markdown(_position_row(pos), unsafe_allow_html=True)
            else:
                st.markdown(
                    ui.empty_state(
                        "No open stock positions",
                        "The scanner will enter when a symbol scores ≥60/100 "
                        "during market hours.",
                    ),
                    unsafe_allow_html=True,
                )

        with col_right:
            trades_today = get_stock_trades_today()
            st.markdown(
                ui.section_header(
                    "Today's Trades",
                    f"{len(trades_today)} trade{'s' if len(trades_today) != 1 else ''} today",
                ),
                unsafe_allow_html=True,
            )
            if trades_today:
                import pandas as pd

                rows_display = []
                for t in trades_today:
                    pnl = float(t.get("pnl_usd") or 0)
                    rows_display.append(
                        {
                            "Time": str(t.get("ts", ""))[:16],
                            "Symbol": t.get("symbol", ""),
                            "Action": t.get("action", ""),
                            "Qty": t.get("qty", 0),
                            "Price": f"${float(t.get('price') or 0):,.2f}",
                            "P&L": _fmt_pnl(pnl) if pnl != 0 else "–",
                        }
                    )
                st.dataframe(
                    pd.DataFrame(rows_display),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.markdown(
                    ui.empty_state(
                        "No trades today", "Trades appear here when the bot executes."
                    ),
                    unsafe_allow_html=True,
                )

    # ── TAB 2: Opportunity Board ──────────────────────────────────────────────
    with tab_board:
        st.markdown(
            ui.info_callout(
                "Recent scan candidates from the stock universe — scored, entered, or blocked. "
                "Each card shows why the scanner saw it and what happened.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        ctrl_l, ctrl_r = st.columns([1, 1])
        with ctrl_l:
            hours_sel = st.selectbox(
                "Time window",
                ["1h", "6h", "24h", "7d"],
                index=2,
                key="stocks_board_hours",
            )
        with ctrl_r:
            filter_sel = st.radio(
                "Filter",
                ["All", "Entered", "Blocked", "No Data"],
                horizontal=True,
                key="stocks_board_filter",
            )

        hours_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
        rows = get_stock_candidates(hours=hours_map[hours_sel])

        if filter_sel == "Entered":
            rows = [r for r in rows if r.get("decision") == "entered"]
        elif filter_sel == "Blocked":
            rows = [
                r
                for r in rows
                if r.get("decision") not in ("entered", "data_unavailable")
            ]
        elif filter_sel == "No Data":
            rows = [r for r in rows if r.get("decision") == "data_unavailable"]

        if rows:
            st.caption(f"{len(rows)} candidates in the last {hours_sel}")
            for row in rows[:50]:
                st.markdown(_candidate_card_stocks(row), unsafe_allow_html=True)
        else:
            st.markdown(
                ui.empty_state(
                    "No stock candidates in this window",
                    "The scanner runs every 30 minutes during market hours. "
                    "Come back after the next cycle, or widen the time window.",
                ),
                unsafe_allow_html=True,
            )

    # ── TAB 3: Trade Console ──────────────────────────────────────────────────
    with tab_console:
        st.markdown(
            ui.info_callout(
                "Manual stock trade entry. Select a symbol, preview the entry parameters "
                "(price, stop, target from ATR sizing), then execute. "
                "All executions go through the same broker as the live bot.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        # Account summary row
        hdr_r = get_stock_header()
        m1, m2, m3 = st.columns(3)
        m1.metric("Account Value", f"${float(hdr_r.get('account_value') or 0):,.0f}")
        m2.metric("Open Positions", str(hdr_r.get("open_count", 0)))
        m3.metric("PDT Day Trades (5d)", str(hdr_r.get("pdt_count", 0)))

        st.markdown("---")

        col_form, col_result = st.columns([1, 1])

        with col_form:
            st.markdown(
                ui.section_header("Manual Entry", "Symbol → preview → execute"),
                unsafe_allow_html=True,
            )

            symbol_sel = st.selectbox(
                "Symbol",
                STOCK_UNIVERSE,
                key="stocks_console_symbol",
            )
            qty_input = st.number_input(
                "Shares (override — 0 = use ATR sizing)",
                min_value=0,
                value=0,
                step=1,
                key="stocks_console_qty",
            )

            preview_clicked = st.button("Preview Entry", key="stocks_preview_btn")
            execute_clicked = st.button(
                "Execute BUY", key="stocks_execute_btn", type="primary"
            )

        with col_result:
            st.markdown(
                ui.section_header("Preview / Result", ""),
                unsafe_allow_html=True,
            )

            # Show persisted results from previous execute
            if st.session_state.get("stocks_execute_result"):
                res = st.session_state["stocks_execute_result"]
                if res.get("ok"):
                    st.success(res["msg"])
                else:
                    st.error(res["msg"])

            if preview_clicked or execute_clicked:
                # Fetch price and compute sizing
                with st.spinner(f"Fetching {symbol_sel} data..."):
                    try:
                        import yfinance as yf

                        df_prev = yf.download(
                            symbol_sel,
                            period="3mo",
                            interval="1d",
                            auto_adjust=True,
                            progress=False,
                            threads=False,
                        )
                        if df_prev is not None and not df_prev.empty:
                            if hasattr(df_prev.columns, "levels"):
                                df_prev.columns = [
                                    c[0] if isinstance(c, tuple) else c
                                    for c in df_prev.columns
                                ]
                            # Compute ATR and sizing
                            from scheduler.stock_runner import (
                                _atr,
                                _score_symbol,
                                _compute_size,
                            )

                            score_p, signals_p = _score_symbol(df_prev)
                            current_price = float(df_prev["Close"].iloc[-1])
                            account_val = float(hdr_r.get("account_value") or 5000)

                            if account_val <= 0:
                                account_val = 5000.0

                            auto_shares, stop_p, target_p = _compute_size(
                                df_prev, account_val, current_price
                            )
                            shares_final = (
                                int(qty_input) if qty_input > 0 else auto_shares
                            )

                            preview_html = (
                                ui.metric_row("Symbol", symbol_sel)
                                + ui.metric_row(
                                    "Current Price", f"${current_price:,.2f}"
                                )
                                + ui.metric_row(
                                    "Score",
                                    f"{score_p}/100",
                                    ui.C_GREEN if score_p >= 60 else ui.C_AMBER,
                                )
                                + ui.metric_row("Shares (ATR-sized)", str(auto_shares))
                                + ui.metric_row(
                                    "Stop Price", f"${stop_p:,.2f}", ui.C_RED
                                )
                                + ui.metric_row(
                                    "Target Price", f"${target_p:,.2f}", ui.C_GREEN
                                )
                                + ui.metric_row(
                                    "Signals fired",
                                    ", ".join(signals_p.keys()) or "none",
                                )
                            )
                            st.markdown(
                                ui.detail_card(
                                    "Entry Preview",
                                    f"{symbol_sel} — ATR-based bracket sizing",
                                    preview_html,
                                ),
                                unsafe_allow_html=True,
                            )

                            if execute_clicked:
                                if shares_final <= 0:
                                    st.session_state["stocks_execute_result"] = {
                                        "ok": False,
                                        "msg": f"Cannot execute: ATR sizing returned 0 shares for {symbol_sel}",
                                    }
                                else:
                                    try:
                                        broker = _get_broker_safe()
                                        result = broker.buy_stock(
                                            symbol=symbol_sel,
                                            qty=shares_final,
                                            stop_price=stop_p,
                                            target_price=target_p,
                                            strategy="stocks_swing",
                                        )
                                        if result:
                                            st.session_state[
                                                "stocks_execute_result"
                                            ] = {
                                                "ok": True,
                                                "msg": (
                                                    f"BUY {shares_final} {symbol_sel} @ "
                                                    f"${result.get('price', current_price):,.2f} "
                                                    f"stop=${stop_p:,.2f} target=${target_p:,.2f} "
                                                    f"order={result.get('order_id', '?')}"
                                                ),
                                            }
                                        else:
                                            st.session_state[
                                                "stocks_execute_result"
                                            ] = {
                                                "ok": False,
                                                "msg": f"Broker returned None for {symbol_sel} — check TWS connection",
                                            }
                                    except Exception as ex:
                                        st.session_state["stocks_execute_result"] = {
                                            "ok": False,
                                            "msg": f"Execute error: {ex}",
                                        }
                                st.rerun()
                        else:
                            st.error(f"No data returned from yfinance for {symbol_sel}")
                    except Exception as ex:
                        st.error(f"Preview error: {ex}")

        # ── Manual sell section ───────────────────────────────────────────────
        st.markdown("---")
        st.markdown(
            ui.section_header("Manual Close", "Close an open position"),
            unsafe_allow_html=True,
        )
        open_pos = get_stock_positions()
        if open_pos:
            for pos in open_pos:
                sym_p = pos.get("symbol", "?")
                qty_p = int(pos.get("qty") or 0)
                entry_p = float(pos.get("entry") or 0)
                col_info, col_btn = st.columns([3, 1])
                with col_info:
                    st.caption(f"{sym_p} — {qty_p} shares @ ${entry_p:,.2f} entry")
                with col_btn:
                    if st.button(f"Sell {sym_p}", key=f"stocks_sell_{sym_p}"):
                        try:
                            broker = _get_broker_safe()
                            sell_result = broker.sell_stock(
                                sym_p, qty_p, reason="manual"
                            )
                            if sell_result:
                                pnl_s = float(sell_result.get("pnl") or 0)
                                st.session_state["stocks_execute_result"] = {
                                    "ok": True,
                                    "msg": (
                                        f"SOLD {qty_p} {sym_p} @ "
                                        f"${sell_result.get('exit_price', 0):,.2f} "
                                        f"P&L: {_fmt_pnl(pnl_s)}"
                                    ),
                                }
                            else:
                                st.session_state["stocks_execute_result"] = {
                                    "ok": False,
                                    "msg": f"Sell returned None for {sym_p}",
                                }
                        except Exception as ex:
                            st.session_state["stocks_execute_result"] = {
                                "ok": False,
                                "msg": f"Sell error: {ex}",
                            }
                        st.rerun()
        else:
            st.caption("No open positions to close.")

    # ── TAB 4: Stats ──────────────────────────────────────────────────────────
    with tab_stats:
        st.markdown(
            ui.info_callout(
                "All-time performance for the stock swing-trading lane. "
                "P&L is realized only — open positions not included.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        stats = get_stock_all_time_stats()
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        closes = stats.get("closes", 0)
        pf = stats.get("profit_factor", 0.0)
        total_pnl = stats.get("total_pnl", 0.0)
        win_rate = (wins / closes * 100) if closes > 0 else 0.0

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total Trades", str(closes))
        s2.metric("Win Rate", f"{win_rate:.1f}%" if closes > 0 else "–")
        s3.metric(
            "Profit Factor",
            f"{pf:.2f}" if pf != float("inf") else "∞",
        )
        s4.metric("Total P&L", _fmt_pnl(total_pnl) if total_pnl != 0 else "$0.00")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        by_symbol = stats.get("by_symbol", [])
        if by_symbol:
            import pandas as pd

            st.markdown(
                ui.section_header("P&L by Symbol", "All-time realized P&L"),
                unsafe_allow_html=True,
            )

            chart_data = {
                r["symbol"]: float(r.get("total_pnl") or 0) for r in by_symbol
            }
            st.bar_chart(chart_data)

            table_rows = []
            for r in by_symbol:
                sym_s = r.get("symbol", "")
                cl_s = int(r.get("closes") or 0)
                wins_s = int(r.get("wins") or 0)
                wr_s = f"{wins_s / cl_s * 100:.1f}%" if cl_s > 0 else "–"
                pnl_s = float(r.get("total_pnl") or 0)
                table_rows.append(
                    {
                        "Symbol": sym_s,
                        "Trades": cl_s,
                        "Win Rate": wr_s,
                        "Total P&L": _fmt_pnl(pnl_s),
                    }
                )
            st.dataframe(
                pd.DataFrame(table_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.markdown(
                ui.empty_state(
                    "No closed trades yet",
                    "Stats appear once the first stock position is closed.",
                ),
                unsafe_allow_html=True,
            )

        # Recent trade log
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.markdown(
            ui.section_header("Recent Trade Log", "All buys and sells"),
            unsafe_allow_html=True,
        )
        recent = get_stock_recent_trades(limit=30)
        if recent:
            import pandas as pd

            tlog = []
            for t in recent:
                pnl_t = float(t.get("pnl_usd") or 0)
                tlog.append(
                    {
                        "Time": str(t.get("ts", ""))[:16],
                        "Symbol": t.get("symbol", ""),
                        "Action": t.get("action", ""),
                        "Qty": t.get("qty", 0),
                        "Price": f"${float(t.get('price') or 0):,.2f}",
                        "P&L": _fmt_pnl(pnl_t) if pnl_t != 0 else "–",
                        "Order ID": str(t.get("order_id", ""))[:12],
                    }
                )
            st.dataframe(
                pd.DataFrame(tlog),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No trade history yet.")


# ── Broker accessor (dashboard-safe) ─────────────────────────────────────────


def _get_broker_safe():
    """Lazily import and return the stock broker instance."""
    from execution.ibkr_stock_broker import IBKRStockBroker

    # Use module-level singleton from stock_runner if already instantiated,
    # otherwise create a fresh instance for dashboard use.
    try:
        import scheduler.stock_runner as _sr

        return _sr._get_broker()
    except Exception:
        return IBKRStockBroker()
