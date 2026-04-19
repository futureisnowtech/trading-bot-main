"""
dashboard/widgets/pages/forecast_page.py — FORECAST page.

Wraps the existing forecast_dashboard widget with a heartbeat-truth banner.
If the forecast lane heartbeat is stale (> 300s), shows a warning before
rendering so the operator knows the data may not reflect current state.
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

from db import _q1
from formatters import _ts_age_s


def render_forecast_page():
    st.caption(
        "ForecastEx event-contract trading lane — U.S. economic indicators only. "
        "Zero commission. ~$100 bankroll. Max 2 concurrent positions."
    )

    # ── Heartbeat truth banner ────────────────────────────────────────────────
    _STALE_THRESHOLD_S = 300
    try:
        lane = _q1(
            "SELECT active, last_heartbeat_at FROM lane_runtime_state "
            "WHERE lane_id='forecast' ORDER BY id DESC LIMIT 1"
        )
        hb_ts = lane.get("last_heartbeat_at")
        if hb_ts:
            age_s = _ts_age_s(hb_ts)
            if age_s > _STALE_THRESHOLD_S:
                st.warning(
                    f"Forecast heartbeat stale ({age_s}s ago) — "
                    "data shown may not reflect current state."
                )
        elif not lane.get("active"):
            st.info(
                "Forecast lane is not active. Set FORECAST_LANE_ACTIVE=true to enable."
            )
    except Exception:
        pass

    # ── ForecastEx info banner ────────────────────────────────────────────────
    st.markdown(
        """
<div style="background:rgba(168,85,247,0.08); border-left:3px solid #a855f7;
            padding:10px 14px; border-radius:4px; margin-bottom:12px; font-size:0.85em;">
<strong style="color:#a855f7">FORECAST EVENT CONTRACTS · ForecastEx (IBKR)</strong><br>
Trades YES/NO event contracts on <strong>U.S. economic events</strong>
(CPI, NFP, FOMC, Unemployment) via IBKR ForecastEx.<br>
Pricing substrate: <strong>bid/ask midpoint only</strong> — no trade prints.<br>
Zero commission. Cannot short — flatten by buying the opposite side.<br>
Max deployed: <strong>35%</strong> · Max per event: <strong>10%</strong> ·
Max concurrent: <strong>2</strong> · Kelly cap: <strong>0.10</strong>
</div>
""",
        unsafe_allow_html=True,
    )

    # ── Forecast dashboard widget ─────────────────────────────────────────────
    try:
        from widgets.forecast.forecast_dashboard import render_forecast_trading

        render_forecast_trading()
    except Exception as e:
        st.error(f"Forecast dashboard unavailable: {e}")
