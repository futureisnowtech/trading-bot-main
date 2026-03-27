"""
dashboard/app.py — The King's War Room v9.0

ONE VIEW. One toggle. That is all.

THE KING (standard): Lakers gold on navy on black.
  Row 1 — Giant P&L scoreboard. Gold positive. Red negative.
  Row 2 — LeBron quote rotating through the day.
  Row 3 — Six metrics: balance, today P&L, all-time P&L, 20-trade WR, edge score, API cost.
  Row 4 — Three market panels: crypto / MES futures / perp. Position + last signal + last trade.
  Row 5 — Recent trades table | Recent signals feed.
  Row 6 — Claude AI chat (full context injected).
  Row 7 — Risk gauges: daily loss bar, position counts, watchdog.

SAIYAN MODE: Same data. DBZ skin. Power levels. Ki bars. Z-Fighter agent names.

One button top-right toggles the entire UI.
Two modes total. Nothing else.

Run: streamlit run dashboard/app.py
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
    EQUITY_ENABLED, CRYPTO_ENABLED, FUTURES_ENABLED,
    MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT, MAX_STRATEGY_LOSS_STREAK,
    CRYPTO_SCAN_INTERVAL_SECONDS,
    MAX_RISK_PER_TRADE_PCT, MAX_POSITIONS_CRYPTO,
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    FULL_DEBATE_AGENTS,
    CRYPTO_POSITION_SIZE_USD, EQUITY_POSITION_SIZE_USD,
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
        return {'status': 'UNCERTAIN', 'edge_score': 0.5, 'profit_factor': 1.5,
                'consecutive_bad': 0, 'sizing_multiplier': 1.0, 'should_block': False,
                'window_trades': 0}

try:
    from risk.drawdown_controller import get_heat_level
except Exception:
    def get_heat_level(paper=True):
        return {'level': 0, 'label': 'NORMAL', 'size_factor': 1.0, 'daily_pnl': 0.0, 'pct_drawn': 0.0}

# ── LeBron quotes — REAL documented quotes only ───────────────────────────────
# Keyed by context so the right quote fires at the right moment.
LEBRON = {
    'startup':   "We're in the lab. Let's get to work.",
    'win':       "That's preparation meeting opportunity.",
    'loss':      "Losses are tuition. On to the next.",
    'halt':      "Not today. Live to play tomorrow.",
    'goal':      "We came, we worked, we're done.",
    'high':      "This is what the work looks like.",
    'edge_on':   "Nothing is given. Everything is earned.",
    'edge_off':  "I like criticism. It makes you strong.",
    'overnight': "I treated every day like my last.",
    'patience':  "Sometimes the best move is no move.",
}

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="👑 The King's War Room",
    page_icon='👑',
    layout='wide',
    initial_sidebar_state='collapsed',
)

# ─── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base ── */
.main, .stApp { background: #000 !important; }
* { box-sizing: border-box; }
.stButton > button {
    background: #0d1e3a; color: #FDB927; border: 1px solid #FDB927;
    font-weight: 700; border-radius: 4px; font-size: 12px;
    padding: 4px 12px;
}
.stButton > button:hover { background: #FDB927; color: #000; }
.stExpander { border: 1px solid #111 !important; }
div[data-testid="stChatInput"] { border-color: #1D428A !important; }

/* ── GIANT P&L SCOREBOARD ── */
.scoreboard {
    text-align: center;
    padding: 24px 0 10px 0;
    font-family: 'Impact', 'Arial Black', sans-serif;
    letter-spacing: 2px;
    line-height: 1;
}
.scoreboard-pnl {
    font-size: 72px;
    font-weight: 900;
    display: block;
}
.scoreboard-sub {
    font-size: 14px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-top: 4px;
}

/* ── LeBron quote banner ── */
.quote-bar {
    border-left: 3px solid #FDB927;
    padding: 8px 16px;
    margin: 8px 0 14px 0;
    background: #0a0f1a;
    border-radius: 0 4px 4px 0;
}
.quote-text {
    color: #FDB927;
    font-size: 16px;
    font-style: italic;
    font-weight: 700;
}
.quote-attr {
    color: #444;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-top: 3px;
}

/* ── Metric pill ── */
.metric-pill {
    background: #050a14;
    border: 1px solid #1a2a3a;
    border-radius: 6px;
    padding: 10px 12px;
    text-align: center;
}
.metric-pill-lbl {
    color: #444;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 4px;
}
.metric-pill-val {
    font-size: 20px;
    font-weight: 900;
    font-family: 'Impact', sans-serif;
}
.metric-pill-sub {
    color: #333;
    font-size: 10px;
    margin-top: 3px;
}

/* ── Market panel ── */
.market-panel {
    background: #030912;
    border: 1px solid #0d1e3a;
    border-radius: 6px;
    padding: 12px 14px;
    min-height: 130px;
}
.mp-header {
    color: #FDB927;
    font-size: 10px;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 3px;
    border-bottom: 1px solid #0d1e3a;
    padding-bottom: 6px;
    margin-bottom: 8px;
}
.mp-position {
    background: #0d1e3a22;
    border-left: 2px solid #FDB927;
    padding: 5px 8px;
    border-radius: 0 3px 3px 0;
    font-size: 11px;
    margin-bottom: 5px;
}
.mp-empty {
    color: #222;
    font-size: 11px;
    font-style: italic;
    padding: 8px 0;
}

/* ── Trade table ── */
.trade-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    border-bottom: 1px solid #080808;
    font-family: monospace;
    font-size: 11px;
}

/* ── Signal feed ── */
.sig-row {
    padding: 3px 0;
    border-bottom: 1px solid #080808;
    font-family: monospace;
    font-size: 11px;
    display: flex;
    gap: 6px;
}

/* ── Risk gauge ── */
.gauge-bar-bg {
    background: #0a0a0a;
    border-radius: 3px;
    height: 8px;
    overflow: hidden;
    margin: 4px 0;
}
.gauge-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.3s;
}

/* ── Halt banner ── */
.halt-banner {
    background: #FF1744;
    color: #fff;
    padding: 10px 16px;
    border-radius: 4px;
    text-align: center;
    font-weight: 900;
    font-size: 15px;
    letter-spacing: 2px;
    margin: 6px 0;
}
.paper-banner {
    background: #0d1e3a;
    color: #FDB927;
    padding: 5px 16px;
    border-radius: 4px;
    text-align: center;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 2px;
    margin-bottom: 6px;
}

/* ── Chat ── */
.chat-user {
    background: #0d1e3a;
    color: #ddd;
    padding: 8px 12px;
    border-radius: 12px 12px 4px 12px;
    margin: 4px 0;
    font-size: 13px;
}
.chat-bot {
    background: #0a0a0a;
    color: #ccc;
    padding: 8px 12px;
    border-radius: 12px 12px 12px 4px;
    margin: 4px 0;
    font-size: 13px;
    border-left: 3px solid #FDB927;
}

/* ── SAIYAN overrides ── */
.saiyan .main, .saiyan .stApp { background: #050005 !important; }
.saiyan-score { font-family: 'Impact', sans-serif; font-size: 72px; font-weight: 900;
    text-align: center; text-shadow: 0 0 30px currentColor; }
.ki-track { background: #0a0005; border-radius: 20px; height: 10px;
    overflow: hidden; margin: 3px 0; }
.ki-fill { height: 100%; border-radius: 20px; }
.warrior-card {
    border-radius: 8px;
    padding: 12px;
    font-size: 11px;
}
.w-bardock { background: #1a0005; border: 1px solid #8B0000; }
.w-vegeta  { background: #00001a; border: 1px solid #1D428A; }
.w-krillin { background: #1a0d00; border: 1px solid #FF8C00; }

/* ── Animations ── */
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
@keyframes gold-glow { 0%,100%{text-shadow:0 0 10px #FDB92755} 50%{text-shadow:0 0 25px #FDB927cc} }
.live-dot { animation: pulse 2s infinite; }
.gold-glow { animation: gold-glow 3s ease-in-out infinite; }

/* ── Divider ── */
.divider { border-top: 1px solid #0d0d0d; margin: 12px 0; }

/* ── Popover ── */
div[data-testid="stPopover"] > button {
    background: transparent !important; border: 1px solid #1a1a1a !important;
    color: #333 !important; padding: 0 !important;
    min-height: 18px !important; height: 18px !important; width: 18px !important;
    border-radius: 50% !important; font-size: 10px !important;
}
</style>
""", unsafe_allow_html=True)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def et_now() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%b %-d  %-I:%M:%S %p ET')

