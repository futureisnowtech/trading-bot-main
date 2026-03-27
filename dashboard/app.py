"""
dashboard/app.py — THE KING × SAIYAN MODE v9.0

Three-tab championship experience:
  👑 THE KING    — Lakers gold command center. LeBron energy. Championship metrics.
  ⚡ SAIYAN MODE  — Dragon Ball Z. Agent warriors. Ki energy. Power transformations.
  📋 FILM ROOM   — Full debate transcripts. Signal analysis. Strategy controls.

Run: streamlit run dashboard/app.py
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
import streamlit.components.v1 as components

from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    CRYPTO_PAIRS, ANTHROPIC_API_KEY, CLAUDE_MODEL,
    EQUITY_ENABLED, CRYPTO_ENABLED, FUTURES_ENABLED,
    MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT, MAX_STRATEGY_LOSS_STREAK,
    CRYPTO_SCAN_INTERVAL_SECONDS, EQUITY_SCAN_INTERVAL_SECONDS,
    MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
    MAX_RISK_PER_TRADE_PCT, MAX_POSITIONS_CRYPTO, MAX_POSITIONS_EQUITY,
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    FULL_DEBATE_AGENTS, FULL_DEBATE_MIN_AGREEMENT,
    CRYPTO_POSITION_SIZE_USD, EQUITY_POSITION_SIZE_USD,
    COINBASE_TAKER_FEE_PCT,
)
from logging_db.trade_logger import (
    get_todays_trades, get_todays_signals, get_todays_pnl, get_todays_fees,
    get_scan_feed, get_daily_trade_count, get_all_time_stats, get_recent_debates,
    get_monthly_api_cost, get_win_rate, get_recent_trades, get_recent_events,
    get_recent_notifications, get_today_stats, get_strategy_consecutive_losses,
    get_performance_attribution,
)
from risk.risk_manager import get_risk_manager

# ── Optional modules (fail-silent) ────────────────────────────────────────────
try:
    from data.edge_monitor import get_edge_state, format_edge_context
    _EDGE_MONITOR = True
except Exception:
    _EDGE_MONITOR = False
    def get_edge_state(s, paper=True):
        return {'status': 'UNCERTAIN', 'edge_score': 0.5, 'profit_factor': 1.5,
                'consecutive_bad': 0, 'sizing_multiplier': 1.0, 'should_block': False,
                'window_trades': 0}

try:
    from data.options_flow import get_options_signals
    _OPTIONS_FLOW = True
except Exception:
    _OPTIONS_FLOW = False
    def get_options_signals():
        return {'iv_rank': 0.5, 'iv_regime': 'NORMAL_IV', 'term_structure': 'FLAT',
                'vix_level': None, 'vvix_level': None, 'skew_level': None,
                'panic_signal': False, 'tail_risk_elevated': False,
                'options_regime': 'Options data unavailable'}

try:
    from risk.drawdown_controller import get_heat_level
    _HEAT_AVAILABLE = True
except Exception:
    _HEAT_AVAILABLE = False
    def get_heat_level(paper=True):
        return {'level': 0, 'label': 'NORMAL', 'size_factor': 1.0,
                'daily_pnl': 0.0, 'pct_drawn': 0.0}

# ── LeBron quotes ──────────────────────────────────────────────────────────────
LEBRON_QUOTES = [
    ("Nothing is given. Everything is earned.", "The grind never stops."),
    ("Strive for greatness.", "Every day. Every trade. Every decision."),
    ("I have short goals — to get better every day.", "Compound gains. Compound growth."),
    ("The best come from somewhere. Remember yours.", "Built different. Trade different."),
    ("I like criticism. It makes you strong.", "Every loss is tuition. Pay attention."),
    ("You can't be afraid to fail. That's how you get better.", "Risk managed. Lessons earned."),
    ("Sometimes the best move is no move. Stay patient.", "The hold is the discipline."),
    ("I treat every single game like it's my last.", "Every scan. Every signal. Full focus."),
    ("Ask me to play. I'll play. Ask me to shoot. I'll shoot.", "Ready. Disciplined. Dangerous."),
    ("I promise you I will do everything in my power.", "The system never sleeps."),
    ("Every day is a new opportunity to improve.", "Yesterday's data builds tomorrow's edge."),
    ("We're in the lab. Let's get to work.", "The algorithm is always working."),
]

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="👑 THE KING × SAIYAN",
    page_icon='👑',
    layout='wide',
    initial_sidebar_state='collapsed',
)

# ─── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base ── */
.main, .stApp { background: #000814 !important; }
body { font-family: 'Helvetica Neue', Arial, sans-serif; }
h1,h2,h3,h4 { color: #FDB927 !important; }
div[data-testid="stMetricValue"] { color: #FDB927 !important; font-weight: 900 !important; }
.stButton>button { background: #0d1e3a; color: #FDB927; border: 1px solid #FDB927;
    font-weight: 700; border-radius: 6px; transition: all 0.2s; }
.stButton>button:hover { background: #FDB927; color: #000; transform: translateY(-1px); }
.stProgress > div > div { background: #FDB927 !important; }
.stExpander { border: 1px solid #1D428A !important; }

/* ── Tab nav ── */
div[data-testid="stTabs"] button {
    font-size: 14px !important;
    font-weight: 700 !important;
    color: #555 !important;
    letter-spacing: 1px;
    border-radius: 0 !important;
    padding: 10px 20px !important;
}
div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #FDB927 !important;
    border-bottom: 3px solid #FDB927 !important;
    background: transparent !important;
}
div[data-testid="stTabs"] > div { border-bottom: 1px solid #1a1a1a; }

/* ── LeBron Quote Banner ── */
.lebron-banner {
    background: linear-gradient(135deg, #0d1e3a 0%, #000814 40%, #1D428A22 100%);
    border-left: 4px solid #FDB927;
    border-radius: 0 8px 8px 0;
    padding: 16px 24px;
    margin-bottom: 16px;
    position: relative;
    overflow: hidden;
}
.lebron-quote {
    color: #FDB927;
    font-size: 22px;
    font-weight: 900;
    font-style: italic;
    letter-spacing: 0.5px;
    line-height: 1.3;
    margin: 0;
}
.lebron-sub {
    color: #555;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-top: 6px;
}
.lebron-crown {
    position: absolute;
    right: 20px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 48px;
    opacity: 0.15;
}

/* ── Status bar ── */
.status-bar {
    background: linear-gradient(90deg, #0d1e3a 0%, #000814 50%, #0d1e3a 100%);
    border-bottom: 2px solid #FDB927;
    padding: 8px 20px;
    margin: -20px -20px 14px -20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
}

/* ── Championship metric cells ── */
.champ-metric {
    background: linear-gradient(180deg, #0d1e3a 0%, #000814 100%);
    border: 1px solid #1D428A;
    border-radius: 10px;
    padding: 16px 12px;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.champ-metric::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent, #FDB927, transparent);
}
.champ-val {
    font-size: 28px;
    font-weight: 900;
    font-family: 'Impact', sans-serif;
    line-height: 1.1;
}
.champ-lbl {
    font-size: 9px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 6px;
}
.champ-sub {
    font-size: 10px;
    color: #444;
    margin-top: 4px;
}

/* ── Panels ── */
.panel {
    background: #050a14;
    border: 1px solid #1a2a3a;
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 12px;
}
.panel-title {
    color: #FDB927;
    font-weight: 900;
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    border-bottom: 1px solid #1a2a3a;
    padding-bottom: 6px;
}

/* ── Edge cards ── */
.edge-card { border-radius: 8px; padding: 12px 14px; margin: 4px 0;
    font-family: monospace; border: 1px solid #333; }
.edge-strong { border-color: #00C853 !important; background: #001a0a; }
.edge-ok { border-color: #FDB927 !important; background: #1a1200; }
.edge-degraded { border-color: #FF8F00 !important; background: #1a0d00; }
.edge-blocked { border-color: #FF1744 !important; background: #1a0005; }
.edge-uncertain { border-color: #444 !important; background: #0d0d0d; }

/* ── Ring progress ── */
.ring-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 6px;
    margin: 4px 0;
    font-size: 12px;
}
.ring-earned { background: #1a1200; border-left: 3px solid #FDB927; }
.ring-pending { background: #050505; border-left: 3px solid #1a1a1a; opacity: 0.6; }
.ring-next { background: #0d1e3a22; border-left: 3px solid #1D428A; }

/* ── Position card ── */
.pos-card {
    background: #0a0f1a;
    border: 1px solid #1a2a3a;
    border-radius: 6px;
    padding: 10px 12px;
    margin: 6px 0;
    font-size: 12px;
}

/* ── Trade row ── */
.trade-row {
    padding: 4px 0;
    font-family: monospace;
    font-size: 11px;
    border-bottom: 1px solid #0d0d0d;
    display: flex;
    gap: 8px;
    align-items: center;
}

/* ── Scan feed ── */
.feed-row { padding: 3px 0; font-family: monospace; font-size: 11px;
    border-bottom: 1px solid #0a0a0a; display: flex; gap: 8px; }

/* ── Halt / Paper banners ── */
.halt-banner { background: #FF1744; color: #fff; padding: 12px;
    border-radius: 6px; text-align: center; font-weight: 900; font-size: 16px;
    margin: 8px 0; letter-spacing: 2px; }
.paper-banner { background: #0d1e3a; color: #FDB927; padding: 6px;
    border-radius: 4px; text-align: center; font-weight: 700; font-size: 12px;
    letter-spacing: 1px; margin: 4px 0; }

/* ── SAIYAN MODE ── */
.saiyan-bg { background: #050005 !important; }

.warrior-card {
    border-radius: 10px;
    padding: 16px;
    margin: 4px 0;
    position: relative;
    overflow: hidden;
}
.warrior-bardock {
    background: linear-gradient(135deg, #1a0005 0%, #050005 100%);
    border: 1px solid #8B0000;
}
.warrior-vegeta {
    background: linear-gradient(135deg, #00001a 0%, #050005 100%);
    border: 1px solid #1D428A;
}
.warrior-krillin {
    background: linear-gradient(135deg, #1a0d00 0%, #050005 100%);
    border: 1px solid #FF8C00;
}
.warrior-name { font-size: 18px; font-weight: 900; letter-spacing: 2px; }
.warrior-role { font-size: 9px; text-transform: uppercase; letter-spacing: 3px; opacity: 0.7; margin-top: 2px; }
.warrior-verdict-buy { color: #00C853; font-size: 20px; font-weight: 900; }
.warrior-verdict-hold { color: #888; font-size: 20px; font-weight: 900; }
.warrior-verdict-sell { color: #FF1744; font-size: 20px; font-weight: 900; }

/* ── Ki energy bar ── */
.ki-bar-container {
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    border-radius: 20px;
    height: 12px;
    overflow: hidden;
    margin: 6px 0;
}
.ki-bar-fill {
    height: 100%;
    border-radius: 20px;
    transition: width 0.5s ease;
}

/* ── Heat/Transformation ── */
.transform-card {
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 16px;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.transform-normal { background: linear-gradient(135deg, #001a0a, #000814); border: 2px solid #00C853; }
.transform-caution { background: linear-gradient(135deg, #1a1200, #000814); border: 2px solid #FDB927; }
.transform-warning { background: linear-gradient(135deg, #1a0d00, #000814); border: 2px solid #FF8F00; }
.transform-danger  { background: linear-gradient(135deg, #1a0005, #000814); border: 2px solid #FF1744; }
.transform-halt    { background: linear-gradient(135deg, #3a0000, #000814); border: 2px solid #FF0000; }

.transform-name { font-size: 28px; font-weight: 900; letter-spacing: 4px; text-transform: uppercase; }
.transform-power { font-size: 12px; letter-spacing: 2px; opacity: 0.7; margin-top: 4px; }

/* ── ML Scouter ── */
.scouter-card {
    background: #000814;
    border: 2px solid #00FF00;
    border-radius: 8px;
    padding: 16px;
    font-family: 'Courier New', monospace;
    color: #00FF00;
    text-align: center;
}
.scouter-power { font-size: 36px; font-weight: 900; color: #00FF00; }
.scouter-label { font-size: 10px; letter-spacing: 3px; text-transform: uppercase; opacity: 0.8; }

/* ── Debate card ── */
.debate-card { background: #0a0a14; border: 1px solid #1a1a2a;
    border-radius: 8px; padding: 10px 12px; margin: 6px 0; }
.agent-vote-buy { color: #00C853; font-weight: 900; }
.agent-vote-hold { color: #888; }
.agent-vote-sell { color: #FF1744; font-weight: 900; }

/* ── Chat ── */
.chat-user { background: #0d1e3a; color: #ddd; padding: 9px 13px;
    border-radius: 12px 12px 4px 12px; margin: 5px 0; font-size: 13px; }
.chat-bot { background: #0a0a0a; color: #ccc; padding: 9px 13px;
    border-radius: 12px 12px 12px 4px; margin: 5px 0; font-size: 13px;
    border-left: 3px solid #FDB927; }

/* ── Signal row ── */
.signal-row {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 8px; border-radius: 4px; margin: 2px 0; font-size: 11px;
}
.signal-fire { background: #0d1e3a22; }
.signal-hold { background: #0a0a0a; opacity: 0.5; }

/* ── Misc ── */
.amygdala-rule { padding: 4px 0; font-size: 11px; display: flex; gap: 8px; }
.rule-pass { color: #00C853; }
.rule-warn { color: #FF8F00; }
.rule-fail { color: #FF1744; }
.metric-cell { background: linear-gradient(135deg,#0d1e3a,#000814);
    border: 1px solid #1D428A; border-radius: 8px; padding: 12px 8px;
    text-align: center; }
.metric-val { font-size: 22px; font-weight: 900; font-family: 'Impact',sans-serif; }
.metric-lbl { font-size: 9px; color: #666; text-transform: uppercase;
    letter-spacing: 2px; margin-bottom: 4px; }
.metric-sub { font-size: 10px; color: #555; margin-top: 3px; }

/* ── Animations ── */
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
@keyframes aura { 0%,100%{box-shadow:0 0 10px #FDB92744} 50%{box-shadow:0 0 25px #FDB927aa} }
@keyframes gold-aura { 0%,100%{text-shadow:0 0 8px #FDB92777} 50%{text-shadow:0 0 20px #FDB927ff} }
@keyframes ssj-flash { 0%{opacity:1} 20%{opacity:0.7} 40%{opacity:1} 100%{opacity:1} }
@keyframes ki-pulse { 0%,100%{opacity:0.8} 50%{opacity:1} }

.live-badge { animation: pulse 2s infinite; }
.champ-glow { animation: aura 3s ease-in-out infinite; }
.ssj-active { animation: ssj-flash 0.5s ease-in-out 3; }
.ki-glow { animation: ki-pulse 2s ease-in-out infinite; }

/* ── Info popover ── */
div[data-testid="stPopover"] > button {
    background: transparent !important; border: 1px solid #2a2a2a !important;
    color: #555 !important; padding: 0 !important; font-size: 11px !important;
    min-height: 22px !important; height: 22px !important; width: 22px !important;
    border-radius: 50% !important; line-height: 1 !important; margin-top: 2px;
}
div[data-testid="stPopover"] > button:hover {
    border-color: #FDB927 !important; color: #FDB927 !important;
}

/* ── Notification pills ── */
.notif-error { border-left: 3px solid #FF1744; background: #1a000522; padding: 6px 10px;
    border-radius: 0 4px 4px 0; margin: 3px 0; font-size: 11px; }
.notif-warn  { border-left: 3px solid #FF8F00; background: #1a0d0022; padding: 6px 10px;
    border-radius: 0 4px 4px 0; margin: 3px 0; font-size: 11px; }
.notif-trade { border-left: 3px solid #00C853; background: #001a0a22; padding: 6px 10px;
    border-radius: 0 4px 4px 0; margin: 3px 0; font-size: 11px; }
.notif-info  { border-left: 3px solid #1D428A; background: #0d1e3a22; padding: 6px 10px;
    border-radius: 0 4px 4px 0; margin: 3px 0; font-size: 11px; }
</style>
""", unsafe_allow_html=True)


