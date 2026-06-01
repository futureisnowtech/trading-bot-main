"""
dashboard/widgets/forecast/forecast_dashboard.py — FORECAST TRADING tab widget.

Question: How is our ForecastEx event-contract strategy performing?

Sections:
  1. Lane status banner (from runtime truth)
  2. Operational funnel (lane → broker → underliers → contracts → quotes → bars → strategy)
  3. System health metrics
  4. Active positions
  5. Deployed capital
  6. Performance summary (zero-state aware)
  7. EV / calibration
  8. Recent trades
  9. Active economic markets / enrollment status

Refresh: 30s fragment.
Asset class: FORECAST EVENT CONTRACTS
"""

import os
import sys
from datetime import datetime, timezone

_DASH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROOT = os.path.dirname(_DASH_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)

import streamlit as st


def _hb_age_str(hb_ts: str | None) -> str:
    """Return human-readable heartbeat age from an ISO timestamp string."""
    if not hb_ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(hb_ts.replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 120:
            return f"{age:.0f}s ago"
        if age < 3600:
            return f"{age / 60:.0f}m ago"
        return f"{age / 3600:.1f}h ago"
    except Exception:
        return str(hb_ts)[:16]


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

    lane_state = readiness.get("lane_state", "LANE_NOT_STARTED")
    lane_hb = readiness.get("lane_heartbeat_at") or health.get("lane_heartbeat_at")
    underliers = readiness.get("underliers_visible", 0)
    unavailable = readiness.get("contracts_unavailable_count", 0)
    contracts = health.get("active_contracts", 0)
    lag = health.get("quote_lag_minutes")
    bars = health.get("bars_5m_count", 0)
    lane_alive = health.get("lane_started", False)

    # ── asset badge ────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="badge-forecast">FORECAST EVENT CONTRACTS · ForecastEx (IBKR)</div>',
        unsafe_allow_html=True,
    )

    # ── 1. LANE STATUS BANNER ──────────────────────────────────────────────────
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

    # Human-readable headline for the current state
    _state_headlines = {
        "LANE_NOT_STARTED": "Forecast lane not running",
        "BROKER_DISCONNECTED": "Broker disconnected",
        "NO_UNDERLIERS": "No underliers discovered yet",
        "UNDERLIERS_ONLY": "Underliers visible — awaiting OPT contracts",
        "NO_TRADABLE_CONTRACTS_RIGHT_NOW": "Lane alive, no tradable contracts available",
        "NO_QUOTES": "Contracts exist but no fresh quotes",
        "QUOTES_NO_BARS": "Quotes flowing — building bars",
        "OPERATIONAL": "OPERATIONAL",
    }
    headline = _state_headlines.get(lane_state, lane_state)

    hb_str = f"  ·  heartbeat {_hb_age_str(lane_hb)}" if lane_alive and lane_hb else ""
    st.markdown(
        f"""<div style="background:rgba(0,0,0,0.25); border-left:4px solid {status_color};
             padding:10px 16px; border-radius:6px; margin-bottom:14px;">
        <strong style="color:{status_color}; font-size:1.05em;">
            {status_icon} {headline}
        </strong>
        <span style="color:#64748b; font-size:0.82em;">{hb_str}</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── 2. OPERATIONAL FUNNEL ──────────────────────────────────────────────────
    st.markdown('<p class="panel-title">Operational Funnel</p>', unsafe_allow_html=True)

    def _funnel_row(label: str, ok: bool, detail: str):
        dot = (
            '<span style="color:#4ade80">●</span>'
            if ok
            else '<span style="color:#64748b">○</span>'
        )
        color = "#e2e8f0" if ok else "#64748b"
        st.markdown(
            f'<div style="display:flex; gap:8px; margin:3px 0; font-size:0.83em">'
            f"{dot} "
            f'<span style="color:#94a3b8; min-width:140px">{label}</span>'
            f'<span style="color:{color}">{detail}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )

    _funnel_row(
        "Lane alive", lane_alive, "active — running" if lane_alive else "not started"
    )
    _funnel_row(
        "Broker connected",
        lane_alive,  # if lane is alive, broker is at least reachable
        "IBKR connected" if lane_alive else "not connected",
    )
    _funnel_row(
        "Underliers discovered",
        underliers > 0,
        f"{underliers} IND underlier(s) visible" if underliers > 0 else "none found",
    )
    _funnel_row(
        "Tradable contracts",
        contracts > 0,
        f"{contracts} active OPT contracts"
        if contracts > 0
        else (
            "none available — likely no active event period or enrollment limitation"
            if underliers > 0
            else "none"
        ),
    )
    _funnel_row(
        "Quotes flowing",
        lag is not None and lag < 10.0,
        f"last quote {lag:.0f}m ago" if lag is not None else "no quotes",
    )
    _funnel_row(
        "Bars built",
        bars > 0,
        f"{bars} 5m bars" if bars > 0 else "pending — need quotes first",
    )
    _funnel_row(
        "Strategy eligible",
        contracts > 0 and bars > 0,
        "ready to evaluate entries"
        if (contracts > 0 and bars > 0)
        else "waiting for contracts + bars",
    )
    _funnel_row(
        "Positions open",
        bool(positions),
        f"{len(positions)} open" if positions else "none",
    )
    _funnel_row(
        "Trades / resolutions",
        pnl.get("total_trades", 0) > 0,
        f"{pnl.get('total_trades', 0)} closed"
        if pnl.get("total_trades", 0) > 0
        else "none yet",
    )

    st.divider()

    # ── 3. SYSTEM HEALTH METRICS ───────────────────────────────────────────────
    st.markdown('<p class="panel-title">System Health</p>', unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)

    lag_str = f"{lag:.0f}m" if lag is not None else "—"
    lag_ok = lag is not None and lag < 5.0

    c1.metric("Underliers", underliers)
    c2.metric("Contracts", contracts)
    c3.metric(
        "Quote lag",
        lag_str,
        delta="LIVE" if lag_ok else ("NO QUOTES" if lag is None else "STALE"),
        delta_color="normal" if lag_ok else "inverse",
    )
    c4.metric("5m bars", bars)
    tables_status = "✅ OK" if health.get("tables_exist") else "❌ MISSING"
    c5.metric("DB tables", tables_status)

    # Enrollment note (shown when underliers visible but no contracts)
    if underliers > 0 and contracts == 0:
        st.info(
            f"**{underliers} underlier(s) visible, 0 tradable OPT contracts.** "
            "This means the IBKR IND underlier index is reachable but event-contract "
            "(OPT) trading is not yet available — either no active event period right now, "
            "or the account needs ForecastEx enrollment via the IBKR portal. "
            "This is a brokerage-side state, not a code failure.",
            icon="ℹ️",
        )

    with st.expander("Readiness checks", expanded=False):
        for chk in readiness.get("checks", []):
            icon = (
                "✅"
                if chk["status"] == "PASS"
                else ("⚠️" if chk["status"] == "ACTION_NEEDED" else "❌")
            )
            st.markdown(f"{icon} **{chk['name']}** — {chk['detail']}")

    st.divider()

    # ── 4. ACTIVE POSITIONS ────────────────────────────────────────────────────
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

    # ── 4b. SOVEREIGN INTELLIGENCE ─────────────────────────────────────────────
    if positions:
        st.markdown('<p class="panel-title">Sovereign Intelligence</p>', unsafe_allow_html=True)
        from dashboard.data.forecast import get_sovereign_weather_insights
        
        for pos in positions:
            ticker = pos.get("symbol", "")
            if "KXHIGH" in ticker or "KXLOW" in ticker:
                insights = get_sovereign_weather_insights(ticker)
                if insights:
                    with st.expander(f"Insights: {ticker}", expanded=True):
                        c1, c2, c3 = st.columns(3)
                        
                        # Consensus
                        p_gfs = insights.get("prob_gfs")
                        p_ec = insights.get("prob_ecmwf")
                        c1.markdown("**Consensus**")
                        c1.markdown(f"GFS: `{p_gfs:.1%}`" if p_gfs is not None else "GFS: `—`")
                        c1.markdown(f"ECMWF: `{p_ec:.1%}`" if p_ec is not None else "ECMWF: `—`")
                        
                        # Ground Truth (METAR)
                        temp = insights.get("metar_temp")
                        thresh = insights.get("threshold")
                        c2.markdown("**Ground Truth**")
                        if temp is not None and thresh is not None:
                            diff = temp - thresh
                            color = "red" if abs(diff) < 1.0 else "green"
                            c2.markdown(f"METAR: `{temp:.1f}F`")
                            c2.markdown(f"Diff: <span style='color:{color}'>{diff:+.1f}F</span>", unsafe_allow_html=True)
                        else:
                            c2.markdown("METAR: `pending`")
                        
                        # Intraday Risk (HRRR)
                        hrrr = insights.get("hrrr_high")
                        trend = insights.get("hrrr_trend")
                        c3.markdown("**Intraday Risk**")
                        c3.markdown(f"HRRR: `{hrrr:.1f}F`" if hrrr else "HRRR: `pending`")
                        c3.markdown(f"Trend: `{trend}`" if trend else "Trend: `—`")

    # ── 5. DEPLOYED CAPITAL ────────────────────────────────────────────────────
    if positions:
        try:
            # Use config ACCOUNT_SIZE for ForecastEx bankroll (~$100 live account)
            from config import ACCOUNT_SIZE

            bankroll = float(ACCOUNT_SIZE) if ACCOUNT_SIZE else 100.0
        except Exception:
            bankroll = 100.0
        deployed = sum(
            (p.get("price") or 0) * (p.get("qty") or 0) * 100 for p in positions
        )
        deployed_pct = deployed / max(bankroll, 1.0) * 100
        st.progress(
            min(deployed_pct / 35.0, 1.0),
            text=f"Deployed: ${deployed:.0f} ({deployed_pct:.1f}% of ${bankroll:.0f} ACCOUNT_SIZE) — cap 35%",
        )

    st.divider()

    # ── 6. PERFORMANCE SUMMARY ────────────────────────────────────────────────
    st.markdown('<p class="panel-title">Performance</p>', unsafe_allow_html=True)

    total_trades = pnl.get("total_trades", 0)
    if total_trades == 0:
        st.caption(
            "No forecast trades have been closed yet. Performance metrics will appear "
            "once the first ForecastEx position is opened and resolved."
        )
    else:
        p1, p2, p3, p4 = st.columns(4)
        wr = pnl.get("win_rate", 0.0)
        tp = pnl.get("total_pnl", 0.0)
        p1.metric("Trades closed", total_trades)
        p2.metric("Win rate", f"{wr:.0%}" if wr else "—")
        p3.metric(
            "Total P&L",
            f"${tp:+.2f}" if tp != 0 else "$0.00",
            delta_color="normal" if tp >= 0 else "inverse",
        )
        p4.metric("Today P&L", f"${pnl.get('today_pnl', 0):+.2f}")

    # ── 7. EV / CALIBRATION ───────────────────────────────────────────────────
    n_res = ev_summary.get("resolutions_tracked", 0)
    if n_res > 0:
        st.markdown('<p class="panel-title">Calibration</p>', unsafe_allow_html=True)
        st.caption(
            f"{n_res} resolved contracts tracked. Calibration analysis available once ≥10 resolutions."
        )
    elif total_trades == 0:
        # Zero-state: explicit explanation
        st.caption(
            "EV calibration pending — will populate after forecast trades close and "
            "contracts resolve."
        )

    st.divider()

    # ── 8. RECENT TRADES ──────────────────────────────────────────────────────
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
        st.caption("No forecast trades recorded yet.")

    st.divider()

    # ── 9. ACTIVE ECONOMIC MARKETS ────────────────────────────────────────────
    st.markdown(
        '<p class="panel-title">Active Economic Markets</p>', unsafe_allow_html=True
    )
    if markets:
        for m in markets[:10]:
            lag_m = "—"
            if m.get("last_quote_ts"):
                try:
                    lq = datetime.fromisoformat(m["last_quote_ts"])
                    if not lq.tzinfo:
                        lq = lq.replace(tzinfo=timezone.utc)
                    lag_m = f"{(datetime.now(timezone.utc) - lq).total_seconds() / 60:.0f}m ago"
                except Exception:
                    pass
            n_contracts = m.get("contract_count", 0)
            contracts_str = (
                f"{n_contracts} contracts"
                if n_contracts > 0
                else "IND visible, no OPT contracts"
            )
            st.markdown(
                f"**{m.get('market_symbol', '?')}** — {m.get('market_name', '')[:55]}  "
                f"· {contracts_str}  · quote {lag_m}",
            )
    elif underliers == 0:
        st.caption("No markets discovered yet. Run discovery to populate.")
    else:
        st.caption("Market data loading…")

    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")
