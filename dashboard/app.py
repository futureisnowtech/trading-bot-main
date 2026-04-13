"""
dashboard/app.py — Algo Trading Operator Panel (v14 widget architecture)
Thin orchestrator: page config → CSS → tab layout → widget calls.
All data, formatting, and render logic lives in dashboard/data/ and dashboard/widgets/.
"""

import os
import sys

# ── path setup (must run before any local imports) ─────────────────────────────
_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DASH_DIR)
if _DASH_DIR not in sys.path:
    sys.path.insert(0, _DASH_DIR)
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

import streamlit as st

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Algo Trading — Operator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
section[data-testid="stSidebar"] { display: none; }
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding: 16px 24px 60px 24px !important; max-width: 100% !important; }
.status-green  { color: #4ade80; font-weight: 700; }
.status-yellow { color: #facc15; font-weight: 700; }
.status-red    { color: #f87171; font-weight: 700; }
.badge-pass    { background: rgba(74,222,128,0.18);  color:#4ade80; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.badge-warn    { background: rgba(250,204,21,0.18);  color:#facc15; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.badge-fail    { background: rgba(248,113,113,0.18); color:#f87171; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.panel-title   { font-size:1em; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:#94a3b8; margin-bottom:4px; }
.badge-crypto  { background: rgba(251,146,60,0.15);  color:#fb923c; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; border-left:3px solid #fb923c; margin-bottom:6px; display:inline-block; }
.badge-futures { background: rgba(96,165,250,0.15);  color:#60a5fa; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; border-left:3px solid #60a5fa; margin-bottom:6px; display:inline-block; }
</style>
""",
    unsafe_allow_html=True,
)

# ── widget imports ─────────────────────────────────────────────────────────────
# Status hero (the new primary overview widget)
from widgets.mission_control.status_hero import render_status_hero

# Main-view widgets (always visible on Mission Control)
from widgets.mission_control.open_positions import render_positions_compact
from widgets.mission_control.activity_log import render_smart_logs
from widgets.mission_control.equity_curve import render_equity_curve_compact

# Detail widgets (live inside the "System details" expander)
from widgets.mission_control.system_health import render_system_integrity
from widgets.mission_control.edge_quality import render_edge_quality
from widgets.mission_control.alert_feed import render_alert_feed
from widgets.mission_control.scanner_funnel import render_scanner_funnel
from widgets.mission_control.failure_modes import render_failures_compact
from widgets.mission_control.execution_quality import render_execution_quality
from widgets.mission_control.decision_quality import render_decision_quality

# Other tabs
from widgets.crypto_performance.deep_analysis import render_deep_analysis
from widgets.trade_approval.manual_scan import render_manual_scan
from widgets.futures.mes_dashboard import render_futures
from widgets.system_settings.dev_config import render_dev_config


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    st.title("Algo Trading — Operator Panel")

    tab_mc, tab_cp, tab_ta, tab_fut, tab_ss = st.tabs(
        [
            "MISSION CONTROL",
            "PERFORMANCE",
            "TRADE APPROVAL",
            "S&P 500 FUTURES (MES)",
            "SYSTEM SETTINGS",
        ]
    )

    # ── Tab 1: MISSION CONTROL ─────────────────────────────────────────────────
    with tab_mc:
        # ① Hero: status banner + 4 big metrics + plain-English narrative
        render_status_hero()

        st.divider()

        # ② What's happening now: open positions + activity feed
        col_pos, col_act = st.columns([1.1, 0.9])
        with col_pos:
            render_positions_compact()
        with col_act:
            render_smart_logs()

        st.divider()

        # ③ Equity curve: is the account value growing?
        render_equity_curve_compact()

        st.divider()

        # ④ System details: all the technical readings, hidden by default.
        #    Only dig in here if something looks wrong above.
        with st.expander(
            "⚙️  System details — health checks, scanner, execution quality",
            expanded=False,
        ):
            st.caption(
                "Technical readings that power the numbers above. "
                "These are for diagnosing problems — you don't need to check them unless something looks off."
            )

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                render_system_integrity()
            with col_b:
                render_edge_quality()
            with col_c:
                render_alert_feed()

            st.markdown("---")

            col_d, col_e = st.columns(2)
            with col_d:
                render_scanner_funnel()
                render_execution_quality()
            with col_e:
                render_failures_compact()
                render_decision_quality()

    # ── Tab 2: PERFORMANCE ─────────────────────────────────────────────────────
    with tab_cp:
        st.caption(
            "How is the strategy doing over time? Start with the report card — "
            "expand any section for the full breakdown."
        )
        render_deep_analysis()

    # ── Tab 3: TRADE APPROVAL ──────────────────────────────────────────────────
    with tab_ta:
        st.caption("Run a fresh scan and hand-pick which trades to execute.")
        render_manual_scan()

    # ── Tab 4: S&P 500 FUTURES (MES) ──────────────────────────────────────────
    with tab_fut:
        st.caption(
            "Our automated S&P 500 futures strategy — completely separate from crypto."
        )
        st.markdown(
            """
<div style="background:rgba(96,165,250,0.08); border-left:3px solid #60a5fa;
            padding:10px 14px; border-radius:4px; margin-bottom:12px; font-size:0.85em;">
<strong style="color:#60a5fa">S&P 500 FUTURES · MES</strong><br>
Trades the <strong>Micro E-mini S&P 500 (MES)</strong> contract via IBKR paper account (port 7497).<br>
Two strategies: <strong>Opening Range Breakout</strong> (10:00–15:45 ET) and
<strong>VWAP Mean Reversion</strong> (10:00–14:30 ET).<br>
This account is <strong>completely separate</strong> from crypto perps — different broker,
different capital, different risk rules.
</div>
""",
            unsafe_allow_html=True,
        )
        render_futures()

    # ── Tab 5: SYSTEM SETTINGS ─────────────────────────────────────────────────
    with tab_ss:
        st.caption("All tuning knobs, signal scoring rules, and raw system constants.")
        render_dev_config()


if __name__ == "__main__":
    main()
else:
    main()
