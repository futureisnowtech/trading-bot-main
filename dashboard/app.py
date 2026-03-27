"""
dashboard/app.py — Trading War Room v10.0

Single view. Data-first. No themes. No fluff.
Every section has an ℹ️ info button explaining what you're looking at.

Layout:
  Header     — status bar: mode · bot health · last scan · ET clock
  Row 1      — Giant P&L scoreboard
  Row 2      — Six key metrics
  Row 3      — Edge monitor (most important component)
  Row 4      — Open positions with stop/target distances
  Row 5      — Three market panels (crypto / MES / perp)
  Row 6      — Risk gauges (daily loss · heat level · watchdog)
  Row 7      — Recent trades | Recent signals
  Row 8      — Claude AI chat
  Expanders  — Debate history · Notifications · Controls
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import urllib.request
from datetime import datetime, timedelta

import plotly.graph_objects as go
import pytz
import streamlit as st

from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    CRYPTO_PAIRS, ANTHROPIC_API_KEY, CLAUDE_MODEL,
    CRYPTO_ENABLED, FUTURES_ENABLED,
    MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT,
    CRYPTO_SCAN_INTERVAL_SECONDS,
    MAX_RISK_PER_TRADE_PCT, MAX_POSITIONS_CRYPTO,
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    FULL_DEBATE_AGENTS,
    CRYPTO_POSITION_SIZE_USD,
    COINBASE_TAKER_FEE_PCT,
)
from logging_db.trade_logger import (
    get_todays_pnl, get_todays_fees, get_todays_signals,
    get_scan_feed, get_all_time_stats, get_recent_debates,
    get_monthly_api_cost, get_win_rate, get_recent_trades,
    get_recent_events, get_recent_notifications, get_today_stats,
)
from risk.risk_manager import get_risk_manager

# ── Optional modules ──────────────────────────────────────────────────────────
try:
    from data.edge_monitor import get_edge_state
    _EDGE_MONITOR = True
except Exception:
    _EDGE_MONITOR = False
    def get_edge_state(s, paper=True):
        return {
            'status': 'UNCERTAIN', 'edge_score': 0.5, 'profit_factor': 1.5,
            'win_rate_20': 0.5, 'consecutive_bad': 0,
            'sizing_multiplier': 1.0, 'should_block': False, 'window_trades': 0,
        }

try:
    from risk.drawdown_controller import get_heat_level
except Exception:
    def get_heat_level(paper=True):
        return {'level': 0, 'label': 'NORMAL', 'size_factor': 1.0,
                'daily_pnl': 0.0, 'pct_drawn': 0.0}

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading War Room",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS — clean dark terminal ─────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base ── */
.main, .stApp { background: #050505 !important; }
* { box-sizing: border-box; }

/* ── Buttons ── */
.stButton > button {
    background: #111; color: #aaa; border: 1px solid #222;
    font-weight: 600; border-radius: 4px; font-size: 12px;
    padding: 4px 14px; transition: all 0.15s;
}
.stButton > button:hover { background: #1e1e1e; color: #fff; border-color: #444; }

/* ── Section header ── */
.sec-hdr {
    display: flex; align-items: center; gap: 8px;
    color: #888; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    padding: 6px 0 4px 0;
    border-bottom: 1px solid #111;
    margin-bottom: 10px;
}
.sec-hdr-accent { color: #FDB927; }

/* ── Scoreboard ── */
.scoreboard {
    text-align: center; padding: 18px 0 8px 0;
    font-family: 'SF Mono', 'Consolas', monospace;
    line-height: 1;
}
.score-pnl {
    font-size: 68px; font-weight: 900;
    display: block; letter-spacing: -1px;
}
.score-label {
    font-size: 10px; color: #333;
    text-transform: uppercase; letter-spacing: 4px;
    margin-top: 6px;
}

/* ── Metric card ── */
.m-card {
    background: #0d0d0d;
    border: 1px solid #1a1a1a;
    border-radius: 6px;
    padding: 10px 12px 8px 12px;
    text-align: center;
}
.m-lbl {
    color: #444; font-size: 9px;
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 4px;
}
.m-val {
    font-size: 19px; font-weight: 900;
    font-family: 'SF Mono', 'Consolas', monospace;
}
.m-sub { color: #333; font-size: 10px; margin-top: 3px; }

/* ── Edge panel ── */
.edge-panel {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 6px;
    padding: 12px 14px;
}
.edge-market {
    color: #555; font-size: 9px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    margin-bottom: 8px;
}
.edge-status {
    font-size: 11px; font-weight: 900;
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 6px;
}
.edge-bar-bg {
    background: #111; border-radius: 3px; height: 6px;
    overflow: hidden; margin: 6px 0 8px 0;
}
.edge-bar-fill { height: 100%; border-radius: 3px; }
.edge-stat { color: #555; font-size: 10px; font-family: monospace; }

/* ── Position table ── */
.pos-row {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 8px; border-radius: 4px;
    border-left: 2px solid #FDB927;
    background: #0d0d0d; margin-bottom: 4px;
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 11px; color: #ccc;
}
.pos-symbol { color: #fff; font-weight: 700; min-width: 90px; }
.pos-detail { color: #555; font-size: 10px; }
.pos-pnl { font-weight: 700; margin-left: auto; font-size: 13px; }
.pos-empty {
    color: #222; font-size: 11px; font-style: italic;
    padding: 12px 0; text-align: center;
}

/* ── Market panel ── */
.mkt-panel {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 6px;
    padding: 12px 14px;
}
.mkt-hdr {
    color: #555; font-size: 9px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    border-bottom: 1px solid #0f0f0f;
    padding-bottom: 6px; margin-bottom: 8px;
}
.mkt-row { font-family: monospace; font-size: 10px; color: #444; padding: 2px 0; }
.mkt-val { color: #aaa; }

/* ── Risk gauge ── */
.gauge-wrap { margin-bottom: 10px; }
.gauge-lbl {
    display: flex; justify-content: space-between;
    color: #555; font-size: 10px; font-family: monospace;
    margin-bottom: 3px;
}
.gauge-bg {
    background: #111; border-radius: 3px;
    height: 8px; overflow: hidden;
}
.gauge-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }

/* ── Trade/signal rows ── */
.t-row {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 0; border-bottom: 1px solid #0d0d0d;
    font-family: monospace; font-size: 11px;
}
.s-row {
    padding: 4px 0; border-bottom: 1px solid #0d0d0d;
    font-family: monospace; font-size: 10px; color: #555;
}

/* ── Status badges ── */
.badge {
    display: inline-block; padding: 1px 6px;
    border-radius: 3px; font-size: 9px;
    font-weight: 700; text-transform: uppercase; letter-spacing: 1px;
}
.badge-paper { background: #1a120a; color: #FDB927; border: 1px solid #3a2a0a; }
.badge-live  { background: #1a0505; color: #FF4444; border: 1px solid #3a0a0a; }
.badge-halted { background: #FF1744; color: #fff; }
.badge-ok { background: #0a1a0a; color: #00C896; border: 1px solid #0a3a1a; }

/* ── Banners ── */
.halt-banner {
    background: #FF1744; color: #fff;
    padding: 8px 16px; border-radius: 4px;
    text-align: center; font-weight: 900;
    font-size: 14px; letter-spacing: 2px; margin: 6px 0;
}

/* ── Chat ── */
.chat-user {
    background: #0f1525; color: #ddd;
    padding: 8px 12px; border-radius: 12px 12px 4px 12px;
    margin: 4px 0; font-size: 13px;
}
.chat-bot {
    background: #0a0a0a; color: #ccc;
    padding: 8px 12px; border-radius: 12px 12px 12px 4px;
    margin: 4px 0; font-size: 13px;
    border-left: 3px solid #FDB927;
}

/* ── Popover info button ── */
div[data-testid="stPopover"] > button {
    background: transparent !important;
    border: 1px solid #1e1e1e !important;
    color: #333 !important; padding: 0 !important;
    min-height: 16px !important; height: 16px !important;
    width: 16px !important; border-radius: 50% !important;
    font-size: 9px !important; line-height: 1 !important;
}
div[data-testid="stPopover"] > button:hover {
    color: #888 !important; border-color: #444 !important;
}

/* ── Misc ── */
.stExpander { border: 1px solid #111 !important; }
div[data-testid="stChatInput"] textarea {
    background: #0d0d0d !important; border-color: #222 !important;
    color: #ccc !important;
}
.divider { border-top: 1px solid #0d0d0d; margin: 14px 0; }
</style>
""", unsafe_allow_html=True)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def et_now() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%b %-d  %-I:%M:%S %p ET')

