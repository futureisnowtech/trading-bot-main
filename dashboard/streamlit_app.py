"""Sovereign Kalshi cockpit Streamlit app."""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_ROOT)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.cockpit_data import get_cockpit_payload

st.set_page_config(
    page_title="Sovereign Kalshi Cockpit",
    page_icon="🌪",
    layout="wide",
    initial_sidebar_state="expanded",
)

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --bg-1: #050816;
  --bg-2: #0b132c;
  --panel: rgba(10, 18, 38, 0.82);
  --panel-2: rgba(8, 13, 28, 0.90);
  --line: rgba(74, 242, 214, 0.22);
  --cyan: #4af2d6;
  --mint: #8cffb2;
  --amber: #ffd166;
  --red: #ff6b88;
  --blue: #6fd3ff;
  --text: #eaf6ff;
  --muted: #91a3c2;
}

.stApp {
  background:
    radial-gradient(circle at 15% 18%, rgba(74, 242, 214, 0.16), transparent 24%),
    radial-gradient(circle at 82% 12%, rgba(111, 211, 255, 0.14), transparent 22%),
    radial-gradient(circle at 78% 78%, rgba(140, 255, 178, 0.11), transparent 20%),
    linear-gradient(140deg, #040610 0%, #08111f 42%, #040914 100%);
  color: var(--text);
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(6, 11, 24, 0.98), rgba(4, 8, 18, 0.96));
  border-right: 1px solid rgba(74, 242, 214, 0.10);
}

body, .stMarkdown, .stDataFrame, .stMetric {
  color: var(--text);
}

h1, h2, h3 {
  font-family: "Orbitron", sans-serif;
  letter-spacing: 0.06em;
}

p, li, div, span, label {
  font-family: "IBM Plex Mono", monospace;
}

.hero {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(74, 242, 214, 0.22);
  background:
    linear-gradient(140deg, rgba(10, 17, 34, 0.94), rgba(5, 8, 20, 0.96)),
    linear-gradient(90deg, rgba(74, 242, 214, 0.18), rgba(111, 211, 255, 0.08));
  border-radius: 24px;
  padding: 1.4rem 1.5rem 1.3rem 1.5rem;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255,255,255,0.03);
}

.hero:before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(transparent 0%, rgba(255,255,255,0.03) 50%, transparent 100%);
  transform: translateY(-40%);
  pointer-events: none;
}

.eyebrow {
  color: var(--cyan);
  text-transform: uppercase;
  letter-spacing: 0.24em;
  font-size: 0.78rem;
}

.hero-title {
  font-family: "Orbitron", sans-serif;
  font-size: 2.4rem;
  font-weight: 800;
  margin: 0.28rem 0 0.4rem 0;
}

.hero-sub {
  color: var(--muted);
  max-width: 58rem;
  line-height: 1.5;
}

