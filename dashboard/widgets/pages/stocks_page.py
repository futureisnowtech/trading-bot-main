"""
dashboard/widgets/pages/stocks_page.py — Thin wrapper for the STOCKS page.
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

from data.stocks import get_stock_header


def render_stocks_page() -> None:
    hdr = get_stock_header()
    connected = bool(hdr.get("connected"))
    account_value = float(hdr.get("account_value") or 0.0)
    open_count = int(hdr.get("open_count") or 0)
    mode_label = hdr.get("mode_label", "UNKNOWN")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            ui.summary_card(
                "Stocks Lane Role",
                "Dormant Ready",
                "Not Primary",
                "info",
                "This lane stays visible and recoverable, but it should not compete with the crypto workflow for operator attention right now.",
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            ui.summary_card(
                "Runner State",
                "Running" if connected else "Stopped",
                mode_label,
                "good" if connected else "watch",
                "Stocks can remain promotion-ready without being an autonomous primary lane.",
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            ui.summary_card(
                "Live Snapshot",
                f"{open_count} open",
                f"${account_value:,.0f}" if account_value > 0 else "No account value",
                "info" if account_value > 0 else "neutral",
                "Promotion condition: prove edge and PDT-aware operating rules before making stocks a primary autonomous business lane.",
            ),
            unsafe_allow_html=True,
        )

    st.markdown(
        ui.info_callout(
            "Stocks are intentionally sidelined right now. Keep this tab for readiness, signals, and recovery, not as the main autonomous profit engine.",
            "info",
        ),
        unsafe_allow_html=True,
    )

    try:
        from widgets.stocks.stocks_dashboard import render_stocks

        render_stocks()
    except Exception as e:
        st.error(f"Stocks dashboard unavailable: {e}")
