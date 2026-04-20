"""MES Futures page — wraps the existing mes_dashboard widget."""

import streamlit as st


def render_mes_page() -> None:
    try:
        from widgets.futures.mes_dashboard import render_futures

        render_futures()
    except Exception as e:
        st.error(f"MES dashboard unavailable: {e}")
