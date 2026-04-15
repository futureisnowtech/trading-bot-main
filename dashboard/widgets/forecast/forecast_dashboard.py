"""
dashboard/widgets/forecast/forecast_dashboard.py — FORECAST TRADING tab widget.

Question: How is our ForecastEx event-contract strategy performing?

Sections:
  1. Readiness status (READY / BLOCKED / ACTION NEEDED)
  2. System health (tables, quotes, bars, TWS connection)
  3. Active positions
  4. Deployed capital
  5. Recent trades
  6. EV / calibration summary
  7. Decision quality / failure modes
  8. Active markets

Refresh: 30s fragment (light data, no heavy queries).
Asset class: FORECAST EVENT CONTRACTS
"""

import os
import sys

_DASH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROOT = os.path.dirname(_DASH_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)

import streamlit as st


@st.fragment(run_every=30)
def render_forecast_trading():
    """
    Widget: ForecastEx event-contract dashboard.
    Tab: FORECAST TRADING
    Refresh: 30s
    Asset class: FORECAST EVENT CONTRACTS
    """
    # ── imports ────────────────────────────────────────────────────────────────
    try:
        from dashboard.data.forecast import (
            get_active_markets_summary,
            get_forecast_ev_summary,
            get_forecast_health,
            get_forecast_pnl_summary,
            get_forecast_positions,
            get_forecast_readiness,
            get_forecast_trades,
        )
    except ImportError:
        try:
            sys.path.insert(0, _ROOT)
            from dashboard.data.forecast import (
                get_active_markets_summary,
                get_forecast_ev_summary,
                get_forecast_health,
                get_forecast_pnl_summary,
                get_forecast_positions,
                get_forecast_readiness,
                get_forecast_trades,
            )
        except Exception as e:
            st.error(f"Forecast data layer unavailable: {e}")
            return

    # ── data fetch ─────────────────────────────────────────────────────────────
    health = get_forecast_health()
    readiness = get_forecast_readiness()
    positions = get_forecast_positions()
    pnl = get_forecast_pnl_summary()
    ev_summary = get_forecast_ev_summary()
    trades = get_forecast_trades(limit=20)
    markets = get_active_markets_summary()

    # ── asset badge ────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="badge-forecast">FORECAST EVENT CONTRACTS · ForecastEx (IBKR)</div>',
        unsafe_allow_html=True,
    )

    # ── 1. READINESS STATUS ────────────────────────────────────────────────────
    status_color = {
        "READY": "#4ade80",
        "ACTION_NEEDED": "#facc15",
        "BLOCKED": "#f87171",
    }.get(readiness["status"], "#94a3b8")

    status_icon = {
        "READY": "✅",
        "ACTION_NEEDED": "⚠️",
        "BLOCKED": "❌",
    }.get(readiness["status"], "❓")

    st.markdown(
        f"""<div style="background:rgba(0,0,0,0.25); border-left:4px solid {status_color};
             padding:10px 16px; border-radius:6px; margin-bottom:14px;">
        <strong style="color:{status_color}; font-size:1.05em;">
            {status_icon} LANE STATUS: {readiness["status"]}
        </strong></div>""",
        unsafe_allow_html=True,
    )

    with st.expander("Readiness checks", expanded=(readiness["status"] != "READY")):
        for chk in readiness.get("checks", []):
            icon = (
                "✅"
                if chk["status"] == "PASS"
                else ("⚠️" if chk["status"] == "ACTION_NEEDED" else "❌")
            )
            st.markdown(f"{icon} **{chk['name']}** — {chk['detail']}")

    st.divider()

    # ── 2. SYSTEM HEALTH METRICS ───────────────────────────────────────────────
    st.markdown('<p class="panel-title">System Health</p>', unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)

    lag = health.get("quote_lag_minutes")
    lag_str = f"{lag:.0f}m" if lag is not None else "—"
    lag_ok = lag is not None and lag < 5.0

    c1.metric("Markets", health.get("active_markets", 0))
    c2.metric("Contracts", health.get("active_contracts", 0))
    c3.metric(
        "Quote lag",
        lag_str,
        delta="LIVE" if lag_ok else "STALE",
        delta_color="normal" if lag_ok else "inverse",
    )
    c4.metric("5m bars", health.get("bars_5m_count", 0))
    tables_status = "✅ OK" if health.get("tables_exist") else "❌ MISSING"
    c5.metric("DB tables", tables_status)

    st.divider()

    # ── 3. ACTIVE POSITIONS ────────────────────────────────────────────────────
    st.markdown('<p class="panel-title">Active Positions</p>', unsafe_allow_html=True)

    if positions:
        for pos in positions:
            notes = pos.get("notes", "") or ""
            side = "YES" if "side=YES" in notes or "right=C" in notes else "NO"
            side_color = "#4ade80" if side == "YES" else "#f87171"
            st.markdown(
                f"""<div style="background:rgba(255,255,255,0.04); padding:8px 12px;
                     border-radius:6px; margin-bottom:6px; border-left:3px solid {side_color};">
                <strong>{pos.get("symbol", "?")}</strong>
                <span style="color:{side_color}; margin-left:8px;">{side}</span>
                <span style="margin-left:12px; color:#94a3b8;">×{pos.get("qty", 0)}</span>
                <span style="margin-left:12px; color:#e2e8f0;">
                    entry {pos.get("price", 0):.4f}
                </span>
                <span style="margin-left:12px; font-size:0.8em; color:#64748b;">
                    {(pos.get("ts", "") or "")[:16]}
                </span>
                </div>""",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No open positions.")

    # ── 4. DEPLOYED CAPITAL ────────────────────────────────────────────────────
    if positions:
        # Approximate deployed: sum of cost basis (price × qty × 100 shares/contract)
        from config import ACCOUNT_SIZE

        bankroll = float(ACCOUNT_SIZE) if ACCOUNT_SIZE else 100.0
        deployed = sum(
            (p.get("price") or 0) * (p.get("qty") or 0) * 100 for p in positions
        )
        deployed_pct = deployed / max(bankroll, 1.0) * 100
        st.progress(
            min(deployed_pct / 35.0, 1.0),
            text=f"Deployed: ${deployed:.0f} ({deployed_pct:.1f}% of ${bankroll:.0f}) — cap 35%",
        )

    st.divider()

    # ── 5. P&L SUMMARY ────────────────────────────────────────────────────────
    st.markdown('<p class="panel-title">Performance</p>', unsafe_allow_html=True)
    p1, p2, p3, p4 = st.columns(4)
    pnl_color = "#4ade80" if pnl.get("total_pnl", 0) >= 0 else "#f87171"
    p1.metric("Trades closed", pnl.get("total_trades", 0))
    wr = pnl.get("win_rate", 0.0)
    p2.metric("Win rate", f"{wr:.0%}" if wr else "—")
    tp = pnl.get("total_pnl", 0.0)
    p3.metric(
        "Total P&L",
        f"${tp:+.2f}" if tp != 0 else "$0.00",
        delta_color="normal" if tp >= 0 else "inverse",
    )
    p4.metric("Today P&L", f"${pnl.get('today_pnl', 0):+.2f}")

    # ── 6. EV / CALIBRATION ───────────────────────────────────────────────────
    n_res = ev_summary.get("resolutions_tracked", 0)
    if n_res > 0:
        st.markdown('<p class="panel-title">Calibration</p>', unsafe_allow_html=True)
        st.caption(
            f"{n_res} resolved contracts tracked. Calibration analysis available once ≥10 resolutions."
        )

    st.divider()

    # ── 7. RECENT TRADES ──────────────────────────────────────────────────────
    st.markdown('<p class="panel-title">Recent Trades</p>', unsafe_allow_html=True)
    if trades:
        import pandas as pd

        df = pd.DataFrame(trades)[
            ["ts", "symbol", "action", "qty", "price", "pnl_usd"]
        ].rename(
            columns={
                "ts": "Time",
                "symbol": "Symbol",
                "action": "Action",
                "qty": "Qty",
                "price": "Price",
                "pnl_usd": "P&L",
            }
        )
        df["Time"] = df["Time"].astype(str).str[:16]
        df["Price"] = df["Price"].apply(lambda x: f"{x:.4f}" if x else "—")
        df["P&L"] = df["P&L"].apply(lambda x: f"${x:+.2f}" if x and x != 0 else "—")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No trades recorded yet.")

    st.divider()

    # ── 8. ACTIVE MARKETS ─────────────────────────────────────────────────────
    st.markdown(
        '<p class="panel-title">Active Economic Markets</p>', unsafe_allow_html=True
    )
    if markets:
        for m in markets[:10]:
            lag_m = "—"
            if m.get("last_quote_ts"):
                try:
                    from datetime import datetime, timezone

                    lq = datetime.fromisoformat(m["last_quote_ts"])
                    lag_m = f"{(datetime.now(timezone.utc) - lq).total_seconds() / 60:.0f}m ago"
                except Exception:
                    pass
            st.markdown(
                f"**{m.get('market_symbol', '?')}** — {m.get('market_name', '')[:60]}  "
                f"· {m.get('contract_count', 0)} contracts  · quote {lag_m}",
            )
    else:
        st.caption("No markets discovered yet. Run discovery to populate.")
