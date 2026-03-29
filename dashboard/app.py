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
    BINANCE_SPOT_MAKER_FEE_PCT,
    LANE3_ENABLED,
)
from logging_db.trade_logger import (
    get_todays_pnl, get_todays_fees, get_todays_trade_fees, get_todays_api_cost,
    get_todays_signals,
    get_scan_feed, get_all_time_stats, get_recent_debates,
    get_monthly_api_cost, get_win_rate, get_recent_trades,
    get_recent_notifications, get_performance_attribution,
    get_intelligence_log,
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
    from logging_db.trade_logger import get_trade_quality_stats, get_open_position_health
except (ImportError, Exception):
    def get_trade_quality_stats(**kwargs):
        return {'entry_timing': 5.0, 'exit_efficiency': 5.0, 'thesis_hit_rate': 0.5,
                'agent_edge_pct': 0.0, 'exit_type_dist': {}, 'avg_super_score': 0.0, 'n': 0}
    def get_open_position_health(**kwargs):
        return []

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
.block-container {{ padding: 20px 24px 40px 24px !important; max-width: 100% !important; }}
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
    padding: 40px 40px;
    text-align: center;
    position: relative;
    overflow: hidden;
}}
.hero::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, {GOLD}55, transparent);
}}
.hero-label {{
    font-size: 12px; font-weight: 700;
    letter-spacing: 5px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 14px;
}}
.hero-pnl {{
    font-size: 96px; font-weight: 900; line-height: 1;
    letter-spacing: -3px;
    font-family: 'Inter', sans-serif;
}}
.hero-sub {{
    font-size: 16px; color: {TEXT2};
    margin-top: 18px; font-weight: 500;
}}

/* ── METRIC CARD ── */
.met {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 22px 24px 20px 24px;
    height: 100%;
}}
.met-label {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 3px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 12px;
}}
.met-value {{
    font-size: 34px; font-weight: 800;
    line-height: 1; letter-spacing: -0.5px;
    font-family: 'Inter', sans-serif;
}}
.met-sub {{
    font-size: 13px; color: {TEXT2};
    margin-top: 10px; font-weight: 500;
}}

/* ── SECTION HEADER ── */
.sec {{
    font-size: 12px; font-weight: 700;
    letter-spacing: 3px; text-transform: uppercase;
    color: {TEXT3}; padding: 28px 0 12px 0;
    display: flex; align-items: center; gap: 10px;
}}
.sec-line {{
    flex: 1; height: 1px; background: {BORDER};
}}

/* ── EDGE CARD ── */
.edge-card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 24px 24px 22px 24px;
}}
.edge-market {{
    font-size: 11px; font-weight: 700;
    letter-spacing: 4px; text-transform: uppercase;
    color: {TEXT3}; margin-bottom: 14px;
}}
.edge-status {{
    font-size: 22px; font-weight: 800;
    letter-spacing: 1px; margin-bottom: 16px;
}}
.edge-track {{
    background: {SURFACE}; border-radius: 100px;
    height: 12px; overflow: hidden; margin-bottom: 16px;
}}
.edge-fill {{
    height: 100%; border-radius: 100px;
    transition: width 0.4s ease;
}}
.edge-row {{
    display: flex; justify-content: space-between;
    font-size: 14px; color: {TEXT2}; margin-bottom: 8px;
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
    font-size: 22px; font-weight: 800; color: {TEXT};
    font-family: 'JetBrains Mono', monospace;
}}
.pos-strategy {{
    font-size: 12px; color: {TEXT3};
    font-weight: 600; letter-spacing: 1px;
    text-transform: uppercase; margin-top: 3px;
}}
.pos-age {{
    font-size: 13px; color: {TEXT2}; font-weight: 500;
    background: {CARD}; border-radius: 20px;
    padding: 4px 12px; border: 1px solid {BORDER};
}}
.pos-bars {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
}}
.pos-dist-label {{
    font-size: 12px; color: {TEXT3};
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 5px;
}}
.pos-dist-val {{
    font-size: 20px; font-weight: 700;
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
    align-items: flex-start; margin-bottom: 12px;
}}
.mkt-key {{
    font-size: 13px; color: {TEXT2}; font-weight: 500;
    flex-shrink: 0; margin-right: 10px;
}}
.mkt-val {{
    font-size: 14px; color: {TEXT};
    font-weight: 600; font-family: 'JetBrains Mono', monospace;
    text-align: right;
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
    font-size: 12px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 2px;
    color: {TEXT2};
}}
.gauge-val {{
    font-size: 16px; font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
}}
.gauge-track {{
    background: {SURFACE}; border-radius: 100px;
    height: 10px; overflow: hidden;
}}
.gauge-fill {{
    height: 100%; border-radius: 100px;
    transition: width 0.3s;
}}
.gauge-sub {{
    font-size: 12px; color: {TEXT3};
    margin-top: 8px;
}}

/* ── TRADE ROW ── */
.tr {{
    display: flex; align-items: center;
    padding: 14px 0; border-bottom: 1px solid {BORDER};
    gap: 0;
}}
.tr:last-child {{ border-bottom: none; }}
.tr-time {{
    font-size: 13px; color: {TEXT2};
    font-family: 'JetBrains Mono', monospace;
    min-width: 90px;
}}
.tr-sym {{
    font-size: 15px; font-weight: 700; color: {TEXT};
    min-width: 120px; font-family: 'JetBrains Mono', monospace;
}}
.tr-act {{
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    min-width: 50px; color: {TEXT2};
}}
.tr-pnl {{
    margin-left: auto; font-size: 17px; font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
}}

