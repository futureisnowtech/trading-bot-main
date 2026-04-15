"""
Widget: MES Futures Dashboard
Question: How is our S&P 500 futures strategy performing today?
Tab: S&P 500 FUTURES (MES)
Refresh: 10s
Asset class: S&P FUTURES
"""

import streamlit as st
from datetime import datetime

from tooltips import TIPS
from formatters import _fmt_pnl, _time_ago, _asset_badge
from data.futures import (
    get_mes_state,
    get_mes_trades_today,
    get_mes_daily_pnl,
    get_mes_all_time_stats,
)


@st.fragment(run_every=10)
def render_futures():
    import pandas as pd

    st.markdown(_asset_badge("futures"), unsafe_allow_html=True)

    try:
        import pytz

        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        h, m = now_et.hour, now_et.minute
        is_open = now_et.weekday() < 5 and (
            (h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 45)
        )
        pre_open = now_et.weekday() < 5 and h == 9 and m < 30
        time_str = now_et.strftime("%H:%M ET")
        mkt_status = "OPEN" if is_open else ("PRE-OPEN" if pre_open else "CLOSED")
    except Exception:
        is_open, mkt_status, time_str = False, "UNKNOWN", "--:--"

    mes_state = get_mes_state()
    daily_pnl = get_mes_daily_pnl()
    all_stats = get_mes_all_time_stats()
    trades_today = get_mes_trades_today()

    price = mes_state.get("price")
    or_high = mes_state.get("or_high")
    or_low = mes_state.get("or_low")
    or_locked = mes_state.get("or_locked", False)
    has_pos = mes_state.get("has_pos", False)
    state_time = mes_state.get("time_et", "--")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Market", mkt_status, delta=time_str)
    c2.metric("MES Price", f"{price:.2f}" if price else "–")
    c3.metric("Today P&L", _fmt_pnl(daily_pnl), help=TIPS.get("mes_pnl"))
    c4.metric("Position", "ACTIVE" if has_pos else "FLAT")
    c5.metric(
        "All-Time W/L",
        f"{all_stats['wins']}W / {all_stats['closes'] - all_stats['wins']}L",
    )
    pf = all_stats["profit_factor"]
    c6.metric(
        "Profit Factor",
        f"{pf:.2f}" if pf != float("inf") else "∞",
        help=TIPS.get("mes_profit_factor"),
    )

    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("Opening Range (9:30–10:00 ET)")
        st.caption(
            "The first 30 minutes of trading set the range. A break above triggers a LONG; below triggers a SHORT."
        )
        if or_locked and or_high and or_low:
            or_range = or_high - or_low
            long_entry = round(or_high + 0.25, 2)
            short_entry = round(or_low - 0.25, 2)
            r1, r2, r3 = st.columns(3)
            r1.metric("OR High", f"{or_high:.2f}", help=TIPS.get("or_high"))
            r2.metric("OR Low", f"{or_low:.2f}", help=TIPS.get("or_low"))
            r3.metric("Range (pts)", f"{or_range:.2f}", help=TIPS.get("or_range"))
            st.caption(
                f"Long trigger: ≥ {long_entry}  |  Short trigger: ≤ {short_entry}"
            )
            st.caption(f"Last update: {state_time}")
        elif is_open and not or_locked:
            st.info("Building opening range… (9:30–10:00 ET)")
        elif not is_open:
            st.info("Market closed. Opening range resets at 9:30 ET.")
        else:
            st.info("Waiting for runner state (FUTURES_ENABLED must be True in .env)")

    with col_r:
        st.subheader("Strategy Playbook")
        st.caption("**Strategy 1 — Opening Range Breakout**")
        for k, v in [
            (
                "Trigger",
                "Price breaks above OR high (+0.25) → LONG / below OR low (−0.25) → SHORT",
            ),
            ("Stop", "Opposite end of OR ± 0.25 buffer"),
            ("Target", "2× stop distance, min 4 pts ($20/contract)"),
            ("Window", "10:00–15:45 ET; hard EOD close 15:45"),
        ]:
            st.text(f"  {k + ':':<10} {v}")
        st.divider()
        st.caption("**Strategy 2 — VWAP Mean Reversion**")
        for k, v in [
            (
                "Trigger",
                "Price >2 ATR from VWAP AND RSI >68 → SHORT / <2 ATR AND RSI <32 → LONG",
            ),
            ("Stop", "1.5 ATR past entry"),
            ("Target", "VWAP"),
            ("Window", "10:00–14:30 ET"),
        ]:
            st.text(f"  {k + ':':<10} {v}")

    st.divider()

    st.subheader(f"Today's MES Trades ({len(trades_today)})")
    if trades_today:
        rows = []
        for t in trades_today:
            pnl = t.get("pnl_usd") or 0
            rows.append(
                {
                    "Time": _time_ago(t.get("ts", "")),
                    "Action": t.get("action", ""),
                    "Qty": t.get("qty", ""),
                    "Price": t.get("price", ""),
                    "P&L": _fmt_pnl(pnl) if pnl else "–",
                    "Notes": (t.get("notes") or "")[:80],
                    "Result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "OPEN"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(
            "No MES trades today."
            if is_open
            else "No MES trades today — market closed."
        )

    st.divider()

    with st.expander("Futures configuration & risk rules"):
        try:
            from config import FUTURES_ENABLED, FUTURES_NUM_CONTRACTS, ACCOUNT_SIZE
            from config.venue_specs import (
                MES_EXPIRY,
                MES_POINT_VALUE,
                MES_TICK_SIZE,
                MES_TICK_VALUE,
                MES_EOD_CLOSE_TIME,
                MES_MAX_DAILY_LOSS_USD,
                IBKR_PORT,
            )

            st.text(f"  FUTURES_ENABLED:       {FUTURES_ENABLED}")
            st.text(f"  FUTURES_NUM_CONTRACTS: {FUTURES_NUM_CONTRACTS}")
            st.text(f"  Account size:          ${float(ACCOUNT_SIZE):,.0f}")
            st.text("  Contract:    MES (Micro E-mini S&P 500) — CME")
            st.text(f"  Expiry:      {MES_EXPIRY}  (update .env on quarterly roll)")
            st.text(f"  Point value: ${MES_POINT_VALUE:.2f} / full point")
            st.text(
                f"  Tick size:   {MES_TICK_SIZE} pts = ${MES_TICK_VALUE:.2f} / tick"
            )
            st.text("  Commission:  ~$0.47/side = $0.94 round-trip (IBKR)")
            st.text(f"  Connection:  IBKR TWS port {IBKR_PORT}")
            st.text(
                f"  Daily limit: ${MES_MAX_DAILY_LOSS_USD:.0f} — no new entries after this"
            )
            st.text(f"  Hard EOD:    {MES_EOD_CLOSE_TIME} ET — all positions closed")
        except Exception as e:
            st.error(f"config: {e}")
