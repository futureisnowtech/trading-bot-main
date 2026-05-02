"""
dashboard/app.py — Algo Trading Operator Panel
"""

from __future__ import annotations
import importlib.util, os, sys

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DASH_DIR)
for _p in (_DASH_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ddir = os.path.join(_DASH_DIR, "data")
_spec = importlib.util.spec_from_file_location(
    "data", os.path.join(_ddir, "__init__.py"), submodule_search_locations=[_ddir]
)
_data_pkg = importlib.util.module_from_spec(_spec)
_data_pkg.__path__ = [_ddir]
_spec.loader.exec_module(_data_pkg)
sys.modules["data"] = _data_pkg

import streamlit as st

st.set_page_config(
    page_title="Algo — Operator",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """<style>
[data-testid="stAppViewContainer"],[data-testid="stMain"],.main{background:#07090e!important;}
section[data-testid="stSidebar"]{display:none!important;}
#MainMenu,footer,header,.stDeployButton,[data-testid="stToolbar"]{visibility:hidden!important;}
.block-container{padding:28px 36px 80px!important;max-width:1480px!important;margin:0 auto!important;}
[data-testid="column"]{padding:0 5px!important;}
div[data-testid="stHorizontalBlock"]{gap:0!important;}
[data-testid="stButton"]>button{
  background:#0d1018!important;border:1px solid rgba(255,255,255,0.08)!important;
  border-radius:10px!important;color:#8892a4!important;font-size:0.75em!important;
  font-weight:600!important;padding:6px 16px!important;letter-spacing:0.05em!important;}
</style>""",
    unsafe_allow_html=True,
)

# ── Data ───────────────────────────────────────────────────────────────────────
from data.bot_state import get_symbol_grid, get_decision_log, get_bot_pulse
from data.account import get_account
from data.positions import get_spot_positions_dashboard
from data.performance import get_per_symbol_stats, get_performance_stats

pulse = get_bot_pulse()
syms = get_symbol_grid()
decisions = get_decision_log(12)
eq, is_paper, cash = get_account()
holdings = get_spot_positions_dashboard()
sym_stats = get_per_symbol_stats()
perf = get_performance_stats()

pnl_total = perf.get("total_pnl") or 0.0
closes = perf.get("closes") or 0
wins = perf.get("wins") or 0
ks_active = pulse["kill_switch_active"]
mode = pulse["mode"].upper()

# ── Hero narrative — trust DB decision, never recalculate ──────────────────────
entered_now = [s for s in syms if s.get("entered")]
valid = [s for s in syms if s["has_data"] and s["score"] > 0]
near = sorted(valid, key=lambda s: s["floor"] - s["score"])
best = near[0] if near else None

if ks_active:
    hero_title = "HALTED"
    hero_color = "#ff1744"
    hero_sub = f"Kill switch armed · {pulse['ks_reason'][:80] if pulse['ks_reason'] else 'manual halt'}"
elif entered_now:
    s = entered_now[0]
    hero_title = "ENTERED"
    hero_color = "#00e676"
    hero_sub = f"{s['symbol']} · {s['setup'] or 'setup'} · {s['regime_label']} · score {s['score']:.1f}"
elif best:
    gap = best["floor"] - best["score"]
    if gap <= 0:
        # Exceeded floor but still here? Must be blocked (e.g. Econ or dual exposure)
        hero_title = "BLOCKED"
        hero_color = "#37415a"
        hero_sub = f"{best['symbol']} signal {best['score']:.1f} passed floor {best['floor']:.0f} but was blocked: {best['decision_label']}"
    elif gap <= 2:
        hero_title = "CLOSE"
        hero_color = "#ffb300"
        hero_sub = f"{best['symbol']} is {gap:.1f} pts from entry · score {best['score']:.1f} vs floor {best['floor']:.1f} · {best['regime_label']}"
    else:
        hero_title = "WATCHING"
        hero_color = "#37415a"
        hero_sub = f"Best candidate: {best['symbol']} · {best['score']:.1f} vs floor {best['floor']:.0f} ({gap:.1f} pts short) · {best['regime_label']}"
else:
    hero_title = "WATCHING"
    hero_color = "#37415a"
    hero_sub = "Scanning 8 symbols — no classifiable setups yet this cycle"

pnl_color = "#00e676" if pnl_total >= 0 else "#ff4757"
pnl_sign = "+" if pnl_total >= 0 else "−"
wr = wins / closes * 100 if closes else 0.0


def _score_color(score, floor):
    if not score or not floor:
        return "#2a3040"
    r = score / floor
    if r >= 1.0:
        return "#00e676"
    if r >= 0.92:
        return "#ffb300"
    return "#ff4757"


def _decision_color(status):
    return {
        "good": "#00e676",
        "watch": "#ffb300",
        "problem": "#ff4757",
        "neutral": "#37415a",
    }.get(status, "#37415a")


def _bar(score, floor):
    if not floor:
        return 0
    return min(100, int(score / floor * 100))


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — one big HTML block keeps CSS control away from Streamlit
# ══════════════════════════════════════════════════════════════════════════════

# ── Top bar ────────────────────────────────────────────────────────────────────
mode_bg = "rgba(255,23,68,0.12)" if mode == "LIVE" else "rgba(64,196,255,0.10)"
mode_fg = "#ff4757" if mode == "LIVE" else "#40c4ff"
pulse_col = (
    "#00e676"
    if pulse["healthy"] and not ks_active
    else ("#ff4757" if ks_active else "#ffb300")
)
ks_badge = (
    f'<span style="background:rgba(255,23,68,0.15);color:#ff4757;border:1px solid rgba(255,23,68,0.3);padding:4px 14px;border-radius:100px;font-size:0.68em;font-weight:700;letter-spacing:0.08em;">⚠ KILL SWITCH</span>'
    if ks_active
    else ""
)

st.markdown(
    f"""
<div style="display:flex;align-items:center;gap:12px;padding-bottom:22px;
            border-bottom:1px solid rgba(255,255,255,0.04);margin-bottom:28px;">
  <span style="flex:1;font-size:0.60em;font-weight:700;letter-spacing:0.20em;
               text-transform:uppercase;color:#2a3040;">Algo Trading — Operator Panel</span>
  <span style="background:{mode_bg};color:{mode_fg};border:1px solid {mode_fg}30;
               padding:4px 14px;border-radius:100px;font-size:0.68em;font-weight:800;
               letter-spacing:0.10em;display:flex;align-items:center;gap:7px;">
    <span style="width:7px;height:7px;border-radius:50%;background:{pulse_col};
                 box-shadow:0 0 8px {pulse_col};display:inline-block;"></span>
    {mode}
  </span>
  {ks_badge}
  <span style="color:#2a3040;font-size:0.65em;">Last scan {pulse["last_scan_age"]}</span>
  <span style="color:#2a3040;font-size:0.65em;">{"● Healthy" if pulse["healthy"] else "⚠ Degraded"}</span>
</div>
""" ,
    unsafe_allow_html=True,
)

# ── Global Kill-Switch Control ────────────────────────────────────────────────
col_ks1, col_ks2 = st.columns([10, 2])
with col_ks2:
    if ks_active:
        if st.button("🔓 RESUME SYSTEM", use_container_width=True):
            import kill_switch
            kill_switch.resume(reason="Manual resume via dashboard")
            st.rerun()
    else:
        if st.button("🛑 EMERGENCY HALT", use_container_width=True):
            import kill_switch
            kill_switch._trigger(reason="Manual halt via dashboard")
            st.rerun()


# ── Hero status ────────────────────────────────────────────────────────────────
st.markdown(
    f"""
<div style="background:linear-gradient(135deg,#0d1018 0%,#0a0c12 100%);
            border:1px solid rgba(255,255,255,0.05);
            border-left:4px solid {hero_color};
            border-radius:20px;padding:28px 32px;margin-bottom:24px;">
  <div style="font-size:0.58em;font-weight:700;letter-spacing:0.18em;
              text-transform:uppercase;color:#2a3040;margin-bottom:10px;">Bot Status</div>
  <div style="font-size:2.2em;font-weight:900;color:{hero_color};
              letter-spacing:0.04em;line-height:1;">{hero_title}</div>
  <div style="font-size:0.88em;color:#8892a4;margin-top:10px;line-height:1.5;">{hero_sub}</div>
</div>
""",
    unsafe_allow_html=True,
)


# ── Account strip — bot trading capital only, no external holdings ─────────────
wr_color = "#00e676" if wr >= 50 else ("#ffb300" if wr >= 35 else "#ff4757")
st.markdown(
    f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px;">

  <div style="background:#0d1018;border:1px solid rgba(255,255,255,0.05);
              border-top:2px solid #40c4ff;border-radius:18px;padding:20px 22px;">
    <div style="font-size:0.56em;font-weight:700;letter-spacing:0.14em;
                text-transform:uppercase;color:#2a3040;margin-bottom:10px;">Trading Capital</div>
    <div style="font-size:2.0em;font-weight:800;color:#e8edf3;line-height:1;
                font-variant-numeric:tabular-nums;">${cash:,.2f}</div>
    <div style="font-size:0.72em;color:#2a3040;margin-top:8px;">Coinbase · available to deploy</div>
  </div>

  <div style="background:#0d1018;border:1px solid rgba(255,255,255,0.05);
              border-top:2px solid {pnl_color};border-radius:18px;padding:20px 22px;">
    <div style="font-size:0.56em;font-weight:700;letter-spacing:0.14em;
                text-transform:uppercase;color:#2a3040;margin-bottom:10px;">Realized P&amp;L</div>
    <div style="font-size:2.0em;font-weight:800;color:{pnl_color};line-height:1;
                font-variant-numeric:tabular-nums;">{pnl_sign}${abs(pnl_total):,.2f}</div>
    <div style="font-size:0.72em;color:#2a3040;margin-top:8px;">{closes} closed trades · all time</div>
  </div>

  <div style="background:#0d1018;border:1px solid rgba(255,255,255,0.05);
              border-top:2px solid {wr_color};border-radius:18px;padding:20px 22px;">
    <div style="font-size:0.56em;font-weight:700;letter-spacing:0.14em;
                text-transform:uppercase;color:#2a3040;margin-bottom:10px;">Win Rate</div>
    <div style="font-size:2.0em;font-weight:800;color:{wr_color};line-height:1;
                font-variant-numeric:tabular-nums;">{wr:.1f}%</div>
    <div style="font-size:0.72em;color:#2a3040;margin-top:8px;">{wins}W / {closes - wins}L · live trades</div>
  </div>

  <div style="background:#0d1018;border:1px solid rgba(255,255,255,0.05);
              border-top:2px solid #37415a;border-radius:18px;padding:20px 22px;">
    <div style="font-size:0.56em;font-weight:700;letter-spacing:0.14em;
                text-transform:uppercase;color:#2a3040;margin-bottom:10px;">Bot Status</div>
    <div style="font-size:2.0em;font-weight:800;color:#e8edf3;line-height:1;">{pulse["readiness"].replace("_", " ")}</div>
    <div style="font-size:0.72em;color:#2a3040;margin-top:8px;">{"Kill switch armed" if ks_active else "Scanning · " + pulse["last_scan_age"]}</div>
  </div>

</div>
""",
    unsafe_allow_html=True,
)


# ── Symbol grid ────────────────────────────────────────────────────────────────
st.markdown(
    """<div style="font-size:0.56em;font-weight:700;letter-spacing:0.16em;
  text-transform:uppercase;color:#2a3040;margin-bottom:14px;padding-bottom:10px;
  border-bottom:1px solid rgba(255,255,255,0.03);">Live Market Scan — Bot's Current View</div>""",
    unsafe_allow_html=True,
)

cards_html = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px;">'

for sym in syms:
    score = sym["score"]
    floor = sym["floor"]
    bar_pct = _bar(score, floor)
    sc_color = _score_color(score, floor)
    dc_color = _decision_color(sym["decision_status"])
    rc_color = sym["regime_color"]
    entered = sym["decision_status"] == "good"
    no_data = not sym["has_data"] or score == 0
    gap = floor - score if not entered and floor and score else 0
    gap_html = (
        f'<span style="font-size:0.70em;color:#2a3040;margin-left:6px;">({gap:.1f} short)</span>'
        if gap > 0
        else ""
    )
    direction = sym.get("direction", "")
    dir_html = (
        f'<span style="color:#37415a;font-size:0.70em;margin-left:4px;">{direction}</span>'
        if direction
        else ""
    )

    border_col = (
        "#00e67630"
        if entered
        else (
            "#ffb30020"
            if sym["decision_status"] == "watch"
            else "rgba(255,255,255,0.04)"
        )
    )
    opacity = "opacity:0.38;" if no_data else ""

    cards_html += f"""
<div style="background:#0d1018;border:1px solid {border_col};border-radius:18px;
            padding:20px 20px 16px;{opacity}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;">
    <span style="font-size:1.3em;font-weight:900;color:#e8edf3;letter-spacing:0.01em;">{sym["symbol"]}</span>
    <span style="background:{rc_color}18;color:{rc_color};border:1px solid {rc_color}28;
                 padding:3px 10px;border-radius:100px;font-size:0.58em;font-weight:700;
                 letter-spacing:0.08em;text-transform:uppercase;">{sym["regime_label"]}</span>
  </div>

  <div style="font-size:2.4em;font-weight:900;color:{sc_color};line-height:1;
              font-variant-numeric:tabular-nums;margin-bottom:4px;">
    {f"{score:.1f}" if score else "—"}
  </div>
  <div style="font-size:0.65em;color:#2a3040;margin-bottom:10px;font-variant-numeric:tabular-nums;">
    floor {floor:.0f}{gap_html}
  </div>

  <div style="height:4px;background:rgba(255,255,255,0.05);border-radius:2px;margin-bottom:14px;overflow:hidden;">
    <div style="height:100%;width:{bar_pct}%;background:{sc_color};border-radius:2px;opacity:0.8;"></div>
  </div>

  <div style="font-size:0.70em;font-weight:700;letter-spacing:0.07em;
              text-transform:uppercase;color:{dc_color};">{sym["decision_label"]}</div>
  <div style="font-size:0.60em;color:#2a3040;margin-top:4px;">
    {sym.get("setup", "") or "—"}{dir_html}&nbsp;&nbsp;<span style="color:#1e2430;">{sym["age"]}</span>
  </div>
</div>"""

cards_html += "</div>"
st.markdown(cards_html, unsafe_allow_html=True)


# ── Bottom two columns: P&L by symbol | Decision log ──────────────────────────
col_l, col_r = st.columns([1, 1], gap="medium")

with col_l:
    st.markdown(
        """<div style="font-size:0.56em;font-weight:700;letter-spacing:0.16em;
      text-transform:uppercase;color:#2a3040;margin-bottom:14px;padding-bottom:10px;
      border-bottom:1px solid rgba(255,255,255,0.03);">P&L by Symbol</div>""",
        unsafe_allow_html=True,
    )

    max_loss = max((abs(s["total_pnl"]) for s in sym_stats), default=1) or 1
    bars_html = ""
    for s in sym_stats:
        pnl = s["total_pnl"]
        w = int(abs(pnl) / max_loss * 100)
        col = "#00e676" if pnl >= 0 else "#ff4757"
        sign = "+" if pnl >= 0 else "−"
        bars_html += f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
  <span style="font-size:0.72em;font-weight:700;color:#8892a4;width:36px;">{s["symbol"]}</span>
  <div style="flex:1;height:6px;background:rgba(255,255,255,0.04);border-radius:3px;overflow:hidden;">
    <div style="height:100%;width:{w}%;background:{col};border-radius:3px;opacity:0.75;"></div>
  </div>
  <span style="font-size:0.70em;font-weight:600;color:{col};width:58px;text-align:right;
               font-variant-numeric:tabular-nums;">{sign}${abs(pnl):,.2f}</span>
</div>"""
    st.markdown(
        f'<div style="background:#0d1018;border:1px solid rgba(255,255,255,0.04);border-radius:18px;padding:20px 22px;">{bars_html}</div>',
        unsafe_allow_html=True,
    )

with col_r:
    st.markdown(
        """<div style="font-size:0.56em;font-weight:700;letter-spacing:0.16em;
      text-transform:uppercase;color:#2a3040;margin-bottom:14px;padding-bottom:10px;
      border-bottom:1px solid rgba(255,255,255,0.03);">Decision Log</div>""",
        unsafe_allow_html=True,
    )

    log_html = ""
    for d in decisions:
        dc = _decision_color(d["decision_status"])
        rc = d["regime_color"]
        sc_disp = f"{d['score']:.1f}" if d["score"] else "—"
        log_html += f"""
<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:10px;
            margin-bottom:4px;background:rgba(255,255,255,0.015);">
  <span style="font-size:0.76em;font-weight:800;color:#e8edf3;width:38px;flex-shrink:0;">{d["symbol"]}</span>
  <span style="font-size:0.65em;font-weight:700;letter-spacing:0.05em;
               text-transform:uppercase;color:{dc};flex:1;">{d["decision_label"]}</span>
  <span style="font-size:0.64em;color:#37415a;font-variant-numeric:tabular-nums;width:34px;text-align:right;">{sc_disp}</span>
  <span style="font-size:0.60em;font-weight:600;color:{rc};width:50px;text-align:right;">{d["regime_label"]}</span>
  <span style="font-size:0.58em;color:#1e2430;width:44px;text-align:right;">{d["age"]}</span>
</div>"""

    st.markdown(
        f'<div style="background:#0d1018;border:1px solid rgba(255,255,255,0.04);border-radius:18px;padding:14px 14px;">{log_html}</div>',
        unsafe_allow_html=True,
    )


# ── Refresh ────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
c1, c2 = st.columns([8, 1])
with c2:
    if st.button("↻ Refresh"):
        st.rerun()
