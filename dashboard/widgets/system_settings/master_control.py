"""
Widget: System Settings / Master Trading Control
Question: Where do trades die, and is that a strategy issue, a system/policy
issue, or a real bug/integrity issue?
Tab: SYSTEM SETTINGS
Refresh: 30s
"""

import streamlit as st

from data.trading_control import get_trading_control_snapshot


@st.fragment(run_every=30)
def render_master_control():
    snap = get_trading_control_snapshot(hours=24)
    crypto = snap["crypto"]
    forecast = snap["forecast"]

    st.subheader("Master Trading Control")
    st.caption(
        "One control-plane view for crypto and forecast. "
        "Use this to decide whether we need a strategy change, a system/policy change, or a true bug fix."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Crypto scanned (24h)", crypto["funnel"].get("scanner_candidates_total", 0))
    c2.metric("Crypto entered (24h)", crypto["funnel"].get("entered", 0))
    c3.metric("Crypto conversion", f"{crypto.get('conversion_pct', 0):.1f}%")
    c4.metric("Forecast state", forecast["readiness"].get("lane_state", "UNKNOWN"))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Strategy rejects", crypto["issue_breakdown"].get("strategy", 0))
    c6.metric("System/policy blocks", crypto["issue_breakdown"].get("system", 0))
    c7.metric("Bug/integrity flags", crypto["issue_breakdown"].get("bug", 0))
    c8.metric("Blank crypto truth rows", crypto.get("blank_tradeability_count", 0))

    with st.expander("Truth Checks", expanded=True):
        for chk in snap["checks"]:
            if chk["status"] == "PASS":
                st.success(f"{chk['name']}: {chk['detail']}")
            else:
                st.warning(f"{chk['name']}: {chk['detail']}")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**Crypto stage breakdown (24h)**")
        for row in crypto["stage_rows"]:
            badge = {
                "strategy": ":orange[STRATEGY]",
                "system": ":blue[SYSTEM]",
                "bug": ":red[BUG]",
                "success": ":green[SUCCESS]",
            }.get(row["class"], ":gray[FLOW]")
            st.markdown(f"- {badge} {row['stage']}: **{row['count']}**")

        st.markdown("**Top crypto blockers (24h)**")
        blockers = crypto.get("top_blockers", [])
        if not blockers:
            st.caption("No recent blockers found.")
        else:
            for b in blockers:
                st.markdown(f"- `{b.get('reason', 'unknown')}` — **{b.get('n', 0)}**")

    with col_right:
        st.markdown("**Forecast control plane**")
        lane = forecast.get("lane", {})
        health = forecast.get("health", {})
        readiness = forecast.get("readiness", {})
        st.markdown(f"- Lane active: **{bool(lane.get('active'))}**")
        st.markdown(f"- Readiness: **{readiness.get('lane_state', 'UNKNOWN')}**")
        st.markdown(f"- Underliers visible: **{health.get('underliers_visible', 0)}**")
        st.markdown(f"- Active contracts: **{health.get('active_contracts', 0)}**")
        st.markdown(f"- 5m bars: **{health.get('bars_5m_count', 0)}**")
        if health.get("quote_lag_minutes") is None:
            st.markdown("- Quote freshness: **no quotes**")
        else:
            st.markdown(f"- Quote lag: **{health.get('quote_lag_minutes', 0)}m**")

        st.markdown("**Forecast contradictions**")
        contradictions = forecast.get("contradictions", [])
        if contradictions:
            for msg in contradictions:
                st.warning(msg)
        else:
            st.caption("No forecast truth contradictions detected.")