def fmt_ts(ts: str, short: bool = False) -> str:
    if not ts:
        return '—'
    try:
        dt = datetime.fromisoformat(ts)
        tz = pytz.timezone(MARKET_TIMEZONE)
        dt = dt.astimezone(tz) if dt.tzinfo else tz.localize(dt)
        return dt.strftime('%-I:%M %p') if short else dt.strftime('%b %-d %-I:%M %p')
    except Exception:
        return ts[5:16] if len(ts) >= 16 else ts

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db')

def _db():
    import sqlite3 as sq
    c = sq.connect(_DB, timeout=2)
    c.row_factory = sq.Row
    return c

def _bot_last_seen() -> float | None:
    try:
        from datetime import timezone as _tz
        conn = _db()
        row = conn.execute("SELECT ts FROM system_events ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if not row:
            return None
        dt = datetime.fromisoformat(row[0])
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=_tz.utc)
        return (datetime.now(_tz.utc) - dt).total_seconds()
    except Exception:
        return None

def _live_halt() -> tuple[bool, str]:
    try:
        conn = _db()
        row = conn.execute("""
            SELECT level, message FROM system_events
            WHERE source='RiskManager'
              AND (level='HALT' OR (level='INFO' AND message LIKE '%Halt cleared%'))
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn.close()
        if not row:
            return False, ''
        return (True, row['message']) if row['level'] == 'HALT' else (False, '')
    except Exception:
        return False, ''

def _live_positions() -> dict:
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM open_positions WHERE paper=?", (int(PAPER_TRADING),)
        ).fetchall()
        conn.close()
    except Exception:
        return {}
    result: dict = {}
    for r in rows:
        s = r['strategy']
        if s not in result:
            result[s] = {}
        result[s][r['symbol']] = {
            'qty':      r['qty'],
            'entry':    r['entry'],
            'stop':     r['stop'],
            'target':   r['target'],
            'direction': r['direction'] if 'direction' in r.keys() else 'LONG',
            'ts_entry': r['ts_entry'],
        }
    return result

def _write_env(updates: dict) -> None:
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    try:
        with open(env_path) as f:
            lines = f.readlines()
        written = set()
        new_lines = []
        for line in lines:
            s = line.strip()
            if '=' in s and not s.startswith('#'):
                k = s.split('=', 1)[0].strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}\n")
                    written.add(k)
                    continue
            new_lines.append(line)
        for k, v in updates.items():
            if k not in written:
                new_lines.append(f"{k}={v}\n")
        with open(env_path, 'w') as f:
            f.writelines(new_lines)
    except Exception as e:
        st.error(f"Failed to write .env: {e}")

def _info(text: str):
    """Inline info popover button."""
    with st.popover("ℹ️"):
        st.markdown(f'<div style="font-size:12px;color:#aaa;">{text}</div>',
                    unsafe_allow_html=True)

def _section(title: str, info_text: str, accent: bool = False):
    """Section header with info button."""
    c1, c2 = st.columns([20, 1])
    with c1:
        color = '#FDB927' if accent else '#555'
        st.markdown(
            f'<div class="sec-hdr"><span style="color:{color};">{title}</span></div>',
            unsafe_allow_html=True,
        )
    with c2:
        _info(info_text)

def call_claude(messages: list, ctx: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "Set ANTHROPIC_API_KEY in .env to enable chat."
    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL, "max_tokens": 1200,
            "system": ctx, "messages": messages[-12:]
        }).encode()
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages', data=payload,
            headers={'Content-Type': 'application/json',
                     'x-api-key': ANTHROPIC_API_KEY,
                     'anthropic-version': '2023-06-01'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())['content'][0]['text']
    except Exception as e:
        return f"Error: {e}"

def build_context() -> str:
    pos    = _live_positions()
    pnl    = get_todays_pnl(paper=PAPER_TRADING)
    fees   = get_todays_fees(paper=PAPER_TRADING)
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    rm     = get_risk_manager()
    risk   = rm.status_report()
    wr14   = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    mo     = get_monthly_api_cost()
    recent = get_recent_trades(limit=10, paper=PAPER_TRADING)
    pos_lines = ''
    for strat, syms in pos.items():
        for sym, p in syms.items():
            pos_lines += f"  {strat} {sym} entry=${p['entry']:.4f} stop=${p['stop']:.4f}\n"
    trade_lines = '\n'.join(
        f"  {fmt_ts(t.get('ts',''))} {t.get('action','')} {t.get('symbol','')} "
        f"P&L=${t.get('pnl_usd',0):+.4f}"
        for t in recent
    ) or '  None'
    _n = len(FULL_DEBATE_AGENTS)
    return f"""You are the AI brain of this autonomous trading system. Be direct. Protect capital first.
Philosophy: edge preservation over time. The rolling edge monitor is the most important component.
Amygdala removed: never chase, never average down, stops are sacred, FOMO is not a signal.

LIVE STATE ({et_now()})
Mode: {"PAPER" if PAPER_TRADING else "LIVE"} | Account: ${ACCOUNT_SIZE:,.0f}
Today P&L: ${pnl:+.2f} | Fees: ${fees:.2f} | 14d WR: {wr14:.1%} | API/month: ${mo:.4f}
All-time: {stats.get('total',0)} trades | WR {stats.get('win_rate',0):.1%} | P&L ${stats.get('total_pnl',0):+.2f}
Halted: {risk.get('halted',False)}

POSITIONS:
{pos_lines or '  None'}
RECENT TRADES:
{trade_lines}

RULES: {_n} agents, 2/3 BUY = BUY | no entries 2-3am ET | daily loss limit {MAX_DAILY_LOSS_PCT*100:.0f}%"""


# ══════════════════════════════════════════════════════════════════════════════
# ROW 0 — STATUS BAR
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def row_status_bar():
    is_halted, halt_reason = _live_halt()
    secs = _bot_last_seen()

    if PAPER_TRADING:
        mode_html = '<span class="badge badge-paper">PAPER</span>'
    else:
        mode_html = '<span class="badge badge-live">LIVE — REAL MONEY</span>'

    if secs is None:
        bot_html = '<span style="color:#888;font-size:10px;">⚪ BOT NOT STARTED</span>'
    elif secs > 180:
        bot_html = f'<span style="color:#FF4444;font-size:10px;">🔴 STALE ({int(secs)}s)</span>'
    else:
        bot_html = f'<span style="color:#00C896;font-size:10px;">🟢 RUNNING ({int(secs)}s ago)</span>'

    c1, c2, c3 = st.columns([3, 5, 3])
    with c1:
        st.markdown(
            f'<div style="padding:6px 0;">{mode_html}</div>',
            unsafe_allow_html=True,
        )
    with c2:
        if is_halted:
            st.markdown(
                f'<div class="halt-banner">⛔ HALTED — {halt_reason or "Limit reached"}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="padding:6px 0;text-align:center;">{bot_html}</div>',
                unsafe_allow_html=True,
            )
    with c3:
        st.markdown(
            f'<div style="text-align:right;color:#333;font-size:10px;'
            f'font-family:monospace;padding:8px 0;">{et_now()}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 1 — GIANT P&L SCOREBOARD
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def row_scoreboard():
    c_hdr, c_info = st.columns([20, 1])
    with c_hdr:
        st.markdown('<div class="sec-hdr">P&L SCOREBOARD</div>', unsafe_allow_html=True)
    with c_info:
        _info("Today's net P&L = realized gross profits minus all fees paid today. "
              "Resets to $0 at midnight ET. Gold = above breakeven. Red = in the hole.")

    pnl   = get_todays_pnl(paper=PAPER_TRADING)
    fees  = get_todays_fees(paper=PAPER_TRADING)
    net   = pnl - fees
    stats = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)

    color = '#00C896' if net >= 0 else '#FF4444'
    prefix = '+' if net >= 0 else ''
    bal_color = '#00C896' if real_bal >= ACCOUNT_SIZE else '#FF4444'
    at_color = '#00C896' if stats.get('total_pnl', 0) >= 0 else '#FF4444'

    l, m, r = st.columns([2, 4, 2])
    with m:
        st.markdown(
            f'<div class="scoreboard">'
            f'<span class="score-pnl" style="color:{color};">{prefix}${net:.2f}</span>'
            f'<div class="score-label">TODAY NET P&L</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with l:
        st.markdown(
            f'<div style="padding-top:24px;">'
            f'<div style="color:#333;font-size:9px;text-transform:uppercase;letter-spacing:2px;">Gross</div>'
            f'<div style="color:#888;font-size:16px;font-weight:700;font-family:monospace;">'
            f'{prefix}${pnl:.2f}</div>'
            f'<div style="color:#333;font-size:9px;margin-top:4px;">Fees: −${fees:.2f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with r:
        st.markdown(
            f'<div style="padding-top:24px;text-align:right;">'
            f'<div style="color:#333;font-size:9px;text-transform:uppercase;letter-spacing:2px;">Balance</div>'
            f'<div style="color:{bal_color};font-size:16px;font-weight:700;font-family:monospace;">'
            f'${real_bal:,.2f}</div>'
            f'<div style="color:{at_color};font-size:9px;margin-top:4px;">'
            f'All-time: {("+" if stats.get("total_pnl",0)>=0 else "")}${stats.get("total_pnl",0):.2f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 2 — SIX KEY METRICS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def row_metrics():
    _section(
        "KEY METRICS",
        "Six numbers that matter most. "
        "Win Rate = last 20 trades (rolling). "
        "Edge Score = composite of win rate × profit factor × consistency (0–1 scale; above 0.7 = size up, below 0.3 = size down). "
        "Daily Loss % = how much of today's 4% hard limit you've used. "
        "API Cost = Claude AI spend this month."
    )

    pnl    = get_todays_pnl(paper=PAPER_TRADING)
    fees   = get_todays_fees(paper=PAPER_TRADING)
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    wr20   = get_win_rate(lookback_days=7, paper=PAPER_TRADING)
    mo     = get_monthly_api_cost()
    heat   = get_heat_level(paper=PAPER_TRADING)
    es     = get_edge_state('crypto_macd_consensus', paper=PAPER_TRADING)

    net        = pnl - fees
    daily_pct  = heat.get('pct_drawn', 0.0)
    edge_score = es.get('edge_score', 0.5)

    def _color(v, lo, hi):
        if v >= hi:
            return '#00C896'
        if v <= lo:
            return '#FF4444'
        return '#FDB927'

    def _card(label, value, sub, color='#aaa'):
        return (
            f'<div class="m-card">'
            f'<div class="m-lbl">{label}</div>'
            f'<div class="m-val" style="color:{color};">{value}</div>'
            f'<div class="m-sub">{sub}</div>'
            f'</div>'
        )

    cols = st.columns(6)
    cards = [
        ("BALANCE",       f'${real_bal:,.2f}',
         f'start ${ACCOUNT_SIZE:,.0f}',
         _color(real_bal, ACCOUNT_SIZE * 0.95, ACCOUNT_SIZE * 1.05)),
        ("TODAY NET",     f'{"+" if net>=0 else ""}{net:.2f}',
         f'gross {("+" if pnl>=0 else "")}{pnl:.2f}',
         '#00C896' if net >= 0 else '#FF4444'),
        ("ALL-TIME P&L",  f'{"+" if stats.get("total_pnl",0)>=0 else ""}'
                          f'${stats.get("total_pnl",0):.2f}',
         f'{stats.get("total",0)} trades',
         '#00C896' if stats.get('total_pnl', 0) >= 0 else '#FF4444'),
        ("20-TRADE WR",   f'{wr20:.1%}',
         'need ≥52% for live',
         _color(wr20, 0.45, 0.55)),
        ("EDGE SCORE",    f'{edge_score:.2f}',
         es.get('status', 'UNCERTAIN'),
         _color(edge_score, 0.3, 0.7)),
        ("DAILY LOSS",    f'{daily_pct:.1%}',
         f'limit {MAX_DAILY_LOSS_PCT:.0%}',
         _color(MAX_DAILY_LOSS_PCT - daily_pct, 0.005, 0.02)),
    ]
    for col, (lbl, val, sub, clr) in zip(cols, cards):
        with col:
            st.markdown(_card(lbl, val, sub, clr), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ROW 3 — EDGE MONITOR (most important component)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def row_edge_monitor():
    _section(
        "EDGE MONITOR",
        "⚠️ Most important component in the system. "
        "Tracks rolling 20-trade performance per market. "
        "When edge_score < 0.30 for 2 consecutive windows: position sizing automatically cut 50%. "
        "When edge_score > 0.70 for 2 consecutive windows: sizing scales toward Kelly max. "
        "STRONG (≥0.70) · NORMAL (0.40–0.70) · FADING (0.30–0.40) · DEGRADED (<0.30). "
        "Sizing multiplier shows what fraction of normal size the system is currently using.",
        accent=True,
    )

    MARKETS = [
        ('CRYPTO',       'crypto_macd_consensus'),
        ('MES FUTURES',  'futures_scalper'),
        ('PERP',         'crypto_perp'),
    ]

    STATUS_COLORS = {
        'STRONG':    '#00C896',
        'NORMAL':    '#FDB927',
        'FADING':    '#FF8C00',
        'DEGRADED':  '#FF4444',
        'UNCERTAIN': '#555',
    }

    cols = st.columns(3)
    for col, (mkt_name, strat_key) in zip(cols, MARKETS):
        es = get_edge_state(strat_key, paper=PAPER_TRADING)
        status    = es.get('status', 'UNCERTAIN')
        score     = es.get('edge_score', 0.5)
        wr20      = es.get('win_rate_20', 0.5)
        pf        = es.get('profit_factor', 1.0)
        mult      = es.get('sizing_multiplier', 1.0)
        n_trades  = es.get('window_trades', 0)
        consec    = es.get('consecutive_bad', 0)
        clr       = STATUS_COLORS.get(status, '#555')
        bar_pct   = min(100, int(score * 100))

        with col:
            st.markdown(
                f'<div class="edge-panel">'
                f'<div class="edge-market">{mkt_name}</div>'
                f'<div class="edge-status" style="color:{clr};">{status}</div>'
                f'<div class="edge-bar-bg">'
                f'<div class="edge-bar-fill" style="width:{bar_pct}%;background:{clr};"></div>'
                f'</div>'
                f'<div class="edge-stat">Score: <span style="color:#aaa;">{score:.2f}</span>'
                f' &nbsp; WR-20: <span style="color:#aaa;">{wr20:.1%}</span></div>'
                f'<div class="edge-stat">PF: <span style="color:#aaa;">{pf:.2f}</span>'
                f' &nbsp; Size: <span style="color:#aaa;">{mult:.0%}</span>'
                f' &nbsp; Trades: <span style="color:#aaa;">{n_trades}</span></div>'
                + (f'<div class="edge-stat" style="color:#FF4444;margin-top:4px;">'
                   f'⚠ {consec} consecutive bad windows</div>' if consec >= 2 else '')
                + '</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 4 — OPEN POSITIONS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def row_positions():
    _section(
        "OPEN POSITIONS",
        "All active positions with real-time stop and target distances. "
        "% to Stop = how far price must move against you before the stop triggers. "
        "% to Target = how far to take-profit. "
        "Age = how long the position has been open. "
        "Stops are never moved wider after entry (hard rule)."
    )

    positions = _live_positions()

    if not positions:
        st.markdown('<div class="pos-empty">No open positions</div>', unsafe_allow_html=True)
        return

    for strat, syms in positions.items():
        for sym, p in syms.items():
            entry   = float(p.get('entry', 0))
            stop    = float(p.get('stop', 0))
            target  = float(p.get('target', 0))
            ts      = p.get('ts_entry', '')
            direc   = p.get('direction', 'LONG')

            # Age
            age_str = '—'
            try:
                from datetime import timezone as _tz
                dt = datetime.fromisoformat(ts)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=_tz.utc)
                mins = int((datetime.now(_tz.utc) - dt).total_seconds() / 60)
                age_str = f'{mins}m' if mins < 60 else f'{mins//60}h {mins%60}m'
            except Exception:
                pass

            # Distances
            if entry > 0 and stop > 0:
                to_stop   = abs(entry - stop) / entry * 100
                to_target = abs(target - entry) / entry * 100 if target else 0
            else:
                to_stop = to_target = 0

            stop_clr   = '#FF4444' if to_stop < 0.5 else '#888'
            target_clr = '#00C896'

            st.markdown(
                f'<div class="pos-row">'
                f'<span class="pos-symbol">{sym}</span>'
                f'<span class="pos-detail">{strat} · {direc}</span>'
                f'<span class="pos-detail">Entry: {entry:.5g}</span>'
                f'<span style="color:{stop_clr};font-size:10px;font-family:monospace;">'
                f'Stop: {to_stop:.2f}% away</span>'
                f'<span style="color:{target_clr};font-size:10px;font-family:monospace;">'
                f'Target: {to_target:.2f}% away</span>'
                f'<span class="pos-detail">{age_str}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 5 — THREE MARKET PANELS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def row_market_panels():
    _section(
        "MARKETS",
        "Per-market status panel. Shows current position count, the most recent AI debate signal, "
        "and the most recent completed trade. Each market runs its own strategy and edge monitor independently."
    )

    positions = _live_positions()
    recent    = get_recent_trades(limit=3, paper=PAPER_TRADING)
    signals   = get_todays_signals(paper=PAPER_TRADING) or []

    def _last_signal(strategy_key: str) -> str:
        for s in signals:
            if strategy_key.lower() in str(s.get('strategy', '')).lower():
                action = s.get('action', '')
                sym    = s.get('symbol', '')
                ts     = fmt_ts(s.get('ts', ''), short=True)
                return f'{action} {sym} @ {ts}'
        return '—'

    def _last_trade(strategy_key: str) -> str:
        for t in recent:
            if strategy_key.lower() in str(t.get('strategy', '')).lower():
                sym  = t.get('symbol', '')
                pnl  = t.get('pnl_usd', 0)
                ts   = fmt_ts(t.get('ts', ''), short=True)
                sign = '+' if pnl >= 0 else ''
                clr  = '#00C896' if pnl >= 0 else '#FF4444'
                return f'{sym} <span style="color:{clr};">{sign}${pnl:.2f}</span> @ {ts}'
        return '—'

    PANELS = [
        ('CRYPTO',      'crypto',   'Coinbase · 1-min candles · up to 5 positions'),
        ('MES FUTURES', 'futures',  'Tradovate · MES opening range breakout · yfinance prices in paper'),
        ('PERP',        'perp',     'Binance USD-M perp · funding rate aware · 4h flat exit'),
    ]

    cols = st.columns(3)
    for col, (title, key, note) in zip(cols, PANELS):
        pos_count = sum(
            1 for strat, syms in positions.items()
            if key in strat.lower()
            for _ in syms
        )
        sig_html   = _last_signal(key)
        trade_html = _last_trade(key)
        es         = get_edge_state(
            'crypto_macd_consensus' if key == 'crypto' else
            'futures_scalper'       if key == 'futures' else
            'crypto_perp',
            paper=PAPER_TRADING,
        )
        edge_score = es.get('edge_score', 0.5)
        bar_pct    = min(100, int(edge_score * 100))
        bar_clr    = ('#00C896' if edge_score >= 0.7 else
                      '#FDB927' if edge_score >= 0.4 else '#FF4444')

        with col:
            st.markdown(
                f'<div class="mkt-panel">'
                f'<div class="mkt-hdr">{title}</div>'
                f'<div class="mkt-row">Positions: <span class="mkt-val">{pos_count}</span></div>'
                f'<div class="mkt-row">Last signal: <span class="mkt-val">{sig_html}</span></div>'
                f'<div class="mkt-row">Last trade: <span class="mkt-val">{trade_html}</span></div>'
                f'<div style="margin-top:8px;">'
                f'<div style="color:#333;font-size:9px;margin-bottom:3px;">EDGE</div>'
                f'<div class="edge-bar-bg">'
                f'<div class="edge-bar-fill" style="width:{bar_pct}%;background:{bar_clr};"></div>'
                f'</div>'
                f'</div>'
                f'<div style="color:#222;font-size:9px;margin-top:6px;">{note}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 6 — RISK GAUGES
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def row_risk_gauges():
    _section(
        "RISK STATUS",
        "Daily loss gauge: fills as you approach the 4% hard limit. At 100%, all trading halts automatically. "
        "Heat levels: NORMAL → CAUTION (75% size) → WARNING (50%) → DANGER (25%) → HALT (0%). "
        "Fee drag: daily fees as % of account — limit is 10% ($50 on $500). "
        "Watchdog: time since last bot activity."
    )

    heat     = get_heat_level(paper=PAPER_TRADING)
    fees     = get_todays_fees(paper=PAPER_TRADING)
    secs     = _bot_last_seen()
    rm       = get_risk_manager()
    risk     = rm.status_report()
    stats    = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = max(ACCOUNT_SIZE + stats.get('total_pnl', 0), 1.0)

    daily_loss_pct = heat.get('pct_drawn', 0.0)
    daily_limit    = MAX_DAILY_LOSS_PCT
    daily_fill     = min(100, int(daily_loss_pct / daily_limit * 100)) if daily_limit > 0 else 0
    daily_clr      = ('#00C896' if daily_fill < 50 else
                      '#FDB927' if daily_fill < 75 else
                      '#FF8C00' if daily_fill < 90 else '#FF4444')

    fee_pct  = fees / real_bal
    fee_fill = min(100, int(fee_pct / MAX_DAILY_FEE_DRAG_PCT * 100)) if MAX_DAILY_FEE_DRAG_PCT > 0 else 0
    fee_clr  = ('#00C896' if fee_fill < 50 else
                '#FDB927' if fee_fill < 75 else '#FF4444')

    wdog_fill = 0
    wdog_clr  = '#00C896'
    wdog_lbl  = 'OK'
    if secs is None:
        wdog_fill, wdog_clr, wdog_lbl = 100, '#888', 'NOT STARTED'
    elif secs > 900:
        wdog_fill, wdog_clr, wdog_lbl = 100, '#FF4444', f'STALE {int(secs)}s'
    elif secs > 300:
        wdog_fill, wdog_clr, wdog_lbl = int(secs / 9), '#FF8C00', f'{int(secs)}s'
    else:
        wdog_fill, wdog_lbl = int(secs / 9), f'{int(secs)}s ago'

    HEAT_COLORS = {
        'NORMAL': '#00C896', 'CAUTION': '#FDB927',
        'WARNING': '#FF8C00', 'DANGER': '#FF4444', 'HALT': '#FF1744',
    }
    heat_label = heat.get('label', 'NORMAL')
    heat_clr   = HEAT_COLORS.get(heat_label, '#555')

    def _gauge(label, fill_pct, color, right_label):
        return (
            f'<div class="gauge-wrap">'
            f'<div class="gauge-lbl"><span>{label}</span><span style="color:{color};">{right_label}</span></div>'
            f'<div class="gauge-bg">'
            f'<div class="gauge-fill" style="width:{fill_pct}%;background:{color};"></div>'
            f'</div>'
            f'</div>'
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            _gauge(f'Daily Loss', daily_fill,
                   daily_clr, f'{daily_loss_pct:.1%} / {daily_limit:.0%}'),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="gauge-wrap">'
            f'<div class="gauge-lbl"><span>Heat Level</span>'
            f'<span style="color:{heat_clr};">{heat_label}</span></div>'
            f'<div class="gauge-bg"><div class="gauge-fill" style="'
            f'width:{int((heat.get("level",0)/4)*100)}%;background:{heat_clr};"></div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _gauge('Fee Drag', fee_fill, fee_clr,
                   f'${fees:.2f} ({fee_pct:.1%})'),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            _gauge('Watchdog', wdog_fill, wdog_clr, wdog_lbl),
            unsafe_allow_html=True,
        )

    # Position counts
    open_pos = risk.get('open_positions', 0)
    max_pos  = MAX_POSITIONS_CRYPTO
    st.markdown(
        f'<div style="color:#333;font-size:10px;font-family:monospace;margin-top:4px;">'
        f'Positions: {open_pos}/{max_pos} crypto · '
        f'Deployed: {risk.get("deployed_pct", 0):.1%} · '
        f'Size factor: {heat.get("size_factor",1.0):.0%}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 7 — RECENT TRADES + SIGNALS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def row_trades_signals():
    left, right = st.columns(2)

    with left:
        _section(
            "RECENT TRADES",
            "Last 20 closed trades. P&L is net after fees. "
            "Green = winner, Red = loser. "
            "Win rate and profit factor feed directly into the edge monitor.",
        )
        trades = get_recent_trades(limit=20, paper=PAPER_TRADING) or []
        if not trades:
            st.markdown('<div style="color:#222;font-size:11px;">No trades yet</div>',
                        unsafe_allow_html=True)
        else:
            rows_html = ''
            for t in trades:
                pnl   = t.get('pnl_usd', 0)
                clr   = '#00C896' if pnl > 0 else '#FF4444' if pnl < 0 else '#555'
                sign  = '+' if pnl > 0 else ''
                sym   = t.get('symbol', '—')
                act   = t.get('action', '')
                ts    = fmt_ts(t.get('ts', ''), short=True)
                rows_html += (
                    f'<div class="t-row">'
                    f'<span style="color:#555;min-width:60px;">{ts}</span>'
                    f'<span style="color:#888;min-width:100px;">{sym}</span>'
                    f'<span style="color:#555;min-width:40px;font-size:9px;">{act}</span>'
                    f'<span style="color:{clr};margin-left:auto;">{sign}${pnl:.2f}</span>'
                    f'</div>'
                )
            st.markdown(rows_html, unsafe_allow_html=True)

    with right:
        _section(
            "RECENT SIGNALS",
            "All signals generated today (acted on and filtered out). "
            "BUY = debate voted 2/3 BUY. HOLD = debate blocked it. "
            "Watching HOLD signals helps you understand what the system is rejecting and why.",
        )
        signals = get_scan_feed(limit=30) or []
        if not signals:
            st.markdown('<div style="color:#222;font-size:11px;">No signals yet</div>',
                        unsafe_allow_html=True)
        else:
            rows_html = ''
            for s in signals:
                action = str(s.get('action', s.get('signal', ''))).upper()
                sym    = s.get('symbol', '—')
                ts     = fmt_ts(s.get('ts', ''), short=True)
                conf   = s.get('confidence', '')
                clr    = ('#00C896' if action == 'BUY' else
                          '#FF4444' if action == 'SELL' else '#555')
                conf_str = f' · {float(conf):.0%}' if conf else ''
                rows_html += (
                    f'<div class="s-row">'
                    f'<span style="color:#444;min-width:60px;">{ts}</span>'
                    f'<span style="color:{clr};min-width:40px;font-weight:700;">{action}</span>'
                    f'<span style="color:#888;">{sym}{conf_str}</span>'
                    f'</div>'
                )
            st.markdown(rows_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ROW 8 — CLAUDE AI CHAT
# ══════════════════════════════════════════════════════════════════════════════

def row_chat():
    c_hdr, c_info = st.columns([20, 1])
    with c_hdr:
        st.markdown('<div class="sec-hdr">CLAUDE AI CHAT</div>', unsafe_allow_html=True)
    with c_info:
        _info("Ask Claude anything about the portfolio, signals, or strategy. "
              "Full system state is injected automatically: positions, P&L, recent trades, "
              "risk rules, win rate, edge scores. Claude has the same data you see on this dashboard.")

    if 'chat_msgs' not in st.session_state:
        st.session_state['chat_msgs'] = []

    for msg in st.session_state['chat_msgs'][-10:]:
        css = 'chat-user' if msg['role'] == 'user' else 'chat-bot'
        st.markdown(
            f'<div class="{css}">{msg["content"]}</div>',
            unsafe_allow_html=True,
        )

    prompt = st.chat_input("Ask about your portfolio…")
    if prompt:
        st.session_state['chat_msgs'].append({'role': 'user', 'content': prompt})
        ctx  = build_context()
        hist = [{'role': m['role'], 'content': m['content']}
                for m in st.session_state['chat_msgs']]
        with st.spinner(''):
            reply = call_claude(hist, ctx)
        st.session_state['chat_msgs'].append({'role': 'assistant', 'content': reply})
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# EXPANDERS — below the fold
# ══════════════════════════════════════════════════════════════════════════════

def expander_debates():
    with st.expander("AI DEBATE HISTORY"):
        _info("Full reasoning from each 3-agent debate. Bardock = macro/funding. "
              "Vegeta = technical momentum. Krillin = trade economics/fee math. "
              "2/3 BUY = trade taken. Any HOLD = skipped.")
        debates = get_recent_debates(limit=10) or []
        if not debates:
            st.write("No debates yet.")
            return
        for d in debates:
            sym   = d.get('symbol', '?')
            ts    = fmt_ts(d.get('ts', ''))
            res   = d.get('result', '?').upper()
            conf  = d.get('confidence', 0)
            rsn   = d.get('reason', '')
            clr   = '#00C896' if res == 'BUY' else '#FF4444' if res == 'SELL' else '#888'
            st.markdown(
                f'**{ts}** · `{sym}` · '
                f'<span style="color:{clr};font-weight:700;">{res}</span> '
                f'({conf:.0%}) — {rsn[:120]}',
                unsafe_allow_html=True,
            )

def expander_notifications():
    with st.expander("SYSTEM NOTIFICATIONS"):
        _info("All system events: trade opens/closes, halts, errors, watchdog alerts. "
              "ERROR and HALT level events are critical — investigate immediately.")
        notifs = get_recent_notifications(limit=40) or []
        if not notifs:
            st.write("No notifications.")
            return
        for n in notifs:
            level = n.get('level', 'INFO')
            msg   = n.get('message', '')
            ts    = fmt_ts(n.get('ts', ''))
            clr   = ('#FF4444' if level in ('ERROR', 'HALT') else
                     '#FF8C00' if level == 'WARNING' else '#555')
            st.markdown(
                f'<div style="font-family:monospace;font-size:10px;color:{clr};padding:2px 0;">'
                f'[{ts}] [{level}] {msg}</div>',
                unsafe_allow_html=True,
            )

def expander_controls():
    with st.expander("CONTROLS"):
        _info("Bot management and config overrides. Changes to .env take effect on the next scan cycle. "
              "Starting/stopping the bot from here uses subprocess — check the Watchdog gauge to confirm.")
        st.markdown('<div style="color:#555;font-size:10px;">Bot management</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Start Bot (paper)"):
                import subprocess
                subprocess.Popen(
                    ['python3', 'main.py', '--mode', 'paper'],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    start_new_session=True,
                )
                st.success("Bot started.")
        with c2:
            if st.button("Kill Bot"):
                import subprocess
                subprocess.run(['pkill', '-f', 'main.py'])
                st.warning("Kill signal sent.")
        with c3:
            if st.button("Backup DB"):
                import subprocess
                result = subprocess.run(
                    ['bash', 'scripts/backup_db.sh'],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    st.success("Backup complete.")
                else:
                    st.error(result.stderr[:200])

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.markdown('<div style="color:#555;font-size:10px;margin-bottom:8px;">Config overrides (writes .env)</div>',
                    unsafe_allow_html=True)
        c4, c5 = st.columns(2)
        with c4:
            new_size = st.number_input(
                'Crypto position size ($)',
                min_value=10.0, max_value=5000.0,
                value=float(CRYPTO_POSITION_SIZE_USD), step=10.0,
            )
            if st.button("Update position size"):
                _write_env({'CRYPTO_POSITION_SIZE_USD': str(new_size)})
                st.success(f"Set to ${new_size:.0f}. Restart bot to apply.")
        with c5:
            st.metric("Max risk/trade",    f"{MAX_RISK_PER_TRADE_PCT:.1%}",
                      help="Fraction of account risked per trade")
            st.metric("Taker fee",         f"{COINBASE_TAKER_FEE_PCT:.3%}",
                      help="Coinbase taker fee used in P&L math")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    row_status_bar()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_scoreboard()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_metrics()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_edge_monitor()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_positions()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_market_panels()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_risk_gauges()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_trades_signals()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_chat()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    expander_debates()
    expander_notifications()
    expander_controls()


main()
