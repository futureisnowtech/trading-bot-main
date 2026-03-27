"""
dashboard/app.py — Trading War Room v11.0

Mobile-app design language on desktop.
Large, readable, bold. Cards with depth. Clear hierarchy.
Every section has an ℹ️ info button.
No themes. No fluff. Only what matters.
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import urllib.request
from datetime import datetime, timezone as _tz

import pytz
import streamlit as st

from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
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
    get_recent_notifications,
)
from risk.risk_manager import get_risk_manager

try:
    from data.edge_monitor import get_edge_state
except Exception:
    def get_edge_state(s, paper=True):
        return {'status': 'UNCERTAIN', 'edge_score': 0.5, 'profit_factor': 1.5,
                'win_rate_20': 0.5, 'consecutive_bad': 0,
                'sizing_multiplier': 1.0, 'should_block': False, 'window_trades': 0}

try:
    from risk.drawdown_controller import get_heat_level
except Exception:
    def get_heat_level(paper=True):
        return {'level': 0, 'label': 'NORMAL', 'size_factor': 1.0,
                'daily_pnl': 0.0, 'pct_drawn': 0.0}

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="War Room",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Design system ─────────────────────────────────────────────────────────────
BG       = "#08090e"
SURFACE  = "#0f1018"
CARD     = "#13141f"
BORDER   = "#1e2038"
BORDER2  = "#272945"
GOLD     = "#f5a623"
GREEN    = "#10c98f"
RED      = "#f03e5e"
AMBER    = "#f59e0b"
BLUE     = "#4f8ef7"
TEXT     = "#eef0f6"
TEXT2    = "#7c849e"
TEXT3    = "#3a3f58"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&display=swap');

html, body, .stApp, .main {{
    background: {BG} !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    color: {TEXT} !important;
}}

/* Hide Streamlit chrome */
#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ padding: 20px 32px 40px 32px !important; max-width: 1600px !important; }}
.stExpander {{ border: 1px solid {BORDER} !important; border-radius: 12px !important; }}
.stExpander summary {{ padding: 12px 16px !important; }}

/* Columns spacing */
div[data-testid="column"] {{ padding: 0 6px !important; }}

/* ── CARD ── */
.card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 20px 22px;
    position: relative;
}}
.card-sm {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 16px 18px;
}}
.card-flush {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 14px 16px;
}}

/* ── HERO ── */
.hero {{
    background: linear-gradient(135deg, #0f1120 0%, #13141f 60%, #0f1018 100%);
    border: 1px solid {BORDER};
    border-radius: 20px;
    padding: 36px 40px;
    text-align: center;
    position: relative;
    overflow: hidden;
}}
.hero::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, {GOLD}33, transparent);
}}
.hero-label {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 4px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 12px;
}}
.hero-pnl {{
    font-size: 80px; font-weight: 900; line-height: 1;
    letter-spacing: -2px;
    font-family: 'Inter', sans-serif;
}}
.hero-sub {{
    font-size: 14px; color: {TEXT2};
    margin-top: 14px; font-weight: 500;
}}

/* ── METRIC CARD ── */
.met {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 20px 22px 18px 22px;
    height: 100%;
}}
.met-label {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 3px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 10px;
}}
.met-value {{
    font-size: 30px; font-weight: 800;
    line-height: 1; letter-spacing: -0.5px;
    font-family: 'Inter', sans-serif;
}}
.met-sub {{
    font-size: 12px; color: {TEXT2};
    margin-top: 8px; font-weight: 500;
}}

/* ── SECTION HEADER ── */
.sec {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 3px; text-transform: uppercase;
    color: {TEXT3}; padding: 24px 0 10px 0;
    display: flex; align-items: center; gap: 8px;
}}
.sec-line {{
    flex: 1; height: 1px; background: {BORDER};
}}

/* ── EDGE CARD ── */
.edge-card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 22px 22px 20px 22px;
}}
.edge-market {{
    font-size: 10px; font-weight: 700;
    letter-spacing: 4px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 12px;
}}
.edge-status {{
    font-size: 18px; font-weight: 800;
    letter-spacing: 1px; margin-bottom: 14px;
}}
.edge-track {{
    background: {SURFACE}; border-radius: 100px;
    height: 10px; overflow: hidden; margin-bottom: 14px;
}}
.edge-fill {{
    height: 100%; border-radius: 100px;
    transition: width 0.4s ease;
}}
.edge-row {{
    display: flex; justify-content: space-between;
    font-size: 13px; color: {TEXT2}; margin-bottom: 6px;
}}
.edge-val {{ color: {TEXT}; font-weight: 600;
    font-family: 'JetBrains Mono', monospace; }}

/* ── POSITION CARD ── */
.pos-card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 8px;
    border-left: 3px solid {GOLD};
}}
.pos-top {{
    display: flex; justify-content: space-between;
    align-items: flex-start; margin-bottom: 10px;
}}
.pos-symbol {{
    font-size: 18px; font-weight: 800; color: {TEXT};
    font-family: 'JetBrains Mono', monospace;
}}
.pos-strategy {{
    font-size: 11px; color: {TEXT3};
    font-weight: 600; letter-spacing: 1px;
    text-transform: uppercase; margin-top: 2px;
}}
.pos-age {{
    font-size: 12px; color: {TEXT2}; font-weight: 500;
    background: {CARD}; border-radius: 20px;
    padding: 3px 10px; border: 1px solid {BORDER};
}}
.pos-bars {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
}}
.pos-dist-label {{
    font-size: 11px; color: {TEXT3};
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 4px;
}}
.pos-dist-val {{
    font-size: 15px; font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
}}
.pos-track {{
    background: {BORDER}; border-radius: 100px;
    height: 4px; margin-top: 4px; overflow: hidden;
}}
.pos-fill {{ height: 100%; border-radius: 100px; }}

/* ── MARKET PANEL ── */
.mkt {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 20px 22px;
}}
.mkt-title {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 4px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid {BORDER};
}}
.mkt-row {{
    display: flex; justify-content: space-between;
    align-items: center; margin-bottom: 10px;
}}
.mkt-key {{
    font-size: 12px; color: {TEXT2}; font-weight: 500;
}}
.mkt-val {{
    font-size: 13px; color: {TEXT};
    font-weight: 600; font-family: 'JetBrains Mono', monospace;
    text-align: right; max-width: 60%;
}}
.mkt-bar-bg {{
    background: {SURFACE}; border-radius: 100px;
    height: 5px; margin-top: 12px;
}}
.mkt-bar-fill {{
    height: 100%; border-radius: 100px;
}}

/* ── GAUGE ── */
.gauge-block {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 16px 18px;
}}
.gauge-top {{
    display: flex; justify-content: space-between;
    align-items: center; margin-bottom: 10px;
}}
.gauge-name {{
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 2px;
    color: {TEXT2};
}}
.gauge-val {{
    font-size: 14px; font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
}}
.gauge-track {{
    background: {SURFACE}; border-radius: 100px;
    height: 8px; overflow: hidden;
}}
.gauge-fill {{
    height: 100%; border-radius: 100px;
    transition: width 0.3s;
}}
.gauge-sub {{
    font-size: 11px; color: {TEXT3};
    margin-top: 6px;
}}

/* ── TRADE ROW ── */
.tr {{
    display: flex; align-items: center;
    padding: 12px 0; border-bottom: 1px solid {BORDER};
    gap: 0;
}}
.tr:last-child {{ border-bottom: none; }}
.tr-time {{
    font-size: 12px; color: {TEXT2};
    font-family: 'JetBrains Mono', monospace;
    min-width: 90px;
}}
.tr-sym {{
    font-size: 14px; font-weight: 700; color: {TEXT};
    min-width: 110px; font-family: 'JetBrains Mono', monospace;
}}
.tr-act {{
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    min-width: 50px; color: {TEXT2};
}}
.tr-pnl {{
    margin-left: auto; font-size: 15px; font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
}}

/* ── SIGNAL ROW ── */
.sr {{
    display: flex; align-items: center;
    padding: 10px 0; border-bottom: 1px solid {BORDER};
    gap: 10px;
}}
.sr:last-child {{ border-bottom: none; }}
.sr-time {{
    font-size: 11px; color: {TEXT2};
    font-family: 'JetBrains Mono', monospace;
    min-width: 70px;
}}
.sr-badge {{
    font-size: 10px; font-weight: 800;
    text-transform: uppercase; letter-spacing: 1px;
    padding: 2px 8px; border-radius: 20px;
    min-width: 46px; text-align: center;
}}
.sr-sym {{
    font-size: 13px; font-weight: 600;
    color: {TEXT}; font-family: 'JetBrains Mono', monospace;
}}
.sr-conf {{
    margin-left: auto; font-size: 12px;
    color: {TEXT2}; font-family: monospace;
}}

/* ── STATUS BAR ── */
.statusbar {{
    display: flex; align-items: center; gap: 12px;
    padding: 10px 18px;
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-bottom: 20px;
}}
.sb-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.sb-text {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
.sb-muted {{ font-size: 12px; color: {TEXT2}; }}
.sb-sep {{ color: {BORDER2}; }}
.sb-right {{ margin-left: auto; font-size: 12px; color: {TEXT3};
    font-family: 'JetBrains Mono', monospace; }}

/* ── HALT BANNER ── */
.halt {{
    background: linear-gradient(135deg, #2d0a14, #1a0508);
    border: 1px solid {RED};
    border-radius: 12px;
    padding: 14px 20px;
    text-align: center;
    margin-bottom: 12px;
}}
.halt-txt {{
    font-size: 15px; font-weight: 800;
    color: {RED}; letter-spacing: 2px;
    text-transform: uppercase;
}}

/* ── BADGE ── */
.chip {{
    display: inline-block; padding: 3px 10px;
    border-radius: 20px; font-size: 11px;
    font-weight: 700; letter-spacing: 0.5px;
}}
.chip-paper {{ background: #1f1808; color: {GOLD}; border: 1px solid #3d3010; }}
.chip-live  {{ background: #1f0810; color: {RED};  border: 1px solid #3d1020; }}

/* ── INFO POPOVER ── */
div[data-testid="stPopover"] > button {{
    background: {SURFACE} !important;
    border: 1px solid {BORDER} !important;
    color: {TEXT3} !important;
    padding: 0 !important;
    min-height: 20px !important; height: 20px !important;
    width: 20px !important;
    border-radius: 50% !important;
    font-size: 10px !important;
    line-height: 1 !important;
}}
div[data-testid="stPopover"] > button:hover {{
    color: {TEXT2} !important;
    border-color: {BORDER2} !important;
}}

/* ── CHAT ── */
.chat-u {{
    background: #141828; color: {TEXT};
    padding: 12px 16px; border-radius: 16px 16px 4px 16px;
    margin: 6px 0; font-size: 14px; line-height: 1.5;
    border: 1px solid {BORDER};
}}
.chat-a {{
    background: {SURFACE}; color: {TEXT2};
    padding: 12px 16px; border-radius: 16px 16px 16px 4px;
    margin: 6px 0; font-size: 14px; line-height: 1.5;
    border-left: 3px solid {GOLD};
    border-top: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

/* ── BUTTONS ── */
.stButton > button {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 10px;
    font-weight: 600; font-size: 13px;
    padding: 8px 18px; transition: all 0.15s;
}}
.stButton > button:hover {{
    background: {CARD}; border-color: {BORDER2};
    color: #fff;
}}

/* stForm inputs */
.stChatInput textarea {{
    background: {SURFACE} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 12px !important;
    color: {TEXT} !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 14px !important;
}}
</style>
""", unsafe_allow_html=True)


