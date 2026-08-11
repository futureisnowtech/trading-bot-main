"""Summonable cockpit panels.

The cockpit used to render all of this permanently beneath the orb. It is now an
orb-only surface: JARVIS summons a panel with the ``show_panel`` tool and it renders
under the console until dismissed. Each renderer is a plain function of ``ctx``, the
dict streamlit_app builds once per run, so there are no module-level globals to bind.

The four HTML helpers live here rather than in streamlit_app because both modules need
them and one definition beats two.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
import streamlit as st


# ── Shared HTML helpers ─────────────────────────────────────────────

def render_html(block: str) -> None:
    if hasattr(st, "html"):
        st.html(block)
    else:
        st.markdown(block, unsafe_allow_html=True)


def mini_card(label: str, value: str, detail: str, tooltip: str | None = None) -> str:
    explain_html = (
        f'<div style="font-size: 0.73em; color: #bbb; margin-top: 5px; line-height: 1.25; '
        f'border-top: 1px solid rgba(255,255,255,0.05); padding-top: 4px;">{html.escape(tooltip)}</div>'
        if tooltip
        else ""
    )
    return f"""
    <div class="mini-card" style="height: auto; min-height: 110px; padding: 12px; margin-bottom: 8px;">
      <div class="mini-label">
        <span class="label-wrap" style="font-weight: bold; color: #888; text-transform: uppercase; font-size: 0.8em; letter-spacing: 0.5px;">{html.escape(label)}</span>
      </div>
      <div class="mini-value" style="font-size: 1.4em; font-weight: bold; color: #fff; margin-top: 2px;">{html.escape(value)}</div>
      <div class="mini-detail" style="font-size: 0.8em; color: #00e5ff; margin-top: 1px;">{html.escape(detail)}</div>
      {explain_html}
    </div>
    """


def feed_card(title: str, meta: str, body: str, tone: str = "tone-cyan") -> str:
    return f"""
    <div class="feed-card">
      <div class="feed-top">
        <div class="feed-title {tone}">{html.escape(title)}</div>
        <div class="feed-meta">{html.escape(meta)}</div>
      </div>
      <div class="feed-meta" style="margin-top:0.55rem; white-space:pre-wrap;">{html.escape(body)}</div>
    </div>
    """


def fmt_dt(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(value)


# ── Panels ──────────────────────────────────────────────────────────

def render_alerts(ctx: dict[str, Any]) -> None:
    """Release-gate block and broker-vs-DB truth drift, with flatten controls."""
    release_status = ctx["release_status"]
    drift = ctx["drift"]

    if not release_status.get("entries_allowed"):
        blockers = release_status.get("top_infrastructure_blockers") or []
        blocker_text = blockers[0] if blockers else "release audit not yet promoted"
        render_html(
            f"""
            <div class="banner">
              <strong>Fresh entries are paused by the release gate.</strong>
              The runtime is still live for monitoring and exits, but new trades stay blocked until the production blockers clear.
              Current blocker: {html.escape(str(blocker_text))}.
            </div>
            """,
        )

    if not drift.get("has_drift"):
        if release_status.get("entries_allowed"):
            st.success("No release blockers and no truth drift. Broker and ledger agree.")
        return

    details = []
    for p in drift.get("broker_only") or []:
        details.append(
            f"<li>Broker-Only Position: <code>{html.escape(str(p.get('ticker')))}</code> "
            f"({html.escape(str(p.get('side')))}) &mdash; Qty: {p.get('qty')}, Entry: ${p.get('entry_price', 0.0):.2f}</li>"
        )
    for p in drift.get("db_only") or []:
        details.append(
            f"<li>DB-Only Remnant: <code>{html.escape(str(p.get('ticker')))}</code> "
            f"({html.escape(str(p.get('side')))}) &mdash; Qty: {p.get('qty')}, Entry: ${p.get('entry_price', 0.0):.2f}</li>"
        )
    for p in drift.get("qty_mismatches") or []:
        details.append(
            f"<li>Quantity Mismatch: <code>{html.escape(str(p.get('ticker')))}</code> "
            f"({html.escape(str(p.get('side')))}) &mdash; Broker has <b>{p.get('broker_qty')}</b>, DB has <b>{p.get('db_qty')}</b></li>"
        )
    for p in drift.get("entry_mismatches") or []:
        details.append(
            f"<li>Entry Price Mismatch: <code>{html.escape(str(p.get('ticker')))}</code> "
            f"({html.escape(str(p.get('side')))}) &mdash; Broker: ${p.get('broker_entry_price', 0.0):.2f}, "
            f"DB: ${p.get('db_entry_price', 0.0):.2f}</li>"
        )

    details_html = f"<ul style='margin-top: 5px; margin-bottom: 0px;'>{''.join(details)}</ul>" if details else ""
    render_html(
        f"""
        <div class="banner">
          <strong>Truth drift detected.</strong> Broker reality and SQLite do not fully agree right now.
          The cockpit is showing both layers explicitly so you can see whether the issue is a stale local
          ledger, a manual broker action, or a runtime reconciliation lag.
          <div style="margin-top: 10px; font-size: 0.9em; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 8px;">
            <strong>Mismatched Positions:</strong>
            {details_html}
          </div>
        </div>
        """,
    )

    st.write("🔧 **Drift Intervention Controls**")
    for mismatch in (drift.get("qty_mismatches") or []):
        ticker = mismatch["ticker"]
        side = mismatch["side"]
        broker_qty = float(mismatch["broker_qty"] or 0.0)
        col1, col2 = st.columns([3, 1])
        col1.write(f"Flatten mismatched **{ticker}** ({side}) &mdash; Broker has {broker_qty} open contracts.")
        if col2.button(f"Flatten {ticker[:15]}...", key=f"flat_{ticker}", use_container_width=True):
            with st.spinner(f"Flattening {ticker}..."):
                try:
                    from execution.kalshi_broker import get_kalshi_broker
                    from forecast.db import mark_forecast_position_closed

                    broker = get_kalshi_broker()
                    broker.connect()
                    right = "C" if side == "YES" else "P"
                    broker.flatten_position(ticker, right, int(round(broker_qty)))
                    mark_forecast_position_closed(ticker, exit_type="manual_reconcile")
                    st.success(f"Position {ticker} successfully flattened!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to flatten: {e}")


def render_open_book(ctx: dict[str, Any]) -> None:
    """Live Kalshi positions with exposure, mark PnL and time to resolution."""
    rows = ctx["open_book_visual"]
    if not rows:
        st.info("No live Kalshi positions are open right now.")
        return

    pos_df = pd.DataFrame(rows)
    for col in ("gross_mark_pnl", "exposure_usd", "hours_to_resolution"):
        pos_df[col] = pd.to_numeric(pos_df[col], errors="coerce").fillna(0.0)

    m1, m2, m3, m4 = st.columns(4)
    total_exp = float(ctx["open_book_summary"].get("total_exposure_usd") or 0.0)
    total_pnl = pos_df["gross_mark_pnl"].sum()
    m1.metric("Open Positions", len(rows))
    m2.metric("Total Exposure", f"${total_exp:,.2f}")
    m3.metric("Mark PnL", f"${total_pnl:+.2f}", delta=f"{'↑' if total_pnl >= 0 else '↓'} unrealized")
    m4.metric("Soonest Expiry", f"{pos_df['hours_to_resolution'].min():.1f}h" if len(pos_df) > 0 else "—")

    st.dataframe(
        pos_df[["ticker", "side", "qty", "entry_price", "mark", "gross_mark_pnl", "hours_to_resolution", "hub"]].rename(
            columns={"gross_mark_pnl": "mark_pnl", "hours_to_resolution": "hrs_left"}
        ),
        width="stretch",
        hide_index=True,
    )


def render_risk(ctx: dict[str, Any]) -> None:
    """Risk ceilings and gate settings currently in force."""
    render_html(
        '<div class="mini-grid">'
        + "".join(
            mini_card(card["label"], card["value"], card["detail"], card.get("tooltip"))
            for card in ctx["regime_cards"]
        )
        + "</div>",
    )


def render_runtime(ctx: dict[str, Any]) -> None:
    """Disk, database and quote-cache health, plus hub exposure and the veto tape."""
    storage = ctx["storage"]
    market_counts = ctx["market_counts"]
    render_html(
        '<div class="mini-grid">'
        + mini_card("Disk Free", f"{round(float(storage['free_mb']), 0):,.0f} MB", "server headroom")
        + mini_card("DB Footprint", f"{storage['db_mb']} MB", "local SQLite ledger")
        + mini_card("Quote Cache", f"{market_counts['quote_rows']:,}", "forecast quote rows")
        + "</div>",
    )

    t_col1, t_col2 = st.columns(2)
    with t_col1:
        st.markdown("##### 🛡️ Regional Hub Allocations")
        hub_df = pd.DataFrame(ctx["payload"]["hub_exposure"])
        if not hub_df.empty:
            st.dataframe(hub_df, width="stretch", hide_index=True)
        else:
            st.info("No active hub exposure.")
    with t_col2:
        st.markdown("##### 🚫 Veto Cluster Tape")
        recent_vetoes = ctx["recent_vetoes"]
        if recent_vetoes.get("top_reasons"):
            st.dataframe(pd.DataFrame(recent_vetoes["top_reasons"]), width="stretch", hide_index=True)
        else:
            st.success("No recent hard veto clusters.")


def render_events(ctx: dict[str, Any]) -> None:
    """Raw system event tape."""
    recent_events = ctx["recent_events"]
    if not recent_events:
        st.info("No recent system events.")
        return
    for event in recent_events[:12]:
        level = event.get("level")
        tone = "tone-bad" if level in {"ERROR", "CRITICAL"} else "tone-amber" if level == "WARNING" else "tone-cyan"
        st.markdown(
            feed_card(
                f"{event.get('source')} [{level}]",
                fmt_dt(event.get("ts")),
                str(event.get("message") or ""),
                tone=tone,
            ),
            unsafe_allow_html=True,
        )


def render_trades(ctx: dict[str, Any]) -> None:
    """Recent Kalshi trade rows."""
    recent_trades = ctx["recent_trades"]
    if not recent_trades:
        st.info("No recent Kalshi trades found.")
        return
    trades_df = pd.DataFrame(recent_trades)
    trades_df["ts"] = trades_df["ts"].map(fmt_dt)
    st.dataframe(
        trades_df[
            ["ts", "symbol", "action", "qty", "price", "fee_usd", "pnl_usd", "strategy", "contract_side", "forecast_yes_prob"]
        ],
        width="stretch",
        hide_index=True,
    )


# name -> (title, renderer). The name is what JARVIS passes to show_panel.
PANELS: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "alerts": ("🚨 Alerts & Truth Drift", render_alerts),
    "open_book": ("⚡ Live Trading Lane", render_open_book),
    "risk": ("🛡️ Risk Matrix & Controls", render_risk),
    "runtime": ("🩺 Runtime System Integrity", render_runtime),
    "events": ("📡 System Event Tape", render_events),
    "trades": ("🧾 Recent Trade Rows", render_trades),
}

PANEL_NAMES = tuple(PANELS)


def render_panel(name: str, ctx: dict[str, Any]) -> bool:
    """Render one panel by name. Returns False if the name is unknown."""
    entry = PANELS.get(name)
    if not entry:
        return False
    title, fn = entry
    st.markdown(f'<div class="section-title" style="margin-top:16px;">{title}</div>', unsafe_allow_html=True)
    try:
        fn(ctx)
    except Exception as exc:  # a broken panel must not take down the console
        st.error(f"Panel '{name}' failed to render: {exc}")
    return True
