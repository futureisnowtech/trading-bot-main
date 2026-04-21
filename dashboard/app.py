"""
dashboard/app.py — Algo Trading Operator Panel (v17.2 premium redesign)
7-page design: CONTROL TOWER · CRYPTO · STOCKS · FORECAST · FUTURES · PERFORMANCE LAB · ENGINEERING CONSOLE
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

# ── CSS design system ──────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* ── Base / page shell ─────────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"], [data-testid="stMain"], .main {
    background: #0d1117 !important;
}
section[data-testid="stSidebar"] { display: none; }
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] {
    visibility: hidden;
}
.block-container {
    padding: 18px 28px 80px 28px !important;
    max-width: 1520px !important;
    margin: 0 auto !important;
}

/* ── Page header ───────────────────────────────────────────────────────────── */
.ds-page-title {
    font-size: 1.05em;
    font-weight: 700;
    color: #8b949e;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 14px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
}
.ds-mode-badge {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 100px;
    font-size: 0.70em;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-left: 10px;
    vertical-align: middle;
}
.ds-mode-live  { background: rgba(248,81,73,0.15); color: #f85149; }
.ds-mode-paper { background: rgba(88,166,255,0.12); color: #58a6ff; }

/* ── Tab navigation ────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    gap: 0;
    padding: 0 0 0 2px;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    border-radius: 0 !important;
    color: #6e7681 !important;
    font-weight: 600 !important;
    letter-spacing: 0.05em !important;
    font-size: 0.78em !important;
    padding: 9px 18px !important;
    text-transform: uppercase !important;
}
.stTabs [data-baseweb="tab"]:hover { color: #8b949e !important; }
.stTabs [aria-selected="true"] {
    color: #e6edf3 !important;
    border-bottom: 2px solid #bc8cff !important;
}
[data-testid="stTabsContent"] {
    background: transparent !important;
    border: none !important;
    padding-top: 14px !important;
}

/* ── Metric widget ─────────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #161b22 !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 14px !important;
    padding: 12px 16px !important;
}
[data-testid="stMetricLabel"] p {
    color: #6e7681 !important;
    font-size: 0.72em !important;
    text-transform: uppercase !important;
    letter-spacing: 0.07em !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"] {
    color: #e6edf3 !important;
    font-size: 1.45em !important;
    font-weight: 800 !important;
}
[data-testid="stMetricDelta"] { font-size: 0.75em !important; }
[data-testid="stMetricDelta"] svg { display: none !important; }

/* ── Buttons ───────────────────────────────────────────────────────────────── */
.stButton > button {
    background: #1c2333 !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 8px !important;
    color: #e6edf3 !important;
    font-weight: 600 !important;
    font-size: 0.82em !important;
    padding: 7px 16px !important;
}
.stButton > button:hover {
    background: #21293a !important;
    border-color: rgba(188,140,255,0.30) !important;
}

/* ── Selectbox ─────────────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] > div > div {
    background: #161b22 !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 8px !important;
    color: #e6edf3 !important;
    font-size: 0.83em !important;
}
[data-baseweb="select"] [data-testid="stMarkdownContainer"] p {
    color: #8b949e !important;
    font-size: 0.78em !important;
}

/* ── Radio ─────────────────────────────────────────────────────────────────── */
[data-testid="stRadio"] label { color: #8b949e !important; font-size: 0.82em !important; }
[data-testid="stRadio"] [aria-checked="true"] + div { color: #e6edf3 !important; }

/* ── Expander ──────────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: #161b22 !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 14px !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary {
    background: #161b22 !important;
    color: #8b949e !important;
    font-size: 0.82em !important;
    font-weight: 600 !important;
}
[data-testid="stExpanderDetails"] { padding: 4px 16px 12px 16px !important; }

/* ── Divider ───────────────────────────────────────────────────────────────── */
hr {
    border-color: rgba(255,255,255,0.07) !important;
    margin: 10px 0 !important;
}

/* ── DataFrames ────────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    overflow: hidden !important;
}
[data-testid="stDataFrameResizable"] {
    background: #161b22 !important;
}

/* ── Info / Warning / Error alerts ────────────────────────────────────────── */
[data-testid="stAlert"] {
    background: rgba(255,255,255,0.03) !important;
    border-radius: 10px !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
}
.stAlert [data-testid="stMarkdownContainer"] p {
    font-size: 0.83em !important;
}

/* ── Caption ───────────────────────────────────────────────────────────────── */
.stCaption, .stCaption p { color: #484f58 !important; font-size: 0.76em !important; }

/* ── Code block ────────────────────────────────────────────────────────────── */
.stCode, pre {
    background: #0d1117 !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 8px !important;
    font-size: 0.78em !important;
}

/* ── Line chart ────────────────────────────────────────────────────────────── */
[data-testid="stVegaLiteChart"] {
    border-radius: 10px !important;
    overflow: hidden !important;
    background: transparent !important;
}

/* ── Vertical spacer utility ───────────────────────────────────────────────── */
.ds-spacer-sm { height: 8px; }
.ds-spacer-md { height: 14px; }
.ds-spacer-lg { height: 20px; }

/* ── Legacy badge / status classes (backwards compat) ─────────────────────── */
.status-green  { color: #3fb950; font-weight: 700; }
.status-yellow { color: #d29922; font-weight: 700; }
.status-red    { color: #f85149; font-weight: 700; }

.badge-pass     { background: rgba(63,185,80,0.12);   color: #3fb950; padding: 2px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; }
.badge-warn     { background: rgba(210,153,34,0.12);  color: #d29922; padding: 2px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; }
.badge-fail     { background: rgba(248,81,73,0.12);   color: #f85149; padding: 2px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; }
.badge-system   { background: rgba(88,166,255,0.12);  color: #58a6ff; padding: 2px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; }
.badge-archived { background: rgba(110,118,129,0.10); color: #6e7681; padding: 2px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; letter-spacing: 0.05em; }
.badge-crypto   { background: rgba(188,140,255,0.12); color: #bc8cff; padding: 3px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; margin-bottom: 6px; display: inline-block; }
.badge-futures  { background: rgba(88,166,255,0.12);  color: #58a6ff; padding: 3px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; margin-bottom: 6px; display: inline-block; }
.badge-forecast { background: rgba(188,140,255,0.12); color: #bc8cff; padding: 3px 10px; border-radius: 100px; font-size: 0.72em; font-weight: 700; margin-bottom: 6px; display: inline-block; }

/* ── Panel title (legacy, used by existing widget files) ───────────────────── */
.panel-title {
    font-size: 0.69em;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: #6e7681;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
}
</style>
""",
    unsafe_allow_html=True,
)