/* ── SIGNAL ROW ── */
.sr {{
    display: flex; align-items: center;
    padding: 12px 0; border-bottom: 1px solid {BORDER};
    gap: 12px;
}}
.sr:last-child {{ border-bottom: none; }}
.sr-time {{
    font-size: 12px; color: {TEXT2};
    font-family: 'JetBrains Mono', monospace;
    min-width: 72px;
}}
.sr-badge {{
    font-size: 11px; font-weight: 800;
    text-transform: uppercase; letter-spacing: 1px;
    padding: 3px 10px; border-radius: 20px;
    min-width: 50px; text-align: center;
}}
.sr-sym {{
    font-size: 14px; font-weight: 600;
    color: {TEXT}; font-family: 'JetBrains Mono', monospace;
}}
.sr-conf {{
    margin-left: auto; font-size: 13px;
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
        f"Today P&L: ${pnl:+.2f} | Total cost: ${fees:.2f} (trade fees + API) | 14d WR: {wr14:.1%} | API/month: ${mo:.2f}\n"
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
            "Today's net P&L = realized gross P&L minus ALL costs since midnight ET: "
            "exchange commissions (Binance spot 0.1%) AND Claude API costs. "
            "This is the true scoreboard — every dollar this system costs is accounted for. "
            "Balance reflects your starting account plus all-time cumulative P&L."
        )

    pnl        = get_todays_pnl(paper=PAPER_TRADING)
    trade_fees = get_todays_trade_fees(paper=PAPER_TRADING)
    api_cost   = get_todays_api_cost()
    fees       = trade_fees + api_cost
    net        = pnl - fees
    stats      = get_all_time_stats(paper=PAPER_TRADING)
    real_bal   = ACCOUNT_SIZE + stats.get('total_pnl', 0)

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
        f'Exchange&nbsp;fees&nbsp;<strong style="color:{TEXT2};">−${trade_fees:.2f}</strong>'
        f'&emsp;·&emsp;'
        f'API&nbsp;<strong style="color:{TEXT2};">−${api_cost:.3f}</strong>'
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
        "<b>API Cost</b>: Claude AI spend today (debate agents + exit reviews). Monthly total in sub-label."
    )

    pnl        = get_todays_pnl(paper=PAPER_TRADING)
    trade_fees = get_todays_trade_fees(paper=PAPER_TRADING)
    api_today  = get_todays_api_cost()
    fees       = trade_fees + api_today
    stats      = get_all_time_stats(paper=PAPER_TRADING)
    real_bal   = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    wr20       = get_win_rate(lookback_days=7, paper=PAPER_TRADING)
    mo         = get_monthly_api_cost()
    heat       = get_heat_level(paper=PAPER_TRADING)
    es         = get_edge_state('crypto_macd_consensus', paper=PAPER_TRADING)
    net        = pnl - fees
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
                         f"gross {net_sign}${pnl:.2f} · exch −${trade_fees:.2f} · api −${api_today:.3f}", net_clr), unsafe_allow_html=True)
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
# TRADE QUALITY
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def comp_trade_quality():
    _sec(
        "TRADE QUALITY",
        "How well is the system executing? Entry Timing and Exit Efficiency measure "
        "the quality of our fills relative to the trade's range. Thesis Hit Rate measures "
        "how often setups actually play out in our direction. Super Score is the rolling "
        "entry conviction composite. All metrics computed over the last 20 closed trades.",
    )

    qs = get_trade_quality_stats(lookback=20, paper=PAPER_TRADING)
    entry_timing   = float(qs.get('entry_timing', 5.0))
    exit_eff       = float(qs.get('exit_efficiency', 5.0))
    thesis_hit     = float(qs.get('thesis_hit_rate', 0.5))
    avg_super      = float(qs.get('avg_super_score', 0.0))
    exit_dist      = qs.get('exit_type_dist', {}) or {}
    n              = int(qs.get('n', 0))

    # ── Scorecard colors ───────────────────────────────────────────────────────
    def _timing_clr(v):
        return GREEN if v >= 7 else (RED if v < 4 else AMBER)

    def _exit_clr(v):
        return GREEN if v >= 6 else (RED if v < 3 else AMBER)

    def _thesis_clr(v):
        return GREEN if v >= 0.60 else (RED if v < 0.35 else AMBER)

    def _super_clr(v):
        return GREEN if v >= 65 else (RED if v < 45 else AMBER)

    def _met(label, value, sub, color, tooltip):
        return (
            f'<div class="met" title="{tooltip}">'
            f'<div class="met-label">{label}</div>'
            f'<div class="met-value" style="color:{color};">{value}</div>'
            f'<div class="met-sub">{sub}</div>'
            f'</div>'
        )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            _met(
                "ENTRY TIMING",
                f"{entry_timing:.1f} / 10",
                "avg adverse excursion vs stop",
                _timing_clr(entry_timing),
                "How well-timed are entries? 10 = price never went against us. "
                "Below 4 = entering too early or chasing. "
                "Measures avg MAE as fraction of stop distance.",
            ),
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            _met(
                "EXIT EFFICIENCY",
                f"{exit_eff:.1f} / 10",
                "% of MFE captured",
                _exit_clr(exit_eff),
                "Did we capture the move? exit_pnl / max_favorable_excursion. "
                "10 = exited at the peak. "
                "Below 4 = bailing before the move plays out.",
            ),
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            _met(
                "THESIS HIT RATE",
                f"{thesis_hit:.0%}",
                "trades where price cleared stop distance",
                _thesis_clr(thesis_hit),
                "What % of trades did price move in our favor by at least the stop distance? "
                "Below 35% means setups aren't playing out — "
                "wrong market conditions or signal timing.",
            ),
            unsafe_allow_html=True,
        )

    with c4:
        super_val = f"{avg_super:.0f}" if avg_super > 0 else "—"
        st.markdown(
            _met(
                "SUPER SCORE AVG",
                super_val,
                "rolling entry conviction",
                _super_clr(avg_super) if avg_super > 0 else TEXT3,
                "Average SUPER SCORE across last 20 entries. "
                "Composite of ML model, signal confluence, agent consensus, "
                "market context, and microstructure.",
            ),
            unsafe_allow_html=True,
        )

    # ── Exit type distribution ─────────────────────────────────────────────────
    if exit_dist:
        total_exits = sum(exit_dist.values()) or 1
        parts = []
        for etype, count in exit_dist.items():
            pct = count / total_exits
            if etype in ('stop_hit', 'stagnant') and pct > 0.40:
                clr = RED
            elif etype == 'target_hit':
                clr = GREEN
            else:
                clr = TEXT2
            parts.append(
                f'<span style="color:{TEXT3};font-weight:600;">{etype}:</span>'
                f'&nbsp;<span style="color:{clr};font-weight:700;">{count}</span>'
            )
        mix_html = (
            f'<div style="font-size:12px;padding:10px 14px;background:{SURFACE};'
            f'border:1px solid {BORDER};border-radius:10px;margin-top:10px;'
            f'display:flex;align-items:center;gap:16px;flex-wrap:wrap;">'
            f'<span style="font-size:11px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{TEXT3};margin-right:4px;">EXIT MIX (last {n}):</span>'
            + '&ensp;'.join(parts)
            + f'</div>'
        )
        st.markdown(mix_html, unsafe_allow_html=True)

    # ── Open position health cards ─────────────────────────────────────────────
    health_cards = get_open_position_health(paper=PAPER_TRADING)

    if not health_cards:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:16px 0 4px 0;'
            f'text-align:center;font-style:italic;">No open positions</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f'<div style="font-size:11px;font-weight:700;letter-spacing:3px;'
        f'text-transform:uppercase;color:{TEXT3};padding:18px 0 8px 0;">OPEN POSITION HEALTH</div>',
        unsafe_allow_html=True,
    )

    now = datetime.now(_tz.utc)

    for pos in health_cards:
        symbol  = pos.get('symbol', '—')
        strat   = pos.get('strategy', '—')
        direc   = pos.get('direction', 'LONG')
        entry   = float(pos.get('entry', 0))
        stop    = float(pos.get('stop', 0))
        target  = float(pos.get('target', 0))
        qty     = float(pos.get('qty', 0))
        high_se = float(pos.get('high_since_entry', entry))
        low_se  = float(pos.get('low_since_entry', entry))
        ts_raw  = pos.get('ts_entry', '')

        # Time in trade
        mins_in = 0
        age_str = '—'
        try:
            dt = datetime.fromisoformat(ts_raw)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_tz.utc)
            mins_in = int((now - dt).total_seconds() / 60)
            age_str = f'{mins_in}m' if mins_in < 60 else f'{mins_in // 60}h {mins_in % 60}m'
        except Exception:
            pass

        target_range = (target - entry) if target > entry else 0
        stop_range   = (entry - stop)   if entry > stop   else 0

        mfe_pct = 0.0
        if target_range > 0:
            mfe_pct = (high_se - entry) / target_range * 100
            mfe_pct = max(0.0, min(100.0, mfe_pct))

        mae_pct = 0.0
        if stop_range > 0:
            mae_pct = (entry - low_se) / stop_range * 100
            mae_pct = max(0.0, min(100.0, mae_pct))

        pnl_est = (high_se - entry) * qty

        # Status badge
        if mfe_pct >= 30:
            badge = f'<span style="color:{GREEN};font-weight:700;">✅ thesis working</span>'
        elif mae_pct >= 70:
            badge = f'<span style="color:{RED};font-weight:700;">⚠️ near stop</span>'
        else:
            badge = f'<span style="color:{AMBER};font-weight:700;">⏳ developing</span>'

        mfe_bar  = int(mfe_pct)
        mae_bar  = int(mae_pct)
        pnl_sign = '+' if pnl_est >= 0 else ''
        pnl_clr  = GREEN if pnl_est >= 0 else RED

        card_html = (
            f'<div style="background:{SURFACE};border:1px solid {BORDER};'
            f'border-radius:12px;padding:16px 18px;margin-bottom:8px;'
            f'border-left:3px solid {GOLD};">'

            # Header row
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">'
            f'  <div>'
            f'    <span style="font-size:20px;font-weight:800;color:{TEXT};'
            f'    font-family:\'JetBrains Mono\',monospace;">{symbol}</span>'
            f'    &nbsp;&nbsp;'
            f'    <span style="font-size:12px;color:{TEXT3};font-weight:600;'
            f'    text-transform:uppercase;letter-spacing:1px;">{strat} · {direc}</span>'
            f'  </div>'
            f'  <span style="font-size:13px;color:{TEXT2};font-weight:500;'
            f'  background:{CARD};border-radius:20px;padding:4px 12px;'
            f'  border:1px solid {BORDER};">{age_str} in</span>'
            f'</div>'

            # Price levels
            f'<div style="font-size:12px;color:{TEXT3};font-family:\'JetBrains Mono\',monospace;'
            f'margin-bottom:12px;">'
            f'Entry:&nbsp;<span style="color:{TEXT};">{entry:.5g}</span>'
            f'&ensp;·&ensp;Stop:&nbsp;<span style="color:{RED};">{stop:.5g}</span>'
            f'&ensp;·&ensp;Target:&nbsp;<span style="color:{GREEN};">{target:.5g}</span>'
            f'</div>'

            # MFE progress bar
            f'<div style="margin-bottom:10px;">'
            f'  <div style="display:flex;justify-content:space-between;'
            f'  font-size:11px;color:{TEXT3};margin-bottom:4px;">'
            f'    <span style="font-weight:700;letter-spacing:1px;text-transform:uppercase;">MFE Progress</span>'
            f'    <span style="color:{GREEN};font-weight:700;">{mfe_pct:.0f}%</span>'
            f'  </div>'
            f'  <div style="background:{BORDER};border-radius:100px;height:6px;overflow:hidden;">'
            f'    <div style="width:{mfe_bar}%;height:100%;background:{GREEN};border-radius:100px;"></div>'
            f'  </div>'
            f'</div>'

            # MAE exposure bar
            f'<div style="margin-bottom:10px;">'
            f'  <div style="display:flex;justify-content:space-between;'
            f'  font-size:11px;color:{TEXT3};margin-bottom:4px;">'
            f'    <span style="font-weight:700;letter-spacing:1px;text-transform:uppercase;">MAE Exposure</span>'
            f'    <span style="color:{RED};font-weight:700;">{mae_pct:.0f}% of stop distance</span>'
            f'  </div>'
            f'  <div style="background:{BORDER};border-radius:100px;height:6px;overflow:hidden;">'
            f'    <div style="width:{mae_bar}%;height:100%;background:{RED};border-radius:100px;"></div>'
            f'  </div>'
            f'</div>'

            # P&L estimate + status badge
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">'
            f'  <span style="font-size:13px;color:{TEXT2};">'
            f'    P&amp;L est:&nbsp;'
            f'    <span style="color:{pnl_clr};font-weight:700;font-family:\'JetBrains Mono\',monospace;">'
            f'    {pnl_sign}${pnl_est:.2f}</span>'
            f'    <span style="font-size:11px;color:{TEXT3};">&nbsp;(high since entry)</span>'
            f'  </span>'
            f'  {badge}'
            f'</div>'

            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)


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
    signals   = get_todays_signals() or []

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
        ('CRYPTO',      'crypto',   'Binance · 1-min · up to 5 positions', 'crypto_macd_consensus'),
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
        "Fee drag: exchange commissions + Claude API costs today as % of account (limit 10%). "
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

