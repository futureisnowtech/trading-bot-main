"""
dashboard/widgets/pages/forecast_page.py — FORECAST page.

Premium design: status hero card, heartbeat-truth banner, lane explainer,
then the full forecast_dashboard widget.
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
from db import _q1
from formatters import _ts_age_s, _time_ago


def render_forecast_page():
    # ── Lane status + heartbeat truth ──────────────────────────────────────────
    _STALE_THRESHOLD_S = 300

    lane_active = False
    hb_ts = None
    hb_age_s = None
    readiness = "LANE_NOT_STARTED"
    blocked_reason = ""

    try:
        lane = _q1(
            "SELECT active, last_heartbeat_at, readiness_state, blocked_reason, health "
            "FROM lane_runtime_state WHERE lane_id='forecast' ORDER BY id DESC LIMIT 1"
        )
        lane_active = bool(lane.get("active"))
        hb_ts = lane.get("last_heartbeat_at")
        readiness = lane.get("readiness_state") or "LANE_NOT_STARTED"
        blocked_reason = lane.get("blocked_reason") or ""
        if hb_ts:
            hb_age_s = _ts_age_s(hb_ts)
    except Exception:
        pass

    # Derive status for the hero card
    if not lane_active:
        status_label, status_chip_status = "NOT ACTIVE", "neutral"
        status_color = ui.C_NEUTRAL
    elif hb_age_s is not None and hb_age_s > _STALE_THRESHOLD_S:
        status_label, status_chip_status = "STALE", "watch"
        status_color = ui.C_AMBER
    elif readiness == "OPERATIONAL":
        status_label, status_chip_status = "OPERATIONAL", "good"
        status_color = ui.C_GREEN
    else:
        status_label, status_chip_status = readiness.replace("_", " "), "watch"
        status_color = ui.C_AMBER

    # Heartbeat age label
    hb_label = _time_ago(hb_ts) if hb_ts else "Never"

    # ── Top strip: hero card + 3 info cards ───────────────────────────────────
    c1, c2, c3, c4 = st.columns([1.4, 1, 1, 1])

    with c1:
        stats = [
            ("Readiness state", readiness.replace("_", " "), status_color),
            (
                "Last heartbeat",
                hb_label,
                ui.C_GREEN if (hb_age_s or 9999) < 120 else ui.C_AMBER,
            ),
            (
                "Lane active",
                "Yes" if lane_active else "No — set FORECAST_LANE_ACTIVE=true",
                status_color,
            ),
        ]
        st.markdown(
            ui.hero_card(
                "ForecastEx Lane",
                status_label,
                stats,
                "U.S. economic event contracts via IBKR ForecastEx — "
                "CPI, NFP, FOMC, Unemployment, PCE, GDP, PPI",
                gradient=True,
            ),
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            ui.summary_card(
                "Trading Venue",
                "ForecastEx",
                "IBKR",
                "neutral",
                "Interactive Brokers ForecastEx exchange · SecType=OPT · "
                "YES = Right C · NO = Right P · Cannot short",
            ),
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            ui.summary_card(
                "Risk Caps",
                "35% max",
                "Hard limits",
                "info",
                "Max deployed 35% · Max per event 10% · "
                "Max concurrent 2 positions · Kelly cap 0.10",
            ),
            unsafe_allow_html=True,
        )

    with c4:
        st.markdown(
            ui.summary_card(
                "Fee Structure",
                "Zero",
                "Commission-free",
                "good",
                "Zero commission on ForecastEx · Pricing from bid/ask midpoint only — "
                "never last/trade prints · ~$100 bankroll",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Stale / not-active banners ─────────────────────────────────────────────
    if not lane_active:
        st.markdown(
            ui.info_callout(
                "Forecast lane is not active. To enable it, set "
                "<code>FORECAST_LANE_ACTIVE=true</code> in your .env file and restart the bot. "
                "Historical market data and quotes are still visible below.",
                "info",
            ),
            unsafe_allow_html=True,
        )
    elif hb_age_s is not None and hb_age_s > _STALE_THRESHOLD_S:
        age_min = hb_age_s // 60
        st.markdown(
            ui.info_callout(
                f"Forecast heartbeat is stale — last seen {age_min} min ago. "
                "The lane runner may have stopped. Data shown below reflects the last known state "
                "and may not be current. Check <code>logs/service/bot_error.log</code> if unexpected.",
                "warn",
            ),
            unsafe_allow_html=True,
        )

    if blocked_reason:
        st.markdown(
            ui.info_callout(
                f"Blocked reason: <strong>{blocked_reason.replace('_', ' ')}</strong> — "
                "this is why the lane cannot trade even if active. "
                "ForecastEx event-contract trading requires a live funded IBKR account "
                "with explicit ForecastEx enrollment via the IBKR portal.",
                "warn",
            ),
            unsafe_allow_html=True,
        )

    # ── How this lane works explainer (collapsed by default) ──────────────────
    with st.expander("How ForecastEx trading works", expanded=False):
        st.markdown(
            ui.detail_card(
                "ForecastEx — Plain English",
                "What this lane does and how it decides",
                ui.metric_row(
                    "What it trades", "YES/NO binary contracts on U.S. economic events"
                )
                + ui.metric_row(
                    "How it prices",
                    "Log-odds math on bid/ask midpoint series — no trade prints",
                )
                + ui.metric_row(
                    "Strategy families",
                    "Continuation · Mean Reversion · Late Repricing",
                )
                + ui.metric_row(
                    "Entry check", "10-condition economics gate before any entry"
                )
                + ui.metric_row("Position sizing", "Fractional Kelly, capped at 0.10")
                + ui.metric_row(
                    "Exiting", "Buy the opposite side (YES→buy NO to flatten)"
                )
                + ui.metric_row(
                    "Universe",
                    "CPI, NFP/PREMP, FOMC, Unemployment/UNR, PCE, GDP/RGDP, PPI",
                )
                + ui.metric_row(
                    "Rejected categories",
                    "Sports, politics, entertainment — fail-closed",
                ),
            ),
            unsafe_allow_html=True,
        )

    # ── ForecastEx dashboard widget ────────────────────────────────────────────
    try:
        from widgets.forecast.forecast_dashboard import render_forecast_trading

        render_forecast_trading()
    except Exception as e:
        st.markdown(
            ui.info_callout(
                f"Forecast dashboard widget unavailable: {e}",
                "warn",
            ),
            unsafe_allow_html=True,
        )
