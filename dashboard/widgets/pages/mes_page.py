"""MES Futures page — wraps the existing mes_dashboard widget."""

import streamlit as st
import ui


def render_mes_page() -> None:
    c1, c2 = st.columns([1, 1.2])
    with c1:
        st.markdown(
            ui.summary_card(
                "Futures Lane Role",
                "Archived",
                "MES",
                "neutral",
                "MES stays visible as an archived lane so reactivation requirements are explicit and the code path does not disappear.",
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            ui.detail_card(
                "REACTIVATION",
                "What must become true before MES is promoted again",
                ui.metric_row("Approval", "Futures permissions restored")
                + ui.metric_row("Lane flag", "FUTURES_LANE_ACTIVE=true")
                + ui.metric_row("Validation", "MES proofs and runtime checks green"),
                footer="Archived lanes should stay visible, but they should not compete with active business lanes.",
            ),
            unsafe_allow_html=True,
        )

    st.markdown(
        ui.info_callout(
            "FUTURES is an archived lane. This tab is a reactivation surface, not an active operator workflow.",
            "info",
        ),
        unsafe_allow_html=True,
    )
    try:
        from widgets.futures.mes_dashboard import render_futures

        with st.expander("Legacy MES detail", expanded=False):
            render_futures()
    except Exception as e:
        st.error(f"MES dashboard unavailable: {e}")