.chip-row {
  margin-top: 0.8rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.chip {
  border: 1px solid rgba(74, 242, 214, 0.24);
  color: var(--text);
  background: rgba(15, 27, 50, 0.72);
  border-radius: 999px;
  padding: 0.35rem 0.75rem;
  font-size: 0.78rem;
}

.section-title {
  margin-top: 0.3rem;
  margin-bottom: 0.65rem;
  color: var(--blue);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.92rem;
}

.panel {
  background: linear-gradient(180deg, rgba(11, 19, 41, 0.92), rgba(6, 10, 22, 0.94));
  border: 1px solid rgba(74, 242, 214, 0.14);
  border-radius: 22px;
  padding: 1rem 1rem 0.9rem 1rem;
  min-height: 100%;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.85rem;
}

.metric-card {
  border: 1px solid rgba(74, 242, 214, 0.14);
  background: linear-gradient(180deg, rgba(10, 18, 38, 0.90), rgba(5, 10, 22, 0.94));
  border-radius: 20px;
  padding: 0.9rem 1rem;
}

.metric-label {
  font-size: 0.78rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.metric-value {
  font-size: 1.65rem;
  margin-top: 0.3rem;
  font-family: "Orbitron", sans-serif;
}

.metric-sub {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 0.2rem;
}

.tone-good { color: var(--mint); }
.tone-warn { color: var(--amber); }
.tone-bad { color: var(--red); }
.tone-cyan { color: var(--cyan); }
.tone-blue { color: var(--blue); }

.banner {
  border-left: 4px solid var(--red);
  background: rgba(255, 107, 136, 0.09);
  border-radius: 14px;
  padding: 0.95rem 1rem;
  margin-top: 1rem;
}

.feed-card {
  border: 1px solid rgba(74, 242, 214, 0.10);
  border-radius: 18px;
  padding: 0.85rem 0.95rem;
  margin-bottom: 0.7rem;
  background: rgba(10, 18, 38, 0.70);
}

.feed-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}

.feed-title {
  color: var(--text);
  font-weight: 600;
}

.feed-meta {
  color: var(--muted);
  font-size: 0.76rem;
}

.formula {
  border: 1px solid rgba(111, 211, 255, 0.14);
  border-radius: 16px;
  padding: 0.8rem 0.9rem;
  background: rgba(8, 14, 31, 0.84);
  margin-bottom: 0.65rem;
  color: var(--text);
}

@media (max-width: 1100px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
"""


def _fmt_money(value: float | None) -> str:
    value = float(value or 0.0)
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    value = float(value or 0.0)
    return f"{value:.1%}"


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(value)


@st.cache_data(ttl=45, show_spinner=False)
def _load_payload(live_sync: bool) -> dict:
    return get_cockpit_payload(live_sync=live_sync)


def _metric_card(label: str, value: str, subtitle: str, tone: str = "tone-cyan") -> str:
    return f"""
    <div class="metric-card">
      <div class="metric-label">{html.escape(label)}</div>
      <div class="metric-value {tone}">{html.escape(value)}</div>
      <div class="metric-sub">{html.escape(subtitle)}</div>
    </div>
    """


def _feed_card(title: str, meta: str, body: str, tone: str = "tone-cyan") -> str:
    return f"""
    <div class="feed-card">
      <div class="feed-top">
        <div class="feed-title {tone}">{html.escape(title)}</div>
        <div class="feed-meta">{html.escape(meta)}</div>
      </div>
      <div class="feed-meta" style="margin-top:0.55rem; white-space:pre-wrap;">{html.escape(body)}</div>
    </div>
    """


st.markdown(_CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## Cockpit Controls")
    st.caption("Broker truth is cached for 45 seconds to keep the cockpit sharp without burning Kalshi calls.")
    live_sync = st.toggle("Broker Sync", value=True, help="When on, the cockpit pulls live balance, positions, and mark data from Kalshi.")
    if st.button("Refresh Now", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.markdown("### Display")
    st.caption("Read-only cockpit. No order writes, no state mutations.")

payload = _load_payload(live_sync)
truth = payload["truth"]
lane = truth.get("forecast_lane") or {}
regime = payload["regime"]
deploy = payload["deploy"]
positions_live = payload["positions_live"]
positions_db_only = payload["positions_db_only"]
recent_trades = payload["recent_trades"]
recent_events = payload["recent_events"]
notifications = payload["notifications"]
recent_vetoes = payload["recent_vetoes"]
storage = payload["storage"]
market_counts = payload["market_counts"]
snapshot = payload.get("snapshot") or {}

balance = float(truth.get("balance_usd") or 0.0)
drift = truth.get("position_drift") or {}
positions_count = len(positions_live)
realized_curve = payload["realized_pnl_curve"]
realized_pnl = realized_curve[-1]["cumulative_pnl"] if realized_curve else 0.0
equity_snapshot = float(snapshot.get("equity") or balance or 0.0)
hub_cap_now = max(20.0, balance * 0.20)

st.markdown(
    f"""
    <div class="hero">
      <div class="eyebrow">Sovereign Weather Engine</div>
      <div class="hero-title">Kalshi Cockpit</div>
      <div class="hero-sub">
        Broker-first command HUD for live weather trading. This board fuses Kalshi balance and positions,
        local ledger drift, recent veto reasons, notifications, regime math, and deployment provenance into
        one read-only cockpit.
      </div>
      <div class="chip-row">
        <div class="chip">Version {html.escape(str(regime['version']))}</div>
        <div class="chip">Lane {html.escape(str(lane.get('readiness_state') or 'UNKNOWN'))}</div>
        <div class="chip">Health {html.escape(str(lane.get('health') or 'UNKNOWN'))}</div>
        <div class="chip">Broker {'CONNECTED' if truth.get('broker_connected') else 'DISCONNECTED'}</div>
        <div class="chip">Model {html.escape(str(regime['reasoning_model']))}</div>
        <div class="chip">Deploy {html.escape(str(deploy.get('sha') or 'local'))[:7]}</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if drift.get("has_drift"):
    st.markdown(
        """
        <div class="banner">
          <strong>Truth drift detected.</strong> Broker reality and SQLite do not fully agree right now.
          The cockpit is showing both layers explicitly so you can see whether the issue is a stale local
          ledger, a manual broker action, or a runtime reconciliation lag.
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("### Live Core")
metric_html = f"""
<div class="metric-grid">
  {_metric_card("Live Cash", _fmt_money(balance), "Broker-reported balance", "tone-mint")}
  {_metric_card("Open Positions", str(positions_count), "Broker-truth live positions", "tone-cyan")}
  {_metric_card("Active Markets", str(market_counts['active_markets']), f"{market_counts['active_contracts']} active contracts", "tone-blue")}
  {_metric_card("Drift", "YES" if drift.get('has_drift') else "NO", f"{len(positions_db_only)} db-only remnants", "tone-bad" if drift.get('has_drift') else "tone-mint")}
  {_metric_card("Realized P&L", _fmt_money(realized_pnl), "From Kalshi trade ledger", "tone-amber" if realized_pnl < 0 else "tone-mint")}
</div>
"""
st.markdown(metric_html, unsafe_allow_html=True)

col_left, col_right = st.columns([1.6, 1.0], gap="large")

with col_left:
    st.markdown('<div class="section-title">Open Book</div>', unsafe_allow_html=True)
    with st.container(border=False):
        rows = positions_live or positions_db_only
        if rows:
            pos_df = pd.DataFrame(rows)
            pos_df = pos_df[
                [
                    "ticker",
                    "contract_name",
                    "side",
                    "qty",
                    "entry_price",
                    "bid",
                    "ask",
                    "mark",
                    "gross_mark_pnl",
                    "exit_pnl_est",
                    "hub",
                    "state_label",
                    "resolution_at",
                ]
            ].rename(
                columns={
                    "contract_name": "contract",
                    "entry_price": "entry",
                    "gross_mark_pnl": "mark_pnl",
                    "exit_pnl_est": "exit_pnl_est",
                    "resolution_at": "resolves",
                    "state_label": "state",
                }
            )
            st.dataframe(pos_df, width="stretch", hide_index=True)
            st.caption("`mark_pnl` uses current side midpoint. `exit_pnl_est` is a bid-side liquidation estimate with exit fee and any recorded entry fee.")
        else:
            st.info("No live Kalshi positions are open right now.")

    st.markdown('<div class="section-title">Trade Curve</div>', unsafe_allow_html=True)
    if realized_curve:
        curve_df = pd.DataFrame(realized_curve)
        curve_df = curve_df.rename(columns={"ts": "time", "cumulative_pnl": "realized_pnl"})
        st.line_chart(curve_df.set_index("time"))
    else:
        st.info("No realized Kalshi P&L history is available yet.")

    st.markdown('<div class="section-title">Recent Trades</div>', unsafe_allow_html=True)
    if recent_trades:
        trades_df = pd.DataFrame(recent_trades)
        trades_df["ts"] = trades_df["ts"].map(_fmt_dt)
        st.dataframe(
            trades_df[
                [
                    "ts",
                    "symbol",
                    "action",
                    "qty",
                    "price",
                    "fee_usd",
                    "pnl_usd",
                    "strategy",
                    "contract_side",
                    "forecast_yes_prob",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No recent Kalshi trades found.")

with col_right:
    st.markdown('<div class="section-title">Risk Radar</div>', unsafe_allow_html=True)
    hub_df = pd.DataFrame(payload["hub_exposure"])
    if not hub_df.empty:
        st.dataframe(hub_df, width="stretch", hide_index=True)
        st.caption(f"Live hub cap right now: {_fmt_money(hub_cap_now)}")
    else:
        st.info("No active hub exposure.")

    st.markdown('<div class="section-title">Veto Tape</div>', unsafe_allow_html=True)
    if recent_vetoes.get("top_reasons"):
        veto_df = pd.DataFrame(recent_vetoes["top_reasons"])
        st.dataframe(veto_df, width="stretch", hide_index=True)
    else:
        st.success("No recent hard veto cluster in the current lookback window.")

    st.markdown('<div class="section-title">Runtime Integrity</div>', unsafe_allow_html=True)
    integrity = pd.DataFrame(
        [
            {"metric": "disk_free_mb", "value": round(float(storage["free_mb"]), 1)},
            {"metric": "disk_floor_mb", "value": round(float(storage["threshold_mb"]), 1)},
            {"metric": "db_size_mb", "value": storage["db_mb"]},
            {"metric": "bot_log_mb", "value": storage["bot_log_mb"]},
            {"metric": "forecast_log_mb", "value": storage["forecast_log_mb"]},
            {"metric": "quote_rows", "value": market_counts["quote_rows"]},
            {"metric": "bar_rows", "value": market_counts["bar_rows"]},
        ]
    )
    st.dataframe(integrity, width="stretch", hide_index=True)

st.markdown("### Regime Stack")
reg_left, reg_right = st.columns([1.2, 1.0], gap="large")

with reg_left:
    st.markdown(
        """
        <div class="panel">
          <div class="section-title">Math Engine</div>
          <div class="formula"><strong>Weather probability blend</strong><br>60% GFS + 40% ECMWF. AI/GraphCast does not directly set the forecast probability; it only widens or compresses sigma.</div>
          <div class="formula"><strong>Net EV gate</strong><br>Chosen side must beat the post-fee edge floor after a fixed contract fee and taker friction buffer.</div>
          <div class="formula"><strong>Sizing stack</strong><br>Fractional Kelly on fee-adjusted cost, then clipped by Kelly cap, event-risk cap, deployment cap, and hard USD cap.</div>
          <div class="formula"><strong>Exit stack</strong><br>85c take-profit, held-model invalidation, time-decay redeploy, and liquidity-checked limit exits.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with reg_right:
    st.markdown('<div class="section-title">Live Regime Constants</div>', unsafe_allow_html=True)
    regime_rows = []
    for bucket in ("entry_math", "entry_gates", "exit_stack"):
        for item in regime[bucket]:
            regime_rows.append({"bucket": bucket, "detail": item})
    st.dataframe(pd.DataFrame(regime_rows), width="stretch", hide_index=True)
    macro = regime.get("macro_context") or {}
    if macro:
        st.caption(
            f"Macro: risk_score={macro.get('risk_score')} | spy_trend={macro.get('spy_trend')} | "
            f"vix={macro.get('vix_regime')} | treasury={macro.get('treasury_yield')}"
        )

st.markdown("### Event Tape")
evt_left, evt_right = st.columns(2, gap="large")

with evt_left:
    st.markdown('<div class="section-title">System Events</div>', unsafe_allow_html=True)
    if recent_events:
        for event in recent_events[:12]:
            tone = "tone-bad" if event.get("level") in {"ERROR", "CRITICAL"} else "tone-amber" if event.get("level") == "WARNING" else "tone-cyan"
            st.markdown(
                _feed_card(
                    f"{event.get('source')} [{event.get('level')}]",
                    _fmt_dt(event.get("ts")),
                    str(event.get("message") or ""),
                    tone=tone,
                ),
                unsafe_allow_html=True,
            )
    else:
        st.info("No recent system events.")

with evt_right:
    st.markdown('<div class="section-title">Notification Feed</div>', unsafe_allow_html=True)
    if notifications:
        for event in notifications[:12]:
            tone = "tone-bad" if event.get("severity") == "CRITICAL" else "tone-amber" if event.get("severity") == "WARNING" else "tone-blue"
            why = event.get("why") or {}
            why_bits = why.get("top_3_reasons") or []
            why_text = " | ".join(str(x) for x in why_bits[:3]) if why_bits else str(event.get("message") or "")
            ts_value = datetime.fromtimestamp(float(event.get("ts") or 0), tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            st.markdown(
                _feed_card(
                    f"{event.get('category')} :: {event.get('title')}",
                    ts_value,
                    why_text,
                    tone=tone,
                ),
                unsafe_allow_html=True,
            )
    else:
        st.info("No notification feed rows available.")

st.divider()
st.caption(
    f"Generated {_fmt_dt(payload['generated_at'])} | "
    f"Lane updated {_fmt_dt(lane.get('updated_at'))} | "
    f"Deployed SHA {str(deploy.get('sha') or 'unknown')[:12]} | "
    f"Broker sync {'ON' if live_sync else 'OFF'}"
)
