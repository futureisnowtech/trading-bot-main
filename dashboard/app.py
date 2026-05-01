"""
dashboard/app.py — Algo Trading Operator Panel
Single-page redesign: bot reasoning transparency + account reality.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

# ── path setup ─────────────────────────────────────────────────────────────────
_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DASH_DIR)
for _p in (_DASH_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_dash_data_dir = os.path.join(_DASH_DIR, "data")
_dash_data_init = os.path.join(_dash_data_dir, "__init__.py")
_spec = importlib.util.spec_from_file_location(
    "data", _dash_data_init, submodule_search_locations=[_dash_data_dir]
)
_data_pkg = importlib.util.module_from_spec(_spec)
_data_pkg.__path__ = [_dash_data_dir]
_spec.loader.exec_module(_data_pkg)
sys.modules["data"] = _data_pkg

import streamlit as st

st.set_page_config(
    page_title="Algo — Operator",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Design tokens ──────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

[data-testid="stAppViewContainer"], [data-testid="stMain"], .main {
    background: #06080c !important;
    font-family: 'Inter', sans-serif !important;
}
section[data-testid="stSidebar"] { display: none !important; }
#MainMenu, footer, header, .stDeployButton, [data-testid="stToolbar"] {
    visibility: hidden !important;
}
.block-container {
    padding: 24px 32px 80px 32px !important;
    max-width: 1440px !important;
    margin: 0 auto !important;
}

/* ── Typography ── */
.op-label {
    font-size: 0.62em; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: #3d4451;
}
.op-value-lg {
    font-size: 2.4em; font-weight: 800; color: #e8edf3; line-height: 1;
    font-family: 'Inter', sans-serif;
}
.op-value-md {
    font-size: 1.35em; font-weight: 700; color: #e8edf3; line-height: 1.1;
}
.op-muted { color: #3d4451; font-size: 0.76em; }
.mono { font-family: 'JetBrains Mono', monospace; }

/* ── Top bar ── */
.top-bar {
    display: flex; align-items: center; gap: 14px;
    padding: 0 0 22px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    margin-bottom: 24px;
}
.top-bar-title {
    font-size: 0.72em; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; color: #3d4451; flex: 1;
}
.badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 100px;
    font-size: 0.64em; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase;
}
.badge-live   { background: rgba(248,81,73,0.12); color: #f85149; border: 1px solid rgba(248,81,73,0.2); }
.badge-paper  { background: rgba(88,166,255,0.10); color: #58a6ff; border: 1px solid rgba(88,166,255,0.2); }
.badge-good   { background: rgba(0,212,129,0.10); color: #00d481; border: 1px solid rgba(0,212,129,0.2); }
.badge-warn   { background: rgba(245,166,35,0.10); color: #f5a623; border: 1px solid rgba(245,166,35,0.2); }
.badge-halt   { background: rgba(248,81,73,0.12); color: #f85149; border: 1px solid rgba(248,81,73,0.25); }
.badge-neutral{ background: rgba(61,68,81,0.30);  color: #6e7681; border: 1px solid rgba(255,255,255,0.06); }
.pulse { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.pulse-green { background: #00d481; box-shadow: 0 0 6px rgba(0,212,129,0.6); }
.pulse-red   { background: #f85149; box-shadow: 0 0 6px rgba(248,81,73,0.6); }
.pulse-amber { background: #f5a623; box-shadow: 0 0 6px rgba(245,166,35,0.6); }

/* ── Account strip ── */
.acct-strip {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    margin-bottom: 28px;
}
.acct-card {
    background: #0c0f14; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 16px; padding: 18px 20px;
}
.acct-card-accent { border-top: 2px solid #00d481; }
.acct-card-warn   { border-top: 2px solid #f5a623; }
.acct-card-red    { border-top: 2px solid #f85149; }
.acct-card-neutral{ border-top: 2px solid #1e2430; }

/* ── Section headers ── */
.section-title {
    font-size: 0.60em; font-weight: 700; letter-spacing: 0.16em;
    text-transform: uppercase; color: #2a3040;
    margin-bottom: 14px; padding-bottom: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
}

/* ── Symbol grid ── */
.sym-card {
    background: #0c0f14; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 18px; padding: 18px 18px 14px 18px;
    height: 100%; position: relative;
    transition: border-color 0.2s;
}
.sym-card-entered { border-color: rgba(0,212,129,0.35) !important; }
.sym-card-watch   { border-color: rgba(245,166,35,0.2) !important; }
.sym-card-nodata  { opacity: 0.45; }
.sym-name {
    font-size: 1.1em; font-weight: 800; color: #e8edf3;
    letter-spacing: 0.02em; display: inline-block; margin-right: 8px;
}
.regime-chip {
    display: inline-flex; align-items: center;
    padding: 2px 9px; border-radius: 100px;
    font-size: 0.58em; font-weight: 700; letter-spacing: 0.07em;
    text-transform: uppercase;
}
.score-row {
    display: flex; align-items: baseline; gap: 6px;
    margin: 12px 0 6px 0;
}
.score-num {
    font-size: 1.55em; font-weight: 800; line-height: 1;
    font-family: 'JetBrains Mono', monospace;
}
.score-floor {
    font-size: 0.68em; color: #3d4451; font-family: 'JetBrains Mono', monospace;
}
.score-bar-wrap {
    height: 3px; background: rgba(255,255,255,0.05);
    border-radius: 2px; margin-bottom: 12px; overflow: hidden;
}
.score-bar-fill { height: 100%; border-radius: 2px; }
.decision-row {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 2px;
}
.decision-label {
    font-size: 0.62em; font-weight: 700; letter-spacing: 0.07em;
    text-transform: uppercase;
}
.setup-tag {
    font-size: 0.58em; color: #3d4451; font-family: 'JetBrains Mono', monospace;
    margin-top: 4px;
}
.sym-age { font-size: 0.55em; color: #2a3040; }

/* ── Decision log ── */
.dlog-row {
    display: flex; align-items: center; gap: 12px;
    padding: 9px 14px; border-radius: 10px;
    margin-bottom: 4px; background: #0c0f14;
    border: 1px solid rgba(255,255,255,0.04);
}
.dlog-sym {
    font-size: 0.76em; font-weight: 800; color: #e8edf3;
    width: 36px; flex-shrink: 0; letter-spacing: 0.03em;
}
.dlog-decision { font-size: 0.67em; font-weight: 700; letter-spacing: 0.06em; flex: 1; }
.dlog-score {
    font-size: 0.66em; color: #3d4451;
    font-family: 'JetBrains Mono', monospace; width: 38px; text-align: right;
}
.dlog-regime { font-size: 0.60em; width: 54px; text-align: right; }
.dlog-age { font-size: 0.60em; color: #2a3040; width: 46px; text-align: right; }

/* ── Streamlit overrides ── */
[data-testid="stMetric"]          { display: none !important; }
[data-testid="column"]            { padding: 0 6px !important; }
div[data-testid="stHorizontalBlock"] { gap: 0 !important; }
</style>
""",
    unsafe_allow_html=True,
)