# ─── Helpers ───────────────────────────────────────────────────────────────────

_TZ = pytz.timezone(MARKET_TIMEZONE)

def _et(fmt='%b %-d  %-I:%M %p ET'):
    return datetime.now(_TZ).strftime(fmt)

def _fmt_ts(ts: str, short=False) -> str:
    if not ts:
        return '—'
    try:
        dt = datetime.fromisoformat(ts)
        dt = dt.astimezone(_TZ) if dt.tzinfo else _TZ.localize(dt)
        return dt.strftime('%-I:%M %p') if short else dt.strftime('%b %-d  %-I:%M %p')
    except Exception:
        return ts[5:16] if len(ts) >= 16 else ts

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db')

def _db():
    import sqlite3 as sq
    c = sq.connect(_DB, timeout=2)
    c.row_factory = sq.Row
    return c

def _bot_age() -> float | None:
    try:
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

def _halt_state() -> tuple[bool, str]:
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

def _positions() -> dict:
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM open_positions WHERE paper=?", (int(PAPER_TRADING),)
        ).fetchall()
        conn.close()
        result: dict = {}
        for r in rows:
            s = r['strategy']
            result.setdefault(s, {})[r['symbol']] = {
                'qty': r['qty'], 'entry': r['entry'],
                'stop': r['stop'], 'target': r['target'],
                'direction': r['direction'] if 'direction' in r.keys() else 'LONG',
                'ts_entry': r['ts_entry'],
            }
        return result
    except Exception:
        return {}

