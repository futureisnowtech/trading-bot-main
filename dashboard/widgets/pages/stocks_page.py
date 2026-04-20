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


def render_stocks_page() -> None:
    try:
        from widgets.stocks.stocks_dashboard import render_stocks

        render_stocks()
    except Exception as e:
        st.error(f"Stocks dashboard unavailable: {e}")