# ── Data ───────────────────────────────────────────────────────────────────────
from data.bot_state import get_symbol_grid, get_decision_log, get_bot_pulse
from data.account import get_account
from data.positions import get_spot_positions_dashboard


def _fmt_money(v: float, sign: bool = False) -> str:
    s = "+" if sign and v > 0 else ""
    return f"{s}${abs(v):,.2f}"


def _score_color(score: float, floor: float) -> str:
    if score == 0:
        return "#2a3040"
    ratio = score / floor if floor else 0
    if ratio >= 1.0:
        return "#00d481"
    if ratio >= 0.88:
        return "#f5a623"
    return "#f85149"


def _decision_color(status: str) -> str:
    return {
        "good": "#00d481",
        "watch": "#f5a623",
        "problem": "#f85149",
        "neutral": "#3d4451",
    }.get(status, "#3d4451")


# ── Fetch ──────────────────────────────────────────────────────────────────────
pulse = get_bot_pulse()
symbols = get_symbol_grid()
decisions = get_decision_log(14)
equity, is_paper, cash = get_account()
holdings = get_spot_positions_dashboard()
deployed = sum(float(h.get("current_value") or 0) for h in holdings)
unrealized = equity - cash  # rough: equity = cash + realized PnL delta
drawdown_pct = ((cash - equity) / cash * 100) if cash > 0 and equity < cash else 0.0

mode_cls = "badge-paper" if is_paper else "badge-live"
mode_label = "PAPER" if is_paper else "LIVE"
pulse_cls = (
    "pulse-green"
    if pulse["healthy"] and not pulse["kill_switch_active"]
    else ("pulse-red" if pulse["kill_switch_active"] else "pulse-amber")
)
ks_active = pulse["kill_switch_active"]


# ── Top bar ────────────────────────────────────────────────────────────────────
st.markdown(
    f"""
<div class="top-bar">
  <span class="top-bar-title">Algo Trading — Operator</span>
  <span class="badge {mode_cls}">
    <span class="pulse {pulse_cls}"></span>{mode_label}
  </span>
  {'<span class="badge badge-halt">⚠ Kill Switch</span>' if ks_active else ""}
  <span class="badge badge-neutral">Last scan {pulse["last_scan_age"]}</span>
  <span class="badge {"badge-good" if pulse["healthy"] else "badge-warn"}">
    {"● Healthy" if pulse["healthy"] else "● Degraded"}
  </span>
</div>
""",
    unsafe_allow_html=True,
)


# ── Account strip ──────────────────────────────────────────────────────────────
pnl = equity - cash
pnl_color = "#00d481" if pnl >= 0 else "#f85149"
pnl_sign = "+" if pnl >= 0 else "−"
dd_color = (
    "#f85149" if drawdown_pct > 2 else ("#f5a623" if drawdown_pct > 0.5 else "#00d481")
)