def _write_env(updates: dict):
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    try:
        with open(env_path) as f:
            lines = f.readlines()
        written, new_lines = set(), []
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

def _info(text: str, label: str = "ℹ"):
    with st.popover(label):
        st.markdown(f'<p style="font-size:13px;color:{TEXT2};line-height:1.6;">{text}</p>',
                    unsafe_allow_html=True)

def _sec(title: str, info: str = ''):
    c1, c2 = st.columns([30, 1]) if info else (st.columns([1])[0], None)
    with c1:
        st.markdown(
            f'<div class="sec">{title}<div class="sec-line"></div></div>',
            unsafe_allow_html=True,
        )
    if c2 and info:
        with c2:
            _info(info)

def _color_val(v: float, lo=0, hi=0) -> str:
    if v > hi:
        return GREEN
    if v < lo:
        return RED
    return GOLD

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

def _build_ctx() -> str:
    pos    = _positions()
    pnl    = get_todays_pnl(paper=PAPER_TRADING)
    fees   = get_todays_fees(paper=PAPER_TRADING)
    stats  = get_all_time_stats(paper=PAPER_TRADING)
    rm     = get_risk_manager()
    risk   = rm.status_report()
    wr14   = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    mo     = get_monthly_api_cost()
    recent = get_recent_trades(limit=10, paper=PAPER_TRADING)
    pos_lines = '\n'.join(
        f"  {sym} (entry={p['entry']:.5g} stop={p['stop']:.5g})"
        for strat, syms in pos.items() for sym, p in syms.items()
    ) or '  None'
    trade_lines = '\n'.join(
        f"  {_fmt_ts(t.get('ts',''))} {t.get('symbol','')} {t.get('action','')} "
        f"P&L=${t.get('pnl_usd',0):+.2f}"
        for t in recent
    ) or '  None'
    return (
        f"You are the AI brain of this autonomous trading system. Be direct. Protect capital first.\n"
        f"Philosophy: edge preservation. Rolling edge monitor is the most important component.\n"
        f"Rules: never chase, never average down, stops sacred, FOMO is not a signal.\n\n"
        f"LIVE STATE ({_et()})\n"
        f"Mode: {'PAPER' if PAPER_TRADING else 'LIVE'} | Account: ${ACCOUNT_SIZE:,.0f}\n"
        f"Today P&L: ${pnl:+.2f} | Fees: ${fees:.2f} | 14d WR: {wr14:.1%} | API/month: ${mo:.2f}\n"
        f"All-time: {stats.get('total',0)} trades | WR {stats.get('win_rate',0):.1%} | P&L ${stats.get('total_pnl',0):+.2f}\n"
        f"Halted: {risk.get('halted', False)}\n\n"
        f"POSITIONS:\n{pos_lines}\n\nRECENT TRADES:\n{trade_lines}\n\n"
        f"RULES: {len(FULL_DEBATE_AGENTS)} agents | daily loss limit {MAX_DAILY_LOSS_PCT:.0%}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# STATUS BAR
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def comp_status_bar():
    is_halted, halt_reason = _halt_state()
    secs = _bot_age()

    if PAPER_TRADING:
        mode_chip = f'<span class="chip chip-paper">PAPER TRADING</span>'
    else:
        mode_chip = f'<span class="chip chip-live">⚡ LIVE — REAL MONEY</span>'

    if secs is None:
        dot_clr, bot_txt, bot_age = '#555', 'Not started', ''
    elif secs > 180:
        dot_clr, bot_txt, bot_age = RED, 'Stale', f'{int(secs)}s ago'
    else:
        dot_clr, bot_txt, bot_age = GREEN, 'Running', f'{int(secs)}s ago'

    st.markdown(
        f'<div class="statusbar">'
        f'{mode_chip}'
        f'<span class="sb-sep">·</span>'
        f'<span class="sb-dot" style="background:{dot_clr};'
        f'{"box-shadow:0 0 6px " + dot_clr + ";" if dot_clr == GREEN else ""}"></span>'
        f'<span class="sb-text">{bot_txt}</span>'
        f'{"<span class=sb-muted>" + bot_age + "</span>" if bot_age else ""}'
        f'<span class="sb-right">{_et()}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if is_halted:
        st.markdown(
            f'<div class="halt">'
            f'<div class="halt-txt">⛔ TRADING HALTED — {halt_reason or "Daily limit reached"}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# HERO — GIANT P&L
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def comp_hero():
    c1, c2 = st.columns([20, 1])
    with c2:
        _info(
            "Today's net P&L = realized gross P&L minus all fees paid since midnight ET. "
            "This is the scoreboard. Gold/green = profitable today. Red = in the hole. "
            "Balance reflects your starting account plus all-time cumulative P&L."
        )

    pnl   = get_todays_pnl(paper=PAPER_TRADING)
    fees  = get_todays_fees(paper=PAPER_TRADING)
    net   = pnl - fees
    stats = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)

    clr    = GREEN if net >= 0 else RED
    prefix = '+' if net >= 0 else ''
    bal_clr = GREEN if real_bal >= ACCOUNT_SIZE else RED
    at_clr  = GREEN if stats.get('total_pnl', 0) >= 0 else RED
    at_sign = '+' if stats.get('total_pnl', 0) >= 0 else ''

    st.markdown(
        f'<div class="hero">'
        f'<div class="hero-label">Today Net P&L</div>'
        f'<div class="hero-pnl" style="color:{clr};">{prefix}${net:.2f}</div>'
        f'<div class="hero-sub">'
        f'Balance&nbsp;<strong style="color:{bal_clr};">${real_bal:,.2f}</strong>'
        f'&emsp;·&emsp;'
        f'Gross&nbsp;<strong style="color:{TEXT};">{prefix}${pnl:.2f}</strong>'
        f'&emsp;·&emsp;'
        f'Fees&nbsp;<strong style="color:{TEXT2};">−${fees:.2f}</strong>'
        f'&emsp;·&emsp;'
        f'All&#8209;time&nbsp;<strong style="color:{at_clr};">{at_sign}${stats.get("total_pnl",0):.2f}</strong>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6 KEY METRICS — 2 rows of 3
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def comp_metrics():
    _sec(
        "KEY METRICS",
        "Six numbers that define system health. "
        "<b>Win Rate</b>: last 20 trades rolling — need ≥52% before going live. "
        "<b>Edge Score</b>: composite of WR × profit factor × consistency (0–1). "
        "Above 0.70 = strong edge, size up. Below 0.30 = fading, size down automatically. "
        "<b>Daily Loss</b>: how much of the 4% hard limit you've burned today. "
        "<b>API Cost</b>: Claude AI spend this calendar month."
    )

    pnl   = get_todays_pnl(paper=PAPER_TRADING)
    fees  = get_todays_fees(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    wr20  = get_win_rate(lookback_days=7, paper=PAPER_TRADING)
    mo    = get_monthly_api_cost()
    heat  = get_heat_level(paper=PAPER_TRADING)
    es    = get_edge_state('crypto_macd_consensus', paper=PAPER_TRADING)
    net   = pnl - fees
    dp    = heat.get('pct_drawn', 0.0)
    edge  = es.get('edge_score', 0.5)

    def _met(label, value, sub, color):
        return (
            f'<div class="met">'
            f'<div class="met-label">{label}</div>'
            f'<div class="met-value" style="color:{color};">{value}</div>'
            f'<div class="met-sub">{sub}</div>'
            f'</div>'
        )

    row1 = st.columns(3)
    row2 = st.columns(3)

    bal_clr = GREEN if real_bal >= ACCOUNT_SIZE else RED
    net_clr = GREEN if net >= 0 else RED
    at_clr  = GREEN if stats.get('total_pnl', 0) >= 0 else RED
    wr_clr  = (GREEN if wr20 >= 0.52 else RED if wr20 < 0.45 else GOLD)
    ed_clr  = (GREEN if edge >= 0.70 else RED if edge < 0.30 else GOLD)
    dl_clr  = (GREEN if dp < MAX_DAILY_LOSS_PCT * 0.5 else
               AMBER  if dp < MAX_DAILY_LOSS_PCT * 0.75 else RED)

    at_sign = '+' if stats.get('total_pnl', 0) >= 0 else ''
    net_sign = '+' if net >= 0 else ''

    with row1[0]:
        st.markdown(_met("BALANCE", f"${real_bal:,.2f}",
                         f"start ${ACCOUNT_SIZE:,.0f}", bal_clr), unsafe_allow_html=True)
    with row1[1]:
        st.markdown(_met("TODAY NET", f"{net_sign}${net:.2f}",
                         f"gross {net_sign}${pnl:.2f}", net_clr), unsafe_allow_html=True)
    with row1[2]:
        st.markdown(_met("ALL-TIME P&L", f"{at_sign}${stats.get('total_pnl',0):.2f}",
                         f"{stats.get('total',0)} trades", at_clr), unsafe_allow_html=True)
    with row2[0]:
        st.markdown(_met("WIN RATE (20)", f"{wr20:.1%}",
                         "need ≥ 52% for live", wr_clr), unsafe_allow_html=True)
    with row2[1]:
        st.markdown(_met("EDGE SCORE", f"{edge:.2f}",
                         es.get('status', 'UNCERTAIN'), ed_clr), unsafe_allow_html=True)
    with row2[2]:
        st.markdown(_met("DAILY LOSS", f"{dp:.1%}",
                         f"limit {MAX_DAILY_LOSS_PCT:.0%}", dl_clr), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# EDGE MONITOR — most important component
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def comp_edge():
    _sec(
        "EDGE MONITOR",
        "⚠️ Most important component in the entire system. Tracks rolling 20-trade "
        "performance per market. When edge_score < 0.30 for 2 consecutive windows, "
        "position sizing is automatically cut 50%. "
        "When edge_score > 0.70 for 2 windows, sizing scales toward Kelly max. "
        "<b>STRONG ≥ 0.70 · NORMAL 0.40–0.70 · FADING 0.30–0.40 · DEGRADED < 0.30</b>. "
        "The Sizing column shows what fraction of normal position size is currently in use."
    )

    STATUS_CLR = {
        'STRONG': GREEN, 'NORMAL': GOLD,
        'FADING': AMBER, 'DEGRADED': RED, 'UNCERTAIN': TEXT2,
    }

    MARKETS = [
        ('CRYPTO',      'crypto_macd_consensus'),
        ('MES FUTURES', 'futures_scalper'),
        ('PERP',        'crypto_perp'),
    ]

    cols = st.columns(3)
    for col, (mname, skey) in zip(cols, MARKETS):
        es   = get_edge_state(skey, paper=PAPER_TRADING)
        stat = es.get('status', 'UNCERTAIN')
        scr  = es.get('edge_score', 0.5)
        wr20 = es.get('win_rate_20', 0.5)
        pf   = es.get('profit_factor', 1.0)
        mult = es.get('sizing_multiplier', 1.0)
        n    = es.get('window_trades', 0)
        bad  = es.get('consecutive_bad', 0)
        clr  = STATUS_CLR.get(stat, TEXT2)
        bar  = min(100, int(scr * 100))

        warn_html = (
            f'<div style="margin-top:12px;padding:8px 10px;background:#1a0808;'
            f'border:1px solid {RED}33;border-radius:8px;'
            f'font-size:11px;color:{RED};font-weight:600;">'
            f'⚠ {bad} consecutive bad windows — sizing reducing'
            f'</div>'
        ) if bad >= 2 else ''

        with col:
            st.markdown(
                f'<div class="edge-card">'
                f'<div class="edge-market">{mname}</div>'
                f'<div class="edge-status" style="color:{clr};">{stat}</div>'
                f'<div class="edge-track">'
                f'<div class="edge-fill" style="width:{bar}%;background:{clr};"></div>'
                f'</div>'
                f'<div class="edge-row">'
                f'<span>Score</span><span class="edge-val">{scr:.2f}</span>'
                f'</div>'
                f'<div class="edge-row">'
                f'<span>Win Rate (20)</span><span class="edge-val">{wr20:.1%}</span>'
                f'</div>'
                f'<div class="edge-row">'
                f'<span>Profit Factor</span><span class="edge-val">{pf:.2f}</span>'
                f'</div>'
                f'<div class="edge-row">'
                f'<span>Sizing</span><span class="edge-val">{mult:.0%}</span>'
                f'</div>'
                f'<div class="edge-row">'
                f'<span>Trades in window</span><span class="edge-val">{n}</span>'
                f'</div>'
                f'{warn_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# OPEN POSITIONS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=5)
def comp_positions():
    _sec(
        "OPEN POSITIONS",
        "All live positions. "
        "<b>% to Stop</b>: how far price must move against you before auto-exit. "
        "<b>% to Target</b>: distance to take-profit. "
        "Stops are never moved wider after entry — this is a hard rule. "
        "Positions open > 45 min with < 15% target progress are auto-closed (stagnant trade killer)."
    )

    positions = _positions()

    if not positions:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:24px 0;'
            f'text-align:center;font-style:italic;">No open positions</div>',
            unsafe_allow_html=True,
        )
        return

    for strat, syms in positions.items():
        for sym, p in syms.items():
            entry  = float(p.get('entry', 0))
            stop   = float(p.get('stop', 0))
            target = float(p.get('target', 0))
            direc  = p.get('direction', 'LONG')
            ts     = p.get('ts_entry', '')

            age_str = '—'
            try:
                dt = datetime.fromisoformat(ts)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=_tz.utc)
                mins = int((datetime.now(_tz.utc) - dt).total_seconds() / 60)
                age_str = f'{mins}m' if mins < 60 else f'{mins//60}h {mins%60}m'
            except Exception:
                pass

            to_stop   = abs(entry - stop)   / entry * 100 if entry > 0 and stop > 0   else 0
            to_target = abs(target - entry) / entry * 100 if entry > 0 and target > 0 else 0
            stop_pct  = min(100, int((to_stop / CRYPTO_STOP_LOSS_PCT / 100) * 100))
            tgt_pct   = min(100, int((to_target / CRYPTO_TAKE_PROFIT_PCT / 100) * 100))

            stop_clr = RED if to_stop < 0.3 else (AMBER if to_stop < 0.8 else TEXT2)

            st.markdown(
                f'<div class="pos-card">'
                f'<div class="pos-top">'
                f'  <div>'
                f'    <div class="pos-symbol">{sym}</div>'
                f'    <div class="pos-strategy">{strat} · {direc}</div>'
                f'  </div>'
                f'  <div class="pos-age">{age_str}</div>'
                f'</div>'
                f'<div class="pos-bars">'
                f'  <div>'
                f'    <div class="pos-dist-label">To Stop</div>'
                f'    <div class="pos-dist-val" style="color:{stop_clr};">{to_stop:.2f}%</div>'
                f'    <div class="pos-track">'
                f'      <div class="pos-fill" style="width:{stop_pct}%;background:{stop_clr};"></div>'
                f'    </div>'
                f'  </div>'
                f'  <div>'
                f'    <div class="pos-dist-label">To Target</div>'
                f'    <div class="pos-dist-val" style="color:{GREEN};">{to_target:.2f}%</div>'
                f'    <div class="pos-track">'
                f'      <div class="pos-fill" style="width:{tgt_pct}%;background:{GREEN};"></div>'
                f'    </div>'
                f'  </div>'
                f'</div>'
                f'<div style="margin-top:12px;font-size:12px;color:{TEXT3};'
                f'font-family:\'JetBrains Mono\',monospace;">'
                f'Entry {entry:.5g}  ·  Stop {stop:.5g}  ·  Target {target:.5g}'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# THREE MARKET PANELS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def comp_markets():
    _sec(
        "MARKETS",
        "Per-market status. Each market runs its own strategy and edge monitor independently. "
        "Last signal shows the most recent AI debate outcome for that market. "
        "Last trade shows the most recent completed position with P&L."
    )

    positions = _positions()
    recent    = get_recent_trades(limit=5, paper=PAPER_TRADING) or []
    signals   = get_todays_signals(paper=PAPER_TRADING) or []

    def _last_sig(key):
        for s in signals:
            if key in str(s.get('strategy', '')).lower():
                a = str(s.get('action', '')).upper()
                clr = GREEN if a == 'BUY' else (RED if a == 'SELL' else TEXT2)
                return (
                    f'<span style="color:{clr};font-weight:700;">{a}</span> '
                    f'{s.get("symbol","—")} '
                    f'<span style="color:{TEXT3};">{_fmt_ts(s.get("ts",""), short=True)}</span>'
                )
        return f'<span style="color:{TEXT3};">—</span>'

    def _last_trade(key):
        for t in recent:
            if key in str(t.get('strategy', '')).lower():
                pnl  = t.get('pnl_usd', 0)
                sym  = t.get('symbol', '—')
                sign = '+' if pnl >= 0 else ''
                clr  = GREEN if pnl > 0 else RED
                ts   = _fmt_ts(t.get('ts', ''), short=True)
                return (
                    f'{sym} <span style="color:{clr};font-weight:700;">'
                    f'{sign}${pnl:.2f}</span> '
                    f'<span style="color:{TEXT3};">{ts}</span>'
                )
        return f'<span style="color:{TEXT3};">—</span>'

    PANELS = [
        ('CRYPTO',      'crypto',   'Coinbase · 1-min · up to 5 positions', 'crypto_macd_consensus'),
        ('MES FUTURES', 'futures',  'Tradovate · opening range breakout',   'futures_scalper'),
        ('PERP',        'perp',     'Binance USD-M · funding rate aware',   'crypto_perp'),
    ]

    cols = st.columns(3)
    for col, (title, key, note, ekey) in zip(cols, PANELS):
        pos_count = sum(
            1 for s, syms in positions.items()
            if key in s.lower() for _ in syms
        )
        es    = get_edge_state(ekey, paper=PAPER_TRADING)
        score = es.get('edge_score', 0.5)
        bar   = min(100, int(score * 100))
        bclr  = GREEN if score >= 0.7 else (RED if score < 0.3 else GOLD)

        with col:
            st.markdown(
                f'<div class="mkt">'
                f'<div class="mkt-title">{title}</div>'
                f'<div class="mkt-row">'
                f'  <span class="mkt-key">Open Positions</span>'
                f'  <span class="mkt-val" style="color:{GREEN if pos_count else TEXT3};">'
                f'  {pos_count}</span>'
                f'</div>'
                f'<div class="mkt-row">'
                f'  <span class="mkt-key">Last Signal</span>'
                f'  <span class="mkt-val">{_last_sig(key)}</span>'
                f'</div>'
                f'<div class="mkt-row">'
                f'  <span class="mkt-key">Last Trade</span>'
                f'  <span class="mkt-val">{_last_trade(key)}</span>'
                f'</div>'
                f'<div class="mkt-bar-bg">'
                f'  <div class="mkt-bar-fill" style="width:{bar}%;background:{bclr};height:5px;border-radius:100px;"></div>'
                f'</div>'
                f'<div style="margin-top:6px;font-size:11px;color:{TEXT3};">{note}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# RISK GAUGES
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=10)
def comp_risk():
    _sec(
        "RISK STATUS",
        "Daily loss bar: fills toward the 4% hard limit. At 100% all trading halts. "
        "Heat levels reduce position size before the hard halt: "
        "CAUTION = 75% size, WARNING = 50%, DANGER = 25%, HALT = 0%. "
        "Fee drag: total fees today as % of account (limit 10%). "
        "Watchdog: time since last bot scan cycle (stale > 3 min = problem)."
    )

    heat    = get_heat_level(paper=PAPER_TRADING)
    fees    = get_todays_fees(paper=PAPER_TRADING)
    secs    = _bot_age()
    rm      = get_risk_manager()
    risk    = rm.status_report()
    stats   = get_all_time_stats(paper=PAPER_TRADING)
    real_bal = max(ACCOUNT_SIZE + stats.get('total_pnl', 0), 1.0)

    dp      = heat.get('pct_drawn', 0.0)
    dl_fill = min(100, int(dp / MAX_DAILY_LOSS_PCT * 100)) if MAX_DAILY_LOSS_PCT else 0
    dl_clr  = GREEN if dl_fill < 50 else (AMBER if dl_fill < 75 else RED)

    HEAT_CLR = {
        'NORMAL': GREEN, 'CAUTION': GOLD,
        'WARNING': AMBER, 'DANGER': RED, 'HALT': RED,
    }
    hl      = heat.get('label', 'NORMAL')
    hl_clr  = HEAT_CLR.get(hl, TEXT2)
    hl_fill = int((heat.get('level', 0) / 4) * 100)

    fee_pct  = fees / real_bal
    fee_fill = min(100, int(fee_pct / MAX_DAILY_FEE_DRAG_PCT * 100)) if MAX_DAILY_FEE_DRAG_PCT else 0
    fee_clr  = GREEN if fee_fill < 50 else (AMBER if fee_fill < 75 else RED)

    if secs is None:
        wd_fill, wd_clr, wd_val = 0, TEXT3, 'Not started'
    elif secs > 900:
        wd_fill, wd_clr, wd_val = 100, RED, f'Stale {int(secs)}s'
    elif secs > 300:
        wd_fill, wd_clr, wd_val = int(secs / 9), AMBER, f'{int(secs)}s ago'
    else:
        wd_fill, wd_clr, wd_val = int(secs / 9), GREEN, f'{int(secs)}s ago'

    def _g(name, fill, color, val, sub=''):
        return (
            f'<div class="gauge-block">'
            f'<div class="gauge-top">'
            f'  <span class="gauge-name">{name}</span>'
            f'  <span class="gauge-val" style="color:{color};">{val}</span>'
            f'</div>'
            f'<div class="gauge-track">'
            f'  <div class="gauge-fill" style="width:{fill}%;background:{color};"></div>'
            f'</div>'
            f'{"<div class=gauge-sub>" + sub + "</div>" if sub else ""}'
            f'</div>'
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_g("Daily Loss", dl_fill, dl_clr,
                       f"{dp:.1%} / {MAX_DAILY_LOSS_PCT:.0%}",
                       f"${heat.get('daily_pnl',0):.2f} today"), unsafe_allow_html=True)
    with c2:
        st.markdown(_g("Heat Level", hl_fill, hl_clr, hl,
                       f"{heat.get('size_factor',1.0):.0%} size"), unsafe_allow_html=True)
    with c3:
        st.markdown(_g("Fee Drag", fee_fill, fee_clr,
                       f"{fee_pct:.1%}",
                       f"${fees:.2f} of {MAX_DAILY_FEE_DRAG_PCT:.0%} limit"), unsafe_allow_html=True)
    with c4:
        st.markdown(_g("Watchdog", wd_fill, wd_clr, wd_val,
                       f"{risk.get('open_positions',0)}/{MAX_POSITIONS_CRYPTO} positions open"),
                    unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# RECENT TRADES + SIGNALS
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def comp_trades_signals():
    left, right = st.columns(2)

    with left:
        _sec("RECENT TRADES",
             "Last 20 closed trades. P&L is net after fees. "
             "Green = winner, red = loser. "
             "Win rate and profit factor from these trades feed the edge monitor.")
        trades = get_recent_trades(limit=20, paper=PAPER_TRADING) or []
        if not trades:
            st.markdown(f'<div style="color:{TEXT3};padding:16px 0;font-size:14px;">'
                        f'No trades yet</div>', unsafe_allow_html=True)
        else:
            html = ''
            for t in trades:
                pnl  = t.get('pnl_usd', 0)
                clr  = GREEN if pnl > 0 else (RED if pnl < 0 else TEXT2)
                sign = '+' if pnl > 0 else ''
                html += (
                    f'<div class="tr">'
                    f'<span class="tr-time">{_fmt_ts(t.get("ts",""), short=True)}</span>'
                    f'<span class="tr-sym">{t.get("symbol","—")}</span>'
                    f'<span class="tr-act">{t.get("action","")}</span>'
                    f'<span class="tr-pnl" style="color:{clr};">{sign}${pnl:.2f}</span>'
                    f'</div>'
                )
            st.markdown(f'<div class="card-flush">{html}</div>', unsafe_allow_html=True)

    with right:
        _sec("RECENT SIGNALS",
             "All signals generated today. "
             "BUY = debate voted 2/3 agents BUY — trade was taken. "
             "HOLD = debate blocked it (or ML gate filtered it). "
             "Confidence shown where available.")
        signals = get_scan_feed(limit=30) or []
        if not signals:
            st.markdown(f'<div style="color:{TEXT3};padding:16px 0;font-size:14px;">'
                        f'No signals yet</div>', unsafe_allow_html=True)
        else:
            html = ''
            for s in signals:
                act  = str(s.get('action', s.get('signal', ''))).upper()
                sym  = s.get('symbol', '—')
                conf = s.get('confidence', '')
                ts   = _fmt_ts(s.get('ts', ''), short=True)

                if act == 'BUY':
                    badge_bg, badge_clr = '#0d2218', GREEN
                elif act == 'SELL':
                    badge_bg, badge_clr = '#220d10', RED
                else:
                    badge_bg, badge_clr = SURFACE, TEXT2

                conf_str = f'<span class="sr-conf">{float(conf):.0%}</span>' if conf else ''

                html += (
                    f'<div class="sr">'
                    f'<span class="sr-time">{ts}</span>'
                    f'<span class="sr-badge" style="background:{badge_bg};color:{badge_clr};'
                    f'border:1px solid {badge_clr}33;">{act}</span>'
                    f'<span class="sr-sym">{sym}</span>'
                    f'{conf_str}'
                    f'</div>'
                )
            st.markdown(f'<div class="card-flush">{html}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE AI CHAT
# ══════════════════════════════════════════════════════════════════════════════

def comp_chat():
    _sec("AI CHAT",
         "Ask Claude anything about the portfolio. "
         "Full system context is injected automatically: positions, P&L, recent trades, "
         "risk rules, win rate, edge scores. Claude sees the same data you do on this dashboard.")

    if 'msgs' not in st.session_state:
        st.session_state['msgs'] = []

    if st.session_state['msgs']:
        st.markdown('<div class="card-sm">', unsafe_allow_html=True)
        for m in st.session_state['msgs'][-12:]:
            css = 'chat-u' if m['role'] == 'user' else 'chat-a'
            st.markdown(f'<div class="{css}">{m["content"]}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    prompt = st.chat_input("Ask about positions, signals, or strategy…")
    if prompt:
        st.session_state['msgs'].append({'role': 'user', 'content': prompt})
        with st.spinner(''):
            reply = call_claude(
                [{'role': m['role'], 'content': m['content']}
                 for m in st.session_state['msgs']],
                _build_ctx()
            )
        st.session_state['msgs'].append({'role': 'assistant', 'content': reply})
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# EXPANDERS
# ══════════════════════════════════════════════════════════════════════════════

def expander_debates():
    with st.expander("AI DEBATE HISTORY"):
        _info("Full reasoning from each 3-agent debate. "
              "Bardock = macro/funding/OI. Vegeta = technical momentum. "
              "Krillin = trade economics and fee math. 2/3 BUY = trade taken.")
        debates = get_recent_debates(limit=10) or []
        if not debates:
            st.write("No debates yet.")
            return
        for d in debates:
            res  = str(d.get('result', '?')).upper()
            clr  = GREEN if res == 'BUY' else (RED if res == 'SELL' else TEXT2)
            conf = d.get('confidence', 0)
            st.markdown(
                f'**{_fmt_ts(d.get("ts",""))}** &nbsp; '
                f'`{d.get("symbol","?")}` &nbsp; '
                f'<span style="color:{clr};font-weight:700;">{res}</span> '
                f'({conf:.0%}) — {str(d.get("reason",""))[:150]}',
                unsafe_allow_html=True,
            )

def expander_notifications():
    with st.expander("NOTIFICATIONS"):
        _info("All system events. ERROR and HALT level items require immediate attention.")
        notifs = get_recent_notifications(limit=50) or []
        if not notifs:
            st.write("No notifications.")
            return
        for n in notifs:
            level = n.get('level', 'INFO')
            clr   = RED if level in ('ERROR', 'HALT') else (AMBER if level == 'WARNING' else TEXT3)
            st.markdown(
                f'<div style="font-family:monospace;font-size:12px;color:{clr};'
                f'padding:3px 0;border-bottom:1px solid {BORDER};">'
                f'[{_fmt_ts(n.get("ts",""))}] <b>[{level}]</b> {n.get("message","")}</div>',
                unsafe_allow_html=True,
            )

def expander_controls():
    with st.expander("CONTROLS"):
        _info("Bot management. .env changes take effect on the next scan cycle.")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("▶ Start Bot (paper)"):
                import subprocess
                subprocess.Popen(
                    ['python3', 'main.py', '--mode', 'paper'],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    start_new_session=True,
                )
                st.success("Bot started.")
        with c2:
            if st.button("⏹ Kill Bot"):
                import subprocess
                subprocess.run(['pkill', '-f', 'main.py'])
                st.warning("Kill signal sent.")
        with c3:
            if st.button("💾 Backup DB"):
                import subprocess
                r = subprocess.run(
                    ['bash', 'scripts/backup_db.sh'],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    capture_output=True, text=True,
                )
                st.success("Backup complete.") if r.returncode == 0 else st.error(r.stderr[:200])

        st.markdown(f'<div style="height:16px;"></div>', unsafe_allow_html=True)
        c4, c5 = st.columns(2)
        with c4:
            new_size = st.number_input(
                'Crypto position size ($)',
                min_value=10.0, max_value=5000.0,
                value=float(CRYPTO_POSITION_SIZE_USD), step=10.0,
            )
            if st.button("Update"):
                _write_env({'CRYPTO_POSITION_SIZE_USD': str(new_size)})
                st.success(f"Set to ${new_size:.0f}. Restart bot to apply.")
        with c5:
            st.metric("Max risk/trade", f"{MAX_RISK_PER_TRADE_PCT:.1%}",
                      help="Fraction of account risked per trade")
            st.metric("Taker fee", f"{COINBASE_TAKER_FEE_PCT:.3%}",
                      help="Coinbase taker fee used in P&L math")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    comp_status_bar()
    comp_hero()
    comp_metrics()
    comp_edge()
    comp_positions()
    comp_markets()
    comp_risk()
    comp_trades_signals()
    comp_chat()
    st.markdown(f'<div style="height:24px;"></div>', unsafe_allow_html=True)
    expander_debates()
    expander_notifications()
    expander_controls()


main()