def expander_lane3():
    """Lane 3 — Prediction Markets panel (only shown when LANE3_ENABLED=true)."""
    try:
        from config import LANE3_ENABLED, POLYMARKET_ENABLED, KALSHI_ENABLED, POLYMARKET_PAPER, KALSHI_PAPER
        if not LANE3_ENABLED:
            return
    except Exception:
        return

    with st.expander("LANE 3 — PREDICTION MARKETS"):
        pm_tag = "PAPER" if POLYMARKET_PAPER else "LIVE"
        kx_tag = "PAPER" if KALSHI_PAPER else "LIVE"
        enabled = []
        if POLYMARKET_ENABLED:
            enabled.append(f"Polymarket ({pm_tag})")
        if KALSHI_ENABLED:
            enabled.append(f"Kalshi ({kx_tag})")
        st.markdown(
            f'<div style="font-size:13px;color:{TEXT2};margin-bottom:8px;">'
            f'Active platforms: {", ".join(enabled) if enabled else "None configured"}</div>',
            unsafe_allow_html=True,
        )

        # Open prediction market positions (from lane3 trades)
        try:
            import sqlite3
            from config import DB_PATH, PAPER_TRADING
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT ts, strategy, symbol, action, qty, price, notes
                   FROM trades
                   WHERE notes LIKE '%lane=lane3%' AND pnl_usd=0
                   ORDER BY ts DESC LIMIT 20"""
            ).fetchall()
            conn.close()

            if rows:
                st.markdown(f'<div style="font-size:12px;color:{TEXT3};font-weight:600;margin-bottom:4px;">OPEN PREDICTIONS</div>', unsafe_allow_html=True)
                for r in rows:
                    notes = r["notes"] or ""
                    side = "YES" if "pm_side=YES" in notes or "kx_side=YES" in notes else "NO"
                    platform = "PM" if "polymarket" in r["strategy"] else "KX"
                    st.markdown(
                        f'<div style="font-family:monospace;font-size:11px;color:{TEXT2};'
                        f'padding:2px 0;border-bottom:1px solid {BORDER};">'
                        f'[{_fmt_ts(r["ts"])}] {platform} {side} {r["symbol"]} '
                        f'${r["qty"]*r["price"]:.2f} @ {r["price"]:.3f}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(f'<div style="font-size:12px;color:{TEXT3};">No open prediction market positions.</div>', unsafe_allow_html=True)

            # Recent resolutions
            resolved = conn.execute(
                """SELECT ts, strategy, symbol, action, pnl_usd, notes
                   FROM trades
                   WHERE notes LIKE '%lane=lane3%' AND pnl_usd != 0
                   ORDER BY ts DESC LIMIT 10"""
            ).fetchall()
            conn.close()
            if resolved:
                st.markdown(f'<div style="font-size:12px;color:{TEXT3};font-weight:600;margin-top:8px;margin-bottom:4px;">RECENT RESOLUTIONS</div>', unsafe_allow_html=True)
                for r in resolved:
                    pnl = r["pnl_usd"]
                    clr = GREEN if pnl >= 0 else RED
                    result = "WON" if pnl >= 0 else "LOST"
                    st.markdown(
                        f'<div style="font-family:monospace;font-size:11px;color:{clr};'
                        f'padding:2px 0;border-bottom:1px solid {BORDER};">'
                        f'[{_fmt_ts(r["ts"])}] {result} {r["symbol"]} P&L=${pnl:+.2f}</div>',
                        unsafe_allow_html=True,
                    )

            # Calibration stats
            try:
                from learning.pm_calibrator import get_pm_calibration_stats
                stats = get_pm_calibration_stats()
                total = stats.get("total_records", 0)
                st.markdown(
                    f'<div style="font-size:11px;color:{TEXT3};margin-top:8px;">'
                    f'Calibration: {total} resolved outcomes tracked '
                    f'(Platt scaling activates after 30)</div>',
                    unsafe_allow_html=True,
                )
            except Exception:
                pass

        except Exception as e:
            st.markdown(f'<div style="color:{RED};font-size:12px;">Lane 3 data error: {e}</div>', unsafe_allow_html=True)


def expander_intelligence():
    with st.expander("🧠 SELF-LEARNING LOG"):
        _info(
            "Everything the bot has taught itself. "
            "<b>Meta-Analysis</b>: Claude reviews the last 100 trades and identifies patterns the math can't see — "
            "runs after every 10 trade closes. "
            "<b>Signal Recommendations</b>: specific weight adjustments Claude recommended and why. "
            "<b>Agent Accuracy</b>: how often each debate agent's BUY vote led to a winning trade. "
            "<b>ML Retrains</b>: when the prediction model was updated and on how many trades. "
            "<b>Signal Leaderboard</b>: current Bayesian win-rate ranking (updates after every close)."
        )

        log = get_intelligence_log(limit=20)

        # ── Meta-analysis runs ───────────────────────────────────────────────
        st.markdown(f'<div style="font-size:13px;font-weight:700;color:{GOLD};'
                    f'letter-spacing:0.08em;margin:8px 0 10px;">WHAT CLAUDE LEARNED</div>',
                    unsafe_allow_html=True)

        meta = log.get('meta_analyses', [])
        if not meta:
            st.markdown(f'<div style="color:{TEXT3};font-size:13px;padding:8px 0;">'
                        f'No meta-analyses yet — fires after every 10 trade closes.</div>',
                        unsafe_allow_html=True)
        else:
            for m in meta:
                wr     = m.get('win_rate')
                wr_str = f"{wr*100:.0f}%" if wr is not None else "?"
                wr_clr = GREEN if wr and wr >= 0.52 else (AMBER if wr and wr >= 0.45 else RED)
                ts_str = _fmt_ts(m.get('created_at', ''))
                n      = m.get('trades_analyzed', 0)
                recs   = m.get('recs_count', 0)
                insight = m.get('key_insight') or '—'
                patterns = m.get('patterns_found') or ''

                st.markdown(
                    f'<div style="border:1px solid {BORDER};border-radius:10px;'
                    f'padding:14px 16px;margin-bottom:10px;background:{SURFACE};">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
                    f'  <span style="font-size:12px;color:{TEXT3};">{ts_str}</span>'
                    f'  <span style="font-size:12px;color:{TEXT2};">{n} trades · '
                    f'WR <span style="color:{wr_clr};font-weight:700;">{wr_str}</span> · '
                    f'{recs} recs made</span>'
                    f'</div>'
                    f'<div style="font-size:13px;color:{TEXT};font-weight:600;margin-bottom:6px;">'
                    f'💡 {insight}</div>'
                    + (f'<div style="font-size:12px;color:{TEXT2};font-style:italic;">{patterns[:300]}</div>'
                       if patterns else '')
                    + f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

        # ── Active signal recommendations ────────────────────────────────────
        st.markdown(f'<div style="font-size:13px;font-weight:700;color:{GOLD};'
                    f'letter-spacing:0.08em;margin:8px 0 10px;">SIGNAL WEIGHT CHANGES</div>',
                    unsafe_allow_html=True)

        recs = log.get('recommendations', [])
        if not recs:
            st.markdown(f'<div style="color:{TEXT3};font-size:13px;padding:8px 0;">'
                        f'No recommendations yet.</div>', unsafe_allow_html=True)
        else:
            html = ''
            for r in recs:
                delta   = float(r.get('weight_delta') or 0)
                applied = bool(r.get('applied'))
                delta_clr = GREEN if delta > 0 else RED
                sign      = '+' if delta > 0 else ''
                arrow     = '▲' if delta > 0 else '▼'
                conf      = float(r.get('confidence') or 0)
                status    = f'<span style="color:{TEXT3};font-size:11px;">applied</span>' \
                            if applied else f'<span style="color:{AMBER};font-size:11px;">active</span>'
                regime_str = r.get('regime') or 'any'
                reasoning  = (r.get('reasoning') or '')[:120]
                html += (
                    f'<div style="padding:10px 0;border-bottom:1px solid {BORDER};">'
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'
                    f'  <span style="font-weight:700;color:{TEXT};font-size:13px;">'
                    f'    {r.get("signal_name","?")}</span>'
                    f'  <span style="color:{TEXT3};font-size:12px;">[{regime_str}]</span>'
                    f'  <span style="color:{delta_clr};font-weight:700;font-size:13px;">'
                    f'    {arrow} {sign}{delta:+.1f} pts</span>'
                    f'  <span style="color:{TEXT3};font-size:11px;">conf {conf:.0%}</span>'
                    f'  {status}'
                    f'</div>'
                    f'<div style="font-size:12px;color:{TEXT2};font-style:italic;">{reasoning}</div>'
                    f'</div>'
                )
            st.markdown(f'<div class="card-flush">{html}</div>', unsafe_allow_html=True)

        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

        # ── Two-column: Agent accuracy + ML events ───────────────────────────
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown(f'<div style="font-size:13px;font-weight:700;color:{GOLD};'
                        f'letter-spacing:0.08em;margin:8px 0 10px;">AGENT ACCURACY</div>',
                        unsafe_allow_html=True)
            agents = log.get('agent_accuracy', [])
            if not agents:
                st.markdown(f'<div style="color:{TEXT3};font-size:13px;">No agent data yet.</div>',
                            unsafe_allow_html=True)
            else:
                agent_labels = {
                    'funding_regime':    'Bardock (Macro)',
                    'momentum_structure':'Vegeta (Momentum)',
                    'risk_economics':    'Krillin (Risk)',
                }
                for a in agents:
                    name  = a.get('agent_name', '?')
                    label = agent_labels.get(name, name)
                    acc   = a.get('accuracy')
                    total = a.get('total_assessed', 0)
                    acc_str = f"{acc*100:.0f}%" if acc is not None else "—"
                    acc_clr = GREEN if acc and acc >= 0.55 else (AMBER if acc and acc >= 0.45 else RED)
                    bar_w   = int((acc or 0) * 100)
                    st.markdown(
                        f'<div style="padding:8px 0;border-bottom:1px solid {BORDER};">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                        f'  <span style="font-size:13px;color:{TEXT};">{label}</span>'
                        f'  <span style="font-size:13px;font-weight:700;color:{acc_clr};">'
                        f'    {acc_str}</span>'
                        f'</div>'
                        f'<div style="font-size:11px;color:{TEXT3};margin-bottom:4px;">'
                        f'  {total} assessed</div>'
                        f'<div style="height:4px;background:{BORDER};border-radius:2px;">'
                        f'  <div style="height:4px;width:{bar_w}%;background:{acc_clr};'
                        f'    border-radius:2px;transition:width 0.3s;"></div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        with col_b:
            st.markdown(f'<div style="font-size:13px;font-weight:700;color:{GOLD};'
                        f'letter-spacing:0.08em;margin:8px 0 10px;">ML MODEL RETRAINS</div>',
                        unsafe_allow_html=True)
            ml = log.get('ml_events', [])
            if not ml:
                st.markdown(f'<div style="color:{TEXT3};font-size:13px;">'
                            f'No retrains yet — triggers after 50 trade closes '
                            f'(currently runs as background subprocess).</div>',
                            unsafe_allow_html=True)
            else:
                html = ''
                for e in ml:
                    html += (
                        f'<div style="font-family:monospace;font-size:12px;color:{TEXT2};'
                        f'padding:5px 0;border-bottom:1px solid {BORDER};">'
                        f'<span style="color:{TEXT3};">{_fmt_ts(e.get("ts",""), short=True)}</span>'
                        f'&nbsp; {e.get("message","")[:120]}'
                        f'</div>'
                    )
                st.markdown(f'<div class="card-flush">{html}</div>', unsafe_allow_html=True)

        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

        # ── Signal leaderboard ───────────────────────────────────────────────
        st.markdown(f'<div style="font-size:13px;font-weight:700;color:{GOLD};'
                    f'letter-spacing:0.08em;margin:8px 0 10px;">SIGNAL LEADERBOARD '
                    f'<span style="font-weight:400;color:{TEXT3};font-size:11px;">'
                    f'(Bayesian pts, ≥5 fires)</span></div>',
                    unsafe_allow_html=True)

        signals = log.get('signal_shifts', [])
        if not signals:
            st.markdown(f'<div style="color:{TEXT3};font-size:13px;">No signal data yet.</div>',
                        unsafe_allow_html=True)
        else:
            max_pts = max((float(s.get('bayesian_pts') or 1) for s in signals), default=1)
            html = ''
            for i, s in enumerate(signals):
                pts    = float(s.get('bayesian_pts') or 0)
                wr     = float(s.get('win_rate') or 0)
                fires  = int(s.get('fires') or 0)
                wins   = int(s.get('wins') or 0)
                regime = s.get('regime', 'any')
                wr_clr = GREEN if wr >= 0.55 else (AMBER if wr >= 0.45 else RED)
                bar_w  = int(pts / max_pts * 100) if max_pts > 0 else 0
                rank_clr = GOLD if i == 0 else (TEXT2 if i < 3 else TEXT3)
                html += (
                    f'<div style="padding:8px 0;border-bottom:1px solid {BORDER};">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                    f'  <div>'
                    f'    <span style="color:{rank_clr};font-weight:700;font-size:12px;">#{i+1}</span>'
                    f'    &nbsp;<span style="font-size:13px;color:{TEXT};">'
                    f'      {s.get("signal_name","?")}</span>'
                    f'    &nbsp;<span style="font-size:11px;color:{TEXT3};">[{regime}]</span>'
                    f'  </div>'
                    f'  <div style="text-align:right;">'
                    f'    <span style="font-size:13px;font-weight:700;color:{wr_clr};">'
                    f'      {wr*100:.0f}% WR</span>'
                    f'    &nbsp;<span style="font-size:11px;color:{TEXT3};">'
                    f'      {wins}/{fires} · {pts:.1f}pts</span>'
                    f'  </div>'
                    f'</div>'
                    f'<div style="height:3px;background:{BORDER};border-radius:2px;">'
                    f'  <div style="height:3px;width:{bar_w}%;background:{wr_clr};border-radius:2px;"></div>'
                    f'</div>'
                    f'</div>'
                )
            st.markdown(f'<div class="card-flush">{html}</div>', unsafe_allow_html=True)


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
            st.metric("Exchange fee", f"{BINANCE_SPOT_MAKER_FEE_PCT:.3%}",
                      help="Binance spot maker fee (0.10%) — applied on both entry and exit")


# ══════════════════════════════════════════════════════════════════════════════
# CRYPTO SPOT TAB
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def comp_crypto_tab():
    positions = _positions()
    crypto_pos = positions.get('crypto_macd_consensus', {})
    # Also catch any strategy key with 'crypto' that isn't 'perp'
    all_crypto_pos: dict = {}
    for strat_key, syms in positions.items():
        if 'crypto' in strat_key.lower() and 'perp' not in strat_key.lower():
            all_crypto_pos.update(syms)

    # ── Open positions (crypto spot only) ──────────────────────────────────────
    _sec("CRYPTO SPOT POSITIONS",
         "Open positions from crypto_macd and mean_reversion strategies. "
         "Perp positions are in the PERP tab.")

    if not all_crypto_pos:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:16px 0;'
            f'text-align:center;font-style:italic;">No open crypto spot positions</div>',
            unsafe_allow_html=True,
        )
    else:
        for sym, p in all_crypto_pos.items():
            entry  = float(p.get('entry', 0))
            stop   = float(p.get('stop', 0))
            target = float(p.get('target', 0))
            direc  = p.get('direction', 'LONG')
            ts     = p.get('ts_entry', '')
            age_str = '—'
            try:
                from datetime import timezone as _tz2
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
            stop_clr  = RED if to_stop < 0.3 else (AMBER if to_stop < 0.8 else TEXT2)
            st.markdown(
                f'<div class="pos-card">'
                f'<div class="pos-top">'
                f'  <div>'
                f'    <div class="pos-symbol">{sym}</div>'
                f'    <div class="pos-strategy">crypto spot · {direc}</div>'
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

    # ── Per-strategy breakdown ─────────────────────────────────────────────────
    _sec("STRATEGY BREAKDOWN",
         "Performance split by strategy: crypto_macd vs mean_reversion. "
         "Based on last 30 days of closed trades.")
    try:
        perf = get_performance_attribution(paper=PAPER_TRADING, lookback_days=30)
        crypto_strats = {k: v for k, v in perf.items()
                         if 'crypto' in k.lower() and 'perp' not in k.lower()}
        if crypto_strats:
            parts = []
            for sname, sv in crypto_strats.items():
                n   = sv.get('total', 0)
                wr  = sv.get('win_rate', 0)
                pnl = sv.get('total_pnl', 0)
                wr_clr  = GREEN if wr >= 0.52 else (RED if wr < 0.40 else GOLD)
                pnl_clr = GREEN if pnl >= 0 else RED
                pnl_sgn = '+' if pnl >= 0 else ''
                parts.append(
                    f'<div class="card-sm" style="flex:1;">'
                    f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;'
                    f'text-transform:uppercase;color:{TEXT3};margin-bottom:8px;">{sname}</div>'
                    f'<div style="font-size:22px;font-weight:800;color:{wr_clr};">{wr:.0%}</div>'
                    f'<div style="font-size:12px;color:{TEXT2};margin-top:4px;">{n} trades</div>'
                    f'<div style="font-size:14px;font-weight:700;color:{pnl_clr};'
                    f'font-family:\'JetBrains Mono\',monospace;margin-top:6px;">{pnl_sgn}${pnl:.2f}</div>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="display:flex;gap:12px;">{"".join(parts)}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{TEXT3};font-size:14px;">No crypto spot attribution data yet.</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:13px;">Attribution unavailable: {e}</div>',
            unsafe_allow_html=True,
        )

    # ── Recent trades + scan feed ──────────────────────────────────────────────
    _sec("RECENT TRADES & SCAN FEED")
    left, right = st.columns(2)

    with left:
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:3px;'
            f'text-transform:uppercase;color:{TEXT3};padding-bottom:8px;">CRYPTO SPOT TRADES</div>',
            unsafe_allow_html=True,
        )
        all_trades = get_recent_trades(limit=100, paper=PAPER_TRADING) or []
        spot_trades = [
            t for t in all_trades
            if 'perp' not in t.get('strategy', '').lower()
            and 'equity' not in t.get('strategy', '').lower()
            and t.get('strategy', '') != ''
            and ('crypto' in t.get('strategy', '').lower()
                 or 'mean_reversion' in t.get('strategy', '').lower())
        ]
        if not spot_trades:
            st.markdown(
                f'<div style="color:{TEXT3};font-size:14px;padding:12px 0;">No crypto spot trades yet.</div>',
                unsafe_allow_html=True,
            )
        else:
            html = ''
            for t in spot_trades[:20]:
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
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:3px;'
            f'text-transform:uppercase;color:{TEXT3};padding-bottom:8px;">SCAN FEED</div>',
            unsafe_allow_html=True,
        )
        feed = get_scan_feed(limit=50) or []
        if not feed:
            st.markdown(
                f'<div style="color:{TEXT3};font-size:14px;padding:12px 0;">No scan feed yet.</div>',
                unsafe_allow_html=True,
            )
        else:
            html = ''
            for s in feed:
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

    # ── Last crypto AI debate ──────────────────────────────────────────────────
    _sec("LAST AI DEBATE",
         "Most recent 3-agent debate for a crypto spot pair. "
         "Bardock = macro/funding. Vegeta = momentum. Krillin = fee math.")
    try:
        debates = get_recent_debates(limit=10) or []
        crypto_debate = None
        for d in debates:
            sym = str(d.get('symbol', ''))
            strat = str(d.get('strategy', ''))
            if ('-USDC' in sym or '-USD' in sym or 'crypto' in strat.lower()):
                if 'perp' not in strat.lower():
                    crypto_debate = d
                    break
        if crypto_debate:
            res  = str(crypto_debate.get('result', '?')).upper()
            clr  = GREEN if res == 'BUY' else (RED if res == 'SELL' else TEXT2)
            conf = crypto_debate.get('confidence', 0)
            reason = str(crypto_debate.get('reason', ''))[:300]
            st.markdown(
                f'<div class="card-sm">'
                f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">'
                f'  <span style="font-size:18px;font-weight:800;color:{TEXT};'
                f'  font-family:\'JetBrains Mono\',monospace;">'
                f'  {crypto_debate.get("symbol","?")}</span>'
                f'  <span style="font-size:16px;font-weight:800;color:{clr};">{res}</span>'
                f'  <span style="font-size:13px;color:{TEXT2};">{conf:.0%} confidence</span>'
                f'  <span style="margin-left:auto;font-size:12px;color:{TEXT3};">'
                f'  {_fmt_ts(crypto_debate.get("ts",""))}</span>'
                f'</div>'
                f'<div style="font-size:13px;color:{TEXT2};line-height:1.5;">{reason}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{TEXT3};font-size:14px;padding:12px 0;">No crypto debates yet.</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:13px;">Debate unavailable: {e}</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# PERP TAB
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=15)
def comp_perp_tab():
    positions = _positions()
    # Collect all perp positions across any strategy key containing 'perp'
    perp_pos: dict = {}
    for strat_key, syms in positions.items():
        if 'perp' in strat_key.lower():
            perp_pos.update({sym: (strat_key, p) for sym, p in syms.items()})

    # ── Open perp positions ────────────────────────────────────────────────────
    _sec("OPEN PERP POSITIONS",
         "Binance USD-M perpetual futures positions. "
         "Funding-rate aware strategy. Positions auto-close after 4h if flat.")

    if not perp_pos:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:16px 0;'
            f'text-align:center;font-style:italic;">No open perp positions</div>',
            unsafe_allow_html=True,
        )
    else:
        for sym, (strat_key, p) in perp_pos.items():
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
            stop_pct  = min(100, int(to_stop * 20))
            tgt_pct   = min(100, int(to_target * 10))
            stop_clr  = RED if to_stop < 0.3 else (AMBER if to_stop < 0.8 else TEXT2)
            dir_clr   = GREEN if direc == 'LONG' else RED
            st.markdown(
                f'<div class="pos-card" style="border-left-color:{dir_clr};">'
                f'<div class="pos-top">'
                f'  <div>'
                f'    <div class="pos-symbol">{sym}</div>'
                f'    <div class="pos-strategy">perp · '
                f'    <span style="color:{dir_clr};">{direc}</span></div>'
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

    # ── Perp stats ─────────────────────────────────────────────────────────────
    _sec("PERP PERFORMANCE",
         "All-time perp strategy performance. "
         "4h flat exit prevents funding cost drag on stagnant positions.")
    try:
        perf = get_performance_attribution(paper=PAPER_TRADING, lookback_days=90)
        perp_strats = {k: v for k, v in perf.items() if 'perp' in k.lower()}
        if perp_strats:
            for sname, sv in perp_strats.items():
                n    = sv.get('total', 0)
                wins = sv.get('wins', 0)
                losses = sv.get('losses', 0)
                wr   = sv.get('win_rate', 0)
                pnl  = sv.get('total_pnl', 0)
                wr_clr  = GREEN if wr >= 0.52 else (RED if wr < 0.40 else GOLD)
                pnl_clr = GREEN if pnl >= 0 else RED
                pnl_sgn = '+' if pnl >= 0 else ''
                st.markdown(
                    f'<div class="card-sm">'
                    f'<div style="display:flex;gap:24px;flex-wrap:wrap;">'
                    f'  <div>'
                    f'    <div style="font-size:11px;color:{TEXT3};font-weight:700;'
                    f'    letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">Win Rate</div>'
                    f'    <div style="font-size:28px;font-weight:800;color:{wr_clr};">{wr:.0%}</div>'
                    f'  </div>'
                    f'  <div>'
                    f'    <div style="font-size:11px;color:{TEXT3};font-weight:700;'
                    f'    letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">Total P&L</div>'
                    f'    <div style="font-size:28px;font-weight:800;color:{pnl_clr};'
                    f'    font-family:\'JetBrains Mono\',monospace;">{pnl_sgn}${pnl:.2f}</div>'
                    f'  </div>'
                    f'  <div>'
                    f'    <div style="font-size:11px;color:{TEXT3};font-weight:700;'
                    f'    letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">Trades</div>'
                    f'    <div style="font-size:28px;font-weight:800;color:{TEXT};">{n}</div>'
                    f'  </div>'
                    f'  <div>'
                    f'    <div style="font-size:11px;color:{TEXT3};font-weight:700;'
                    f'    letter-spacing:2px;text-transform:uppercase;margin-bottom:4px;">W / L</div>'
                    f'    <div style="font-size:18px;font-weight:700;">'
                    f'    <span style="color:{GREEN};">{wins}W</span>'
                    f'    &nbsp;/&nbsp;'
                    f'    <span style="color:{RED};">{losses}L</span>'
                    f'    </div>'
                    f'  </div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div class="card-sm">'
                f'<div style="color:{TEXT2};font-size:14px;line-height:1.7;">'
                f'Perp is running on testnet — paper trading accumulating track record.<br>'
                f'<span style="color:{TEXT3};font-size:12px;font-family:\'JetBrains Mono\',monospace;">'
                f'Run <code>python3 scripts/promote_perp_live.py</code> to check readiness for live.</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:13px;">Perp stats unavailable: {e}</div>',
            unsafe_allow_html=True,
        )

    # ── Recent perp trades ─────────────────────────────────────────────────────
    _sec("RECENT PERP TRADES")
    all_trades = get_recent_trades(limit=100, paper=PAPER_TRADING) or []
    perp_trades = [t for t in all_trades if 'perp' in t.get('strategy', '').lower()]
    if not perp_trades:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:12px 0;">No perp trades yet.</div>',
            unsafe_allow_html=True,
        )
    else:
        html = ''
        for t in perp_trades[:20]:
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

    # ── Funding rate context ───────────────────────────────────────────────────
    _sec("FUNDING CONTEXT",
         "Most recent funding rate data from the scan feed. "
         "Funding > 0.05%/8h = market overheated (Bardock blocks entry).")
    try:
        feed = get_scan_feed(limit=100) or []
        funding_msg = None
        for s in feed:
            msg = str(s.get('message', '') or s.get('notes', '') or '')
            if 'funding' in msg.lower():
                funding_msg = msg
                break
        if funding_msg:
            st.markdown(
                f'<div class="card-sm" style="font-family:\'JetBrains Mono\',monospace;'
                f'font-size:12px;color:{TEXT2};">{funding_msg[:300]}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{TEXT3};font-size:14px;padding:8px 0;">'
                f'No funding data in recent scan feed.</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        st.markdown(
            f'<div style="color:{TEXT3};font-size:14px;padding:8px 0;">—</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    comp_status_bar()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊  OVERVIEW", "₿  CRYPTO SPOT", "⚡  PERP", "🎯  PREDICTIONS", "🧠  INTELLIGENCE", "⚙️  SYSTEM"
    ])

    with tab1:
        comp_hero()
        comp_metrics()
        col_l, col_r = st.columns(2)
        with col_l:
            comp_positions()
        with col_r:
            comp_markets()

    with tab2:
        comp_crypto_tab()

    with tab3:
        comp_perp_tab()

    with tab4:
        if LANE3_ENABLED:
            expander_lane3()
        else:
            st.markdown(
                f'<div class="card" style="text-align:center;padding:40px;">'
                f'<div style="font-size:32px;margin-bottom:16px;">🎯</div>'
                f'<div style="font-size:20px;font-weight:800;color:{TEXT};margin-bottom:12px;">'
                f'Prediction Markets — Lane 3</div>'
                f'<div style="font-size:14px;color:{TEXT2};margin-bottom:20px;">'
                f'Status: <span style="color:{RED};font-weight:700;">DISABLED</span>'
                f' (LANE3_ENABLED=false in .env)</div>'
                f'<div style="font-size:13px;color:{TEXT3};font-family:\'JetBrains Mono\',monospace;">'
                f'To activate: set LANE3_ENABLED=true, POLYMARKET_ENABLED=true</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with tab5:
        comp_edge()
        comp_trade_quality()
        comp_risk()

    with tab6:
        comp_trades_signals()
        comp_chat()
        st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)
        expander_debates()
        expander_notifications()
        expander_intelligence()
        expander_controls()


main()