st.markdown(
    f"""
<div class="acct-strip">
  <div class="acct-card acct-card-accent">
    <div class="op-label">Account Equity</div>
    <div class="op-value-lg">{_fmt_money(equity)}</div>
    <div class="op-muted" style="margin-top:6px;">
      {pnl_sign}{_fmt_money(abs(pnl))} realized
    </div>
  </div>
  <div class="acct-card acct-card-neutral">
    <div class="op-label">Cash Available</div>
    <div class="op-value-lg">{_fmt_money(cash)}</div>
    <div class="op-muted" style="margin-top:6px;">Coinbase spot</div>
  </div>
  <div class="acct-card {"acct-card-red" if deployed > 0 else "acct-card-neutral"}">
    <div class="op-label">Deployed</div>
    <div class="op-value-lg">{_fmt_money(deployed)}</div>
    <div class="op-muted" style="margin-top:6px;">{len(holdings)} position{"s" if len(holdings) != 1 else ""}</div>
  </div>
  <div class="acct-card {"acct-card-warn" if drawdown_pct > 1 else "acct-card-neutral"}">
    <div class="op-label">Drawdown</div>
    <div class="op-value-lg" style="color:{dd_color};">{drawdown_pct:.1f}%</div>
    <div class="op-muted" style="margin-top:6px;">from starting capital</div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ── Symbol grid ────────────────────────────────────────────────────────────────
st.markdown(
    '<div class="section-title">Market Scan — Bot\'s Current View</div>',
    unsafe_allow_html=True,
)

cols = st.columns(4, gap="small")
for i, sym in enumerate(symbols):
    score = sym["score"]
    floor = sym["floor"]
    bar_pct = min(100, int(score / floor * 100)) if floor and score else 0
    bar_color = _score_color(score, floor)
    d_color = _decision_color(sym["decision_status"])
    r_color = sym["regime_color"]
    entered = sym["decision_status"] == "good"
    nodata = not sym["has_data"]

    card_cls = (
        "sym-card-entered"
        if entered
        else (
            "sym-card-nodata"
            if nodata
            else "sym-card-watch"
            if sym["decision_status"] == "watch"
            else ""
        )
    )
    score_disp = f"{score:.1f}" if score else "—"
    dir_tag = f" · {sym['direction']}" if sym["direction"] else ""
    setup_disp = (
        f"{sym['setup']}{dir_tag}" if sym["setup"] else dir_tag.lstrip(" · ") or "—"
    )

    with cols[i % 4]:
        st.markdown(
            f"""
<div class="sym-card {card_cls}" style="margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <span class="sym-name">{sym["symbol"]}</span>
    <span class="regime-chip"
      style="background:{r_color}18;color:{r_color};border:1px solid {r_color}30;">
      {sym["regime_label"]}
    </span>
  </div>

  <div class="score-row">
    <span class="score-num" style="color:{bar_color};">{score_disp}</span>
    <span class="score-floor">/ {floor:.0f}</span>
  </div>

  <div class="score-bar-wrap">
    <div class="score-bar-fill" style="width:{bar_pct}%;background:{bar_color};opacity:0.75;"></div>
  </div>

  <div class="decision-row">
    <span class="decision-label" style="color:{d_color};">{sym["decision_label"]}</span>
    <span class="sym-age">{sym["age"]}</span>
  </div>
  <div class="setup-tag mono">{setup_disp}</div>
</div>
""",
            unsafe_allow_html=True,
        )

    if i == 3:
        cols = st.columns(4, gap="small")


# ── Decision log ───────────────────────────────────────────────────────────────
st.markdown(
    '<div class="section-title" style="margin-top:28px;">Decision Log — Last 14 Scans</div>',
    unsafe_allow_html=True,
)

log_html = ""
for d in decisions:
    d_color = _decision_color(d["decision_status"])
    score_disp = f"{d['score']:.1f}" if d["score"] else "—"
    log_html += f"""
<div class="dlog-row">
  <span class="dlog-sym">{d["symbol"]}</span>
  <span class="dlog-decision" style="color:{d_color};">{d["decision_label"]}</span>
  <span class="dlog-score mono">{score_disp}</span>
  <span class="dlog-regime" style="color:{d["regime_color"]};font-size:0.60em;font-weight:600;">{d["regime_label"]}</span>
  <span class="dlog-age">{d["age"]}</span>
</div>"""

st.markdown(log_html, unsafe_allow_html=True)

# ── Auto-refresh ───────────────────────────────────────────────────────────────
st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
col_r, col_s = st.columns([6, 1])
with col_s:
    if st.button("↻ Refresh", use_container_width=True):
        st.rerun()
with col_r:
    st.markdown(
        f"<div class='op-muted' style='padding-top:8px;'>"
        f"Auto-refresh off · {pulse['last_scan_msg'] or 'awaiting scan'}"
        f"</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    pass