# ── page imports ───────────────────────────────────────────────────────────────
from widgets.pages.control_tower import render_control_tower
from widgets.pages.crypto_page import render_crypto_page
from widgets.pages.stocks_page import render_stocks_page
from widgets.pages.forecast_page import render_forecast_page
from widgets.pages.mes_page import render_mes_page
from widgets.pages.performance_lab import render_performance_lab
from widgets.pages.engineering_console import render_engineering_console


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    # ── Page header ────────────────────────────────────────────────────────────
    try:
        from db import _runtime_paper_flag

        _is_paper = _runtime_paper_flag()
    except Exception:
        _is_paper = True
    _mode_cls = "ds-mode-paper" if _is_paper else "ds-mode-live"
    _mode_label = "PAPER" if _is_paper else "LIVE"
    st.markdown(
        f'<div class="ds-page-title">Algo Trading — Operator Panel'
        f'<span class="ds-mode-badge {_mode_cls}">{_mode_label}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    tab_ct, tab_cr, tab_st, tab_fc, tab_fu, tab_pl, tab_ec = st.tabs(
        [
            "CONTROL TOWER",
            "CRYPTO",
            "STOCKS",
            "FORECAST",
            "FUTURES",
            "PERFORMANCE LAB",
            "ENGINEERING CONSOLE",
        ]
    )

    with tab_ct:
        render_control_tower()

    with tab_cr:
        render_crypto_page()

    with tab_st:
        render_stocks_page()

    with tab_fc:
        render_forecast_page()

    with tab_fu:
        render_mes_page()

    with tab_pl:
        render_performance_lab()

    with tab_ec:
        render_engineering_console()


if __name__ == "__main__":
    main()
else:
    main()
