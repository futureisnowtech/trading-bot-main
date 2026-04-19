"""
dashboard/app.py — Algo Trading Operator Panel (v17.0 control-tower architecture)
5-page design: CONTROL TOWER · CRYPTO · FORECAST · PERFORMANCE LAB · ENGINEERING CONSOLE
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
.badge-system  { background: rgba(96,165,250,0.18);  color:#60a5fa; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.badge-archived{ background: rgba(100,116,139,0.18); color:#94a3b8; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; }
.panel-title   { font-size:1em; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:#94a3b8; margin-bottom:4px; }
.badge-crypto  { background: rgba(251,146,60,0.15);  color:#fb923c; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; border-left:3px solid #fb923c; margin-bottom:6px; display:inline-block; }
.badge-futures  { background: rgba(96,165,250,0.15);  color:#60a5fa; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; border-left:3px solid #60a5fa; margin-bottom:6px; display:inline-block; }
.badge-forecast { background: rgba(168,85,247,0.15);  color:#a855f7; padding:2px 8px; border-radius:4px; font-size:0.8em; font-weight:700; border-left:3px solid #a855f7; margin-bottom:6px; display:inline-block; }
</style>
""",
    unsafe_allow_html=True,
)

# ── page imports ───────────────────────────────────────────────────────────────
from widgets.pages.control_tower import render_control_tower
from widgets.pages.crypto_page import render_crypto_page
from widgets.pages.forecast_page import render_forecast_page
from widgets.pages.performance_lab import render_performance_lab
from widgets.pages.engineering_console import render_engineering_console


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    st.title("Algo Trading — Operator Panel")

    tab_ct, tab_cr, tab_fc, tab_pl, tab_ec = st.tabs(
        [
            "CONTROL TOWER",
            "CRYPTO",
            "FORECAST",
            "PERFORMANCE LAB",
            "ENGINEERING CONSOLE",
        ]
    )

    with tab_ct:
        render_control_tower()

    with tab_cr:
        render_crypto_page()

    with tab_fc:
        render_forecast_page()

    with tab_pl:
        render_performance_lab()

    with tab_ec:
        render_engineering_console()


if __name__ == "__main__":
    main()
else:
    main()