def fmt_ts(ts: str, short: bool = False) -> str:
    if not ts:
        return ''
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

def _quote(pnl: float, is_halted: bool, edge_status: str, hour: int) -> str:
    if is_halted:
        return LEBRON['halt']
    if pnl > 10:
        return LEBRON['win']
    if pnl < -5:
        return LEBRON['loss']
    if edge_status == 'STRONG':
        return LEBRON['edge_on']
    if edge_status in ('DEGRADED', 'BLOCKED'):
        return LEBRON['edge_off']
    if hour < 6:
        return LEBRON['overnight']
    return [LEBRON['startup'], LEBRON['patience'], LEBRON['goal']][hour % 3]

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
    pos   = _live_positions()
    pnl   = get_todays_pnl(paper=PAPER_TRADING)
    fees  = get_todays_fees(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)
    rm    = get_risk_manager()
    risk  = rm.status_report()
    wr14  = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    mo    = get_monthly_api_cost()
    recent = get_recent_trades(limit=10, paper=PAPER_TRADING)

    pos_lines = ''
    for strat, syms in pos.items():
        for sym, p in syms.items():
            pos_lines += f"  {strat} {sym} entry=${p['entry']:.4f} stop=${p['stop']:.4f}\n"

    trade_lines = '\n'.join(
        f"  {fmt_ts(t.get('ts',''))} {t.get('action','')} {t.get('symbol','')} P&L=${t.get('pnl_usd',0):+.4f}"
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
# ROW 1 — GIANT P&L SCOREBOARD
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def row_scoreboard(saiyan: bool):
    pnl      = get_todays_pnl(paper=PAPER_TRADING)
    fees     = get_todays_fees(paper=PAPER_TRADING)
    net      = pnl - fees
    stats    = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    secs     = _bot_last_seen()
    is_halted, halt_reason = _live_halt()

    color = '#00C853' if net >= 0 else '#FF1744'

    # Mode banner
    if PAPER_TRADING:
        st.markdown(
            '<div class="paper-banner">📄 PAPER TRADING — Simulated capital</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="halt-banner" style="background:#c60020;">💰 LIVE — REAL MONEY</div>',
            unsafe_allow_html=True,
        )

    if is_halted:
        st.markdown(
            f'<div class="halt-banner">⛔ HALTED — {halt_reason or "Limit reached"}</div>',
            unsafe_allow_html=True,
        )

    # Bot alive indicator
    if secs is None:
        bot_html = '<span style="color:#FF8F00;">🟡 STARTING</span>'
    elif secs > 180:
        bot_html = f'<span style="color:#FF1744;" class="live-dot">🔴 STALE {int(secs)}s ago</span>'
    else:
        bot_html = (f'<span style="color:#00C853;font-weight:900;">🟢</span>'
                    f'<span style="color:#333;font-size:10px;margin-left:4px;">{int(secs)}s ago</span>')

    left_col, mid_col, right_col = st.columns([2, 4, 2])

    with left_col:
        st.markdown(
            f'<div style="padding-top:20px;">'
            f'{bot_html}'
            f'<div style="color:#222;font-size:9px;font-family:monospace;margin-top:4px;">{et_now()}</div>'
            f'</div>', unsafe_allow_html=True,
        )

    with mid_col:
        if saiyan:
            # DBZ power level display
            power = max(1, int(abs(net) * 1000 + 9001))
            label = "POWER LEVEL"
            glow = 'gold-glow' if net >= 0 else ''
            st.markdown(
                f'<div class="scoreboard {glow}">'
                f'<span class="scoreboard-pnl" style="color:{color};">{power:,}</span>'
                f'<div class="scoreboard-sub" style="color:{color}88;">{label}</div>'
                f'</div>', unsafe_allow_html=True,
            )
        else:
            prefix = '+' if net >= 0 else ''
            glow = 'gold-glow' if net > 5 else ''
            st.markdown(
                f'<div class="scoreboard {glow}">'
                f'<span class="scoreboard-pnl" style="color:{color};">{prefix}${net:.2f}</span>'
                f'<div class="scoreboard-sub">TODAY NET P&L</div>'
                f'</div>', unsafe_allow_html=True,
            )

    with right_col:
        bal_color = '#00C853' if real_bal >= ACCOUNT_SIZE else '#FF1744'
        st.markdown(
            f'<div style="padding-top:20px;text-align:right;">'
            f'<div style="color:#333;font-size:9px;text-transform:uppercase;letter-spacing:2px;">Balance</div>'
            f'<div style="color:{bal_color};font-size:22px;font-weight:900;">${real_bal:,.2f}</div>'
            f'<div style="color:#222;font-size:10px;">gross ${pnl:+.2f} · fees −${fees:.2f}</div>'
            f'</div>', unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 2 — LEBRON QUOTE
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=60)
def row_quote(saiyan: bool):
    pnl      = get_todays_pnl(paper=PAPER_TRADING)
    is_halted, _ = _live_halt()
    hour     = datetime.now(pytz.timezone(MARKET_TIMEZONE)).hour
    es       = get_edge_state('crypto_macd_consensus', paper=PAPER_TRADING)
    quote    = _quote(pnl, is_halted, es['status'], hour)

    if saiyan:
        st.markdown(
            f'<div style="border-left:3px solid #FDB927;padding:6px 14px;margin:6px 0 12px 0;'
            f'background:#0a0005;">'
            f'<div style="color:#FDB927;font-size:14px;font-style:italic;font-weight:700;">'
            f'⚡ "{quote}"</div>'
            f'<div style="color:#333;font-size:9px;letter-spacing:3px;margin-top:2px;">'
            f'— THE KING · SAIYAN TRANSMISSION</div>'
            f'</div>', unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="quote-bar">'
            f'<div class="quote-text">"{quote}"</div>'
            f'<div class="quote-attr">— LeBron James</div>'
            f'</div>', unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 3 — SIX METRICS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def row_six_metrics(saiyan: bool):
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    wr14   = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    monthly = get_monthly_api_cost()
    es     = get_edge_state('crypto_macd_consensus', paper=PAPER_TRADING)

    balance  = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    pnl_today = get_todays_pnl(paper=PAPER_TRADING)
    fees_today = get_todays_fees(paper=PAPER_TRADING)
    total_pnl = stats.get('total_pnl', 0)
    edge_score = es.get('edge_score', 0.5)
    edge_status = es.get('status', 'UNCERTAIN')

    def _col(color): return color
    bal_c    = '#00C853' if balance >= ACCOUNT_SIZE else '#FF1744'
    pnl_c    = '#00C853' if pnl_today >= 0 else '#FF1744'
    all_c    = '#00C853' if total_pnl >= 0 else '#FF1744'
    wr_c     = '#00C853' if wr14 >= 0.52 else '#FDB927' if wr14 >= 0.45 else '#FF1744'
    edge_c   = {'STRONG':'#00C853','OK':'#FDB927','DEGRADED':'#FF8F00',
                'BLOCKED':'#FF1744','UNCERTAIN':'#555'}.get(edge_status,'#555')
    api_c    = '#00C853' if monthly < 5 else '#FF8F00'

    METRICS = [
        ('ACCOUNT BALANCE', f'${balance:,.2f}', f'+${ACCOUNT_SIZE:,.0f} base', bal_c),
        ('TODAY P&L',       f'${pnl_today:+.2f}', f'net after ${fees_today:.2f} fees', pnl_c),
        ('ALL-TIME P&L',    f'${total_pnl:+.2f}', f'{stats.get("total",0)} trades', all_c),
        ('20-TRADE WIN RATE', f'{wr14:.1%}',        f'{stats.get("wins",0)}W/{stats.get("losses",0)}L', wr_c),
        ('EDGE SCORE',      f'{edge_score:.2f}',    edge_status, edge_c),
        ('API COST / MO',   f'${monthly:.2f}',      '← keep this low', api_c),
    ]

    if saiyan:
        DBZ_NAMES = ['POWER LEVEL', 'KI GAINED', 'BATTLE RECORD', 'WIN RATIO', 'EDGE POWER', 'SPIRIT COST']
        METRICS = [(DBZ_NAMES[i], v, s, c) for i, (_, v, s, c) in enumerate(METRICS)]

    cols = st.columns(6)
    for i, (lbl, val, sub, color) in enumerate(METRICS):
        with cols[i]:
            st.markdown(
                f'<div class="metric-pill">'
                f'<div class="metric-pill-lbl">{lbl}</div>'
                f'<div class="metric-pill-val" style="color:{color};">{val}</div>'
                f'<div class="metric-pill-sub">{sub}</div>'
                f'</div>', unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 4 — THREE MARKET PANELS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def row_markets(saiyan: bool):
    pos      = _live_positions()
    debates  = get_recent_debates(limit=3)
    recent   = get_recent_trades(limit=20, paper=PAPER_TRADING)
    is_halted, _ = _live_halt()

    # Get last debate / last trade per market
    def _last_debate(market_keys):
        for d in debates:
            strat = d.get('strategy', '') or ''
            sym   = d.get('symbol', '') or ''
            if any(k in strat or k in sym.lower() for k in market_keys):
                return d
        return debates[0] if debates else {}

    def _last_trade(market_keys):
        for t in recent:
            s = (t.get('strategy') or '').lower()
            if any(k in s for k in market_keys):
                return t
        return {}

    MARKETS = [
        {
            'name':   '₿ CRYPTO',
            'saiyan': '⚡ BARDOCK SECTOR',
            'keys':   ['crypto', 'btc', 'eth', 'sol', 'usdc'],
            'strats': ['crypto', 'perp'],
            'color':  '#FDB927',
            'border': '#1D428A',
        },
        {
            'name':   '📈 MES FUTURES',
            'saiyan': '🔵 VEGETA SECTOR',
            'keys':   ['futures', 'mes', 'scalper'],
            'strats': ['futures_scalper'],
            'color':  '#4488FF',
            'border': '#1D428A',
        },
        {
            'name':   '⚡ PERP / OTHER',
            'saiyan': '🟠 KRILLIN SECTOR',
            'keys':   ['perp', 'mean', 'reversion'],
            'strats': ['perp', 'crypto_mean_reversion'],
            'color':  '#FF8C00',
            'border': '#1D428A',
        },
    ]

    cols = st.columns(3)
    for i, m in enumerate(MARKETS):
        title = m['saiyan'] if saiyan else m['name']
        color = m['color']
        border = '#5a0000' if saiyan and i == 0 else ('#00001a' if saiyan and i == 1 else ('#1a0d00' if saiyan else '#0d1e3a'))

        # Collect open positions for this market
        open_pos = []
        for strat, syms in pos.items():
            if any(k in strat.lower() for k in m['keys'] + m['strats']):
                for sym, p in syms.items():
                    open_pos.append((sym, p))

        last_d = _last_debate(m['keys'] + m['strats'])
        last_t = _last_trade(m['keys'] + m['strats'])

        # Edge state
        es_key = {'₿ CRYPTO': 'crypto_macd_consensus',
                  '📈 MES FUTURES': 'futures_scalper',
                  '⚡ PERP / OTHER': 'crypto_mean_reversion'}.get(m['name'], 'crypto_macd_consensus')
        es = get_edge_state(es_key, paper=PAPER_TRADING)
        es_color = {'STRONG':'#00C853','OK':'#FDB927','DEGRADED':'#FF8F00',
                    'BLOCKED':'#FF1744','UNCERTAIN':'#333'}.get(es['status'],'#333')

        pos_html = ''
        if open_pos:
            for sym, p in open_pos[:2]:
                entry = float(p.get('entry', 0))
                stop  = float(p.get('stop', 0))
                tgt   = float(p.get('target', 0))
                stop_pct = abs(entry - stop) / entry * 100 if entry > 0 else 0
                dir_c = '#00C853' if p.get('direction','LONG') == 'LONG' else '#FF1744'
                pos_html += (
                    f'<div class="mp-position">'
                    f'<span style="color:{color};font-weight:900;">{sym}</span>'
                    f'<span style="color:{dir_c};margin-left:6px;font-size:10px;">{p.get("direction","LONG")}</span>'
                    f'<div style="color:#444;margin-top:2px;">'
                    f'in ${entry:.4f} · stop ${stop:.4f} ({stop_pct:.1f}%) · tgt ${tgt:.4f}'
                    f'</div>'
                    f'</div>'
                )
        else:
            pos_html = '<div class="mp-empty">No open position</div>'

        debate_html = ''
        if last_d:
            sig    = (last_d.get('final_signal') or '').upper()
            sym_d  = last_d.get('symbol', '')
            conf   = last_d.get('confidence', 0)
            bv     = last_d.get('buy_votes', 0)
            hv     = last_d.get('hold_votes', 0)
            sc     = '#00C853' if sig == 'BUY' else '#FF1744' if sig in ('SELL','SHORT') else '#555'
            debate_html = (
                f'<div style="font-size:10px;color:#333;margin-top:6px;">'
                f'<span style="color:#222;text-transform:uppercase;letter-spacing:1px;">Last signal</span>'
                f'<span style="color:{color};margin-left:6px;">{sym_d}</span>'
                f'<span style="color:{sc};font-weight:900;margin-left:6px;">{sig}</span>'
                f'<span style="color:#222;margin-left:4px;">{bv}B/{hv}H {conf:.0%}</span>'
                f'</div>'
            )

        trade_html = ''
        if last_t:
            tpnl   = float(last_t.get('pnl_usd') or 0)
            tsym   = last_t.get('symbol', '')
            tts    = fmt_ts(last_t.get('ts', ''), short=True)
            tc     = '#00C853' if tpnl > 0 else '#FF1744' if tpnl < 0 else '#444'
            trade_html = (
                f'<div style="font-size:10px;color:#333;margin-top:4px;">'
                f'<span style="color:#222;text-transform:uppercase;letter-spacing:1px;">Last trade</span>'
                f'<span style="color:{color};margin-left:6px;">{tsym}</span>'
                f'<span style="color:{tc};font-weight:900;margin-left:6px;">${tpnl:+.2f}</span>'
                f'<span style="color:#222;margin-left:4px;">{tts}</span>'
                f'</div>'
            )

        edge_bar_pct = int(es['sizing_multiplier'] * 100)
        edge_bar_html = (
            f'<div style="margin-top:6px;">'
            f'<div style="font-size:9px;color:#222;letter-spacing:1px;text-transform:uppercase;margin-bottom:2px;">'
            f'Edge: <span style="color:{es_color};">{es["status"]}</span>'
            f'<span style="color:#1a1a1a;margin-left:6px;">PF {es["profit_factor"]:.2f}</span>'
            f'</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:{edge_bar_pct}%;background:{es_color};"></div></div>'
            f'</div>'
        )

        with cols[i]:
            st.markdown(
                f'<div class="market-panel" style="background:{border}18;border-color:{color}33;">'
                f'<div class="mp-header" style="color:{color};">{title}</div>'
                f'{pos_html}'
                f'{debate_html}'
                f'{trade_html}'
                f'{edge_bar_html}'
                f'</div>', unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROW 5 — RECENT TRADES | RECENT SIGNALS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=8)
def row_trades_signals(saiyan: bool):
    col_l, col_r = st.columns(2)

    with col_l:
        trades = get_recent_trades(limit=15, paper=PAPER_TRADING)
        label = '⚡ BATTLE LOG' if saiyan else '📋 RECENT TRADES'
        st.markdown(
            f'<div style="color:#FDB927;font-size:10px;font-weight:900;'
            f'letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">'
            f'{label}</div>', unsafe_allow_html=True,
        )
        if not trades:
            st.markdown('<div style="color:#222;font-size:11px;font-style:italic;">'
                        'No trades yet.</div>', unsafe_allow_html=True)
        else:
            html = ''
            for t in trades:
                pnl    = float(t.get('pnl_usd') or 0)
                sym    = t.get('symbol', '')
                action = t.get('action', '')
                ts     = fmt_ts(t.get('ts', ''), short=True)
                if pnl > 0:
                    icon, color = ('✓', '#00C853')
                elif pnl < 0:
                    icon, color = ('✗', '#FF1744')
                else:
                    icon, color = ('→', '#444')
                pnl_str = f'${pnl:+.3f}' if pnl != 0 else '(open)'
                html += (
                    f'<div class="trade-row">'
                    f'<span style="color:{color};min-width:12px;">{icon}</span>'
                    f'<span style="color:#ccc;min-width:100px;">{sym[:12]}</span>'
                    f'<span style="color:#333;min-width:52px;">{ts}</span>'
                    f'<span style="color:{color};margin-left:auto;font-weight:700;">{pnl_str}</span>'
                    f'</div>'
                )
            st.markdown(f'<div style="max-height:240px;overflow-y:auto;">{html}</div>',
                        unsafe_allow_html=True)

    with col_r:
        signals = get_todays_signals()
        label = '🔭 SCOUTER FEED' if saiyan else '📡 SIGNAL FEED'
        recent_sigs = sorted(signals, key=lambda x: x.get('ts', ''), reverse=True)[:15]
        st.markdown(
            f'<div style="color:#FDB927;font-size:10px;font-weight:900;'
            f'letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">'
            f'{label} '
            f'<span style="background:#FF1744;color:#fff;font-size:8px;font-weight:900;'
            f'padding:1px 5px;border-radius:2px;" class="live-dot">● LIVE</span>'
            f'</div>', unsafe_allow_html=True,
        )
        if not recent_sigs:
            st.markdown('<div style="color:#222;font-size:11px;font-style:italic;">'
                        'Watching...</div>', unsafe_allow_html=True)
        else:
            html = ''
            for s in recent_sigs:
                sym    = s.get('symbol', '?')
                conf   = float(s.get('confidence', 0))
                acted  = bool(s.get('acted_on', False))
                ts     = fmt_ts(s.get('ts', ''), short=True)
                color  = '#FDB927' if acted else '#222'
                prefix = '⚡' if acted else '·'
                bar_n  = int(conf * 10)
                bar = ''.join(
                    f'<span style="color:{color};font-size:9px;">█</span>' if j < bar_n
                    else '<span style="color:#0d0d0d;font-size:9px;">█</span>'
                    for j in range(10)
                )
                html += (
                    f'<div class="sig-row">'
                    f'<span style="color:{color};min-width:12px;">{prefix}</span>'
                    f'<span style="color:#aaa;min-width:90px;">{sym[:12]}</span>'
                    f'<span style="font-family:monospace;">{bar}</span>'
                    f'<span style="color:{color};min-width:30px;text-align:right;">{conf:.0%}</span>'
                    f'<span style="color:#222;min-width:48px;text-align:right;">{ts}</span>'
                    f'</div>'
                )
            st.markdown(f'<div style="max-height:240px;overflow-y:auto;">{html}</div>',
                        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ROW 6 — CLAUDE AI CHAT
# ══════════════════════════════════════════════════════════════════════════════

def row_chat(saiyan: bool):
    label = '🤖 ASK THE SAIYAN AI BRAIN' if saiyan else '🤖 ASK THE KING\'S AI BRAIN'
    with st.expander(label, expanded=False):
        if 'chat' not in st.session_state:
            st.session_state['chat'] = []
        for msg in st.session_state['chat']:
            css = 'chat-user' if msg['role'] == 'user' else 'chat-bot'
            st.markdown(f'<div class="{css}">{msg["content"]}</div>', unsafe_allow_html=True)
        user_input = st.chat_input("Ask about trades, signals, edge, or risk...")
        if user_input:
            st.session_state['chat'].append({'role': 'user', 'content': user_input})
            ctx  = build_context()
            resp = call_claude(st.session_state['chat'], ctx)
            st.session_state['chat'].append({'role': 'assistant', 'content': resp})
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ROW 7 — RISK GAUGES
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def row_risk_gauges(saiyan: bool):
    heat     = get_heat_level(paper=PAPER_TRADING)
    secs     = _bot_last_seen()
    is_halted, _ = _live_halt()
    pos      = _live_positions()
    stats    = get_all_time_stats(paper=PAPER_TRADING)
    monthly  = get_monthly_api_cost()

    total_pos = sum(len(syms) for syms in pos.values())
    pct_drawn = max(heat.get('pct_drawn', 0), 0)
    heat_lvl  = heat.get('level', 0)
    heat_label = heat.get('label', 'NORMAL')
    heat_colors = {0: '#00C853', 1: '#FDB927', 2: '#FF8F00', 3: '#FF4444', 4: '#FF0000'}
    heat_color = heat_colors.get(heat_lvl, '#333')

    watchdog_ok   = secs is not None and secs < 180
    watchdog_color = '#00C853' if watchdog_ok else '#FF1744'

    if saiyan:
        HEAT_NAMES = {0:'BASE FORM', 1:'KAIOKEN x2', 2:'SUPER SAIYAN', 3:'SSJ2', 4:'POWER LIMIT'}
        heat_label = HEAT_NAMES.get(heat_lvl, heat_label)

    g1, g2, g3, g4, g5 = st.columns(5)

    # Daily loss gauge
    with g1:
        bar_pct = min(pct_drawn / MAX_DAILY_LOSS_PCT, 1.0) * 100 if MAX_DAILY_LOSS_PCT > 0 else 0
        st.markdown(
            f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">'
            f'{"⚡ Ki Drain" if saiyan else "Daily Loss"}</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:{bar_pct:.0f}%;background:{heat_color};"></div></div>'
            f'<div style="font-size:10px;color:{heat_color};margin-top:2px;">'
            f'{heat_label} ({pct_drawn*100:.1f}%/{MAX_DAILY_LOSS_PCT*100:.0f}%)</div>',
            unsafe_allow_html=True,
        )

    # Position count
    with g2:
        pos_pct = min(total_pos / max(MAX_POSITIONS_CRYPTO, 1), 1.0) * 100
        pos_color = '#FF8F00' if total_pos >= MAX_POSITIONS_CRYPTO else '#FDB927'
        st.markdown(
            f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">'
            f'{"Active Battles" if saiyan else "Open Positions"}</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:{pos_pct:.0f}%;background:{pos_color};"></div></div>'
            f'<div style="font-size:10px;color:{pos_color};margin-top:2px;">'
            f'{total_pos} / {MAX_POSITIONS_CRYPTO} max</div>',
            unsafe_allow_html=True,
        )

    # Watchdog
    with g3:
        wd_pct = 100 if watchdog_ok else 0
        wd_txt = f'{int(secs)}s ago' if secs else 'no data'
        st.markdown(
            f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">'
            f'{"Scouter Link" if saiyan else "Watchdog"}</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:{wd_pct}%;background:{watchdog_color};"></div></div>'
            f'<div style="font-size:10px;color:{watchdog_color};margin-top:2px;">'
            f'{"🟢 " if watchdog_ok else "🔴 "}{wd_txt}</div>',
            unsafe_allow_html=True,
        )

    # API cost gauge
    with g4:
        # Flag if > $10/month
        api_pct = min(monthly / 10.0, 1.0) * 100
        api_c   = '#00C853' if monthly < 3 else '#FF8F00' if monthly < 8 else '#FF1744'
        st.markdown(
            f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">'
            f'{"Spirit Cost" if saiyan else "API Cost/Mo"}</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:{api_pct:.0f}%;background:{api_c};"></div></div>'
            f'<div style="font-size:10px;color:{api_c};margin-top:2px;">${monthly:.2f} / $10 cap</div>',
            unsafe_allow_html=True,
        )

    # Halt status
    with g5:
        halt_c = '#FF1744' if is_halted else '#00C853'
        halt_t = ('⛔ HALTED' if is_halted else '🟢 TRADING') if not saiyan else ('⛔ POWER OFF' if is_halted else '⚡ POWER ON')
        st.markdown(
            f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">'
            f'{"System Power" if saiyan else "System Status"}</div>'
            f'<div class="gauge-bar-bg"><div class="gauge-bar-fill" '
            f'style="width:100%;background:{halt_c};"></div></div>'
            f'<div style="font-size:10px;color:{halt_c};font-weight:900;margin-top:2px;">{halt_t}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL EXPANDERS (on-demand — below the fold)
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def expander_edge(saiyan: bool):
    title = '⬡ EDGE MONITOR' if not saiyan else '⚡ SCOUTER — STRATEGY POWER LEVELS'
    with st.expander(title, expanded=False):
        strategies = [
            ('crypto_macd_consensus', '₿ Crypto MACD',     'BARDOCK' if saiyan else None),
            ('futures_scalper',       '📈 MES Futures',     'VEGETA'  if saiyan else None),
            ('crypto_mean_reversion', '↔ Mean Reversion',  'KRILLIN' if saiyan else None),
        ]
        for strat, label, z_name in strategies:
            es     = get_edge_state(strat, paper=PAPER_TRADING)
            status = es['status']
            color  = {'STRONG':'#00C853','OK':'#FDB927','DEGRADED':'#FF8F00',
                      'BLOCKED':'#FF1744','UNCERTAIN':'#555'}.get(status,'#555')
            mult   = es['sizing_multiplier']
            pf     = es['profit_factor']
            trades = es['window_trades']
            bar_n  = int(mult * 10)
            bar = ''.join(
                f'<span style="color:{color};">█</span>' if j < bar_n
                else '<span style="color:#111;">█</span>'
                for j in range(10)
            )
            display = f'{z_name} — {label}' if z_name else label
            st.markdown(
                f'<div style="background:#050a14;border:1px solid {color}33;border-radius:4px;'
                f'padding:8px 12px;margin:4px 0;font-size:11px;">'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<span style="color:#aaa;">{display}</span>'
                f'<span style="color:{color};font-weight:900;">{status}</span>'
                f'</div>'
                f'<div style="font-family:monospace;margin:4px 0;">{bar}</div>'
                f'<div style="display:flex;gap:16px;color:#444;">'
                f'<span>PF <span style="color:{color};">{pf:.2f}</span></span>'
                f'<span>size <span style="color:#aaa;">{mult:.0%}</span></span>'
                f'<span>{trades} trades</span>'
                f'</div>'
                f'</div>', unsafe_allow_html=True,
            )


@st.fragment(run_every=30)
def expander_debates(saiyan: bool):
    title = '🤖 AI DEBATES' if not saiyan else '⚔ WARRIOR DEBATE LOGS'
    with st.expander(title, expanded=False):
        debates = get_recent_debates(limit=8)
        if not debates:
            st.markdown('<div style="color:#333;font-size:11px;">No debates yet.</div>',
                        unsafe_allow_html=True)
            return
        AGENT_LABELS = {
            'funding_regime':     ('BARDOCK' if saiyan else 'Funding/Macro', '#FF4444'),
            'momentum_structure': ('VEGETA'  if saiyan else 'Momentum',      '#4488FF'),
            'risk_economics':     ('KRILLIN' if saiyan else 'Risk/Econ',     '#FFB347'),
        }
        for d in debates:
            sym  = d.get('symbol', '?')
            sig  = (d.get('final_signal') or '').upper()
            conf = float(d.get('confidence', 0))
            bv   = d.get('buy_votes', 0)
            hv   = d.get('hold_votes', 0)
            reas = str(d.get('reasoning', ''))[:300]
            ts   = fmt_ts(d.get('ts', ''), short=True)
            sc   = '#00C853' if sig == 'BUY' else '#FF1744' if sig in ('SELL','SHORT') else '#555'

            agents_raw = d.get('agent_votes') or d.get('agents') or ''
            agent_data = {}
            if isinstance(agents_raw, str):
                try:
                    agent_data = json.loads(agents_raw)
                except Exception:
                    pass
            elif isinstance(agents_raw, dict):
                agent_data = agents_raw

            agent_html = ''
            for key, vote_data in agent_data.items():
                if not isinstance(vote_data, dict):
                    continue
                v_sig  = (vote_data.get('signal', vote_data.get('verdict', '?'))).upper()
                v_conf = float(vote_data.get('confidence', 0))
                v_reas = str(vote_data.get('reason', ''))[:60]
                name, clr = AGENT_LABELS.get(key, (key.upper(), '#888'))
                vc = '#00C853' if v_sig == 'BUY' else '#FF1744' if v_sig in ('SELL','SHORT') else '#555'
                agent_html += (
                    f'<span style="color:{clr};font-weight:700;min-width:60px;display:inline-block;">{name}</span>'
                    f'<span style="color:{vc};font-weight:900;min-width:40px;display:inline-block;">{v_sig}</span>'
                    f'<span style="color:#444;">{v_conf:.0%} — {v_reas}</span><br>'
                )

            with st.expander(f"{sym} → {sig}  {bv}B/{hv}H  {conf:.0%}  {ts}", expanded=False):
                if agent_html:
                    st.markdown(
                        f'<div style="background:#030303;border-radius:4px;padding:8px;'
                        f'font-size:11px;font-family:monospace;line-height:1.8;">'
                        f'{agent_html}</div>', unsafe_allow_html=True,
                    )
                if reas:
                    st.markdown(
                        f'<div style="color:#666;font-size:11px;margin-top:6px;">{reas}</div>',
                        unsafe_allow_html=True,
                    )


@st.fragment(run_every=5)
def expander_scanfeed(saiyan: bool):
    title = '📡 SCAN FEED' if not saiyan else '🔭 SCOUTER TRANSMISSION'
    with st.expander(title, expanded=False):
        entries = get_scan_feed(limit=60)
        if not entries:
            st.markdown('<div style="color:#333;padding:8px;">Waiting...</div>', unsafe_allow_html=True)
            return
        html = ''
        for e in entries[:50]:
            msg = e.get('message', '')
            ts  = fmt_ts(e.get('ts', ''), short=True)
            if any(k in msg for k in ('BUY', 'LONG', 'SHORT', '🟢')):
                c, dot = '#FDB927', '●'
            elif any(k in msg for k in ('CLOSED', 'EXITED', 'P&L')):
                c, dot = '#00C853', '✓'
            elif any(k in msg for k in ('HALT', 'ERROR', 'block', 'VETO')):
                c, dot = '#FF1744', '⊘'
            elif any(k in msg for k in ('Debate', 'agent')):
                c, dot = '#9966ff', '🤖'
            else:
                c, dot = '#1a1a1a', '·'
            html += (
                f'<div class="sig-row">'
                f'<span style="color:#1a1a1a;min-width:48px;flex-shrink:0;">{ts}</span>'
                f'<span style="color:{c};">{dot} {msg[:100]}</span>'
                f'</div>'
            )
        st.markdown(
            f'<div style="max-height:300px;overflow-y:auto;background:#030303;'
            f'border:1px solid #0a0a0a;border-radius:4px;padding:8px;">{html}</div>',
            unsafe_allow_html=True,
        )


def expander_controls(saiyan: bool):
    title = '⚙️ CONTROLS' if not saiyan else '⚙️ POWER CONTROLS'
    with st.expander(title, expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown('<div style="color:#555;font-size:10px;text-transform:uppercase;'
                        'letter-spacing:1px;margin-bottom:6px;">Position Sizes</div>',
                        unsafe_allow_html=True)
            nc = st.number_input('Crypto ($)', 10.0, 2000.0, float(CRYPTO_POSITION_SIZE_USD), 10.0, key='cs')
            ne = st.number_input('Equity ($)', 10.0, 2000.0, float(EQUITY_POSITION_SIZE_USD), 10.0, key='es')
            if st.button('Save Sizes', key='save_sz'):
                _write_env({'CRYPTO_POSITION_SIZE_USD': str(nc), 'EQUITY_POSITION_SIZE_USD': str(ne)})
                st.success('Saved — restart main.py.')

        with col2:
            st.markdown('<div style="color:#555;font-size:10px;text-transform:uppercase;'
                        'letter-spacing:1px;margin-bottom:6px;">Halt Control</div>',
                        unsafe_allow_html=True)
            rm = get_risk_manager()
            is_halted, reason = _live_halt()
            if is_halted:
                st.error(f'HALTED: {reason}')
                if st.button('▶ Resume', key='resume', type='primary'):
                    try:
                        rm.resume_trading()
                        st.success('Resumed.')
                    except Exception as ex:
                        st.error(str(ex))
            else:
                st.success('Active')
                if st.button('⏸ Emergency Halt', key='ehalt'):
                    try:
                        rm.halt_trading('Manual dashboard halt')
                        st.warning('Halted.')
                    except Exception as ex:
                        st.error(str(ex))

        with col3:
            try:
                from config import DEBATE_MAX_TOKENS, EXIT_REVIEW_MAX_TOKENS
            except Exception:
                DEBATE_MAX_TOKENS, EXIT_REVIEW_MAX_TOKENS = 400, 800
            st.markdown('<div style="color:#555;font-size:10px;text-transform:uppercase;'
                        'letter-spacing:1px;margin-bottom:6px;">Cost Lab</div>',
                        unsafe_allow_html=True)
            mo = get_monthly_api_cost()
            st.metric('This month', f'${mo:.4f}')
            nd = st.slider('Debate tokens', 100, 600, DEBATE_MAX_TOKENS, 50, key='dbt')
            ni = st.slider('Scan interval (min)', 1, 15, max(1, CRYPTO_SCAN_INTERVAL_SECONDS//60), 1, key='sci')
            if st.button('Apply', key='apply_cost', type='primary'):
                _write_env({'DEBATE_MAX_TOKENS': str(nd),
                            'CRYPTO_SCAN_INTERVAL_SECONDS': str(ni * 60)})
                st.success('Written — restart main.py.')


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Saiyan mode toggle (top-right) ────────────────────────────────────────
    if 'saiyan' not in st.session_state:
        st.session_state['saiyan'] = False

    header_l, header_r = st.columns([8, 1])
    with header_r:
        if st.session_state['saiyan']:
            if st.button('👑 THE KING', key='toggle_king'):
                st.session_state['saiyan'] = False
                st.rerun()
        else:
            if st.button('⚡ SAIYAN', key='toggle_saiyan'):
                st.session_state['saiyan'] = True
                st.rerun()

    saiyan = st.session_state['saiyan']

    # ── Row 1: Scoreboard ─────────────────────────────────────────────────────
    row_scoreboard(saiyan)

    # ── Row 2: LeBron quote ───────────────────────────────────────────────────
    row_quote(saiyan)

    # ── Row 3: Six metrics ────────────────────────────────────────────────────
    row_six_metrics(saiyan)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Row 4: Three market panels ────────────────────────────────────────────
    row_markets(saiyan)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Row 5: Trades + signals ───────────────────────────────────────────────
    row_trades_signals(saiyan)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Row 6: Chat ───────────────────────────────────────────────────────────
    row_chat(saiyan)

    # ── Row 7: Risk gauges ────────────────────────────────────────────────────
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    row_risk_gauges(saiyan)

    # ── Detail expanders (below the fold) ─────────────────────────────────────
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    expander_edge(saiyan)
    expander_debates(saiyan)
    expander_scanfeed(saiyan)
    expander_controls(saiyan)

    # Footer
    mode_str = '⚡ SAIYAN MODE' if saiyan else '👑 THE KING'
    st.markdown(
        f'<div style="text-align:center;color:#111;font-size:9px;font-family:monospace;'
        f'padding:12px 0;letter-spacing:3px;">'
        f'THE KING\'S WAR ROOM v9.0 · {mode_str} · {et_now()} · '
        f'{"PAPER" if PAPER_TRADING else "LIVE"}</div>',
        unsafe_allow_html=True,
    )


if __name__ == '__main__':
    main()