# ─── Core helpers ──────────────────────────────────────────────────────────────

def et_now() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%b %-d  %-I:%M:%S %p ET')

def fmt_ts(ts: str, show_date: bool = True, show_seconds: bool = False) -> str:
    if not ts:
        return ''
    try:
        dt = datetime.fromisoformat(ts)
        tz_et = pytz.timezone(MARKET_TIMEZONE)
        dt = dt.astimezone(tz_et) if dt.tzinfo else tz_et.localize(dt)
        tfmt = '%-I:%M:%S %p' if show_seconds else '%-I:%M %p'
        return dt.strftime(f'%b %-d, {tfmt}') if show_date else dt.strftime(tfmt)
    except Exception:
        return ts[5:16] if len(ts) >= 16 else ts

def _quote_of_day() -> tuple[str, str]:
    hour = datetime.now(pytz.timezone(MARKET_TIMEZONE)).hour
    q = LEBRON_QUOTES[(hour // 2) % len(LEBRON_QUOTES)]
    return q[0], q[1]

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db')

def _db_conn():
    import sqlite3 as _sq
    c = _sq.connect(_DB, timeout=2)
    c.row_factory = _sq.Row
    return c

def _bot_last_seen() -> float | None:
    try:
        from datetime import timezone as _tz
        conn = _db_conn()
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

def _live_halt_state() -> tuple[bool, str]:
    try:
        conn = _db_conn()
        row = conn.execute("""
            SELECT level, message FROM system_events
            WHERE source='RiskManager'
              AND (level='HALT' OR (level='INFO' AND message LIKE '%Halt cleared%'))
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn.close()
        if not row:
            return False, ''
        if row['level'] == 'HALT':
            return True, row['message']
        return False, ''
    except Exception:
        return False, ''

def _live_positions() -> dict:
    try:
        conn = _db_conn()
        rows = conn.execute(
            "SELECT * FROM open_positions WHERE paper=?", (int(PAPER_TRADING),)
        ).fetchall()
        conn.close()
    except Exception:
        return {}
    result: dict = {}
    for r in rows:
        strat = r['strategy']
        sym   = r['symbol']
        if strat not in result:
            result[strat] = {}
        result[strat][sym] = {
            'qty':              r['qty'],
            'entry':            r['entry'],
            'stop':             r['stop'],
            'target':           r['target'],
            'high_since_entry': r['high_since_entry'],
            'direction':        r['direction'] if 'direction' in r.keys() else 'LONG',
            'entry_reason':     r['entry_reason'] if 'entry_reason' in r.keys() else '',
            'ts_entry':         r['ts_entry'],
        }
    return result

def _info(label: str, body: str) -> None:
    with st.popover("ℹ", use_container_width=False):
        st.markdown(
            f'<div style="font-size:12px;line-height:1.7;color:#ccc;max-width:320px;">'
            f'<b style="color:#FDB927;">{label}</b><br><br>{body}</div>',
            unsafe_allow_html=True,
        )

def _write_env_values(updates: dict) -> None:
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
        written = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if '=' in stripped and not stripped.startswith('#'):
                key = stripped.split('=', 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}\n")
                    written.add(key)
                    continue
            new_lines.append(line)
        for k, v in updates.items():
            if k not in written:
                new_lines.append(f"{k}={v}\n")
        with open(env_path, 'w') as f:
            f.writelines(new_lines)
    except Exception as e:
        st.error(f"Failed to write .env: {e}")

def _edge_color(status: str) -> str:
    return {'STRONG':'#00C853','OK':'#FDB927','DEGRADED':'#FF8F00',
            'BLOCKED':'#FF1744','UNCERTAIN':'#666'}.get(status, '#666')

def _edge_css_class(status: str) -> str:
    return {'STRONG':'edge-strong','OK':'edge-ok','DEGRADED':'edge-degraded',
            'BLOCKED':'edge-blocked','UNCERTAIN':'edge-uncertain'}.get(status,'edge-uncertain')

# ─── Chat helpers ──────────────────────────────────────────────────────────────

def call_claude_chat(messages: list, system_ctx: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "⚠️ Set ANTHROPIC_API_KEY in .env to enable this."
    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL, "max_tokens": 1200,
            "system": system_ctx, "messages": messages[-12:]
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages', data=payload,
            headers={'Content-Type':'application/json',
                     'x-api-key': ANTHROPIC_API_KEY,
                     'anthropic-version':'2023-06-01'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())['content'][0]['text']
    except Exception as e:
        return f"❌ Error: {e}"

def build_chat_context() -> str:
    pos   = _live_positions()
    pnl   = get_todays_pnl(paper=PAPER_TRADING)
    fees  = get_todays_fees(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)
    today = get_today_stats(paper=PAPER_TRADING)
    recent_trades = get_recent_trades(limit=20, paper=PAPER_TRADING)
    debates  = get_recent_debates(limit=3)
    events   = get_recent_events(limit=20)
    signals  = get_todays_signals()
    rm       = get_risk_manager()
    risk     = rm.status_report()
    monthly  = get_monthly_api_cost()
    wr_14d   = get_win_rate(lookback_days=14, paper=PAPER_TRADING)

    edge_lines = ''
    for strat in ['crypto_macd_consensus', 'futures_scalper', 'crypto_mean_reversion']:
        es = get_edge_state(strat, paper=PAPER_TRADING)
        edge_lines += f"  {strat}: {es['status']} PF={es['profit_factor']:.2f} sizing={es['sizing_multiplier']:.0%}\n"

    pos_text = ''
    for market, positions in pos.items():
        for sym, p in positions.items():
            pos_text += f"  {market.upper()} {sym}: qty={p.get('qty')} entry=${p.get('entry',0):.4f} stop=${p.get('stop',0):.4f}\n"
    if not pos_text:
        pos_text = '  None\n'

    trade_text = '\n'.join(
        f"  {fmt_ts(t.get('ts',''))} | {t.get('action','')} {t.get('symbol','')} | P&L=${t.get('pnl_usd',0):+.4f}"
        for t in recent_trades
    ) or '  None'

    debate_text = ''
    for d in debates:
        debate_text += (
            f"  [{d.get('symbol','?')}] → {d.get('final_signal','?')} "
            f"({d.get('buy_votes',0)}B/{d.get('hold_votes',0)}H conf={d.get('confidence',0):.0%})\n"
        )
    if not debate_text:
        debate_text = '  None\n'

    _n_agents = len(FULL_DEBATE_AGENTS)
    return f"""You are Claude, AI brain of this autonomous algo trading system. Be direct. Protect this account first.

═══ LIVE STATE ({et_now()}) ═══
Mode: {'📄 PAPER' if PAPER_TRADING else '💰 LIVE'} | Account: ${ACCOUNT_SIZE:,.0f}
Today P&L: ${pnl:+.2f} ({pnl/max(ACCOUNT_SIZE,1)*100:+.2f}%) | Fees today: ${fees:.2f}
All-time: {stats.get('total',0)} trades | WR {stats.get('win_rate',0):.1%} | P&L ${stats.get('total_pnl',0):+.2f}
14-day WR: {wr_14d:.1%} | API cost this month: ${monthly:.4f}
Halted: {risk.get('halted',False)}

═══ EDGE STATE ═══
{edge_lines}
═══ OPEN POSITIONS ═══
{pos_text}
═══ RECENT TRADES ═══
{trade_text}

═══ RECENT DEBATES ═══
{debate_text}
═══ RULES ═══
- Never chase (>3% move) | Never average down | Stops sacred | No FOMO
- Daily loss halt: {MAX_DAILY_LOSS_PCT*100:.0f}% | Crypto stop: {CRYPTO_STOP_LOSS_PCT*100:.1f}% target: {CRYPTO_TAKE_PROFIT_PCT*100:.1f}%
- 2/{_n_agents} agents must say BUY | No entries 2-3am ET"""


# ══════════════════════════════════════════════════════════════════════════════
# THE KING TAB
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=3)
def _king_status_bar():
    pnl      = get_todays_pnl(paper=PAPER_TRADING)
    fees     = get_todays_fees(paper=PAPER_TRADING)
    stats    = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    secs     = _bot_last_seen()
    is_halted, halt_reason = _live_halt_state()

    # Mode banner
    if PAPER_TRADING:
        st.markdown(
            '<div style="background:#0d1e3a;border:2px solid #1D428A;border-radius:6px;'
            'padding:8px 16px;margin-bottom:10px;text-align:center;">'
            '<span style="color:#FDB927;font-weight:900;font-size:14px;letter-spacing:3px;">'
            '📄 PAPER TRADING</span>'
            '<span style="color:#555;font-size:11px;margin-left:12px;">'
            'Simulated capital — building the track record</span>'
            '</div>', unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#3a0000;border:2px solid #FF1744;border-radius:6px;'
            'padding:8px 16px;margin-bottom:10px;text-align:center;" class="live-badge">'
            '<span style="color:#FF1744;font-weight:900;font-size:15px;letter-spacing:3px;">'
            '💰 LIVE TRADING — REAL CAPITAL AT RISK</span>'
            '</div>', unsafe_allow_html=True,
        )

    if is_halted:
        st.markdown(
            f'<div class="halt-banner">⛔ TRADING HALTED — {halt_reason or "Limit reached"}'
            f' — Resume: python3 main.py</div>', unsafe_allow_html=True,
        )

    # Bot status row
    if is_halted:
        bot_html = f'<span style="color:#FF1744;font-weight:900;">🔴 HALTED</span>'
    elif secs is None:
        bot_html = '<span style="color:#FF8F00;">🟡 STARTING...</span>'
    elif secs > 180:
        bot_html = f'<span style="color:#FF1744;">🔴 STALE — {int(secs)}s ago</span>'
    else:
        bot_html = (f'<span style="color:#00C853;font-weight:900;">🟢 ALIVE</span>'
                    f'<span style="color:#444;font-size:10px;margin-left:8px;">last scan {int(secs)}s ago</span>')

    pnl_color = '#00C853' if pnl >= 0 else '#FF1744'
    bal_color = '#00C853' if real_bal >= ACCOUNT_SIZE else '#FF1744'

    st.markdown(
        f'<div class="status-bar">'
        f'<div style="display:flex;align-items:center;gap:16px;">'
        f'{bot_html}'
        f'<span style="color:#444;font-size:10px;font-family:monospace;">{et_now()}</span>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:24px;">'
        f'<span style="color:#555;font-size:11px;">Today: '
        f'<span style="color:{pnl_color};font-weight:900;">${pnl:+.2f}</span>'
        f'<span style="color:#333;font-size:10px;"> (net ${pnl-fees:+.2f})</span></span>'
        f'<span style="color:#555;font-size:11px;">Balance: '
        f'<span style="color:{bal_color};font-weight:900;">${real_bal:,.2f}</span></span>'
        f'</div>'
        f'</div>', unsafe_allow_html=True,
    )


def _king_lebron_banner():
    quote, sub = _quote_of_day()
    st.markdown(
        f'<div class="lebron-banner champ-glow">'
        f'<div class="lebron-quote">"{quote}"</div>'
        f'<div class="lebron-sub">— LeBron James &nbsp;·&nbsp; {sub}</div>'
        f'<div class="lebron-crown">👑</div>'
        f'</div>', unsafe_allow_html=True,
    )


@st.fragment(run_every=10)
def _king_championship_metrics():
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    today  = get_today_stats(paper=PAPER_TRADING)
    fees   = get_todays_fees(paper=PAPER_TRADING)
    wr_14d = get_win_rate(lookback_days=14, paper=PAPER_TRADING)

    total     = stats.get('total', 0)
    wins      = stats.get('wins', 0)
    losses    = stats.get('losses', 0)
    wr_all    = stats.get('win_rate', 0)
    total_pnl = stats.get('total_pnl', 0)

    recent_all = get_recent_trades(limit=200, paper=PAPER_TRADING)
    pnls = [float(t.get('pnl_usd') or 0) for t in recent_all if (t.get('pnl_usd') or 0) != 0]
    gross_wins   = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    pf = gross_wins / gross_losses if gross_losses > 0 else (2.0 if gross_wins > 0 else 1.0)

    sigs = get_todays_signals()
    holds_today  = sum(1 for s in sigs if not s.get('acted_on'))
    trades_today = today.get('total', 0)
    d_score = holds_today / max(holds_today + trades_today, 1)

    pnl_color = '#00C853' if today.get('net_pnl', 0) >= 0 else '#FF1744'
    wr_color  = '#00C853' if wr_all >= 0.52 else '#FDB927' if wr_all >= 0.45 else '#FF1744'
    pf_color  = '#00C853' if pf >= 1.4 else '#FDB927' if pf >= 1.2 else '#FF1744'
    t_color   = '#00C853' if total_pnl >= 0 else '#FF1744'
    d_color   = '#00C853' if d_score >= 0.7 else '#FDB927' if d_score >= 0.4 else '#FF8F00'

    def _champ(col, icon, label, value, sub, color, glow=False):
        glow_cls = ' champ-glow' if glow else ''
        col.markdown(
            f'<div class="champ-metric{glow_cls}">'
            f'<div class="champ-lbl">{icon} {label}</div>'
            f'<div class="champ-val" style="color:{color};">{value}</div>'
            f'<div class="champ-sub">{sub}</div>'
            f'</div>', unsafe_allow_html=True,
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    _champ(c1, '📈', "TODAY P&L",
           f"{'+'if today.get('net_pnl',0)>=0 else ''}${today.get('net_pnl',0):.2f}",
           f"gross {today.get('gross_pnl',0):+.2f} · fees −${fees:.2f}",
           pnl_color, glow=(today.get('net_pnl', 0) > 5))
    _champ(c2, '🎯', "WIN RATE",
           f'{wr_all:.1%}',
           f'{wins}W / {losses}L · 14d: {wr_14d:.0%}',
           wr_color)
    _champ(c3, '📊', "PROFIT FACTOR",
           f'{pf:.2f}x',
           f'need 1.4 for live · {total} trades',
           pf_color)
    _champ(c4, '💰', "ALL-TIME P&L",
           f'${total_pnl:+.2f}',
           f'avg ${total_pnl/max(total,1):+.3f}/trade',
           t_color)
    _champ(c5, '🧘', "DISCIPLINE",
           f'{holds_today} HOLDs',
           f'{trades_today} entries · {d_score:.0%} patience',
           d_color)


@st.fragment(run_every=20)
def _king_positions():
    pos = _live_positions()
    all_positions = []
    for strat, syms in pos.items():
        for sym, p in syms.items():
            all_positions.append((strat, sym, p))

    st.markdown('<div class="panel-title">🏀 OPEN POSITIONS</div>', unsafe_allow_html=True)

    if not all_positions:
        st.markdown(
            '<div style="color:#333;font-size:12px;padding:12px 0;font-style:italic;">'
            '"Sometimes the best move is no move." — waiting for the right setup.</div>',
            unsafe_allow_html=True,
        )
        return

    for strat, sym, p in all_positions:
        entry  = float(p.get('entry', 0) or 0)
        stop   = float(p.get('stop', 0) or 0)
        target = float(p.get('target', 0) or 0)
        ts_e   = p.get('ts_entry', '')
        direction = p.get('direction', 'LONG')

        # Compute progress toward target
        if entry > 0 and target > 0 and stop > 0 and direction == 'LONG':
            total_move = target - entry
            # We'd need current price; approximate from entry
            progress = 0.0
            progress_html = f'<span style="color:#444;">tracking...</span>'
        else:
            progress_html = ''

        stop_pct = abs(entry - stop) / entry * 100 if entry > 0 else 0
        target_pct = abs(target - entry) / entry * 100 if entry > 0 else 0

        dir_color = '#00C853' if direction == 'LONG' else '#FF1744'
        strat_label = {'crypto': '₿ CRYPTO', 'crypto_mean_reversion': '↔ MR',
                       'perp': '⚡ PERP', 'futures_scalper': '📈 MES'}.get(strat, strat.upper())

        age_str = ''
        if ts_e:
            try:
                from datetime import timezone as _tz
                dt = datetime.fromisoformat(ts_e)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=_tz.utc)
                age_mins = int((datetime.now(_tz.utc) - dt).total_seconds() / 60)
                age_str = f'{age_mins}m ago'
            except Exception:
                pass

        st.markdown(
            f'<div class="pos-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="color:#FDB927;font-weight:900;font-size:14px;">{sym}</span>'
            f'<span style="color:{dir_color};font-size:10px;font-weight:700;">{direction}</span>'
            f'<span style="color:#333;font-size:9px;text-transform:uppercase;">{strat_label}</span>'
            f'</div>'
            f'<div style="display:flex;gap:16px;margin-top:6px;font-size:11px;">'
            f'<span style="color:#555;">Entry <span style="color:#aaa;">${entry:.4f}</span></span>'
            f'<span style="color:#555;">Stop <span style="color:#FF1744;">${stop:.4f}</span>'
            f'<span style="color:#444;font-size:10px;"> ({stop_pct:.1f}%)</span></span>'
            f'<span style="color:#555;">Target <span style="color:#00C853;">${target:.4f}</span>'
            f'<span style="color:#444;font-size:10px;"> ({target_pct:.1f}%)</span></span>'
            f'</div>'
            f'<div style="color:#333;font-size:10px;margin-top:4px;">{age_str}</div>'
            f'</div>', unsafe_allow_html=True,
        )


@st.fragment(run_every=15)
def _king_recent_trades():
    trades = get_recent_trades(limit=12, paper=PAPER_TRADING)
    st.markdown('<div class="panel-title">📋 RECENT TRADES</div>', unsafe_allow_html=True)
    if not trades:
        st.markdown('<div style="color:#333;font-size:11px;padding:6px 0;">No trades yet.</div>',
                    unsafe_allow_html=True)
        return
    html = ''
    for t in trades:
        pnl    = float(t.get('pnl_usd') or 0)
        sym    = t.get('symbol', '')
        action = t.get('action', '')
        ts     = fmt_ts(t.get('ts', ''), show_date=False)
        if pnl == 0 and action not in ('SELL', 'CLOSE', 'SELL_SHORT'):
            icon = '→'
            color = '#444'
        elif pnl > 0:
            icon = '✓'
            color = '#00C853'
        elif pnl < 0:
            icon = '✗'
            color = '#FF1744'
        else:
            icon = '○'
            color = '#555'

        pnl_str = f'${pnl:+.2f}' if pnl != 0 else '(open)'
        html += (
            f'<div class="trade-row">'
            f'<span style="color:{color};min-width:14px;">{icon}</span>'
            f'<span style="color:#FDB927;min-width:90px;">{sym[:12]}</span>'
            f'<span style="color:#444;min-width:50px;">{ts}</span>'
            f'<span style="color:{color};margin-left:auto;font-weight:700;">{pnl_str}</span>'
            f'</div>'
        )
    st.markdown(f'<div style="max-height:220px;overflow-y:auto;">{html}</div>',
                unsafe_allow_html=True)


@st.fragment(run_every=30)
def _king_edge_monitor():
    strategies = [
        ('crypto_macd_consensus', '₿ CRYPTO MACD'),
        ('futures_scalper',       '⚡ MES FUTURES'),
        ('crypto_mean_reversion', '↔ MEAN REVERSION'),
    ]
    st.markdown('<div class="panel-title">⬡ EDGE MONITOR</div>', unsafe_allow_html=True)
    status_icons = {'STRONG':'👑', 'OK':'✅', 'DEGRADED':'⚠️', 'BLOCKED':'🚫', 'UNCERTAIN':'◉'}

    for strat, label in strategies:
        es     = get_edge_state(strat, paper=PAPER_TRADING)
        status = es['status']
        color  = _edge_color(status)
        icon   = status_icons.get(status, '◉')
        pf     = es['profit_factor']
        mult   = es['sizing_multiplier']
        trades = es['window_trades']
        css    = _edge_css_class(status)

        bar_filled = int(mult * 10)
        bar_html = ''.join(
            f'<span style="color:{color};">█</span>' if j < bar_filled
            else '<span style="color:#1a1a1a;">█</span>'
            for j in range(10)
        )
        mult_str = f'{mult:.0%}' if mult > 0 else '<span style="color:#FF1744;font-weight:900;">BLOCKED</span>'

        st.markdown(
            f'<div class="edge-card {css}" style="margin:5px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="color:#aaa;font-size:11px;font-weight:700;">{label}</span>'
            f'<span style="color:{color};font-weight:900;font-size:11px;">{icon} {status}</span>'
            f'</div>'
            f'<div style="margin:6px 0 3px 0;font-family:monospace;font-size:11px;">{bar_html}</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:10px;">'
            f'<span style="color:#444;">PF <span style="color:{color};">{pf:.2f}</span></span>'
            f'<span style="color:#444;">size <span style="color:#aaa;">{mult_str}</span></span>'
            f'<span style="color:#444;">{trades} trades</span>'
            f'</div>'
            f'</div>', unsafe_allow_html=True,
        )


@st.fragment(run_every=60)
def _king_signal_leaderboard():
    st.markdown('<div class="panel-title">🏆 SIGNAL LEADERBOARD</div>', unsafe_allow_html=True)
    try:
        conn = _db_conn()
        rows = conn.execute("""
            SELECT signal_name, SUM(fires) as fires,
                   SUM(wins) as wins, SUM(losses) as losses,
                   SUM(total_pnl) as total_pnl,
                   AVG(win_rate) as win_rate, AVG(bayesian_pts) as pts
            FROM signal_stats
            GROUP BY signal_name
            HAVING fires >= 2
            ORDER BY win_rate DESC, total_pnl DESC
        """).fetchall()
        conn.close()
    except Exception:
        rows = []

    if not rows:
        st.markdown('<div style="color:#333;font-size:11px;padding:6px;">No signal data yet — accumulates after trades are attributed.</div>',
                    unsafe_allow_html=True)
        return

    html = ''
    for i, r in enumerate(rows[:8]):
        wr    = r['win_rate'] or 0
        pnl   = r['total_pnl'] or 0
        fires = r['fires'] or 0
        name  = (r['signal_name'] or '').replace('_', ' ').upper()
        wr_color  = '#00C853' if wr >= 0.55 else '#FDB927' if wr >= 0.45 else '#FF1744'
        pnl_color = '#00C853' if pnl >= 0 else '#FF1744'
        rank_icon = '👑' if i == 0 else ('⚡' if i == 1 else ('✅' if wr >= 0.52 else ('⚠️' if wr >= 0.40 else '❌')))
        bar = int(wr * 10)
        bar_html = ''.join(
            f'<span style="color:{wr_color};font-size:9px;">█</span>' if j < bar
            else '<span style="color:#1a1a1a;font-size:9px;">█</span>'
            for j in range(10)
        )
        html += (
            f'<div style="display:flex;align-items:center;gap:6px;padding:3px 0;'
            f'border-bottom:1px solid #0a0a0a;font-size:11px;">'
            f'<span style="min-width:16px;">{rank_icon}</span>'
            f'<span style="color:#ccc;flex:1;font-family:monospace;font-size:10px;">{name[:20]}</span>'
            f'<span style="font-family:monospace;">{bar_html}</span>'
            f'<span style="color:{wr_color};font-weight:900;min-width:32px;text-align:right;">{wr:.0%}</span>'
            f'<span style="color:{pnl_color};min-width:44px;text-align:right;">${pnl:+.2f}</span>'
            f'<span style="color:#333;min-width:24px;text-align:right;">{fires}×</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="font-size:9px;color:#333;text-transform:uppercase;letter-spacing:1px;'
        f'padding-bottom:4px;display:flex;gap:6px;">'
        f'<span style="min-width:16px;"></span><span style="flex:1;">Signal</span>'
        f'<span style="min-width:76px;text-align:right;">Win%</span>'
        f'<span style="min-width:44px;text-align:right;">P&L</span>'
        f'<span style="min-width:24px;text-align:right;">N</span>'
        f'</div>'
        f'{html}', unsafe_allow_html=True,
    )


@st.fragment(run_every=60)
def _king_ring_progress():
    """Scale unlock — championship ring tracker."""
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    total  = stats.get('total', 0)
    wr     = stats.get('win_rate', 0)
    pnl    = stats.get('total_pnl', 0)
    wr_14d = get_win_rate(lookback_days=14, paper=PAPER_TRADING)

    from scripts.check_readiness import check_criteria
    try:
        results = check_criteria(fast_track=False)
    except Exception:
        results = {}

    st.markdown('<div class="panel-title">💍 CHAMPIONSHIP RINGS</div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="color:#555;font-size:10px;letter-spacing:1px;margin-bottom:8px;">'
        'All 8 rings = PAPER → LIVE</div>', unsafe_allow_html=True,
    )

    if 'error' in results:
        st.markdown(
            f'<div style="color:#444;font-size:11px;">{results["error"]}</div>',
            unsafe_allow_html=True,
        )
        return

    ring_labels = {
        'days_trading':   ('📅', 'Days of Activity'),
        'trade_count':    ('📊', 'Trade Count'),
        'win_rate':       ('🎯', 'Win Rate ≥ 52%'),
        'profit_factor':  ('💰', 'Profit Factor ≥ 1.4'),
        'max_daily_loss': ('🛡️', 'No Daily Loss >3.5%'),
        'no_crashes':     ('⚡', 'Zero System Crashes'),
        'positive_pnl':   ('✅', 'Positive Total P&L'),
        'avg_pnl':        ('📈', 'Avg P&L/Trade ≥ $0.10'),
    }

    earned = 0
    html = ''
    for key, (icon, label) in ring_labels.items():
        r = results.get(key, {})
        passed = r.get('pass', False)
        detail = r.get('label', '')
        if passed:
            earned += 1
            css    = 'ring-earned'
            status = '💍'
            color  = '#FDB927'
        else:
            css    = 'ring-pending'
            status = '○'
            color  = '#333'

        html += (
            f'<div class="ring-item {css}">'
            f'<span style="font-size:16px;">{status}</span>'
            f'<div>'
            f'<div style="color:{color};font-size:11px;font-weight:700;">{icon} {label}</div>'
            f'<div style="color:#444;font-size:10px;">{detail}</div>'
            f'</div>'
            f'</div>'
        )

    progress_pct = earned / 8
    bar_filled = int(progress_pct * 10)
    bar_html = ''.join(
        '<span style="color:#FDB927;font-size:14px;">█</span>' if j < bar_filled
        else '<span style="color:#1a1a1a;font-size:14px;">█</span>'
        for j in range(10)
    )
    color_prog = '#00C853' if earned == 8 else '#FDB927' if earned >= 6 else '#FF8F00'

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
        f'<span style="color:{color_prog};font-size:20px;font-weight:900;">{earned}/8</span>'
        f'<span style="font-family:monospace;">{bar_html}</span>'
        f'{"<span style=\"color:#00C853;font-weight:900;font-size:14px;\">🏆 CHAMPIONSHIP!</span>" if earned == 8 else ""}'
        f'</div>'
        f'{html}', unsafe_allow_html=True,
    )


@st.fragment(run_every=30)
def _king_market_regime():
    st.markdown('<div class="panel-title">🌐 MARKET REGIME</div>', unsafe_allow_html=True)
    try:
        from strategies.ai_agents.regime_detector import detect_regime
        opt = get_options_signals()
        iv_rank = opt.get('iv_rank', 0.5)
        iv_reg  = opt.get('iv_regime', 'NORMAL_IV')
        vix     = opt.get('vix_level')
        panic   = opt.get('panic_signal', False)
        ts_val  = opt.get('term_structure', 'FLAT')

        iv_color = '#FF8F00' if iv_reg == 'HIGH_IV' else '#00C853' if iv_reg == 'LOW_IV' else '#FDB927'
        ts_color = '#FF1744' if ts_val == 'BACKWARDATION' else '#00C853' if ts_val == 'CONTANGO' else '#888'

        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;font-size:11px;">'
            f'<div style="background:#0a0a14;border:1px solid #1a1a2a;border-radius:4px;padding:6px 10px;">'
            f'<span style="color:#555;">IV Rank </span>'
            f'<span style="color:{iv_color};font-weight:900;">{iv_rank:.0%} {iv_reg.replace("_IV","")}</span>'
            f'</div>'
            f'<div style="background:#0a0a14;border:1px solid #1a1a2a;border-radius:4px;padding:6px 10px;">'
            f'<span style="color:#555;">Term </span>'
            f'<span style="color:{ts_color};font-weight:900;">{ts_val}</span>'
            f'</div>'
            + (f'<div style="background:#0a0a14;border:1px solid #1a1a2a;border-radius:4px;padding:6px 10px;">'
               f'<span style="color:#555;">VIX </span>'
               f'<span style="color:#aaa;font-weight:900;">{vix:.1f}</span>'
               f'</div>' if vix else '')
            + (f'<div style="background:#3a000022;border:1px solid #FF1744;border-radius:4px;padding:6px 10px;">'
               f'<span style="color:#FF1744;font-weight:900;">⚠️ PANIC SIGNAL</span>'
               f'</div>' if panic else '')
            + f'</div>', unsafe_allow_html=True,
        )
    except Exception:
        st.markdown('<div style="color:#333;font-size:11px;">Market data loading...</div>',
                    unsafe_allow_html=True)


@st.fragment(run_every=10)
def _king_notifications():
    notifs = get_recent_notifications(limit=20)
    st.markdown('<div class="panel-title">🔔 NOTIFICATIONS</div>', unsafe_allow_html=True)
    if not notifs:
        st.markdown('<div style="color:#222;font-size:11px;font-style:italic;padding:6px 0;">Quiet. System is watching.</div>',
                    unsafe_allow_html=True)
        return
    for n in notifs[:12]:
        msg   = n.get('message', '')
        level = n.get('level', 'INFO')
        ts    = fmt_ts(n.get('ts', ''), show_date=False)

        if level in ('ERROR', 'HALT'):
            css, icon = 'notif-error', '🔴'
        elif level == 'WARNING':
            css, icon = 'notif-warn', '⚠️'
        elif any(k in msg for k in ('BUY', 'LONG', 'SHORT', 'CLOSED', 'P&L')):
            css, icon = 'notif-trade', '💰'
        else:
            css, icon = 'notif-info', '·'

        st.markdown(
            f'<div class="{css}">'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#777;">{icon} {msg[:80]}</span>'
            f'<span style="color:#333;font-size:10px;white-space:nowrap;margin-left:8px;">{ts}</span>'
            f'</div>'
            f'</div>', unsafe_allow_html=True,
        )


def render_king_tab():
    _king_status_bar()
    _king_lebron_banner()
    _king_championship_metrics()

    st.markdown('<hr style="border-color:#0d1e3a;margin:14px 0;">', unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([3, 4, 3])

    with col_l:
        _king_positions()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _king_recent_trades()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _king_market_regime()

    with col_c:
        _king_edge_monitor()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _king_signal_leaderboard()

    with col_r:
        _king_ring_progress()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _king_notifications()

    # AI Chat
    st.markdown('<hr style="border-color:#0d1e3a;margin:14px 0;">', unsafe_allow_html=True)
    with st.expander("🤖 Ask The King's AI Brain"):
        if 'chat_messages' not in st.session_state:
            st.session_state['chat_messages'] = []
        for msg in st.session_state['chat_messages']:
            css = 'chat-user' if msg['role'] == 'user' else 'chat-bot'
            st.markdown(f'<div class="{css}">{msg["content"]}</div>', unsafe_allow_html=True)
        user_input = st.chat_input("Ask anything about your trades, signals, or strategy...")
        if user_input:
            st.session_state['chat_messages'].append({'role': 'user', 'content': user_input})
            ctx = build_chat_context()
            response = call_claude_chat(st.session_state['chat_messages'], ctx)
            st.session_state['chat_messages'].append({'role': 'assistant', 'content': response})
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# SAIYAN MODE TAB
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def _saiyan_transformation_state():
    """Heat level → DBZ transformation card."""
    heat = get_heat_level(paper=PAPER_TRADING)
    level  = heat.get('level', 0)
    label  = heat.get('label', 'NORMAL')
    factor = heat.get('size_factor', 1.0)
    pct    = heat.get('pct_drawn', 0.0)
    pnl    = heat.get('daily_pnl', 0.0)

    is_halted, halt_reason = _live_halt_state()
    if is_halted:
        level = 4
        label = 'HALT'

    TRANSFORMS = {
        0: {
            'css': 'transform-normal',
            'name': 'BASE FORM',
            'power': 'OPERATING AT FULL POWER',
            'color': '#00C853',
            'emoji': '💪',
            'desc': 'All systems nominal. Full position sizing. Edge is intact.',
            'icon': '🟢',
        },
        1: {
            'css': 'transform-caution',
            'name': 'KAIOKEN x2',
            'power': 'PUSHING THE LIMITS — CAUTION',
            'color': '#FDB927',
            'emoji': '⚡',
            'desc': 'Drawdown emerging. Sizing reduced to 75%. Stay disciplined.',
            'icon': '🟡',
        },
        2: {
            'css': 'transform-warning',
            'name': 'SUPER SAIYAN',
            'power': 'POWER SURGE — SYSTEM UNDER PRESSURE',
            'color': '#FF8F00',
            'emoji': '⚡⚡',
            'desc': 'Significant drawdown. Sizing at 50%. Every trade must count.',
            'icon': '🟠',
        },
        3: {
            'css': 'transform-danger',
            'name': 'SUPER SAIYAN 2',
            'power': 'CRITICAL — NEAR THE LIMIT',
            'color': '#FF4444',
            'emoji': '🔥',
            'desc': 'Approaching daily loss limit. Only 25% sizing. Prepare for halt.',
            'icon': '🔴',
        },
        4: {
            'css': 'transform-halt',
            'name': 'POWER LIMIT EXCEEDED',
            'power': '⛔ TRADING HALTED ⛔',
            'color': '#FF0000',
            'emoji': '💀',
            'desc': halt_reason or 'Daily loss limit hit. The Saiyan must rest.',
            'icon': '🔴',
        },
    }

    t = TRANSFORMS.get(level, TRANSFORMS[0])

    ki_pct = min(pct / (MAX_DAILY_LOSS_PCT or 0.04), 1.0) if pct > 0 else 0
    ki_color = t['color']

    bar_html = f'''
    <div class="ki-bar-container" style="margin:10px 0;">
        <div class="ki-bar-fill ki-glow"
             style="width:{ki_pct*100:.0f}%;background:linear-gradient(90deg,{ki_color}44,{ki_color});">
        </div>
    </div>
    '''

    st.markdown(
        f'<div class="transform-card {t["css"]}">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<div>'
        f'<div class="transform-name" style="color:{t["color"]};">{t["emoji"]} {t["name"]}</div>'
        f'<div class="transform-power" style="color:{t["color"]}88;">{t["power"]}</div>'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div style="color:{t["color"]};font-size:24px;font-weight:900;">'
        f'${pnl:+.2f}</div>'
        f'<div style="color:{t["color"]}88;font-size:11px;">'
        f'{pct*100:.1f}% of daily limit</div>'
        f'</div>'
        f'</div>'
        f'{bar_html}'
        f'<div style="color:#555;font-size:11px;margin-top:4px;">{t["desc"]}</div>'
        f'<div style="display:flex;gap:16px;margin-top:8px;font-size:11px;">'
        f'<span style="color:#333;">Position sizing: '
        f'<span style="color:{t["color"]};font-weight:900;">{factor:.0%}</span></span>'
        f'<span style="color:#333;">Daily limit: '
        f'<span style="color:#555;">{MAX_DAILY_LOSS_PCT*100:.0f}%</span></span>'
        f'</div>'
        f'</div>', unsafe_allow_html=True,
    )


@st.fragment(run_every=30)
def _saiyan_agent_warriors():
    """3 DBZ agent warrior cards with latest vote."""
    debates = get_recent_debates(limit=1)
    latest = debates[0] if debates else {}

    # Try to get agent votes from latest debate
    agent_votes = {}
    if latest:
        raw_agents = latest.get('agent_votes') or latest.get('agents') or ''
        if isinstance(raw_agents, str):
            try:
                agent_votes = json.loads(raw_agents)
            except Exception:
                pass
        elif isinstance(raw_agents, dict):
            agent_votes = raw_agents

    WARRIORS = [
        {
            'key':       'funding_regime',
            'dbz_name':  'BARDOCK',
            'title':     'Macro & Funding Intel',
            'css':       'warrior-bardock',
            'color':     '#8B0000',
            'accent':    '#FF4444',
            'emoji':     '🔴',
            'domain':    'Funding Rate · OI · VIX · DXY · Macro Score',
            'lore':      'The warrior who reads the galaxy\'s economic energy.',
        },
        {
            'key':       'momentum_structure',
            'dbz_name':  'VEGETA',
            'title':     'Technical Momentum',
            'css':       'warrior-vegeta',
            'color':     '#1D428A',
            'accent':    '#4488FF',
            'emoji':     '🔵',
            'domain':    'ADX · Squeeze · WAE · WaveTrend · MACD · SuperTrend',
            'lore':      'The Prince of all setups. Requires 2+ aligned signals.',
        },
        {
            'key':       'risk_economics',
            'dbz_name':  'KRILLIN',
            'title':     'Trade Economics',
            'css':       'warrior-krillin',
            'color':     '#FF8C00',
            'accent':    '#FFB347',
            'emoji':     '🟠',
            'domain':    'ATR/Fees · Volume Gate · Time-of-Day · Stop Sizing',
            'lore':      'The most disciplined fighter. Hard kill switch.',
        },
    ]

    # Agent accuracy from DB
    try:
        conn = _db_conn()
        agent_acc = {
            r['agent_name']: {
                'accuracy': float(r['correct_votes'] or 0) / max(r['total_votes'] or 1, 1),
                'votes':    r['total_votes'] or 0,
            }
            for r in conn.execute(
                "SELECT agent_name, total_votes, correct_votes FROM agent_stats"
            ).fetchall()
        }
        conn.close()
    except Exception:
        agent_acc = {}

    cols = st.columns(3)
    for i, w in enumerate(WARRIORS):
        key     = w['key']
        vote    = agent_votes.get(key, {})
        verdict = vote.get('signal', vote.get('verdict', '—')).upper() if vote else '—'
        conf    = float(vote.get('confidence', 0)) if vote else 0.0
        reason  = str(vote.get('reason', ''))[:120] if vote else ''
        acc_data = agent_acc.get(key, {})
        acc  = acc_data.get('accuracy', 0)
        nvotes = acc_data.get('votes', 0)

        if verdict == 'BUY':
            v_css   = 'warrior-verdict-buy'
            v_icon  = '▲ BUY'
        elif verdict in ('SELL', 'SHORT'):
            v_css   = 'warrior-verdict-sell'
            v_icon  = '▼ SELL'
        elif verdict == 'HOLD':
            v_css   = 'warrior-verdict-hold'
            v_icon  = '◼ HOLD'
        else:
            v_css   = 'warrior-verdict-hold'
            v_icon  = '— IDLE'

        # Ki power bar (accuracy → power level)
        ki_pct  = acc if nvotes >= 3 else 0.5
        ki_bar = ''.join(
            f'<span style="color:{w["accent"]};">▐</span>' if j < int(ki_pct * 10)
            else '<span style="color:#111;">▐</span>'
            for j in range(10)
        )

        acc_str = f'{acc:.0%} ({nvotes} votes)' if nvotes >= 3 else 'Not yet calibrated'
        acc_color = '#00C853' if acc >= 0.60 else '#FDB927' if acc >= 0.50 else '#FF4444'

        with cols[i]:
            st.markdown(
                f'<div class="warrior-card {w["css"]}">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                f'<div>'
                f'<div class="warrior-name" style="color:{w["accent"]};">{w["emoji"]} {w["dbz_name"]}</div>'
                f'<div class="warrior-role" style="color:{w["color"]};">{w["title"]}</div>'
                f'</div>'
                f'<div class="{v_css}" style="font-size:18px;font-weight:900;">{v_icon}</div>'
                f'</div>'
                f'<div style="font-family:monospace;font-size:9px;margin-top:8px;">{ki_bar}</div>'
                f'<div style="color:{acc_color};font-size:10px;margin-top:4px;">'
                f'Accuracy: {acc_str}</div>'
                f'<div style="color:#333;font-size:9px;text-transform:uppercase;letter-spacing:1px;margin-top:8px;">'
                f'{w["domain"]}</div>'
                + (f'<div style="color:#444;font-size:10px;margin-top:8px;font-style:italic;">'
                   f'"{reason}"</div>' if reason else '')
                + (f'<div style="display:flex;justify-content:space-between;margin-top:8px;">'
                   f'<span style="color:#333;font-size:10px;">Conf</span>'
                   f'<span style="color:{w["accent"]};font-size:11px;font-weight:900;">{conf:.0%}</span>'
                   f'</div>' if conf > 0 else '')
                + f'</div>', unsafe_allow_html=True,
            )


@st.fragment(run_every=60)
def _saiyan_ml_scouter():
    """ML gate as a DBZ scouter reading power levels."""
    st.markdown(
        '<div style="text-align:center;margin-bottom:8px;">'
        '<span style="color:#00FF00;font-weight:900;font-size:11px;letter-spacing:3px;'
        'text-transform:uppercase;font-family:monospace;">⊕ SCOUTER READING</span>'
        '</div>', unsafe_allow_html=True,
    )

    try:
        from learning.ml_signal import _model
        from config import ML_SIGNAL_MIN_PROB
        model_trained = _model is not None
    except Exception:
        model_trained = False
        ML_SIGNAL_MIN_PROB = 0.45

    try:
        conn = _db_conn()
        total_trades = conn.execute(
            "SELECT COUNT(*) as n FROM trades WHERE pnl_usd != 0 AND paper=1"
        ).fetchone()['n']
        conn.close()
    except Exception:
        total_trades = 0

    trades_to_next = max(0, 50 - (total_trades % 50)) if total_trades > 0 else 50

    if model_trained:
        power_color = '#00FF00'
        power_label = 'POWER LEVEL OPTIMAL'
        power_val = f'{ML_SIGNAL_MIN_PROB:.0%}'
        power_desc = 'Scouter calibrated. Gate filtering weak signals.'
    else:
        power_color = '#FF8F00'
        power_label = 'INSUFFICIENT DATA'
        power_val = f'{total_trades}/50'
        power_desc = f'Needs {trades_to_next} more trades to calibrate.'

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f'<div class="scouter-card">'
            f'<div class="scouter-label">ML Gate</div>'
            f'<div class="scouter-power" style="color:{power_color};">{power_val}</div>'
            f'<div class="scouter-label" style="color:{power_color}44;">'
            f'{"ACTIVE" if model_trained else "UNTRAINED"}</div>'
            f'</div>', unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f'<div class="scouter-card">'
            f'<div class="scouter-label">Trades Scanned</div>'
            f'<div class="scouter-power">{total_trades}</div>'
            f'<div class="scouter-label" style="color:#00FF0044;">COMPLETED</div>'
            f'</div>', unsafe_allow_html=True,
        )

    with col3:
        next_retrain = trades_to_next
        st.markdown(
            f'<div class="scouter-card">'
            f'<div class="scouter-label">Next Retrain</div>'
            f'<div class="scouter-power">{next_retrain}</div>'
            f'<div class="scouter-label" style="color:#00FF0044;">TRADES AWAY</div>'
            f'</div>', unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="text-align:center;color:{power_color}44;font-size:10px;'
        f'font-family:monospace;margin-top:6px;letter-spacing:2px;">'
        f'{power_desc}</div>', unsafe_allow_html=True,
    )


@st.fragment(run_every=15)
def _saiyan_power_scanner():
    """Signal power scanner — recent signals as energy bars."""
    sigs = get_todays_signals()
    fired  = [s for s in sigs if s.get('acted_on')]
    passed = [s for s in sigs if not s.get('acted_on')]

    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        'margin-bottom:8px;">'
        '<span style="color:#FDB927;font-weight:900;font-size:11px;letter-spacing:2px;">'
        '⚡ SIGNAL POWER SCANNER</span>'
        f'<span style="color:#555;font-size:10px;">{len(fired)} fired · {len(passed)} passed</span>'
        '</div>', unsafe_allow_html=True,
    )

    recent_sigs = (fired + passed)[:12]
    if not recent_sigs:
        st.markdown(
            '<div style="color:#333;font-size:11px;font-family:monospace;padding:8px;">'
            '--- NO SIGNALS YET ---</div>', unsafe_allow_html=True,
        )
        return

    html = ''
    for s in recent_sigs:
        sym     = s.get('symbol', '?')
        conf    = float(s.get('confidence', 0))
        acted   = bool(s.get('acted_on', False))
        ts      = fmt_ts(s.get('ts', ''), show_date=False)
        strat   = s.get('strategy', '')

        bar_count = int(conf * 10)
        if acted:
            bar_color = '#FDB927'
            row_css   = 'signal-fire'
            prefix    = '⚡'
        else:
            bar_color = '#333'
            row_css   = 'signal-hold'
            prefix    = '·'

        bar_html = ''.join(
            f'<span style="color:{bar_color};font-size:10px;">▐</span>' if j < bar_count
            else '<span style="color:#111;font-size:10px;">▐</span>'
            for j in range(10)
        )

        html += (
            f'<div class="signal-row {row_css}">'
            f'<span style="color:#FDB927;min-width:14px;">{prefix}</span>'
            f'<span style="color:#ccc;min-width:100px;font-family:monospace;">{sym[:12]}</span>'
            f'<span style="font-family:monospace;">{bar_html}</span>'
            f'<span style="color:{bar_color};min-width:32px;text-align:right;font-weight:700;">'
            f'{conf:.0%}</span>'
            f'<span style="color:#333;min-width:44px;text-align:right;font-size:10px;">{ts}</span>'
            f'</div>'
        )

    st.markdown(f'<div style="max-height:200px;overflow-y:auto;">{html}</div>',
                unsafe_allow_html=True)


def render_saiyan_tab():
    # DBZ Header
    st.markdown(
        '<div style="text-align:center;padding:12px 0;">'
        '<div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#FDB927;'
        'text-shadow:0 0 20px #FDB92777;font-family:Impact,sans-serif;">'
        '⚡ SAIYAN MODE ⚡</div>'
        '<div style="color:#555;font-size:11px;letter-spacing:4px;text-transform:uppercase;margin-top:4px;">'
        'Power Level Monitoring System · v9.0</div>'
        '</div>', unsafe_allow_html=True,
    )

    # Transformation state (full width)
    _saiyan_transformation_state()

    st.markdown('<hr style="border-color:#1a001a;margin:12px 0;">', unsafe_allow_html=True)

    # Warrior cards
    st.markdown(
        '<div style="color:#666;font-size:9px;text-transform:uppercase;letter-spacing:3px;'
        'margin-bottom:8px;">⚔ THE THREE AGENTS</div>', unsafe_allow_html=True,
    )
    _saiyan_agent_warriors()

    st.markdown('<hr style="border-color:#1a001a;margin:12px 0;">', unsafe_allow_html=True)

    # Bottom row: ML Scouter + Signal Scanner
    col_l, col_r = st.columns([1, 2])
    with col_l:
        _saiyan_ml_scouter()
    with col_r:
        _saiyan_power_scanner()

    # Latest debate outcome
    st.markdown('<hr style="border-color:#1a001a;margin:12px 0;">', unsafe_allow_html=True)
    debates = get_recent_debates(limit=3)
    if debates:
        st.markdown(
            '<div style="color:#666;font-size:9px;text-transform:uppercase;letter-spacing:3px;'
            'margin-bottom:8px;">⚡ LAST BATTLE RESULTS</div>', unsafe_allow_html=True,
        )
        for d in debates:
            sym  = d.get('symbol', '?')
            sig  = (d.get('final_signal') or '').upper()
            bv   = d.get('buy_votes', 0)
            hv   = d.get('hold_votes', 0)
            conf = float(d.get('confidence', 0))
            reas = str(d.get('reasoning', ''))[:150]

            if sig == 'BUY':
                sig_color, sig_icon = '#00C853', '▲ BUY'
            elif sig in ('SELL', 'SHORT'):
                sig_color, sig_icon = '#FF1744', '▼ SELL'
            else:
                sig_color, sig_icon = '#555', '◼ HOLD'

            vote_bar = ''.join(
                '<span style="color:#00C853;">●</span>' if j < bv
                else '<span style="color:#555;">○</span>'
                for j in range(3)
            )

            st.markdown(
                f'<div class="debate-card" style="margin-bottom:6px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:#FDB927;font-weight:900;">{sym}</span>'
                f'<span style="color:{sig_color};font-weight:900;font-size:18px;">{sig_icon}</span>'
                f'<span style="color:#555;font-size:11px;">{vote_bar} {bv}BUY/{hv}HOLD conf={conf:.0%}</span>'
                f'</div>'
                + (f'<div style="color:#555;font-size:10px;margin-top:6px;">{reas}</div>' if reas else '')
                + f'</div>', unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# FILM ROOM TAB
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def _film_scan_feed():
    entries = get_scan_feed(limit=60)
    last_ts = fmt_ts(entries[0].get('ts',''), show_date=False, show_seconds=True) if entries else '—'
    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">'
        f'<span style="color:#FDB927;font-weight:900;font-size:12px;">📡 LIVE SCAN FEED</span>'
        f'<span style="background:#FF1744;color:#fff;font-size:9px;font-weight:900;'
        f'padding:2px 6px;border-radius:3px;letter-spacing:2px;" class="live-badge">● LIVE</span>'
        f'<span style="color:#333;font-size:9px;font-family:monospace;">last: {last_ts}</span>'
        f'</div>', unsafe_allow_html=True,
    )
    if not entries:
        st.markdown('<div style="color:#333;padding:12px;font-size:12px;">Waiting for first scan...</div>',
                    unsafe_allow_html=True)
        return
    feed_html = ''
    for e in entries[:50]:
        msg = e.get('message', '')
        ts  = fmt_ts(e.get('ts', ''), show_date=False)
        if any(k in msg for k in ('⛔', 'block', 'VETO', 'Edge gate')):
            color, dot = '#333', '⊘'
        elif any(k in msg for k in ('→ BUY', '→ LONG', '→ SHORT', '🟢')):
            color, dot = '#FDB927', '●'
        elif any(k in msg for k in ('CLOSED', 'EXITED', 'TRADED', 'P&L')):
            color, dot = '#00C853', '✓'
        elif any(k in msg for k in ('Scanning', 'Analyzing', '[crypto]')):
            color, dot = '#2255aa', '◎'
        elif any(k in msg for k in ('Debate', 'agent')):
            color, dot = '#9955ff', '🤖'
        elif any(k in msg for k in ('Edge', 'edge')):
            color, dot = '#FF8F00', '⬡'
        else:
            color, dot = '#2a2a2a', '·'
        feed_html += (
            f'<div class="feed-row">'
            f'<span style="color:#222;min-width:52px;flex-shrink:0;">{ts}</span>'
            f'<span style="color:{color};line-height:1.4;">{dot} {msg[:100]}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="max-height:350px;overflow-y:auto;background:#030303;'
        f'border:1px solid #0a0a0a;border-radius:6px;padding:8px;">{feed_html}</div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=30)
def _film_debates():
    debates = get_recent_debates(limit=10)
    if not debates:
        st.markdown('<div style="color:#333;font-size:12px;padding:12px;">No debates yet.</div>',
                    unsafe_allow_html=True)
        return
    for d in debates:
        sym  = d.get('symbol', '?')
        sig  = (d.get('final_signal') or '').upper()
        bv   = d.get('buy_votes', 0)
        hv   = d.get('hold_votes', 0)
        conf = float(d.get('confidence', 0))
        reas = str(d.get('reasoning', ''))[:400]
        ts   = fmt_ts(d.get('ts', ''), show_date=True)
        agents_raw = d.get('agent_votes') or d.get('agents') or ''
        agent_data = {}
        if isinstance(agents_raw, str):
            try:
                agent_data = json.loads(agents_raw)
            except Exception:
                pass
        elif isinstance(agents_raw, dict):
            agent_data = agents_raw

        sig_color = '#00C853' if sig == 'BUY' else '#FF1744' if sig in ('SELL','SHORT') else '#555'

        agent_html = ''
        AGENT_COLORS = {
            'funding_regime':     ('#FF4444', 'BARDOCK'),
            'momentum_structure': ('#4488FF', 'VEGETA'),
            'risk_economics':     ('#FFB347', 'KRILLIN'),
        }
        for key, vote_data in agent_data.items():
            if not isinstance(vote_data, dict):
                continue
            v_sig  = (vote_data.get('signal', vote_data.get('verdict', '?'))).upper()
            v_conf = float(vote_data.get('confidence', 0))
            v_reas = str(vote_data.get('reason', ''))[:80]
            clr, name = AGENT_COLORS.get(key, ('#888', key.upper()))
            v_color = '#00C853' if v_sig == 'BUY' else '#FF1744' if v_sig in ('SELL','SHORT') else '#555'
            agent_html += (
                f'<div style="margin:3px 0;font-size:11px;display:flex;gap:8px;align-items:flex-start;">'
                f'<span style="color:{clr};font-weight:700;min-width:64px;">{name}</span>'
                f'<span style="color:{v_color};font-weight:900;min-width:36px;">{v_sig}</span>'
                f'<span style="color:#444;min-width:32px;">{v_conf:.0%}</span>'
                f'<span style="color:#555;flex:1;">{v_reas}</span>'
                f'</div>'
            )

        with st.expander(f"{sym}  →  {sig}  |  {bv}B/{hv}H  conf={conf:.0%}  |  {ts}", expanded=False):
            if agent_html:
                st.markdown(
                    f'<div style="background:#030303;border:1px solid #0a0a0a;border-radius:4px;'
                    f'padding:8px 10px;margin-bottom:8px;">'
                    f'<div style="color:#555;font-size:9px;text-transform:uppercase;letter-spacing:2px;'
                    f'margin-bottom:6px;">Agent Votes</div>'
                    f'{agent_html}</div>', unsafe_allow_html=True,
                )
            if reas:
                st.markdown(
                    f'<div style="background:#030303;border:1px solid #0a0a0a;border-radius:4px;'
                    f'padding:8px 10px;">'
                    f'<div style="color:#555;font-size:9px;text-transform:uppercase;letter-spacing:2px;'
                    f'margin-bottom:4px;">Synthesis</div>'
                    f'<div style="color:#888;font-size:12px;line-height:1.5;">{reas}</div>'
                    f'</div>', unsafe_allow_html=True,
                )


@st.fragment(run_every=60)
def _film_attribution():
    try:
        conn = _db_conn()
        rows = conn.execute("""
            SELECT strategy, COUNT(*) as total,
                   SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_usd) as total_pnl,
                   AVG(pnl_usd) as avg_pnl
            FROM trades
            WHERE paper=1 AND pnl_usd != 0
              AND ts >= datetime('now','-30 days')
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """).fetchall()
        conn.close()
    except Exception:
        rows = []

    st.markdown('<div class="panel-title">📊 30-DAY ATTRIBUTION</div>', unsafe_allow_html=True)
    if not rows:
        st.markdown('<div style="color:#333;font-size:11px;">Accumulates after trades close.</div>',
                    unsafe_allow_html=True)
        return
    for r in rows:
        strat  = r['strategy'] or 'unknown'
        total  = r['total'] or 0
        wins   = r['wins'] or 0
        tpnl   = r['total_pnl'] or 0
        avg_p  = r['avg_pnl'] or 0
        wr     = wins / max(total, 1)
        wr_c   = '#00C853' if wr >= 0.52 else '#FDB927' if wr >= 0.45 else '#FF1744'
        pnl_c  = '#00C853' if tpnl >= 0 else '#FF1744'
        bar    = int(wr * 10)
        bar_html = ''.join(
            f'<span style="color:{wr_c};font-size:9px;">█</span>' if j < bar
            else '<span style="color:#1a1a1a;font-size:9px;">█</span>'
            for j in range(10)
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0;'
            f'border-bottom:1px solid #0a0a0a;font-size:11px;">'
            f'<span style="color:#aaa;min-width:140px;">{strat[:18]}</span>'
            f'<span style="font-family:monospace;">{bar_html}</span>'
            f'<span style="color:{wr_c};font-weight:900;min-width:32px;">{wr:.0%}</span>'
            f'<span style="color:{pnl_c};min-width:48px;text-align:right;">${tpnl:+.2f}</span>'
            f'<span style="color:#333;min-width:28px;text-align:right;">{total}×</span>'
            f'</div>', unsafe_allow_html=True,
        )


def _film_strategy_controls():
    st.markdown('<div class="panel-title">⚙️ STRATEGY CONTROLS</div>', unsafe_allow_html=True)
    rm = get_risk_manager()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div style="color:#555;font-size:10px;text-transform:uppercase;'
                    'letter-spacing:1px;margin-bottom:6px;">Position Sizes</div>',
                    unsafe_allow_html=True)
        new_crypto = st.number_input('Crypto size ($)', min_value=10.0, max_value=2000.0,
                                     value=float(CRYPTO_POSITION_SIZE_USD), step=10.0, key='ctrl_cs')
        new_equity = st.number_input('Equity size ($)', min_value=10.0, max_value=2000.0,
                                     value=float(EQUITY_POSITION_SIZE_USD), step=10.0, key='ctrl_es')
        if st.button('📝 Update Sizes', key='ctrl_size'):
            _write_env_values({
                'CRYPTO_POSITION_SIZE_USD': str(new_crypto),
                'EQUITY_POSITION_SIZE_USD': str(new_equity),
            })
            st.success('Saved — restart main.py to apply.')

    with col2:
        st.markdown('<div style="color:#555;font-size:10px;text-transform:uppercase;'
                    'letter-spacing:1px;margin-bottom:6px;">Halt Control</div>',
                    unsafe_allow_html=True)
        is_halted, halt_reason = _live_halt_state()
        if is_halted:
            st.error(f'HALTED: {halt_reason}')
            if st.button('▶ Resume Trading', key='ctrl_resume', type='primary'):
                try:
                    rm.resume_trading()
                    st.success('Resumed.')
                except Exception as e:
                    st.error(f'Error: {e}')
        else:
            st.success('Trading ACTIVE')
            if st.button('⏸ Emergency Halt', key='ctrl_halt'):
                try:
                    rm.halt_trading('Manual halt from dashboard')
                    st.warning('Halted.')
                except Exception as e:
                    st.error(f'Error: {e}')


def _film_cost_lab():
    try:
        from config import DEBATE_MAX_TOKENS, EXIT_REVIEW_MAX_TOKENS
    except Exception:
        DEBATE_MAX_TOKENS = 400
        EXIT_REVIEW_MAX_TOKENS = 800

    st.markdown('<div class="panel-title">💰 API COST LAB</div>', unsafe_allow_html=True)
    monthly_cost = get_monthly_api_cost()
    c1, c2, c3 = st.columns(3)
    c1.metric('This month', f'${monthly_cost:.4f}')
    c2.metric('Crypto interval', f'{CRYPTO_SCAN_INTERVAL_SECONDS//60}min')
    c3.metric('Debate tokens', str(DEBATE_MAX_TOKENS))

    col1, col2 = st.columns(2)
    with col1:
        new_tokens   = st.slider('Debate tokens', 100, 600, DEBATE_MAX_TOKENS, 50, key='cost_dt')
        new_interval = st.slider('Scan interval (min)', 1, 15, max(1, CRYPTO_SCAN_INTERVAL_SECONDS//60), 1, key='cost_ci')
    with col2:
        new_exit = st.slider('Exit review tokens', 200, 1200, EXIT_REVIEW_MAX_TOKENS, 100, key='cost_et')

    if st.button('🚀 Apply to .env', key='cost_apply', type='primary'):
        _write_env_values({
            'DEBATE_MAX_TOKENS': str(new_tokens),
            'EXIT_REVIEW_MAX_TOKENS': str(new_exit),
            'CRYPTO_SCAN_INTERVAL_SECONDS': str(new_interval * 60),
        })
        st.success('Written — restart main.py to apply.')


@st.fragment(run_every=60)
def _film_equity_curve():
    try:
        recent = get_recent_trades(limit=200, paper=PAPER_TRADING)
        if not recent:
            st.markdown('<div style="color:#333;font-size:11px;">No trades yet.</div>',
                        unsafe_allow_html=True)
            return
        pnls = []
        for t in sorted(recent, key=lambda x: x.get('ts','')):
            p = float(t.get('pnl_usd') or 0)
            if p != 0:
                pnls.append(p)
        if not pnls:
            return
        cumulative = [ACCOUNT_SIZE] + [ACCOUNT_SIZE + sum(pnls[:i+1]) for i in range(len(pnls))]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            y=cumulative, mode='lines',
            line=dict(color='#FDB927', width=2),
            fill='tozeroy',
            fillcolor='rgba(253,185,39,0.05)',
        ))
        fig.update_layout(
            paper_bgcolor='#000814', plot_bgcolor='#000814',
            margin=dict(l=0, r=0, t=0, b=0), height=180,
            showlegend=False,
            xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor='#0d0d0d', tickfont=dict(color='#444', size=10),
                       zeroline=True, zerolinecolor='#333'),
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    except Exception:
        pass


def render_film_tab():
    st.markdown(
        '<div style="text-align:center;padding:8px 0 16px 0;">'
        '<span style="color:#FDB927;font-weight:900;font-size:22px;letter-spacing:4px;">'
        '📋 FILM ROOM</span>'
        '<div style="color:#444;font-size:10px;letter-spacing:3px;margin-top:4px;">'
        'FULL INTEL · DEBUG FEED · STRATEGY CONTROLS</div>'
        '</div>', unsafe_allow_html=True,
    )

    # Equity curve
    _film_equity_curve()
    st.markdown('<hr style="border-color:#111;margin:10px 0;">', unsafe_allow_html=True)

    col_l, col_r = st.columns([3, 2])
    with col_l:
        st.markdown('<div style="color:#FDB927;font-weight:900;font-size:12px;'
                    'letter-spacing:2px;margin-bottom:8px;">🤖 AI DEBATES</div>',
                    unsafe_allow_html=True)
        _film_debates()

    with col_r:
        _film_attribution()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _film_strategy_controls()
        st.markdown('<div style="margin:10px 0;"></div>', unsafe_allow_html=True)
        _film_cost_lab()

    st.markdown('<hr style="border-color:#111;margin:12px 0;">', unsafe_allow_html=True)
    _film_scan_feed()

    st.markdown('<hr style="border-color:#111;margin:12px 0;">', unsafe_allow_html=True)
    with st.expander("📡 Today's Signals (full list)"):
        sigs = get_todays_signals()
        acted = [s for s in sigs if s.get('acted_on')]
        if acted:
            for s in acted[:30]:
                sym  = s.get('symbol','?')
                conf = float(s.get('confidence',0))
                ts   = fmt_ts(s.get('ts',''), show_date=False)
                strat= s.get('strategy','')
                st.markdown(
                    f'<div style="display:flex;gap:10px;padding:4px 0;font-size:11px;'
                    f'border-bottom:1px solid #0a0a0a;">'
                    f'<span style="color:#FDB927;min-width:100px;">{sym}</span>'
                    f'<span style="color:#00C853;min-width:40px;">{conf:.0%}</span>'
                    f'<span style="color:#444;min-width:80px;">{ts}</span>'
                    f'<span style="color:#333;">{strat}</span>'
                    f'</div>', unsafe_allow_html=True,
                )
        else:
            st.markdown('<div style="color:#333;font-size:11px;">No entries today.</div>',
                        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    tab_king, tab_saiyan, tab_film = st.tabs([
        '👑  THE KING',
        '⚡  SAIYAN MODE',
        '📋  FILM ROOM',
    ])

    with tab_king:
        render_king_tab()

    with tab_saiyan:
        render_saiyan_tab()

    with tab_film:
        render_film_tab()

    # Footer
    st.markdown(
        f'<div style="text-align:center;color:#1a1a1a;font-size:9px;'
        f'font-family:monospace;padding:12px 0;letter-spacing:3px;">'
        f'THE KING × SAIYAN v9.0 · {et_now()} · '
        f'{"📄 PAPER" if PAPER_TRADING else "💰 LIVE"}'
        f'</div>', unsafe_allow_html=True,
    )


if __name__ == '__main__':
    rm = get_risk_manager()
    main()
