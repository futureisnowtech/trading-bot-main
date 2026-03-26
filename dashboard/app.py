"""
dashboard/app.py — The King's War Room
Single unified view: THE KING (with Capacity, Debates, Stats, Milestones below)
Run: streamlit run dashboard/app.py → http://localhost:8501
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import json
import urllib.request
from datetime import datetime
import pytz
import random
import base64
import streamlit.components.v1 as components
import plotly.graph_objects as go

from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    CRYPTO_PAIRS, COINBASE_TAKER_FEE_PCT, ANTHROPIC_API_KEY,
    EQUITY_ENABLED, CRYPTO_ENABLED, FUTURES_ENABLED, CLAUDE_MODEL,
    DEBATE_MAX_TOKENS, EXIT_REVIEW_MAX_TOKENS, MODERATOR_MAX_TOKENS,
    MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
    MAX_DAILY_LOSS_PCT, MAX_DAILY_FEE_DRAG_PCT, MAX_STRATEGY_LOSS_STREAK,
    CRYPTO_SCAN_INTERVAL_SECONDS, EQUITY_SCAN_INTERVAL_SECONDS,
    CRYPTO_MIN_ADX, AUTO_TUNE_FULL_DEBATE_THRESHOLD,
    EQUITY_POSITION_SIZE_USD, CRYPTO_POSITION_SIZE_USD,
    FULL_DEBATE_AGENTS, QUICK_DEBATE_AGENTS, FULL_DEBATE_MIN_AGREEMENT,
    MAX_RISK_PER_TRADE_PCT, MAX_DEPLOYED_PCT,
    CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    MAX_POSITIONS_CRYPTO, MAX_POSITIONS_EQUITY,
    CRYPTO_RSI_OVERSOLD, CRYPTO_RSI_OVERBOUGHT,
    KALMAN_ENTRY_DEV_PCT, AVWAP_ENTRY_DEV_PCT,
    SQUEEZE_MIN_BARS, RV_EXPANSION_THRESHOLD,
    OU_HALFLIFE_MIN_MINUTES, OU_HALFLIFE_MAX_MINUTES,
    KYLE_LAMBDA_LOW_PCT, ATR_FEE_FLOOR_PCT,
    ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER,
)
from logging_db.trade_logger import (
    get_todays_trades, get_todays_signals, get_todays_pnl, get_todays_fees, get_scan_feed,
    get_daily_trade_count, get_all_time_stats, get_recent_debates,
    get_monthly_api_cost, get_win_rate, get_recent_trades, get_recent_events,
    get_recent_notifications, get_today_stats, get_kelly_stats,
)
from risk.risk_manager import get_risk_manager
from data.market_data import is_market_open, is_in_no_trade_window

# ─── LeBron quotes (real, documented quotes) ─────────────────────────────────
LEBRON_QUOTES = [
    ("Strive for greatness.", "morning"),
    ("Nothing is given. Everything is earned.", "morning"),
    ("I promise you I will do everything in my power.", "morning"),
    ("The best come from somewhere. Remember yours.", "morning"),
    ("I like criticism. It makes you strong.", "loss"),
    ("Every day is a new opportunity to improve.", "morning"),
    ("You can't be afraid to fail. That's how you get better.", "loss"),
    ("I treat every single game like it's my last.", "morning"),
    ("The thing about basketball is the ball is round and the rim is round.", "patience"),
    ("Ask me to play. I'll play. Ask me to shoot. I'll shoot.", "morning"),
    ("Somewhere up in the clouds, there's a version of me that got comfortable.", "morning"),
    ("I have short goals — to get better every day.", "morning"),
]

LEBRON_MESSAGES = {
    'startup': "We're in the lab. Let's get to work. 👑",
    'first_trade': "Stay focused. Every possession matters. 🏀",
    'win': "That's preparation meeting opportunity. ⚡",
    'win_big': "THAT'S WHAT THE WORK LOOKS LIKE. 🏆🏆🏆",
    'loss': "We cut that. Losses are tuition. On to the next. 💪",
    'halt': "Not today. Live to play tomorrow. The best know when to sit down. 🙏",
    'goal': "We came, we worked, we're done. See you tomorrow. 🎯",
    'patience': "Sometimes the best move is no move. Stay patient. 🧘",
    'new_high': "THIS IS WHAT THE WORK LOOKS LIKE. NEW HIGH WATERMARK. 👑🔥",
    'paper': "Paper training. Every rep counts. The real game is coming. 📄",
}

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="👑 The King's War Room",
    page_icon='👑',
    layout='wide',
    initial_sidebar_state='collapsed'
)

# ─── CSS for all 4 themes ────────────────────────────────────────────────────
THEME_CSS = {
    'king': """
        .main { background: #000 !important; }
        .stApp { background: #000 !important; }
        .king-header { background: linear-gradient(135deg, #1D428A 0%, #000 50%, #1D428A 100%);
            border-bottom: 3px solid #FDB927; padding: 20px; margin: -20px -20px 20px -20px; }
        .scoreboard { font-size: 72px !important; font-weight: 900; color: #FDB927;
            text-align: center; text-shadow: 0 0 30px #FDB927; letter-spacing: 4px;
            font-family: 'Impact', sans-serif; }
        .scoreboard-neg { color: #ff4444; text-shadow: 0 0 30px #ff4444; }
        .metric-card { background: #1D428A; border: 2px solid #FDB927;
            border-radius: 8px; padding: 16px; margin: 6px 0; text-align: center; }
        .metric-val { font-size: 28px; font-weight: 900; color: #FDB927; }
        .metric-lbl { font-size: 11px; color: #aaa; text-transform: uppercase; letter-spacing: 2px; }
        .signal-buy { color: #FDB927; font-weight: 900; }
        .signal-sell { color: #ff4444; font-weight: 900; }
        .signal-hold { color: #666; }
        .quote-box { background: #111; border-left: 4px solid #FDB927;
            padding: 12px 16px; margin: 12px 0; font-style: italic;
            color: #FDB927; font-size: 16px; }
        .win-flash { animation: winflash 0.5s ease-in-out 3; }
        @keyframes winflash { 0%,100% { opacity:1; } 50% { opacity:0.3; } }
        .chat-user { background: #1D428A; color:#fff; padding:10px 14px;
            border-radius:12px 12px 4px 12px; margin:6px 0; max-width:80%; margin-left:auto; }
        .chat-bot { background: #111; color:#ddd; padding:10px 14px;
            border-radius:12px 12px 12px 4px; margin:6px 0; max-width:90%;
            border-left:3px solid #FDB927; }
        .halt-banner { background:#ff2222; color:#fff; padding:14px;
            border-radius:6px; text-align:center; font-size:18px; font-weight:900;
            border:2px solid #FDB927; }
        .paper-banner { background:#1D428A; color:#FDB927; padding:8px;
            border-radius:6px; text-align:center; font-weight:900; font-size:14px; }
        h1,h2,h3,h4 { color: #FDB927 !important; }
        .stButton>button { background: #1D428A; color: #FDB927; border: 1px solid #FDB927;
            font-weight: 700; }
        .stButton>button:hover { background: #FDB927; color: #000; }
        div[data-testid="stMetricValue"] { color: #FDB927 !important; font-weight: 900 !important; }
    """,
    'saiyan': """
        .main { background: #000 !important; }
        .stApp { background: #000 !important; }
        .saiyan-border { border: 2px solid #00ffff; box-shadow: 0 0 15px #00ffff44;
            border-radius: 6px; padding: 10px; margin: 6px 0; }
        .power-level { font-size: 42px; font-weight: 900; color: #FFD700;
            text-shadow: 0 0 20px #FFD700, 0 0 40px #ff8c00;
            font-family: monospace; text-align: center; }
        .ki-label { color: #00ffff; font-size: 11px; text-transform: uppercase;
            letter-spacing: 3px; }
        .ki-val { color: #FFD700; font-size: 24px; font-weight: 900; }
        .saiyan-header { background: linear-gradient(180deg, #000 0%, #0a0a2e 100%);
            border-bottom: 2px solid #00ffff; padding: 16px; margin: -20px -20px 20px -20px; }
        .over9k { color: #FFD700; font-size: 22px; font-weight: 900;
            text-shadow: 0 0 20px #FFD700; animation: pulse9k 1s infinite; }
        @keyframes pulse9k { 0%,100% { opacity:1; text-shadow: 0 0 20px #FFD700; }
            50% { opacity:0.7; text-shadow: 0 0 40px #ff8c00; } }
        .chat-user { background: #0a0a2e; color:#00ffff; padding:10px 14px;
            border-radius:12px 12px 4px 12px; margin:6px 0; max-width:80%; margin-left:auto;
            border: 1px solid #00ffff; }
        .chat-bot { background: #0a0a00; color:#FFD700; padding:10px 14px;
            border-radius:12px 12px 12px 4px; margin:6px 0; max-width:90%;
            border-left:3px solid #FFD700; }
        h1,h2,h3,h4 { color: #00ffff !important; }
        .stButton>button { background: #0a0a2e; color: #00ffff;
            border: 1px solid #00ffff; font-weight: 700; font-family: monospace; }
        div[data-testid="stMetricValue"] { color: #FFD700 !important; font-weight: 900 !important; }
    """,
    'filmroom': """
        .main { background: #1a1a1a !important; }
        .stApp { background: #1a1a1a !important; }
        .chalk-header { background: #222; border-bottom: 2px solid #ff8c00;
            padding: 16px; margin: -20px -20px 20px -20px; }
        .chalk-text { color: #f5f5dc; font-family: 'Courier New', monospace; }
        .chalk-highlight { color: #ff8c00; font-weight: bold; }
        .reasoning-box { background: #111; border: 1px solid #555; border-radius: 4px;
            padding: 12px; font-family: monospace; font-size: 12px; color: #ccc;
            white-space: pre-wrap; margin: 8px 0; }
        .agent-card { background: #222; border-left: 3px solid #ff8c00;
            padding: 10px; margin: 6px 0; border-radius: 0 6px 6px 0; }
        .buy-card { border-left-color: #44ff88; }
        .sell-card { border-left-color: #ff4444; }
        .hold-card { border-left-color: #888; }
        .chat-user { background: #333; color:#f5f5dc; padding:10px 14px;
            border-radius:4px; margin:6px 0; max-width:80%; margin-left:auto;
            font-family: monospace; }
        .chat-bot { background: #1a1a1a; color:#ccc; padding:10px 14px;
            border-radius:4px; margin:6px 0; max-width:90%;
            border-left:3px solid #ff8c00; font-family: monospace; }
        h1,h2,h3,h4 { color: #ff8c00 !important; font-family: 'Courier New' !important; }
        .stButton>button { background: #333; color: #ff8c00;
            border: 1px solid #ff8c00; font-family: monospace; }
        div[data-testid="stMetricValue"] { color: #f5f5dc !important; }
    """,
    'ring': """
        .main { background: #0a0800 !important; }
        .stApp { background: #0a0800 !important; }
        .trophy-header { background: linear-gradient(135deg, #0a0800 0%, #1a1200 50%, #0a0800 100%);
            border-bottom: 3px solid #FFD700; padding: 20px;
            margin: -20px -20px 20px -20px; }
        .trophy { font-size: 48px; text-align: center; filter: drop-shadow(0 0 12px #FFD700); }
        .ring-stat { background: #1a1200; border: 2px solid #FFD700;
            border-radius: 50%; width: 120px; height: 120px;
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; text-align: center; margin: auto; }
        .ring-val { font-size: 28px; font-weight: 900; color: #FFD700; }
        .ring-lbl { font-size: 10px; color: #888; text-transform: uppercase;
            letter-spacing: 1px; }
        .milestone-banner { background: linear-gradient(90deg, #0a0800, #FFD700, #0a0800);
            text-align: center; padding: 8px; color: #000; font-weight: 900;
            font-size: 14px; letter-spacing: 2px; }
        .chat-user { background: #1a1200; color:#FFD700; padding:10px 14px;
            border-radius:12px 12px 4px 12px; margin:6px 0; max-width:80%; margin-left:auto;
            border: 1px solid #FFD700; }
        .chat-bot { background: #0a0800; color:#ccc; padding:10px 14px;
            border-radius:12px 12px 12px 4px; margin:6px 0; max-width:90%;
            border-left:3px solid #FFD700; }
        h1,h2,h3,h4 { color: #FFD700 !important; }
        .stButton>button { background: #1a1200; color: #FFD700;
            border: 1px solid #FFD700; font-weight: 700; }
        div[data-testid="stMetricValue"] { color: #FFD700 !important; font-weight: 900 !important; }
    """
}


def et_now() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%b %-d, %-I:%M:%S %p ET')


def fmt_ts(ts: str, show_date: bool = True, date_only: bool = False, show_seconds: bool = False) -> str:
    """Parse an ISO timestamp → readable ET string.
    Examples: 'Mar 22, 2:30 PM'  |  '2:30 PM'  |  'Mar 22'
    """
    if not ts:
        return ''
    try:
        dt = datetime.fromisoformat(ts)
        tz_et = pytz.timezone(MARKET_TIMEZONE)
        dt = dt.astimezone(tz_et) if dt.tzinfo else tz_et.localize(dt)
        if date_only:
            return dt.strftime('%b %-d')
        time_fmt = '%-I:%M:%S %p' if show_seconds else '%-I:%M %p'
        return dt.strftime(f'%b %-d, {time_fmt}') if show_date else dt.strftime(time_fmt)
    except Exception:
        return ts[5:16] if len(ts) >= 16 else ts


def get_quote_for_hour() -> tuple:
    hour = datetime.now(pytz.timezone(MARKET_TIMEZONE)).hour
    block = hour // (24 // 5)  # 5 rotation blocks per day
    return LEBRON_QUOTES[block % len(LEBRON_QUOTES)]


def call_claude_chat(messages: list, system_ctx: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "⚠️ Set ANTHROPIC_API_KEY in .env to enable me here."
    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL,
            "max_tokens": 1000,
            "system": system_ctx,
            "messages": messages[-10:]
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['content'][0]['text']
    except Exception as e:
        return f"❌ Error: {e}"


def build_chat_context() -> str:
    rm = get_risk_manager()
    pos = rm.get_all_positions()
    trades = get_todays_trades(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)
    pnl = get_todays_pnl(paper=PAPER_TRADING)
    fees = get_todays_fees(paper=PAPER_TRADING)
    monthly_cost = get_monthly_api_cost()
    win_rate = get_win_rate(lookback_days=14, paper=PAPER_TRADING)
    risk = rm.status_report()
    signals = get_todays_signals()
    debates = get_recent_debates(limit=5)
    recent_trades = get_recent_trades(limit=20, paper=PAPER_TRADING)
    events = get_recent_events(limit=30)
    eq_t = get_daily_trade_count('equity_momentum', PAPER_TRADING)
    cr_t = get_daily_trade_count('crypto_macd_consensus', PAPER_TRADING)

    # Positions
    eq_pos = pos.get('equity', {})
    cr_pos = pos.get('crypto', {})
    pos_lines = ''
    for sym, p in eq_pos.items():
        pos_lines += f"  EQUITY {sym}: qty={p['qty']} entry=${p['entry']:.2f} stop=${p['stop']:.2f} target=${p['target']:.2f}\n"
    for sym, p in cr_pos.items():
        pos_lines += f"  CRYPTO {sym}: qty={p['qty']:.6f} entry=${p['entry']:,.4f} stop=${p['stop']:,.4f} target=${p['target']:,.4f}\n"
    if not pos_lines:
        pos_lines = '  None\n'

    # Today's trades
    trade_lines = '\n'.join(
        f"  {fmt_ts(t.get('ts',''))} | {t.get('action','')} {t.get('symbol','')} "
        f"qty={t.get('qty',0):.6f} @ ${t.get('price',0):,.4f} | P&L=${t.get('pnl_usd',0):+.2f} | "
        f"strategy={t.get('strategy','')} | notes={t.get('notes','')}"
        for t in trades
    ) or '  None yet today'

    # Recent signals
    sig_lines = '\n'.join(
        f"  {fmt_ts(s.get('ts',''))} | {s.get('signal','')} {s.get('symbol','')} "
        f"conf={s.get('confidence',0):.0%} acted={bool(s.get('acted_on',0))} | {s.get('reason','')[:120]}"
        for s in signals[:15]
    ) or '  None yet'

    # Recent debates with full reasoning
    debate_lines = ''
    for d in debates:
        debate_lines += (
            f"  [{fmt_ts(d.get('ts',''))}] {d.get('symbol','?')} → {d.get('final_signal','?')} "
            f"({d.get('buy_votes',0)}B/{d.get('hold_votes',0)}H/{d.get('sell_votes',0)}S | "
            f"conf={d.get('confidence',0):.0%} | regime={d.get('regime','')})\n"
            f"    Reasoning: {d.get('reasoning','')[:200]}\n"
            f"    Bull: {d.get('bull_case','')[:120]}\n"
            f"    Bear: {d.get('bear_case','')[:120]}\n"
            f"    Key risk: {d.get('key_risk','')[:100]}\n"
        )
    if not debate_lines:
        debate_lines = '  None yet\n'

    # System events — errors and warnings first
    errors = [e for e in events if e.get('level') in ('ERROR', 'WARNING')]
    info_events = [e for e in events if e.get('level') == 'INFO']
    event_lines = ''
    for e in errors[:8]:
        event_lines += f"  [{e.get('level','')}] {fmt_ts(e.get('ts',''))} [{e.get('source','')}] {e.get('message','')}\n"
    for e in info_events[:5]:
        event_lines += f"  [INFO] {fmt_ts(e.get('ts',''))} [{e.get('source','')}] {e.get('message','')}\n"
    if not event_lines:
        event_lines = '  No recent events\n'

    # All-time recent trade history for pattern analysis
    history_lines = '\n'.join(
        f"  {fmt_ts(t.get('ts',''))} {t.get('action','')} {t.get('symbol','')} "
        f"P&L=${t.get('pnl_usd',0):+.2f} strategy={t.get('strategy','')}"
        for t in recent_trades
    ) or '  No trade history'

    from config import (MAX_RISK_PER_TRADE_PCT, MAX_DAILY_LOSS_PCT, MAX_POSITIONS_CRYPTO,
                        MAX_POSITIONS_EQUITY, MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
                        CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
                        FULL_DEBATE_AGENTS, FULL_DEBATE_MIN_AGREEMENT,
                        EQUITY_ENABLED, FUTURES_ENABLED, PERP_ENABLED)
    _n_agents = len(FULL_DEBATE_AGENTS)
    _min_agree = max(2, int(FULL_DEBATE_MIN_AGREEMENT * _n_agents))
    _pairs_str = ', '.join(p.replace('-USDC','').replace('-USD','') for p in (CRYPTO_PAIRS if isinstance(CRYPTO_PAIRS, list) else str(CRYPTO_PAIRS).split(',')))

    return f"""You are Claude, the AI brain and co-pilot of this autonomous crypto/equity trading system.
You have FULL real-time access to the system's database — every trade, signal, debate, position, error, and cost.
Be direct. Be honest. Say what's actually wrong, not what's polite. Protect this account.
When asked for strategy suggestions: be specific — name signals, thresholds, and why. Reference the deep research (OU z-score, AVWAP, Kalman, squeeze, microstructure) not vague advice.

═══ LIVE SYSTEM STATE ({et_now()}) ═══
Mode: {'📄 PAPER TRADING' if PAPER_TRADING else '💰 LIVE TRADING'}
Account size: ${ACCOUNT_SIZE:,.0f}
Today P&L: ${pnl:+.2f} ({pnl/max(ACCOUNT_SIZE,1)*100:+.2f}% of account)
Today fees: ${fees:.2f} (limit: ${ACCOUNT_SIZE*0.10:.0f}/day = 10% of account)
System halted: {risk.get('halted', False)} {f"— REASON: {risk.get('halt_reason')}" if risk.get('halted') else ''}
Deployed capital: ${risk.get('deployed_usd', 0):.2f}
Equity trades today: {eq_t}/{MAX_TRADES_PER_DAY_EQUITY} (PDT limit)
Crypto trades today: {cr_t}/{MAX_TRADES_PER_DAY_CRYPTO}
Daily loss used: {abs(pnl)/max(ACCOUNT_SIZE,1)*100:.2f}% / {MAX_DAILY_LOSS_PCT*100:.0f}% halt threshold

═══ OPEN POSITIONS ═══
{pos_lines}
═══ TODAY'S TRADES ═══
{trade_lines}

═══ TODAY'S SIGNALS (last 15) ═══
{sig_lines}

═══ RECENT AI DEBATES (last 5) ═══
{debate_lines}
═══ SYSTEM EVENTS (recent errors first) ═══
{event_lines}
═══ ALL-TIME STATS ═══
Total closed trades: {stats.get('total', 0)}
Wins: {stats.get('wins', 0)} | Losses: {stats.get('losses', 0)}
Overall win rate: {stats.get('win_rate', 0):.1%}
14-day win rate: {win_rate:.1%}
Total P&L all time: ${stats.get('total_pnl', 0):+.2f}
Best trade: ${stats.get('best_trade', 0):+.2f} | Worst: ${stats.get('worst_trade', 0):+.2f}
Monthly Claude API cost: ${monthly_cost:.4f}

═══ RECENT TRADE HISTORY (last 20) ═══
{history_lines}

═══ CURRENT RISK RULES ═══
- {MAX_RISK_PER_TRADE_PCT*100:.0f}% max risk per trade (${ACCOUNT_SIZE*MAX_RISK_PER_TRADE_PCT:.0f} max loss)
- {MAX_DAILY_LOSS_PCT*100:.0f}% daily loss halt (${ACCOUNT_SIZE*MAX_DAILY_LOSS_PCT:.0f})
- {MAX_TRADES_PER_DAY_EQUITY} equity trades/day max (PDT) | {MAX_TRADES_PER_DAY_CRYPTO} crypto max
- {MAX_POSITIONS_CRYPTO} max crypto positions | {MAX_POSITIONS_EQUITY} equity
- Crypto stop: {CRYPTO_STOP_LOSS_PCT*100:.1f}% | target: {CRYPTO_TAKE_PROFIT_PCT*100:.1f}% | R:R {CRYPTO_TAKE_PROFIT_PCT/CRYPTO_STOP_LOSS_PCT:.1f}:1
- No equity entries 9:30–10:00 ET | hard block new crypto 2-5am ET
- Stop losses sacred — never widened after entry
- Never chase (skip if price moved >3% since signal)
- Never average down — one position per symbol, ever

═══ SIGNAL ARCHITECTURE (v4.0) ═══
Entry gates (7 signals, any 1 fires debate):
  1. 3-variant MACD consensus (2/3 agree) — 25 pts conviction
  2. Williams %R ≤ -80 extreme oversold — 20 pts
  3. Momentum + volume breakout (score>0.6 + vol≥1.5x) — 15 pts
  4. BB-Keltner squeeze fire ≥20 bars, direction>0 — 20 pts
  5. RV ratio ≥1.3 vol expansion — 15 pts
  6. Kalman deviation ≤ -1.0% (price below Kalman) — 10 pts
  7. AVWAP deviation ≤ -0.5% (reclaim setup) — 10 pts
  + OU z-score ≤ -1.5 — agents receive this for mean-reversion timing
  + OBI/TFI microstructure from live WebSocket — conviction + veto
RSI: EXIT signals only. NOT an entry gate.
Hurst: REMOVED (was broken, replaced by autocorr_ret + OU z-score)
Min conviction to debate: 30 pts normal / 70 pts 2-5am dead zone
Min agents agreeing to trade: 2 of {_n_agents} (was percentage, now explicit count)

═══ AI DEBATE PANEL ═══
Full debate agents: {', '.join(FULL_DEBATE_AGENTS)}
Moderator synthesizes → min {_min_agree}/{_n_agents} agents must say BUY
Exit review: Tudor Jones + Soros + Simons (any 1 EXIT → we exit)
Memory: LanceDB vector store (learns from every completed trade)

═══ ACTIVE MARKETS ═══
Crypto pairs ({len(CRYPTO_PAIRS) if isinstance(CRYPTO_PAIRS, list) else '?'}): {_pairs_str}
Equity: {'enabled' if EQUITY_ENABLED else 'DISABLED'}
Futures (MES): {'enabled' if FUTURES_ENABLED else 'DISABLED'}
Perp (Bybit): {'enabled' if PERP_ENABLED else 'DISABLED'}"""


def render_chat_column(theme: str = 'king'):
    user_icon, bot_icon = '👤', '👑'
    st.subheader("👑 Ask The King")
    st.caption("Ask anything about your trades, positions, strategy, costs, or market conditions.")

    if 'chat' not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat[-10:]:
        cls = 'chat-user' if msg['role'] == 'user' else 'chat-bot'
        icon = user_icon if msg['role'] == 'user' else bot_icon
        st.markdown(f'<div class="{cls}">{icon} {msg["content"]}</div>',
                    unsafe_allow_html=True)

    st.caption("Quick questions:")
    qc = st.columns(2)
    quick_qs = [
        "Why did it trade today?",
        "What's my win rate?",
        "Any risks right now?",
        "How are costs trending?",
        "💡 Suggest strategy improvements based on my trade history",
        "🔍 What signals are firing most and which are losing?",
        "⚡ Which hours of day are most profitable for me?",
        "🧠 What would you change about my current config?",
    ]
    quick_q = None
    for i, q in enumerate(quick_qs):
        if qc[i % 2].button(q, key=f'qq_{theme}_{i}', use_container_width=True):
            quick_q = q

    user_in = st.chat_input("Ask anything...", key=f'ci_{theme}')
    question = user_in or quick_q

    if question:
        st.session_state.chat.append({'role': 'user', 'content': question})
        with st.spinner("Thinking..."):
            ctx = build_chat_context()
            reply = call_claude_chat(
                [{'role': m['role'], 'content': m['content']} for m in st.session_state.chat],
                ctx
            )
        st.session_state.chat.append({'role': 'assistant', 'content': reply})
        st.rerun()

    if st.button("🗑️ Clear", key=f'clr_{theme}'):
        st.session_state.chat = []
        st.rerun()


# ─── Cost lab helpers ─────────────────────────────────────────────────────────

def _write_env_values(updates: dict) -> None:
    """Write key=value pairs into .env (used by cost lab and strategy lab sliders)."""
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


def _est_monthly_cost(debate_depth: str, debate_tokens: int,
                      exit_tokens: int, crypto_trades_day: int,
                      equity_trades_day: int, crypto_interval_min: int) -> float:
    """Estimate monthly Claude API cost from slider values."""
    trading_days = 22
    # Cost per 1k tokens: Sonnet input ~$0.003, output ~$0.015
    cost_per_1k_out = 0.015
    cost_per_1k_in  = 0.003
    avg_input_tokens = 800  # system prompt + context estimate

    agents = 8 if debate_depth == 'full' else 3
    cost_per_debate = (avg_input_tokens * agents / 1000 * cost_per_1k_in +
                       debate_tokens * agents / 1000 * cost_per_1k_out)
    cost_per_exit   = (avg_input_tokens * 3 / 1000 * cost_per_1k_in +
                       exit_tokens * 3 / 1000 * cost_per_1k_out)

    # Equity: up to equity_trades_day debates + exit reviews per trading day
    equity_monthly = equity_trades_day * (cost_per_debate + cost_per_exit) * trading_days

    # Crypto: scans per day × ~15% signal rate → debates; open positions get exit reviews
    crypto_scans_per_day = (24 * 60) / crypto_interval_min
    crypto_debates_day = crypto_scans_per_day * 0.15  # ~15% of scans produce a signal
    crypto_debates_day = min(crypto_debates_day, crypto_trades_day)
    crypto_monthly = crypto_debates_day * cost_per_debate * 30  # crypto runs 24/7
    crypto_exit_monthly = min(2, crypto_trades_day) * cost_per_exit * 30

    return equity_monthly + crypto_monthly + crypto_exit_monthly


def _cost_lab_ai_advice(goal: str, sliders: dict, current_monthly: float) -> str:
    """Ask Claude for specific slider recommendations toward a financial goal."""
    system = """You are the cost optimization advisor for an autonomous AI trading bot.
The bot uses Claude API for trade debates and exit reviews. You have full knowledge of its architecture.

Cost drivers:
- Debate depth: 3-agent majority vote (~$0.02/debate — all debates use same 3 agents)
- Debate max tokens: more tokens = better reasoning but higher cost
- Exit review tokens: extended thinking on every open position on every candle
- Crypto scan interval: how often crypto is checked (more frequent = more potential debates)
- Max trades per day: caps total debates per day

The user will describe a financial goal. Give SPECIFIC, DIRECT slider recommendations.
Format your response as:
1. One sentence on what's driving cost most right now
2. Exact slider values to set (be specific: "set debate tokens to 200, not 300")
3. Expected monthly cost after changes
4. Trade-off warning if the change reduces signal quality

Be concise. No fluff. Protect the account first, save money second."""

    msg = (f"Current settings: {json.dumps(sliders)}\n"
           f"Current estimated monthly cost: ${current_monthly:.2f}\n"
           f"My goal: {goal}")

    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL,
            "max_tokens": 400,
            "system": system,
            "messages": [{"role": "user", "content": msg}]
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))['content'][0]['text']
    except Exception as e:
        return f"❌ Error: {e}"


def render_cost_lab(theme: str = 'king'):
    """Interactive cost lab: sliders → live estimate → apply button + AI advisor."""
    accent = '#FDB927'

    st.subheader("💰 Cost Lab")
    st.caption("Tune your AI spend. Changes write to .env and take effect on next bot restart.")

    monthly_cost = get_monthly_api_cost()

    # ── Current state ─────────────────────────────────────────────────────────
    live_depth = 'full' if AUTO_TUNE_FULL_DEBATE_THRESHOLD == 0 else 'quick'
    live_agents = 8 if live_depth == 'full' else 3
    live_interval_min = CRYPTO_SCAN_INTERVAL_SECONDS // 60
    live_est = _est_monthly_cost(live_depth, DEBATE_MAX_TOKENS, EXIT_REVIEW_MAX_TOKENS,
                                 MAX_TRADES_PER_DAY_CRYPTO, MAX_TRADES_PER_DAY_EQUITY,
                                 live_interval_min)

    ca, cb, cc = st.columns(3)
    ca.metric("Actual spend this month", f"${monthly_cost:.4f}")
    cb.metric("Est. cost at current settings", f"${live_est:.2f}/mo")
    cc.metric("Est. annual at current settings", f"${live_est * 12:.2f}")

    st.info(
        f"**LIVE NOW:** {live_agents}-agent {live_depth} debates · "
        f"{DEBATE_MAX_TOKENS} debate tokens · {EXIT_REVIEW_MAX_TOKENS} exit tokens · "
        f"crypto every {live_interval_min}min · "
        f"≤{MAX_TRADES_PER_DAY_CRYPTO} crypto/day · ≤{MAX_TRADES_PER_DAY_EQUITY} equity/day"
    )
    st.divider()

    # ── Sliders (default to live config values) ───────────────────────────────
    st.markdown("**⚙️ What if I changed these?**")

    _live_depth_idx = 1 if live_depth == 'full' else 0
    debate_depth = st.radio(
        "Debate depth",
        ["quick (3 agents)", "full (3 agents)"],
        index=_live_depth_idx,
        key=f"cl_depth_{theme}",
        horizontal=True,
    )
    depth_val = 'quick' if debate_depth.startswith('quick') else 'full'

    col1, col2 = st.columns(2)
    with col1:
        debate_tokens = st.slider(
            "Debate max tokens", 100, 600, DEBATE_MAX_TOKENS, step=50,
            key=f"cl_dtok_{theme}",
            help="Tokens per agent per debate. Lower = cheaper, less reasoning depth."
        )
        crypto_trades = st.slider(
            "Max crypto trades/day", 1, 20, MAX_TRADES_PER_DAY_CRYPTO, step=1,
            key=f"cl_ctrades_{theme}",
            help="Caps daily crypto debates."
        )
    with col2:
        exit_tokens = st.slider(
            "Exit review max tokens", 200, 1200, EXIT_REVIEW_MAX_TOKENS, step=100,
            key=f"cl_etok_{theme}",
            help="Tokens for extended thinking exit reviews. Higher = smarter exits."
        )
        crypto_interval = st.slider(
            "Crypto scan interval (min)", 1, 15, max(1, live_interval_min), step=1,
            key=f"cl_cint_{theme}",
            help="How often crypto is scanned. More frequent = more potential debates."
        )

    equity_trades = st.slider(
        "Max equity trades/day", 1, 3, MAX_TRADES_PER_DAY_EQUITY, step=1,
        key=f"cl_etrades_{theme}",
        help="PDT rule caps this at 3 on a cash account."
    )

    # ── Live estimate ─────────────────────────────────────────────────────────
    est = _est_monthly_cost(depth_val, debate_tokens, exit_tokens,
                            crypto_trades, equity_trades, crypto_interval)

    settings_changed = (
        depth_val != live_depth or debate_tokens != DEBATE_MAX_TOKENS or
        exit_tokens != EXIT_REVIEW_MAX_TOKENS or crypto_trades != MAX_TRADES_PER_DAY_CRYPTO or
        equity_trades != MAX_TRADES_PER_DAY_EQUITY or crypto_interval != live_interval_min
    )

    if settings_changed:
        delta_vs_live = est - live_est
        col_a, col_b = st.columns(2)
        col_a.metric("Projected monthly cost", f"${est:.2f}",
                     delta=f"{delta_vs_live:+.2f} vs current settings")
        col_b.metric("Projected annual cost", f"${est * 12:.2f}")
    else:
        st.caption("✓ Sliders match live settings — move them to see projected impact.")

    # Cost breakdown
    agents = 8 if depth_val == 'full' else 3
    st.caption(f"Breakdown: {agents}-agent debates · {debate_tokens} debate tokens · "
               f"{exit_tokens} exit tokens · crypto every {crypto_interval}min · "
               f"≤{crypto_trades} crypto trades/day · ≤{equity_trades} equity trades/day")

    # ── Apply button ──────────────────────────────────────────────────────────
    st.divider()
    if st.button("🚀 Apply Changes to .env", key=f"cl_apply_{theme}", type="primary",
                 use_container_width=True):
        _write_env_values({
            'DEBATE_MAX_TOKENS':          str(debate_tokens),
            'EXIT_REVIEW_MAX_TOKENS':     str(exit_tokens),
            'MAX_TRADES_PER_DAY_CRYPTO':  str(crypto_trades),
            'MAX_TRADES_PER_DAY_EQUITY':  str(equity_trades),
            'CRYPTO_SCAN_INTERVAL_SECONDS': str(crypto_interval * 60),
        })
        # debate depth drives which agents list is used — handled by auto-tune threshold
        if depth_val == 'full':
            _write_env_values({'AUTO_TUNE_FULL_DEBATE_THRESHOLD': '0'})   # always full
        else:
            _write_env_values({'AUTO_TUNE_FULL_DEBATE_THRESHOLD': '999999'})  # always quick
        st.success("✅ Written to .env — restart main.py to apply.")

    # ── AI Advisor ────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("**🤖 AI Cost Advisor**")
    st.caption("Tell it a goal and it'll tell you exactly what to set the sliders to.")

    goal_input = st.text_input(
        "Your goal",
        placeholder='e.g. "Keep AI spend under $5/mo" or "Maximize signal quality on $500 account"',
        key=f"cl_goal_{theme}"
    )

    if st.button("Get Recommendation", key=f"cl_advise_{theme}", use_container_width=True):
        if not goal_input.strip():
            st.warning("Enter a goal first.")
        elif not ANTHROPIC_API_KEY:
            st.error("Set ANTHROPIC_API_KEY in .env first.")
        else:
            sliders_snapshot = {
                'debate_depth': depth_val,
                'debate_max_tokens': debate_tokens,
                'exit_review_max_tokens': exit_tokens,
                'crypto_trades_per_day': crypto_trades,
                'equity_trades_per_day': equity_trades,
                'crypto_scan_interval_min': crypto_interval,
                'estimated_monthly_cost_usd': round(est, 2),
            }
            with st.spinner("Thinking..."):
                advice = _cost_lab_ai_advice(goal_input, sliders_snapshot, est)
            st.session_state[f'cost_advice_{theme}'] = advice

    if f'cost_advice_{theme}' in st.session_state:
        st.info(st.session_state[f'cost_advice_{theme}'])


# ─── Signal card ──────────────────────────────────────────────────────────────

_SIG_STYLE = {
    #         icon    border-color  label-color  badge-bg
    'BUY':   ('🟢',  '#44ff88',    '#44ff88',   '#0d3320'),
    'SELL':  ('🔴',  '#ff4444',    '#ff4444',   '#330d0d'),
    'SHORT': ('🔻',  '#ff4444',    '#ff8888',   '#330d0d'),
    'HOLD':  ('⚪',  '#444',       '#888',      '#1a1a1a'),
}

_STRAT_LABELS = {
    'crypto_macd_consensus': 'MACD 3-variant',
    'crypto_ai_debate':      'AI Debate (3 agents)',
    'equity_momentum':       'Equity Momentum',
    'equity_ai_debate':      'AI Debate (equity)',
    'futures_scalper':       'MES Futures Scalper',
}


def _render_signal_card(s: dict) -> None:
    """
    Render one signal as an informative card.

    Shows: correct timestamp, correct icon for BUY/SELL/SHORT/HOLD,
    confidence badge, acted-on status, strategy name, and full reason
    broken into readable bullet points on | delimiters.
    """
    act   = s.get('signal', 'HOLD')
    icon, border, label_color, badge_bg = _SIG_STYLE.get(act, _SIG_STYLE['HOLD'])
    conf  = s.get('confidence', 0)
    price = s.get('price', 0)
    sym   = s.get('symbol', '')
    strat = _STRAT_LABELS.get(s.get('strategy', ''), s.get('strategy', ''))
    acted = bool(s.get('acted_on'))

    raw_ts = s.get('ts', '')
    sig_time = fmt_ts(raw_ts, show_date=False)

    # ── Confidence badge colour ───────────────────────────────────────────────
    if conf >= 0.75:
        conf_color = '#44ff88'
    elif conf >= 0.55:
        conf_color = '#FDB927'
    else:
        conf_color = '#ff8888'

    # ── Acted-on label ────────────────────────────────────────────────────────
    acted_html = (
        f'<span style="color:#44ff88; font-size:11px;">✓ TRADED</span>'
        if acted else
        f'<span style="color:#555; font-size:11px;">— skipped</span>'
    )

    # ── Header line ───────────────────────────────────────────────────────────
    header = (
        f'<div style="background:#111; border-left:3px solid {border}; '
        f'padding:8px 12px 4px 12px; margin:4px 0 0 0; border-radius:0 6px 0 0;">'
        f'<span style="color:#555; font-size:11px; font-family:monospace;">{sig_time} ET</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color:{label_color}; font-weight:900; font-size:14px;">'
        f'{icon} {act}</span>'
        f'&nbsp;&nbsp;'
        f'<span style="font-weight:700; font-size:13px; color:#ddd;">{sym}</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color:#aaa; font-size:12px;">${price:,.2f}</span>'
        f'&nbsp;&nbsp;'
        f'<span style="background:{badge_bg}; color:{conf_color}; font-size:11px; '
        f'font-weight:700; padding:2px 6px; border-radius:4px; border:1px solid {conf_color}33;">'
        f'{conf:.0%} conf</span>'
        f'&nbsp;&nbsp;{acted_html}'
        f'<span style="color:#444; font-size:10px; float:right; padding-top:2px;">{strat}</span>'
        f'</div>'
    )

    # ── Reason lines (split on | for multi-part reasons) ─────────────────────
    reason = s.get('reason', '').strip()
    if reason:
        parts = [p.strip() for p in reason.split('|') if p.strip()]
        bullets = ''.join(
            f'<div style="color:#aaa; font-size:11px; padding:1px 0;">'
            f'{"&nbsp;&nbsp;·&nbsp;" if i > 0 else "&nbsp;&nbsp;→&nbsp;"}{p}</div>'
            for i, p in enumerate(parts)
        )
        reason_html = (
            f'<div style="background:#0d0d0d; border-left:3px solid {border}; '
            f'padding:5px 12px 8px 12px; margin:0 0 4px 0; border-radius:0 0 6px 0;">'
            f'{bullets}'
            f'</div>'
        )
    else:
        reason_html = (
            f'<div style="background:#0d0d0d; border-left:3px solid {border}; '
            f'padding:4px 12px 8px 12px; margin:0 0 4px 0; border-radius:0 0 6px 0;">'
            f'<span style="color:#444; font-size:11px;">no reason recorded</span>'
            f'</div>'
        )

    st.markdown(header + reason_html, unsafe_allow_html=True)


# ─── Strategy Lab ─────────────────────────────────────────────────────────────

def _parse_backtest_prompt(text: str) -> dict:
    """
    Ask Claude to turn a plain-English backtest request into structured params.
    Returns dict with keys: strategy, symbol, period, interval.
    Falls back to safe defaults on any error.
    """
    system = """You are parsing a natural language backtest request for an algo trading bot.
Return ONLY valid JSON with exactly these fields, nothing else:
{
  "strategy": "crypto_macd_workhorse" | "crypto_macd_classic" | "crypto_macd_sniper" | "equity_momentum",
  "symbol": string (e.g. "BTC-USD", "ETH-USD", "AAPL"),
  "period": "1mo" | "3mo" | "6mo" | "1y",
  "interval": "5m" | "15m" | "30m" | "1h"
}
Rules:
- "sniper" → crypto_macd_sniper | "classic" → crypto_macd_classic | "workhorse" → crypto_macd_workhorse
- Stock tickers or "equity" → equity_momentum, default interval 15m
- "bitcoin"/"btc" → BTC-USD | "ethereum"/"eth" → ETH-USD
- Default: crypto_macd_workhorse, BTC-USD, 6mo, 5m"""

    defaults = {'strategy': 'crypto_macd_workhorse', 'symbol': 'BTC-USD',
                'period': '6mo', 'interval': '5m'}
    if not ANTHROPIC_API_KEY:
        return defaults
    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL, "max_tokens": 150,
            "system": system,
            "messages": [{"role": "user", "content": text}]
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages', data=payload,
            headers={'Content-Type': 'application/json',
                     'x-api-key': ANTHROPIC_API_KEY,
                     'anthropic-version': '2023-06-01'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode('utf-8'))['content'][0]['text']
            start, end = raw.find('{'), raw.rfind('}') + 1
            parsed = json.loads(raw[start:end])
            return {**defaults, **parsed}
    except Exception:
        return defaults


def _strategy_toggle_card(label: str, color: str, icon: str,
                           enabled: bool, env_key: str,
                           stats_lines: list, key: str) -> None:
    """Render one strategy card with a live toggle button."""
    status_color = '#4caf50' if enabled else '#888'
    status_text  = '● ACTIVE' if enabled else '○ PAUSED'
    btn_label    = '⏸ Pause' if enabled else '▶ Enable'
    new_val      = 'false' if enabled else 'true'

    st.markdown(
        f"<div style='background:{color}11; border:1px solid {color}55; "
        f"border-radius:8px; padding:10px 12px 6px 12px;'>"
        f"<div style='color:{color}; font-weight:900; font-size:13px;'>{icon} {label}</div>"
        f"<div style='color:{status_color}; font-size:11px; margin-top:2px;'>{status_text}</div>"
        f"<div style='color:#aaa; font-size:11px; margin-top:6px;'>"
        + '<br>'.join(stats_lines) +
        f"</div></div>",
        unsafe_allow_html=True,
    )
    if st.button(btn_label, key=key, use_container_width=True):
        _write_env_values({env_key: new_val})
        st.success(f"{'Paused' if enabled else 'Enabled'} — restart main.py to apply.")


def render_strategy_lab(theme: str = 'king'):
    st.subheader("🔬 Strategy Lab")

    # ── Strategy toggles ───────────────────────────────────────────────────────
    eq_t  = get_daily_trade_count('equity_momentum', PAPER_TRADING)
    cr_t  = get_daily_trade_count('crypto_macd_consensus', PAPER_TRADING)
    eq_wr = get_win_rate(strategy='equity_momentum', lookback_days=14, paper=PAPER_TRADING)
    cr_wr = get_win_rate(strategy='crypto_macd_consensus', lookback_days=14, paper=PAPER_TRADING)

    c1, c2, c3 = st.columns(3)
    with c1:
        _strategy_toggle_card(
            label='EQUITY MOMENTUM', color='#FDB927', icon='📈',
            enabled=EQUITY_ENABLED, env_key='EQUITY_ENABLED',
            stats_lines=[
                f"Today: {eq_t}/{MAX_TRADES_PER_DAY_EQUITY} trades",
                f"14d win rate: {eq_wr:.0%}",
                f"Size: ${EQUITY_POSITION_SIZE_USD:.0f} · Scan: {EQUITY_SCAN_INTERVAL_SECONDS}s",
            ],
            key=f"sl_eq_toggle_{theme}",
        )
    with c2:
        _strategy_toggle_card(
            label='CRYPTO MACD', color='#00bcd4', icon='₿',
            enabled=CRYPTO_ENABLED, env_key='CRYPTO_ENABLED',
            stats_lines=[
                f"Today: {cr_t}/{MAX_TRADES_PER_DAY_CRYPTO} trades",
                f"14d win rate: {cr_wr:.0%}",
                f"Pairs: {', '.join(CRYPTO_PAIRS)}",
            ],
            key=f"sl_cr_toggle_{theme}",
        )
    with c3:
        _strategy_toggle_card(
            label='MES FUTURES', color='#ff9800', icon='⚡',
            enabled=FUTURES_ENABLED, env_key='FUTURES_ENABLED',
            stats_lines=[
                'Contract: MES (micro E-mini S&P)',
                'Max trades/day: 4',
                'Opening-range breakout strategy',
            ],
            key=f"sl_ft_toggle_{theme}",
        )

    st.divider()

    # ── Natural language backtest prompt ───────────────────────────────────────
    st.markdown("**🧪 What do you want to test?**")
    st.caption('e.g. "sniper on ETH for 3 months" · "classic MACD on BTC 1 year" · "equity momentum on AAPL 6 months"')

    prompt = st.text_area(
        "Backtest prompt", label_visibility='collapsed',
        placeholder='Describe what you want to backtest in plain English...',
        key=f"bt_prompt_{theme}", height=80,
    )

    if st.button("▶ Run Backtest", key=f"bt_run_{theme}", type="primary",
                 use_container_width=True):
        if not prompt.strip():
            st.warning("Type what you want to test first.")
        else:
            with st.spinner("Parsing your request…"):
                params = _parse_backtest_prompt(prompt.strip())

            strat_key  = params['strategy']
            symbol     = params['symbol']
            period     = params['period']
            interval   = params['interval']
            strat_type = 'equity' if strat_key == 'equity_momentum' else 'crypto'

            st.caption(f"Running: **{strat_key}** · {symbol} · {period} · {interval} candles")

            with st.spinner(f"Backtesting {symbol} {interval} over {period}…"):
                try:
                    from backtesting.backtest_engine import (
                        run_crypto_backtest, run_equity_backtest, BACKTESTING_PY
                    )
                    if not BACKTESTING_PY:
                        st.error("pip install backtesting")
                        st.session_state[f'bt_result_{theme}'] = None
                    elif strat_type == 'crypto':
                        variant = strat_key.replace('crypto_macd_', '')
                        result  = run_crypto_backtest(
                            symbol=symbol, period=period, interval=interval,
                            cash=ACCOUNT_SIZE, variant=variant
                        )
                        st.session_state[f'bt_result_{theme}'] = ('crypto', variant, result)
                    else:
                        result = run_equity_backtest(
                            symbol=symbol, period=period, interval=interval, cash=ACCOUNT_SIZE
                        )
                        st.session_state[f'bt_result_{theme}'] = ('equity', None, result)
                except Exception as e:
                    st.error(f"Backtest error: {e}")
                    st.session_state[f'bt_result_{theme}'] = None

    # ── Results ────────────────────────────────────────────────────────────────
    bt_result = st.session_state.get(f'bt_result_{theme}')
    if bt_result:
        kind, variant, data = bt_result
        if isinstance(data, dict) and 'error' in data:
            st.error(f"Backtest failed: {data['error']}")
        elif kind == 'crypto' and isinstance(data, dict) and variant is not None:
            _show_backtest_result(data)
        elif kind == 'crypto' and isinstance(data, dict):
            for vname, vdata in data.items():
                st.markdown(f"**{vname}**")
                _show_backtest_result(vdata)
        else:
            _show_backtest_result(data)


def _show_backtest_result(r: dict) -> None:
    """Render a single backtest result dict as metrics + detail expander."""
    if 'error' in r:
        st.error(r['error'])
        return
    ret    = r.get('total_return_pct', 0)
    bh     = r.get('buy_hold_return_pct', 0)
    wr     = r.get('win_rate_pct', 0)
    trades = r.get('total_trades', 0)
    sharpe = r.get('sharpe_ratio', 0)
    dd     = r.get('max_drawdown_pct', 0)
    equity = r.get('final_equity', 0)

    m1, m2, m3, m4, m5 = st.columns(5)
    ret_delta = f"{ret - bh:+.1f}% vs B&H" if bh else None
    m1.metric("Return", f"{ret:+.1f}%", delta=ret_delta)
    m2.metric("Win Rate", f"{wr:.1f}%")
    m3.metric("# Trades", trades)
    m4.metric("Sharpe", f"{sharpe:.2f}")
    m5.metric("Max Drawdown", f"{dd:.1f}%")

    with st.expander("Full results"):
        st.json({
            'symbol':          r.get('symbol', ''),
            'strategy':        r.get('description', ''),
            'period':          f"{r.get('start','')} → {r.get('end','')}",
            'return_pct':      f"{ret:+.2f}%",
            'buy_hold_pct':    f"{bh:+.2f}%",
            'final_equity':    f"${equity:,.2f}",
            'win_rate_pct':    f"{wr:.1f}%",
            'total_trades':    trades,
            'sharpe_ratio':    f"{sharpe:.2f}",
            'max_drawdown_pct': f"{dd:.2f}%",
            'avg_trade_pct':   f"{r.get('avg_trade_return_pct',0):+.2f}%",
            'best_trade_pct':  f"{r.get('best_trade_pct',0):+.2f}%",
            'worst_trade_pct': f"{r.get('worst_trade_pct',0):+.2f}%",
            'profit_factor':   f"{r.get('profit_factor',0):.2f}",
            'exposure_pct':    f"{r.get('exposure_pct',0):.1f}%",
        })


# ─── Modular Panel System (King view) ────────────────────────────────────────

_PANEL_LABELS = {
    'scan_feed':     '🔍 Scan Feed',
    'positions':     '⚡ Positions',
    'trades':        "📋 Today's Trades",
    'notifications': '🔔 Notifications',
    'signals':       '🏀 Live Signals',
    'risk':          '🛡️ Risk Status',
    'cost_lab':      '💰 Cost Lab',
    'strategy_lab':  '🔬 Strategy Lab',
    'attribution':   '📊 Performance Attribution',
    'chat':          '🤖 Ask Claude',
}

_DEFAULT_LAYOUT: dict = {
    'left':  ['scan_feed', 'positions', 'trades'],
    'mid':   ['signals', 'risk', 'notifications'],
    'right': ['attribution', 'cost_lab', 'chat'],
}


def _init_king_layout():
    if 'king_layout' not in st.session_state:
        st.session_state.king_layout = {k: list(v) for k, v in _DEFAULT_LAYOUT.items()}


def _layout_editor():
    """Drag-free panel editor: ↑↓ to reorder, L/C/R buttons to move between columns."""
    layout = st.session_state.king_layout
    col_keys   = ['left', 'mid', 'right']
    col_labels = {'left': '◀ LEFT', 'mid': '● CENTER', 'right': '▶ RIGHT'}

    with st.expander("📐 Rearrange Panels", expanded=False):
        st.caption("Use ↑↓ to reorder within a column. Click L / C / R to move to that column.")
        ed_l, ed_m, ed_r = st.columns(3)
        editor_cols = {'left': ed_l, 'mid': ed_m, 'right': ed_r}
        action = None

        for ck, ec in editor_cols.items():
            with ec:
                st.markdown(f"**{col_labels[ck]}**")
                panels = layout[ck]
                if not panels:
                    st.caption("_(empty)_")
                for i, pid in enumerate(panels):
                    label = _PANEL_LABELS.get(pid, pid)
                    b_up, b_dn, name_col, b_l, b_c, b_r = st.columns([1, 1, 4, 1, 1, 1])

                    if b_up.button("↑", key=f"_lay_up_{ck}_{pid}", disabled=(i == 0)):
                        if action is None:
                            action = ('up', ck, i)
                    if b_dn.button("↓", key=f"_lay_dn_{ck}_{pid}",
                                   disabled=(i == len(panels) - 1)):
                        if action is None:
                            action = ('dn', ck, i)

                    name_col.markdown(
                        f'<span style="font-size:11px; color:#ccc; line-height:2.2;">'
                        f'{label}</span>',
                        unsafe_allow_html=True,
                    )

                    for dest_ck, btn_col, btn_lbl in [
                        ('left', b_l, 'L'), ('mid', b_c, 'C'), ('right', b_r, 'R')
                    ]:
                        if ck == dest_ck:
                            # Current column — show highlighted indicator, not a button
                            btn_col.markdown(
                                f'<div style="text-align:center; color:#FDB927; '
                                f'font-weight:900; font-size:13px; padding-top:4px;">●</div>',
                                unsafe_allow_html=True,
                            )
                        else:
                            if btn_col.button(btn_lbl, key=f"_lay_mv_{dest_ck}_{ck}_{pid}"):
                                if action is None:
                                    action = ('move', ck, i, dest_ck)

        st.divider()
        if st.button("↺ Reset to default layout", key="_lay_reset"):
            action = ('reset',)

        if action:
            if action[0] == 'up':
                _, ck, i = action
                p = layout[ck]
                p[i], p[i - 1] = p[i - 1], p[i]
            elif action[0] == 'dn':
                _, ck, i = action
                p = layout[ck]
                p[i], p[i + 1] = p[i + 1], p[i]
            elif action[0] == 'move':
                _, ck, i, dest = action
                pid = layout[ck].pop(i)
                layout[dest].append(pid)
            elif action[0] == 'reset':
                st.session_state.king_layout = {k: list(v) for k, v in _DEFAULT_LAYOUT.items()}
            st.rerun()


@st.fragment(run_every=5)
def _panel_scan_feed():
    entries = get_scan_feed(limit=40)
    last_ts = fmt_ts(entries[0].get('ts', ''), show_date=False, show_seconds=True) if entries else '—'
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">'
        f'<span style="font-size:16px; font-weight:900; color:#FDB927;">🔍 Scan Feed</span>'
        f'<span style="background:#ff2222; color:#fff; font-size:10px; font-weight:900; '
        f'padding:2px 7px; border-radius:4px; letter-spacing:2px; animation:pulse9k 1.2s infinite;">● LIVE</span>'
        f'<span style="color:#444; font-size:10px; margin-left:auto;">last: {last_ts}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not entries:
        st.info(f"Waiting for first scan cycle... (crypto every {CRYPTO_SCAN_INTERVAL_SECONDS}s · equity every {EQUITY_SCAN_INTERVAL_SECONDS}s)")
        return
    feed_html = ''
    for e in entries:
        msg = e.get('message', '')
        ts  = fmt_ts(e.get('ts', ''), show_date=False)
        if '⛔' in msg or 'block' in msg.lower() or '🚫' in msg:
            color, dot = '#555', '⊘'
        elif '→ BUY' in msg or '→ SHORT' in msg:
            color, dot = '#FDB927', '●'
        elif 'TRADED' in msg or 'EXITED' in msg or 'CLOSED' in msg:
            color, dot = '#44ff88', '✓'
        elif 'Analyzing' in msg or 'Scanning' in msg:
            color, dot = '#4488ff', '◎'
        elif 'SPY' in msg or 'breadth' in msg.lower():
            color, dot = '#aaa', '◈'
        elif 'regime' in msg.lower() or 'Regime' in msg:
            color, dot = '#9966ff', '▸'
        else:
            color, dot = '#666', '·'
        feed_html += (
            f'<div style="padding:3px 0; font-family:monospace; font-size:11px; '
            f'border-bottom:1px solid #111; display:flex; gap:8px; align-items:flex-start;">'
            f'<span style="color:#444; min-width:58px; flex-shrink:0; padding-top:1px;">{ts}</span>'
            f'<span style="color:{color}; line-height:1.4;">{dot} {msg}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="background:#050505; border:1px solid #1a1a1a; border-radius:6px; '
        f'padding:8px 10px; max-height:420px; overflow-y:auto;">{feed_html}</div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=5)
def _panel_positions(rm):
    st.subheader("⚡ Positions")
    pos      = rm.get_all_positions()
    eq_pos   = pos.get('equity', {})
    fut_pos  = pos.get('futures', {})
    cr_pos   = pos.get('crypto', {})
    perp_pos = pos.get('perp', {})

    has_any = eq_pos or fut_pos or cr_pos or perp_pos
    if not has_any:
        st.markdown(
            '<div style="color:#666; text-align:center; padding:20px;">'
            'No open positions<br>👑 Patience pays</div>',
            unsafe_allow_html=True,
        )
    else:
        # Equity & Futures first (King's domain)
        if eq_pos:
            st.caption("**EQUITY**")
            rows = [{'Symbol': s, 'Qty': p.get('qty',0), 'Entry': f"${p.get('entry',0):.2f}",
                     'Stop': f"${p.get('stop',0):.2f}", 'Target': f"${p.get('target',0):.2f}"}
                    for s, p in eq_pos.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if fut_pos:
            st.caption("**FUTURES (MES/ES)**")
            rows = [{'Contract': s, 'Qty': p.get('qty',0), 'Entry': f"${p.get('entry',0):.2f}",
                     'Stop': f"${p.get('stop',0):.2f}", 'Target': f"${p.get('target',0):.2f}"}
                    for s, p in fut_pos.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if cr_pos:
            st.caption("**CRYPTO SPOT**")
            rows = [{'Pair': s, 'Qty': f"{p.get('qty',0):.6f}",
                     'Entry': f"${p.get('entry',0):,.4f}",
                     'Stop': f"${p.get('stop',0):,.4f}",
                     'Target': f"${p.get('target',0):,.4f}"}
                    for s, p in cr_pos.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if perp_pos:
            st.caption("**PERP (Bybit)**")
            rows = [{'Symbol': s,
                     'Side': p.get('side','long').upper(),
                     'Qty': f"{p.get('qty',0):.4f}",
                     'Entry': f"${p.get('entry',0):,.4f}",
                     'Lev': f"{p.get('leverage',1)}×",
                     'Stop': f"${p.get('stop',0):,.4f}"}
                    for s, p in perp_pos.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


@st.fragment(run_every=10)
def _panel_trades():
    trades = get_todays_trades(paper=PAPER_TRADING)
    st.subheader("📋 Today's Trades")
    if trades:
        st.caption(f"{len(trades)} trade events today")
        rows = []
        for t in trades:
            action = t.get('action', '')
            pnl = t.get('pnl_usd', 0) or 0
            rows.append({
                'Time':     fmt_ts(t.get('ts', ''), show_date=False),
                'Symbol':   t.get('symbol', ''),
                'Action':   action,
                'Qty':      round(t.get('qty', 0), 6),
                'Price':    t.get('price', 0),
                'P&L':      round(pnl, 4),
                'Strategy': t.get('strategy', ''),
            })
        df_t = pd.DataFrame(rows)
        st.dataframe(df_t, use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet today. Waiting for the right setup. 👑")


@st.fragment(run_every=5)
def _panel_notifications():
    """Compact, plain-English activity feed — trades, halts, summaries only."""
    from datetime import timezone as _utc

    notifs = get_recent_notifications(limit=60)

    def _is_relevant(n):
        msg = n.get('message', '')
        lvl = n.get('level', 'INFO')
        if lvl in ('ERROR', 'WARNING'):   return True
        if 'HALT' in msg:                 return True
        if any(k in msg for k in ('CLOSED', '— BUY ', '— SELL ', 'Daily Summary', 'READY')):
            return True
        return False

    def _rel_time(ts_str):
        try:
            dt = datetime.fromisoformat(ts_str)
            now = datetime.now(_utc.utc)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_utc.utc)
            d = int((now - dt).total_seconds())
            if d < 60:    return 'just now'
            if d < 3600:  return f'{d//60}m ago'
            if d < 86400: return f'{d//3600}h ago'
            return f'{d//86400}d ago'
        except Exception:
            return ''

    def _humanize(msg, level):
        subject, _, body = msg.partition(' | ')
        subject = subject.strip().replace('PAPER — ', '').replace('LIVE — ', '')
        fields = {}
        for part in body.split(' | '):
            if ':' in part:
                k, _, v = part.partition(':')
                fields[k.strip()] = v.strip()

        if 'HALT' in subject or level == 'ERROR':
            reason = fields.get('Reason', 'daily loss limit reached').lower().rstrip('.')
            return '🚨', '#ff4444', f"Trading paused — {reason}"

        if 'CLOSED' in subject:
            # e.g. "CLOSED ETH-USDC WIN +$2.34"
            tokens = subject.split()
            symbol  = tokens[1] if len(tokens) > 1 else '?'
            result  = tokens[2] if len(tokens) > 2 else ''
            amount  = tokens[3] if len(tokens) > 3 else ''
            why     = fields.get('Reason', '').replace('_', ' ').lower()
            if 'WIN' in result:
                return '✅', '#44ff88', f"Closed {symbol} → {amount} ({why or 'target reached'})"
            else:
                return '🔴', '#ff6644', f"Closed {symbol} → {amount} ({why or 'stop hit'})"

        if subject.startswith('BUY ') or subject.startswith('SELL ') or subject.startswith('SHORT '):
            tokens  = subject.split()
            verb    = {'BUY': 'Bought', 'SELL': 'Sold', 'SHORT': 'Shorted'}.get(tokens[0], tokens[0])
            symbol  = tokens[1] if len(tokens) > 1 else '?'
            qty_field = fields.get('Qty', '')
            price = qty_field.split('@')[1].strip().split()[0] if '@' in qty_field else '?'
            stop  = fields.get('Stop', '')
            tgt   = fields.get('Target', '')
            tail  = f"  stop {stop} · target {tgt}" if stop else ''
            return '📈', '#FDB927', f"{verb} {symbol} @ {price}{tail}"

        if 'Daily Summary' in subject:
            result_part = subject.split('Daily Summary')[-1].strip()
            trades_info = fields.get('Trades', '')
            wr_info     = fields.get('Win Rate', '')
            return '📊', '#aaa', f"Wrap: {result_part}  {trades_info}  {wr_info} WR"

        if 'READY' in subject:
            return '🏆', '#FFD700', "Ready for live trading — all criteria passed!"

        # fallback
        clean = subject[:72]
        return 'ℹ️', '#555', clean

    filtered = [n for n in notifs if _is_relevant(n)][:6]

    st.markdown(
        '<div style="display:flex; align-items:center; justify-content:space-between; '
        'margin-bottom:5px;">'
        '<span style="font-weight:700; color:#FDB927; font-size:13px;">🔔 Recent Activity</span>'
        f'<span style="color:#333; font-size:9px; font-family:monospace;">'
        f'{len(notifs)} logged</span>'
        '</div>', unsafe_allow_html=True)

    if not filtered:
        st.markdown(
            '<div style="color:#444; font-size:11px; padding:6px 0; font-style:italic;">'
            'All quiet — no trades or alerts yet.</div>',
            unsafe_allow_html=True)
        return

    for n in filtered:
        icon, color, text = _humanize(n.get('message', ''), n.get('level', 'INFO'))
        rel = _rel_time(n.get('ts', ''))
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:7px; padding:4px 8px; '
            f'margin:2px 0; background:#0d0d0d; border-left:2px solid {color}; '
            f'border-radius:0 4px 4px 0;">'
            f'<span style="font-size:13px; flex-shrink:0;">{icon}</span>'
            f'<span style="color:#ccc; font-size:11px; flex:1; line-height:1.35;">{text}</span>'
            f'<span style="color:#333; font-size:9px; white-space:nowrap; '
            f'font-family:monospace; flex-shrink:0;">{rel}</span>'
            f'</div>',
            unsafe_allow_html=True)


@st.fragment(run_every=5)
def _panel_signals():
    sigs = get_todays_signals()
    buys  = [s for s in sigs if s.get('signal') == 'BUY']
    holds = [s for s in sigs if s.get('signal') == 'HOLD']
    sells = [s for s in sigs if s.get('signal') in ('SELL', 'SHORT')]
    acted = [s for s in sigs if s.get('acted_on')]

    # Header with scan status
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap;">'
        f'<span style="font-size:16px; font-weight:900; color:#FDB927;">🏀 Live Signals</span>'
        f'<span style="background:#111; border:1px solid #333; color:#44ff88; font-size:10px; '
        f'padding:2px 7px; border-radius:4px;">{len(buys)} BUY</span>'
        f'<span style="background:#111; border:1px solid #333; color:#ff4444; font-size:10px; '
        f'padding:2px 7px; border-radius:4px;">{len(sells)} SELL</span>'
        f'<span style="background:#111; border:1px solid #333; color:#888; font-size:10px; '
        f'padding:2px 7px; border-radius:4px;">{len(holds)} HOLD</span>'
        f'<span style="background:#1D428A; color:#FDB927; font-size:10px; '
        f'padding:2px 7px; border-radius:4px;">✓ {len(acted)} traded</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Scanning status bar — values pulled live from config (never stale)
    from config import (
        CRYPTO_SCAN_INTERVAL_SECONDS, EQUITY_SCAN_INTERVAL_SECONDS,
        CRYPTO_MIN_ADX, MEAN_REVERSION_RSI_ENTRY,
    )
    pairs_str = ' · '.join(CRYPTO_PAIRS[:6]) + (' +more' if len(CRYPTO_PAIRS) > 6 else '')
    _cr_int = f'{CRYPTO_SCAN_INTERVAL_SECONDS}s'
    _eq_int = f'{EQUITY_SCAN_INTERVAL_SECONDS}s'
    _gate = f'ADX>{CRYPTO_MIN_ADX:.0f} · MACD signal · vol spike'
    _ai   = '3-agent quick debate @ ≥30% conf'
    st.markdown(
        f'<div style="background:#050505; border:1px solid #1a1a1a; border-radius:4px; '
        f'padding:6px 10px; font-family:monospace; font-size:10px; color:#555; margin-bottom:8px;">'
        f'<span style="color:#4488ff;">◎ SCANNING:</span> {pairs_str} &nbsp;|&nbsp; '
        f'<span style="color:#4488ff;">INTERVAL:</span> {_cr_int} crypto · {_eq_int} equity &nbsp;|&nbsp; '
        f'<span style="color:#4488ff;">GATE:</span> {_gate} &nbsp;|&nbsp; '
        f'<span style="color:#9966ff;">AI:</span> {_ai}'
        f'</div>',
        unsafe_allow_html=True,
    )

    if sigs:
        for s in sigs[:12]:
            _render_signal_card(s)
    else:
        st.markdown(
            f'<div style="background:#050505; border:1px solid #1a1a1a; border-radius:6px; '
            f'padding:16px; text-align:center; color:#555; font-family:monospace;">'
            f'◎ Watching {len(CRYPTO_PAIRS)} crypto pairs + equity screener<br>'
            f'<span style="font-size:10px;">Signals fire when ADX>{CRYPTO_MIN_ADX:.0f} + MACD/vol signal + ≥30% confidence</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _panel_risk(pnl, rm):
    st.subheader("🛡️ System Health")

    from logging_db.trade_logger import get_todays_fees, get_strategy_consecutive_losses, get_monthly_api_cost
    import time as _time

    # ── Bot alive? ────────────────────────────────────────────────────────────
    secs_since_ping = _time.time() - rm._last_scan_ts if rm._last_scan_ts > 0 else None
    watchdog_ok = rm.watchdog_ok(max_gap_seconds=120)  # 2x scan interval
    if rm.is_halted:
        bot_status = '🔴 HALTED'
        bot_color  = '#ff4444'
        bot_detail = rm._halt_reason[:60] if rm._halt_reason else 'Unknown reason'
    elif secs_since_ping is None:
        bot_status = '🟡 STARTING'
        bot_color  = '#ffaa00'
        bot_detail = 'No scan completed yet'
    elif not watchdog_ok:
        bot_status = f'🔴 STALE ({int(secs_since_ping)}s ago)'
        bot_color  = '#ff4444'
        bot_detail = 'Last scan too long ago — bot may be frozen'
    else:
        bot_status = f'🟢 LIVE  ({int(secs_since_ping)}s ago)'
        bot_color  = '#44cc44'
        bot_detail = 'Scanning normally'

    st.markdown(
        f'<div style="background:#0a0a0a; border:1px solid {bot_color}44; border-radius:6px; '
        f'padding:8px 12px; margin-bottom:8px; font-family:monospace; font-size:11px;">'
        f'<span style="color:{bot_color}; font-weight:900;">{bot_status}</span>'
        f'<span style="color:#555; margin-left:10px;">{bot_detail}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Risk limit bars ───────────────────────────────────────────────────────
    all_time = get_all_time_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + all_time.get('total_pnl', 0)
    max_loss_usd = real_balance * MAX_DAILY_LOSS_PCT

    daily_loss_pct = abs(pnl) / real_balance * 100 if pnl < 0 else 0
    halt_pct       = MAX_DAILY_LOSS_PCT * 100
    st.progress(
        min(daily_loss_pct / halt_pct, 1.0),
        text=f"Daily loss: {daily_loss_pct:.2f}% / {halt_pct:.0f}% halt  (${abs(pnl):.2f} / ${max_loss_usd:.2f})"
    )

    eq_t = get_daily_trade_count('equity_momentum', PAPER_TRADING)
    st.progress(
        min(eq_t / max(MAX_TRADES_PER_DAY_EQUITY, 1), 1.0),
        text=f"Equity trades: {eq_t}/{MAX_TRADES_PER_DAY_EQUITY} (PDT limit)"
    )

    cr_t = get_daily_trade_count('crypto_macd_consensus', PAPER_TRADING)
    st.progress(
        min(cr_t / max(MAX_TRADES_PER_DAY_CRYPTO, 1), 1.0),
        text=f"Crypto trades: {cr_t}/{MAX_TRADES_PER_DAY_CRYPTO}"
    )

    # ── Fee drag ──────────────────────────────────────────────────────────────
    fees_today   = get_todays_fees(paper=PAPER_TRADING)
    fee_drag_pct = fees_today / real_balance * 100
    fee_limit_pct = MAX_DAILY_FEE_DRAG_PCT * 100
    fee_color = '#ff4444' if fee_drag_pct >= fee_limit_pct * 0.8 else '#888'
    st.progress(
        min(fee_drag_pct / fee_limit_pct, 1.0),
        text=f"Fee drag: {fee_drag_pct:.2f}% / {fee_limit_pct:.1f}% halt  (${fees_today:.2f} today)"
    )

    # ── Circuit breakers ──────────────────────────────────────────────────────
    try:
        macd_streak = get_strategy_consecutive_losses('crypto_macd_consensus', paper=PAPER_TRADING)
        mr_streak   = get_strategy_consecutive_losses('crypto_mean_reversion', paper=PAPER_TRADING)
        cb_rows = []
        for label, streak in [('MACD', macd_streak), ('MeanRev', mr_streak)]:
            pct = streak / MAX_STRATEGY_LOSS_STREAK
            bar_color = '#ff4444' if pct >= 0.75 else '#ffaa00' if pct >= 0.5 else '#333'
            cb_rows.append(
                f'<span style="color:#888;">{label}:</span> '
                f'<span style="color:{bar_color}; font-weight:bold;">{streak}/{MAX_STRATEGY_LOSS_STREAK} losses</span>'
            )
        st.markdown(
            f'<div style="background:#0a0a0a; border:1px solid #1a1a1a; border-radius:4px; '
            f'padding:6px 10px; font-family:monospace; font-size:10px; margin-top:4px;">'
            f'<span style="color:#FDB927;">⚡ Circuit breaker:</span> &nbsp;'
            + ' &nbsp;|&nbsp; '.join(cb_rows) +
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # ── API cost today ────────────────────────────────────────────────────────
    try:
        monthly_cost = get_monthly_api_cost()
        days_in_month = 30
        daily_est = monthly_cost / max(datetime.now(pytz.timezone(MARKET_TIMEZONE)).day, 1)
        st.markdown(
            f'<div style="font-family:monospace; font-size:10px; color:#555; '
            f'padding:4px 0; margin-top:2px;">'
            f'API cost: <span style="color:#9966ff;">${monthly_cost:.4f} this month</span>'
            f' · <span style="color:#777;">~${daily_est:.4f}/day avg</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # ── Broker connection ─────────────────────────────────────────────────────
    try:
        from execution.coinbase_broker import get_coinbase_broker
        cb = get_coinbase_broker()
        cb_status = '🟢 Connected' if cb.is_connected() else '🟡 Paper (not connected)'
    except Exception:
        cb_status = '⚪ Unknown'
    mode_label = '📄 PAPER' if PAPER_TRADING else '🔴 LIVE'
    st.markdown(
        f'<div style="font-family:monospace; font-size:10px; color:#555; padding:4px 0;">'
        f'{mode_label} &nbsp;|&nbsp; Coinbase: {cb_status}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _panel_cost_lab():
    render_cost_lab(theme='king')


def _panel_strategy_lab():
    with st.expander("🔬 Strategy Lab", expanded=False):
        render_strategy_lab(theme='king')


def _panel_attribution():
    st.subheader("📊 Performance Attribution")
    try:
        from logging_db.trade_logger import get_performance_attribution
        attr = get_performance_attribution(paper=PAPER_TRADING, lookback_days=30)
    except Exception:
        attr = {}
    if not attr:
        st.info("No closed trades yet to attribute.")
        return
    for strat, s in attr.items():
        wr = s['win_rate']
        pnl = s['total_pnl']
        wr_color = '#44ff88' if wr >= 0.55 else '#FDB927' if wr >= 0.45 else '#ff4444'
        pnl_color = '#44ff88' if pnl >= 0 else '#ff4444'
        label = {
            'equity_ai_debate': 'Equity AI', 'equity_momentum': 'Equity MACD',
            'crypto_ai_debate': 'Crypto AI', 'crypto_macd_consensus': 'Crypto MACD',
            'futures_scalper': 'Futures',
        }.get(strat, strat)
        st.markdown(
            f'<div style="background:#111; border-left:3px solid {wr_color}; '
            f'padding:8px 12px; margin:3px 0; border-radius:0 6px 6px 0; font-family:monospace;">'
            f'<span style="color:#ddd; font-weight:700;">{label}</span>'
            f'&nbsp;&nbsp;<span style="color:#555; font-size:11px;">{s["total"]} trades</span>'
            f'<span style="color:{wr_color}; font-weight:700; float:right;">{wr:.0%} WR</span>'
            f'<br>'
            f'<span style="color:{pnl_color}; font-size:13px;">P&L ${pnl:+.2f}</span>'
            f'&nbsp;&nbsp;<span style="color:#555; font-size:11px;">avg ${s["avg_pnl"]:+.2f}/trade</span>'
            f'&nbsp;&nbsp;<span style="color:#555; font-size:11px;">'
            f'{s["wins"]}W / {s["losses"]}L</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _panel_chat():
    render_chat_column('king')


# ─── VIEW RENDERERS ───────────────────────────────────────────────────────────

# ─── LOCAL ASSET HELPERS ──────────────────────────────────────────────────────

_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'BRON_DBZ_IMAGES')
_BB  = os.path.join(_ASSET_DIR, 'basketball')
_SW  = os.path.join(_ASSET_DIR, 'saiyan_warriors')
_DE  = os.path.join(_ASSET_DIR, 'dbz_energy')
_AN  = os.path.join(_ASSET_DIR, 'animations')
_GIF = os.path.join(_ASSET_DIR, 'gifs')


def _b64img(filepath: str, mime: str = '') -> str:
    """Read a local image/SVG/GIF → base64 data URI for embedding in HTML blocks."""
    if not filepath or not os.path.exists(filepath):
        return ''
    ext = os.path.splitext(filepath)[1].lower()
    if not mime:
        mime = {
            '.svg': 'image/svg+xml',
            '.gif': 'image/gif',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
        }.get(ext, 'image/png')
    try:
        with open(filepath, 'rb') as f:
            data = base64.b64encode(f.read()).decode('utf-8')
        return f'data:{mime};base64,{data}'
    except Exception:
        return ''


def _local_img(filepath: str, width: int = 80) -> None:
    """Render a local SVG/GIF/PNG via st.image (Streamlit handles both natively)."""
    if filepath and os.path.exists(filepath):
        try:
            st.image(filepath, width=width)
        except Exception:
            pass


def _local_anim(html_path: str, height: int = 200) -> None:
    """Render a local HTML CSS animation via streamlit component."""
    if html_path and os.path.exists(html_path):
        try:
            with open(html_path, 'r') as f:
                html_content = f.read()
            components.html(html_content, height=height, scrolling=False)
        except Exception:
            pass



_LEBRON_URLS = [
    "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png",
    "https://ak-static.cms.nba.com/wp-content/uploads/headshots/nba/latest/260x190/2544.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/LeBron_James_crop1.jpg/330px-LeBron_James_crop1.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/LeBron_James_-_51959723161.jpg/440px-LeBron_James_-_51959723161.jpg",
]

# 20-slot gallery — (url, caption). onerror hides broken images in browser.
_LEBRON_GALLERY = [
    ("https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png",                                                                                              "THE KING"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/LeBron_James_crop1.jpg/330px-LeBron_James_crop1.jpg",                                          "BRON"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/LeBron_James_-_51959723161.jpg/440px-LeBron_James_-_51959723161.jpg",                          "LAKERS"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/LeBron_James_%2815662939969%29_%28cropped%29.jpg/440px-LeBron_James_%2815662939969%29_%28cropped%29.jpg", "MVP"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/LeBron_James_%2820-11%29.jpg/440px-LeBron_James_%2820-11%29.jpg",                             "CHAMP"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/LeBron_James_-_Cleveland_Cavaliers.jpg/330px-LeBron_James_-_Cleveland_Cavaliers.jpg",         "CAVS"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/LeBron_James_-_2014_%28cropped%29.jpg/330px-LeBron_James_-_2014_%28cropped%29.jpg",           "2014"),
    ("https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/LeBron_James_-_2019_%2848074836221%29_%28cropped%29.jpg/330px-LeBron_James_-_2019_%2848074836221%29_%28cropped%29.jpg", "2019"),
    ("https://media.tenor.com/g9sXlTNE9v0AAAAC/lebron-james.gif",                                                                                              "KING GIF"),
    ("https://media.tenor.com/JVGF4GCOhisAAAAC/lebron-james-dunk.gif",                                                                                        "DUNK"),
    ("https://media.tenor.com/WFr39s9MFMYAAAAC/lebron.gif",                                                                                                    "BRON GIF"),
    ("https://media.tenor.com/6MqDpJ3mEqwAAAAC/lebron-james-celebration.gif",                                                                                  "W"),
    ("https://media.tenor.com/q_k1Sd4i89EAAAAC/lebron-james.gif",                                                                                              "LEBRON"),
    ("https://media.tenor.com/sBJXhLFc0X0AAAAC/lebron.gif",                                                                                                    "KING 2"),
    ("https://media.tenor.com/7KlvM3nV_fUAAAAC/lebron-james-basketball.gif",                                                                                   "HOOPS"),
    ("https://media.tenor.com/8RqBd2ZbYHsAAAAC/lebron-james.gif",                                                                                              "FINALS"),
    ("https://ak-static.cms.nba.com/wp-content/uploads/headshots/nba/latest/260x190/2544.png",                                                                 "#23"),
    ("https://media.tenor.com/5FKuFVJlAhIAAAAC/lebron-james-lebron.gif",                                                                                       "CLUTCH"),
    ("https://media.tenor.com/Gq4p9FwKl0UAAAAC/lebron-james.gif",                                                                                              "LEGEND"),
    ("https://media.tenor.com/YsXTNqRdBfQAAAAC/lebron-james.gif",                                                                                              "GOAT"),
]


def _king_lebron_gallery() -> None:
    """Render 20-image LeBron gallery as a CSS grid. Broken images auto-hide via onerror."""
    st.markdown(
        '<div style="color:#FDB927; font-family:\'Impact\',sans-serif; font-size:16px; '
        'letter-spacing:4px; text-align:center; margin:18px 0 10px 0;">👑 THE KING\'S GALLERY 👑</div>',
        unsafe_allow_html=True,
    )
    img_tags = ''
    for url, cap in _LEBRON_GALLERY:
        img_tags += (
            f'<div style="text-align:center; background:#0a0a14; border:1px solid #FDB92755; '
            f'border-radius:6px; overflow:hidden; padding:4px;">'
            f'<img src="{url}" onerror="this.parentElement.style.display=\'none\'" '
            f'style="width:100%; height:90px; object-fit:cover; border-radius:4px;" />'
            f'<div style="color:#FDB927; font-size:8px; font-family:monospace; '
            f'letter-spacing:2px; margin-top:3px;">{cap}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat(5,1fr); gap:6px; '
        f'margin:6px 0 20px 0;">{img_tags}</div>',
        unsafe_allow_html=True,
    )


def _lebron_img(width: int = 120, caption: str = '') -> None:
    """Try each LeBron URL; silently skip if all fail."""
    for url in _LEBRON_URLS:
        try:
            st.image(url, width=width, caption=caption)
            return
        except Exception:
            continue


@st.fragment(run_every=3)
def _live_stat_hub(rm):
    """
    Live stat hub — reruns every 3s via st.fragment without touching the rest of the page.
    Shows: equity curve chart + 4 key metric cards + live trade ticker.
    """
    stats    = get_all_time_stats(paper=PAPER_TRADING)
    today_s  = get_today_stats(paper=PAPER_TRADING)
    recent   = get_recent_trades(limit=50, paper=PAPER_TRADING)
    fees_today = get_todays_fees(paper=PAPER_TRADING)

    _net_pnl_all = stats.get('total_pnl', 0) - stats.get('total_fees', 0)
    real_balance = ACCOUNT_SIZE + _net_pnl_all
    _deployed    = rm._get_deployed()
    wr_today     = today_s['wins'] / (today_s['total'] or 1)
    wr_all       = stats.get('win_rate', 0)

    # ── Equity curve (plotly) ─────────────────────────────────────────────────
    # Build cumulative P&L from all closed trades in chronological order
    closed = [t for t in reversed(recent) if (t.get('pnl_usd') or 0) != 0]
    if closed:
        xs  = [t.get('ts', '')[:16] for t in closed]
        cum = []
        _s  = 0.0
        for t in closed:
            _s += float(t.get('pnl_usd') or 0)
            cum.append(round(_s, 4))

        line_color = '#FDB927' if cum[-1] >= 0 else '#ff4444'
        fill_color = 'rgba(253,185,39,0.12)' if cum[-1] >= 0 else 'rgba(255,68,68,0.10)'

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=xs, y=cum,
            mode='lines+markers',
            line=dict(color=line_color, width=2.5),
            fill='tozeroy',
            fillcolor=fill_color,
            marker=dict(size=5, color=[
                '#44ff88' if v >= 0 else '#ff4444' for v in cum
            ]),
            hovertemplate='%{x}<br>Cum P&L: $%{y:+.4f}<extra></extra>',
            name='P&L',
        ))
        # Zero line
        fig.add_hline(y=0, line_dash='dot', line_color='#444', line_width=1)

        fig.update_layout(
            height=170,
            margin=dict(l=0, r=0, t=4, b=0),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#aaa', size=10),
            xaxis=dict(
                showgrid=False, showticklabels=True, tickfont=dict(size=9),
                tickangle=-30, nticks=8,
            ),
            yaxis=dict(
                showgrid=True, gridcolor='#222', tickprefix='$',
                tickfont=dict(size=9),
            ),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    else:
        st.markdown(
            '<div style="text-align:center; color:#555; padding:30px 0; font-size:13px;">'
            '📉 No closed trades yet — equity curve will appear after first exit</div>',
            unsafe_allow_html=True)

    # ── 4 metric cards + live trade ticker ───────────────────────────────────
    m1, m2, m3, m4, ticker_col = st.columns([1, 1, 1, 1, 1.8])

    def _card(col, label, value, sub, color='#FDB927', icon='📊'):
        border_glow = f'box-shadow: 0 0 14px {color}55; border: 2px solid {color};'
        col.markdown(f"""
        <div style="background:linear-gradient(135deg,#0d1b38 0%,#1D428A 100%);
             {border_glow} border-radius:10px; padding:14px 10px; text-align:center;
             margin:4px 0; min-height:90px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                 letter-spacing:2px; margin-bottom:4px;">{icon} {label}</div>
            <div style="font-size:26px; font-weight:900; color:{color};
                 font-family:'Impact',sans-serif; line-height:1.1;">{value}</div>
            <div style="font-size:10px; color:#666; margin-top:5px;">{sub}</div>
        </div>""", unsafe_allow_html=True)

    _bal_color = '#44ff88' if real_balance >= ACCOUNT_SIZE else '#ff4444'
    _bal_icon  = '💰' if real_balance >= ACCOUNT_SIZE else '📉'
    _card(m1, 'BALANCE', f'${real_balance:,.2f}',
          f'{_net_pnl_all:+.2f} net all-time', _bal_color, _bal_icon)

    _pnl_color = '#44ff88' if today_s['net_pnl'] >= 0 else '#ff4444'
    _pnl_icon  = '🔥' if today_s['net_pnl'] > 5 else '📈' if today_s['net_pnl'] >= 0 else '📉'
    _card(m2, 'TODAY P&L',
          f"{'+' if today_s['net_pnl']>=0 else ''}${today_s['net_pnl']:.2f}",
          f"gross {today_s['gross_pnl']:+.2f} · fees −${fees_today:.3f}",
          _pnl_color, _pnl_icon)

    _wr_color = '#44ff88' if wr_all >= 0.52 else '#FDB927' if wr_all >= 0.45 else '#ff4444'
    _wr_icon  = '👑' if wr_all >= 0.52 else '⚡' if wr_all >= 0.45 else '⚠️'
    _card(m3, 'WIN RATE', f'{wr_all:.1%}',
          f"{stats.get('wins',0)}W / {stats.get('losses',0)}L · {wr_today:.0%} today",
          _wr_color, _wr_icon)

    _dep_pct   = _deployed / ACCOUNT_SIZE if ACCOUNT_SIZE else 0
    _dep_color = '#ff4444' if _dep_pct > 0.8 else '#FDB927' if _dep_pct > 0.3 else '#44ff88'
    _dep_icon  = '⚡' if _dep_pct > 0.5 else '🏦'
    _card(m4, 'DEPLOYED',
          f'${_deployed:.0f}',
          f'{_dep_pct:.0%} of ${ACCOUNT_SIZE:,.0f} · {today_s["total"]} trades',
          _dep_color, _dep_icon)

    # Live trade ticker — last 8 closed trades
    with ticker_col:
        st.markdown('<div style="font-size:10px; color:#aaa; letter-spacing:2px; '
                    'text-transform:uppercase; margin-bottom:4px;">⚡ LIVE TRADES</div>',
                    unsafe_allow_html=True)
        tick_trades = [t for t in recent if (t.get('pnl_usd') or 0) != 0][:8]
        if not tick_trades:
            st.markdown('<div style="color:#555; font-size:12px;">No closed trades yet</div>',
                        unsafe_allow_html=True)
        else:
            rows_html = ''
            for t in tick_trades:
                pnl = float(t.get('pnl_usd') or 0)
                sym = t.get('symbol', '?')[:10]
                ts  = (t.get('ts') or '')[:16].replace('T', ' ')
                pnl_color = '#44ff88' if pnl > 0 else '#ff4444'
                badge = '▲' if pnl > 0 else '▼'
                rows_html += (
                    f'<div style="display:flex; justify-content:space-between; '
                    f'padding:2px 4px; border-bottom:1px solid #1a1a1a; font-size:11px;">'
                    f'<span style="color:#ccc;">{sym}</span>'
                    f'<span style="color:{pnl_color}; font-weight:700;">{badge} ${pnl:+.3f}</span>'
                    f'</div>'
                )
            st.markdown(f'<div style="background:#0a0a0a; border:1px solid #222; '
                        f'border-radius:6px; padding:4px;">{rows_html}</div>',
                        unsafe_allow_html=True)
        # Last updated timestamp
        st.markdown(
            f'<div style="font-size:9px; color:#444; margin-top:4px;">'
            f'<span style="color:#44ff88; font-size:8px;">●</span> LIVE · updated {et_now()}</div>',
            unsafe_allow_html=True)


def render_king():
    st.markdown(f"<style>{THEME_CSS['king']}</style>", unsafe_allow_html=True)

    quote, _ = get_quote_for_hour()

    # ── Check market hours for bouncing basketball idle animation ────────────
    _is_market_open = is_market_open()
    _bb_anim_path   = os.path.join(_AN, 'bouncing_basketball.html')

    # Header: GIF | Title+Quote | GIF  (no stacking with LeBron — one item per side col)
    hcol1, hcol2, hcol3 = st.columns([1, 4, 1])
    with hcol1:
        # During market hours show LeBron; before/after show dunk gif
        if _is_market_open:
            _lebron_img(width=110)
        else:
            _local_img(os.path.join(_GIF, 'dunk_gold_23.gif'), width=90)
    with hcol2:
        st.markdown(f"""
        <div class="king-header" style="margin:0;">
            <div style="text-align:center; color:#FDB927; font-size:28px; font-weight:900; letter-spacing:4px;">
                👑 THE KING'S WAR ROOM 👑
            </div>
            <div style="text-align:center; color:#888; font-size:11px; letter-spacing:6px;
                 font-family:monospace; margin-top:2px;">EQUITIES · FUTURES · COMMAND CENTER</div>
            <div class="quote-box" style="margin-top:10px;">"{quote}" — LeBron James</div>
        </div>
        """, unsafe_allow_html=True)
        if not _is_market_open:
            _local_anim(_bb_anim_path, height=55)
    with hcol3:
        if _is_market_open:
            _lebron_img(width=110, caption='#23')
        else:
            _local_img(os.path.join(_GIF, 'dunk_celebrate_gold.gif'), width=90)

    rm = get_risk_manager()
    _gross_pnl = get_todays_pnl(paper=PAPER_TRADING)
    fees = get_todays_fees(paper=PAPER_TRADING)
    pnl = _gross_pnl - fees   # net P&L — consistent with stat hub
    trades = get_todays_trades(paper=PAPER_TRADING)
    risk = rm.status_report()

    if PAPER_TRADING:
        _bball_icon_b64 = _b64img(os.path.join(_BB, 'basketball_icon_gold.svg'))
        _icon_tag = (f'<img src="{_bball_icon_b64}" style="width:20px; vertical-align:middle; margin-right:6px;">'
                     if _bball_icon_b64 else '')
        st.markdown(
            f'<div class="paper-banner">{_icon_tag}📄 PAPER TRAINING — {LEBRON_MESSAGES["paper"]}</div>',
            unsafe_allow_html=True)
    if rm.is_halted:
        _def_b64 = _b64img(os.path.join(_BB, 'bball_defense_gold_08.svg'))
        _def_tag = (f'<img src="{_def_b64}" style="width:60px; vertical-align:middle; margin-right:10px;">'
                    if _def_b64 else '')
        st.markdown(
            f'<div class="halt-banner">{_def_tag}🚨 {LEBRON_MESSAGES["halt"]}<br>{rm.halt_reason}</div>',
            unsafe_allow_html=True)

    # ── Scoreboard P&L with decorative court ─────────────────────────────────
    pnl_class = "scoreboard" if pnl >= 0 else "scoreboard scoreboard-neg"
    sign = "+" if pnl >= 0 else ""
    pnl_emoji = "🔥" if pnl > 20 else "📈" if pnl > 0 else "📉"
    st.markdown(f'<div class="{pnl_class}">{pnl_emoji} {sign}${pnl:.2f} {pnl_emoji}</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div style="text-align:center; color:#aaa; letter-spacing:4px; margin-bottom:10px;">TODAY\'S NET P&L (after fees) — {et_now()}</div>',
                unsafe_allow_html=True)

    # ── Celebration / defense visual — only one at a time, only if recent ────
    recent = get_recent_trades(limit=3, paper=PAPER_TRADING)
    most_recent_pnl = recent[0].get('pnl_usd', 0) if recent else 0

    # "Recent" = last trade happened within 20 minutes
    _last_trade_ts = recent[0].get('ts', '') if recent else ''
    _trade_is_fresh = False
    if _last_trade_ts:
        try:
            from datetime import timezone as _utc2
            _dt = datetime.fromisoformat(_last_trade_ts)
            if not _dt.tzinfo: _dt = _dt.replace(tzinfo=_utc2.utc)
            _trade_is_fresh = (datetime.now(_utc2.utc) - _dt).total_seconds() < 1200
        except Exception:
            pass

    if rm.is_halted:
        # Halt takes priority
        _sb_l, _sb_c, _sb_r = st.columns([1, 2, 1])
        with _sb_c:
            _local_img(os.path.join(_BB, 'bball_defense_gold_08.svg'), width=80)
    elif _trade_is_fresh and most_recent_pnl > 10:
        # Big win: side celebrate SVGs + dunk text animation in center
        _bw1, _bw2, _bw3 = st.columns([1, 3, 1])
        with _bw1:
            _local_img(os.path.join(_BB, 'bball_celebrate_gold_10.svg'), width=100)
        with _bw2:
            _local_anim(os.path.join(_AN, 'power_text_dunk.html'), height=80)
            st.markdown(f"""
            <div class="win-flash" style="text-align:center; color:#FDB927; font-size:18px;
                 font-weight:900; padding:8px; background:#1D428A33; border-radius:8px; margin:6px 0;">
                🏆 {LEBRON_MESSAGES['win_big']} +${most_recent_pnl:.2f} 🏆
            </div>""", unsafe_allow_html=True)
        with _bw3:
            _local_img(os.path.join(_BB, 'bball_celebrate_gold_10.svg'), width=100)
    elif _trade_is_fresh and most_recent_pnl > 0:
        # Regular win: centered gif + win flash text
        _sb_l, _sb_c, _sb_r = st.columns([1, 2, 1])
        with _sb_c:
            _local_img(os.path.join(_GIF, 'dunk_celebrate_gold.gif'), width=100)
        st.markdown(f"""
        <div class="win-flash" style="text-align:center; color:#FDB927; font-size:18px;
             font-weight:900; padding:8px; background:#1D428A33; border-radius:8px; margin:6px 0;">
            🏆 {LEBRON_MESSAGES['win']} +${most_recent_pnl:.2f} 🏆
        </div>""", unsafe_allow_html=True)

    # ── Live stat hub (fragment — reruns every 3s, not the whole page) ──────────
    _live_stat_hub(rm)

    st.divider()

    # ── Current Brain ─────────────────────────────────────────────────────────
    with st.expander("🧠 Current Brain — System Parameters", expanded=False):

        def _brain_row(label, value, highlight=False):
            color = '#FDB927' if highlight else '#cccccc'
            return (
                f'<div style="display:flex; justify-content:space-between; '
                f'padding:5px 10px; border-bottom:1px solid #1a1a2a; font-size:12px;">'
                f'<span style="color:#888;">{label}</span>'
                f'<span style="color:{color}; font-weight:{"700" if highlight else "400"};">{value}</span>'
                f'</div>'
            )

        def _brain_section(title, rows_html):
            return (
                f'<div style="background:#0a0a18; border:1px solid #1D428A55; border-radius:8px; '
                f'overflow:hidden; margin-bottom:10px;">'
                f'<div style="background:#1D428A; padding:6px 10px; font-size:11px; '
                f'font-weight:900; color:#FDB927; letter-spacing:2px;">{title}</div>'
                f'{rows_html}'
                f'</div>'
            )

        _bc1, _bc2, _bc3 = st.columns(3)

        with _bc1:
            model_short = CLAUDE_MODEL.replace('claude-', '').replace('-20', ' (') + ')' if '-20' in CLAUDE_MODEL else CLAUDE_MODEL.replace('claude-', '')
            _agent_labels = {
                'funding_regime':     'Macro & Funding Agent',
                'momentum_structure': 'Technical Momentum Agent',
                'risk_economics':     'Risk & Economics Agent',
            }
            try:
                from config import ML_SIGNAL_MIN_PROB as _mlp
                _ml_str = f'{_mlp:.0%} (auto-raised)'
            except Exception:
                _ml_str = 'N/A'

            _ai_rows = (
                _brain_row('Model', model_short)
                + _brain_row('Debate tokens', str(DEBATE_MAX_TOKENS))
                + _brain_row('Exit review tokens', str(EXIT_REVIEW_MAX_TOKENS))
                + _brain_row('Decision rule', f'2/{len(FULL_DEBATE_AGENTS)} BUY = BUY', highlight=True)
                + _brain_row('Moderator', 'NONE (removed v8.0)', highlight=True)
                + _brain_row('Cost per debate', '~0.02 USD (was 0.08)', highlight=True)
                + _brain_row('ML gate P(win)', _ml_str, highlight=True)
            )
            _agent_rows = ''.join(
                _brain_row(f'Agent {i+1}', _agent_labels.get(ag, ag), highlight=True)
                for i, ag in enumerate(FULL_DEBATE_AGENTS)
            )
            st.markdown(
                _brain_section('🤖 AI CONFIG — v8.0', _ai_rows)
                + _brain_section('👥 3-AGENT DEBATE PANEL', _agent_rows),
                unsafe_allow_html=True,
            )

        with _bc2:
            _risk_rows = (
                _brain_row('Max risk/trade', f'{MAX_RISK_PER_TRADE_PCT:.0%}')
                + _brain_row('Max daily loss', f'{MAX_DAILY_LOSS_PCT:.0%}')
                + _brain_row('Max deployed', f'{MAX_DEPLOYED_PCT:.0%}')
                + _brain_row('Max fee drag/day', f'{MAX_DAILY_FEE_DRAG_PCT:.0%}')
                + _brain_row('Max crypto positions', str(MAX_POSITIONS_CRYPTO))
                + _brain_row('Max equity positions', str(MAX_POSITIONS_EQUITY))
                + _brain_row('Crypto stop / target', f'{CRYPTO_STOP_LOSS_PCT:.1%} / {CRYPTO_TAKE_PROFIT_PCT:.1%}')
                + _brain_row('Taker fee', f'{COINBASE_TAKER_FEE_PCT:.2%}')
                + _brain_row('Loss streak halt', f'{MAX_STRATEGY_LOSS_STREAK} consecutive')
                + _brain_row('Max crypto trades/day', str(MAX_TRADES_PER_DAY_CRYPTO))
                + _brain_row('Max equity trades/day', str(MAX_TRADES_PER_DAY_EQUITY))
            )
            st.markdown(_brain_section('🛡️ RISK RULES', _risk_rows), unsafe_allow_html=True)

            try:
                _ks = get_kelly_stats(strategy='crypto_ai_debate', paper=PAPER_TRADING, window=50)
                _kelly_rows = (
                    _brain_row('Trades (last 50)', str(_ks.get('n_trades', 0)))
                    + _brain_row('Win rate', f"{_ks.get('win_rate', 0):.1%}", highlight=_ks.get('win_rate', 0) >= 0.45)
                    + _brain_row('Kelly 25%', f"{_ks.get('kelly_25pct', 0):.1%}")
                    + _brain_row('Avg W / L', f"{_ks.get('avg_win', 0):.2f} / {abs(_ks.get('avg_loss', 0)):.2f}")
                )
            except Exception:
                _kelly_rows = _brain_row('Status', 'No trades yet')
            st.markdown(_brain_section('📈 LIVE KELLY', _kelly_rows), unsafe_allow_html=True)

        with _bc3:
            _pairs_list = CRYPTO_PAIRS if isinstance(CRYPTO_PAIRS, list) else str(CRYPTO_PAIRS).split(',')
            _pairs_str = ', '.join(p.replace('-USDC','').replace('-USD','') for p in _pairs_list)
            _kal_pct = KALMAN_ENTRY_DEV_PCT * 100 if abs(KALMAN_ENTRY_DEV_PCT) < 1 else KALMAN_ENTRY_DEV_PCT
            _avw_pct = AVWAP_ENTRY_DEV_PCT * 100 if abs(AVWAP_ENTRY_DEV_PCT) < 1 else AVWAP_ENTRY_DEV_PCT
            _sig_rows = (
                _brain_row('Min ADX (crypto)', str(CRYPTO_MIN_ADX))
                + _brain_row('RSI role', 'Exit only — NOT entry gate')
                + _brain_row('OU z-score entry', '<= -1.5  |  exit >= -0.5')
                + _brain_row('Kalman entry dev', f'{_kal_pct:.1f}%')
                + _brain_row('AVWAP entry dev', f'{_avw_pct:.1f}%')
                + _brain_row('Crypto position size', f'USD {CRYPTO_POSITION_SIZE_USD:.0f}')
                + _brain_row('Equity position size', f'USD {EQUITY_POSITION_SIZE_USD:.0f}')
                + _brain_row('Crypto scan interval', f'{CRYPTO_SCAN_INTERVAL_SECONDS}s')
                + _brain_row(f'Pairs ({len(_pairs_list)})', _pairs_str)
            )
            _new_sig_rows = (
                _brain_row('StochRSI cross up', '+10 pts  (40-45% WR)', highlight=True)
                + _brain_row('CVD bull divergence', '+8 pts  (40-50% WR)', highlight=True)
                + _brain_row('VWAP -2σ touch', '+8 pts  (45-55% WR)', highlight=True)
                + _brain_row('EMA 9/21 golden cross', '+10 pts  (40-45% WR)', highlight=True)
            )
            st.markdown(
                _brain_section('📡 SIGNAL CONFIG', _sig_rows)
                + _brain_section('⚡ HIGH-WR SIGNALS (v8.1)', _new_sig_rows),
                unsafe_allow_html=True,
            )

    # ── Modular panel layout ──────────────────────────────────────────────────
    _init_king_layout()
    _layout_editor()

    def _dispatch(pid):
        if   pid == 'scan_feed':     _panel_scan_feed()
        elif pid == 'positions':     _panel_positions(rm)
        elif pid == 'trades':        _panel_trades()
        elif pid == 'notifications': _panel_notifications()
        elif pid == 'signals':       _panel_signals()
        elif pid == 'risk':          _panel_risk(pnl, rm)
        elif pid == 'cost_lab':      _panel_cost_lab()
        elif pid == 'strategy_lab':  _panel_strategy_lab()
        elif pid == 'attribution':   _panel_attribution()
        elif pid == 'chat':          _panel_chat()

    layout = st.session_state.king_layout
    left, mid, right = st.columns([1.3, 1, 1.2])

    with left:
        panels_l = layout['left']
        for j, pid in enumerate(panels_l):
            _dispatch(pid)
            if j < len(panels_l) - 1:
                st.divider()

    with mid:
        panels_m = layout['mid']
        for j, pid in enumerate(panels_m):
            _dispatch(pid)
            if j < len(panels_m) - 1:
                st.divider()

    with right:
        panels_r = layout['right']
        for j, pid in enumerate(panels_r):
            _dispatch(pid)
            if j < len(panels_r) - 1:
                st.divider()

    # ── LeBron Gallery ────────────────────────────────────────────────────────
    st.divider()
    _king_lebron_gallery()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION A — CAPACITY GAUGES
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📊 Capacity Gauges")
    _king_capacity_gauges()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION B — PERP FUNDING RATES
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("⚡ Perp Funding Rates")
    _king_perp_funding(rm)

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION C — ROLLING WIN RATE (10-TRADE WINDOW)
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📈 Rolling Win Rate (10-Trade Window)")
    _king_rolling_win_rate()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION D — AGENT PERFORMANCE SCORECARD
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🤖 Agent Performance Scorecard")
    _king_agent_scorecard()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION E — RECENT DEBATE TRANSCRIPTS (LAST 6)
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🎬 Recent Debate Transcripts (Last 6)")
    _king_debate_transcripts()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION F — ALL-TIME PERFORMANCE STATS
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📊 All-Time Performance Stats")
    _king_alltime_stats()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION G — MILESTONE TRACKER
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏅 Milestone Tracker")
    _king_milestones()


# ─── Section helpers added below render_king ──────────────────────────────────

def _king_capacity_gauges():
    """Section A: 4 capacity/limit progress bars."""
    pnl = get_todays_pnl(paper=PAPER_TRADING) - get_todays_fees(paper=PAPER_TRADING)
    fees_today = get_todays_fees(paper=PAPER_TRADING)
    rm = get_risk_manager()
    pos = rm.get_all_positions()
    cr_pos = pos.get('crypto', {})
    perp_pos = pos.get('perp', {})

    all_time = get_all_time_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + all_time.get('total_pnl', 0)
    max_loss_usd = real_balance * MAX_DAILY_LOSS_PCT
    daily_loss_pct = abs(pnl) / real_balance * 100 if pnl < 0 else 0
    halt_pct = MAX_DAILY_LOSS_PCT * 100

    st.progress(
        min(daily_loss_pct / halt_pct, 1.0),
        text=f"Daily Loss Used — {daily_loss_pct:.2f}% / {halt_pct:.0f}% halt  (${abs(pnl):.2f} of ${max_loss_usd:.2f} limit)"
    )

    spot_count = len(cr_pos)
    st.progress(
        min(spot_count / max(MAX_POSITIONS_CRYPTO, 1), 1.0),
        text=f"Crypto Positions — {spot_count} / {MAX_POSITIONS_CRYPTO}"
    )

    perp_count = len(perp_pos)
    _MAX_PERP = 3
    st.progress(
        min(perp_count / _MAX_PERP, 1.0),
        text=f"Perp Positions — {perp_count} / {_MAX_PERP}"
    )

    fee_drag_pct = fees_today / real_balance * 100
    fee_limit_pct = MAX_DAILY_FEE_DRAG_PCT * 100
    st.progress(
        min(fee_drag_pct / fee_limit_pct, 1.0),
        text=f"Fee Drag Today — {fee_drag_pct:.2f}% / {fee_limit_pct:.1f}% limit  (${fees_today:.2f} paid)"
    )


def _king_perp_funding(rm):
    """Section B: Perp funding rates — only if open perp positions exist."""
    pos = rm.get_all_positions()
    perp_pos = pos.get('perp', {})
    if not perp_pos:
        st.caption("No open perp positions — funding rates will appear here when positions are open.")
        return
    for sym, p in perp_pos.items():
        funding = p.get('funding_rate', None)
        side = p.get('side', 'long')
        lev = p.get('leverage', 1)
        if funding is not None:
            f_pct = funding * 100
            paying = (side == 'long' and f_pct > 0) or (side == 'short' and f_pct < 0)
            f_color = '#ff4444' if paying else '#44ff88'
            label = "paying" if paying else "receiving"
            st.markdown(
                f'<div style="background:#0a0a0a; border-left:3px solid {f_color}; '
                f'padding:6px 12px; margin:3px 0; border-radius:0 4px 4px 0; font-family:monospace; font-size:12px;">'
                f'<span style="color:#ddd; font-weight:700;">{sym}</span>'
                f'&nbsp;&nbsp;<span style="color:#888;">{side.upper()} ×{lev}</span>'
                f'&nbsp;&nbsp;<span style="color:{f_color}; font-weight:700; float:right;">'
                f'{f_pct:+.4f}%/8h ({label})</span>'
                f'</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="background:#0a0a0a; border-left:3px solid #444; '
                f'padding:6px 12px; margin:3px 0; border-radius:0 4px 4px 0; font-family:monospace; font-size:12px;">'
                f'<span style="color:#ddd; font-weight:700;">{sym}</span>'
                f'&nbsp;&nbsp;<span style="color:#888;">{side.upper()} ×{lev}</span>'
                f'&nbsp;&nbsp;<span style="color:#444; float:right;">no funding rate data</span>'
                f'</div>',
                unsafe_allow_html=True)


def _king_rolling_win_rate():
    """Section C: Rolling 10-trade win rate chart."""
    all_trades = get_recent_trades(limit=200, paper=PAPER_TRADING)
    closed = [t for t in all_trades if t.get('pnl_usd', 0) != 0 and t.get('ts')]
    closed_sorted = sorted(closed, key=lambda x: x.get('ts', ''))

    if len(closed_sorted) < 10:
        st.caption(f"Need 10+ closed trades for win rate trend (have {len(closed_sorted)}).")
        return

    wr_x, wr_y = [], []
    for i in range(9, len(closed_sorted)):
        window = closed_sorted[i-9:i+1]
        wins_w = sum(1 for t in window if t.get('pnl_usd', 0) > 0)
        wr_x.append(closed_sorted[i]['ts'])
        wr_y.append(wins_w / 10.0)

    wr_color = '#44ff88' if (wr_y[-1] >= 0.5 if wr_y else False) else '#ff4444'
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=wr_x, y=wr_y,
        mode='lines',
        line=dict(color=wr_color, width=2),
        fill='tozeroy',
        fillcolor='rgba(68,255,136,0.06)' if wr_color == '#44ff88' else 'rgba(255,68,68,0.06)',
        name='Win Rate'
    ))
    fig.add_hline(y=0.5, line_dash='dash', line_color='#FDB927',
                  line_width=1, annotation_text='50%',
                  annotation_font_color='#FDB927', annotation_font_size=9)
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=180, margin=dict(l=40, r=10, t=10, b=30),
        xaxis=dict(showgrid=False, color='#555', tickfont=dict(size=9)),
        yaxis=dict(showgrid=True, gridcolor='#222', color='#555',
                   tickformat='.0%', tickfont=dict(size=9), range=[0, 1]),
        showlegend=False,
        font=dict(color='#aaa'),
    )
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    if wr_y:
        st.caption(f"Latest 10-trade window: {wr_y[-1]:.0%} win rate")


def _king_agent_scorecard():
    """Section D: Per-agent vote stats pulled from recent debate logs."""
    _AGENT_DISPLAY = {
        'funding_regime':     'Macro & Funding Agent',
        'momentum_structure': 'Technical Momentum Agent',
        'risk_economics':     'Risk & Economics Agent',
    }
    debates = get_recent_debates(limit=50)
    if not debates:
        st.info("No debates recorded yet.")
        return

    agent_stats: dict = {}
    for d in debates:
        try:
            agents = json.loads(d.get('agent_details', '[]'))
            for ag in agents:
                key  = ag.get('agent', '?')
                vote = ag.get('signal', 'HOLD')
                conf = ag.get('confidence', 0)
                if key not in agent_stats:
                    agent_stats[key] = {'votes': 0, 'conf_sum': 0.0,
                                        'buy': 0, 'hold': 0, 'sell': 0}
                agent_stats[key]['votes'] += 1
                agent_stats[key]['conf_sum'] += conf
                agent_stats[key][vote.lower() if vote.lower() in ('buy','hold','sell') else 'hold'] += 1
        except Exception:
            pass

    if not agent_stats:
        st.info("No agent vote data found in debates.")
        return

    cols = st.columns(len(agent_stats))
    for i, (agent_key, s) in enumerate(sorted(agent_stats.items())):
        display = _AGENT_DISPLAY.get(agent_key, agent_key)
        total = s['votes']
        avg_conf = s['conf_sum'] / total if total else 0
        buy_pct  = s['buy']  / total if total else 0
        hold_pct = s['hold'] / total if total else 0
        sell_pct = s['sell'] / total if total else 0
        with cols[i]:
            st.markdown(
                f'<div style="background:#0a0a18; border:1px solid #1D428A; border-radius:8px; '
                f'padding:12px; text-align:center;">'
                f'<div style="color:#FDB927; font-weight:900; font-size:12px; margin-bottom:6px;">{display}</div>'
                f'<div style="color:#aaa; font-size:11px;">{total} total votes</div>'
                f'<div style="margin:8px 0;">'
                f'<span style="color:#44ff88; font-weight:700;">BUY {buy_pct:.0%}</span>'
                f'&nbsp;&nbsp;'
                f'<span style="color:#888;">HOLD {hold_pct:.0%}</span>'
                f'&nbsp;&nbsp;'
                f'<span style="color:#ff4444;">SELL {sell_pct:.0%}</span>'
                f'</div>'
                f'<div style="color:#aaa; font-size:11px;">avg conf {avg_conf:.0%}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _king_debate_transcripts():
    """Section E: Expandable debate transcripts (last 6)."""
    _AGENT_DISPLAY = {
        'funding_regime':     'Macro & Funding Agent',
        'momentum_structure': 'Technical Momentum Agent',
        'risk_economics':     'Risk & Economics Agent',
    }
    debates = get_recent_debates(limit=6)
    if not debates:
        st.info("No debates recorded yet.")
        return
    for d in debates:
        signal = d.get('final_signal', 'HOLD')
        buy_v  = d.get('buy_votes', 0)
        hold_v = d.get('hold_votes', 0)
        sell_v = d.get('sell_votes', 0)
        conf   = d.get('confidence', 0)
        ts_str = fmt_ts(d.get('ts', ''), show_date=True)
        label  = (f"{ts_str} | {d.get('symbol','?')} → {signal} "
                  f"({buy_v}B/{hold_v}H/{sell_v}S | {conf:.0%} conf) | {d.get('regime','?')}")
        with st.expander(label):
            sig_color = '#44ff88' if signal == 'BUY' else '#ff4444' if signal == 'SELL' else '#888'
            st.markdown(
                f'<div style="background:#0a0a18; border-left:3px solid {sig_color}; '
                f'padding:10px 14px; border-radius:0 6px 6px 0; font-size:12px; margin-bottom:8px;">'
                f'<b style="color:#FDB927;">Synthesis:</b> {d.get("reasoning","")}<br>'
                f'<b style="color:#44ff88;">Bull case:</b> {d.get("bull_case","")}<br>'
                f'<b style="color:#ff4444;">Bear case:</b> {d.get("bear_case","")}<br>'
                f'<b style="color:#ff8888;">Key risk:</b> {d.get("key_risk","")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            try:
                agents_list = json.loads(d.get('agent_details', '[]'))
                for ag in agents_list:
                    ag_sig = ag.get('signal', 'HOLD')
                    ag_color = '#44ff88' if ag_sig == 'BUY' else '#ff4444' if ag_sig == 'SELL' else '#888'
                    name = _AGENT_DISPLAY.get(ag.get('agent', ''), ag.get('agent', '?'))
                    st.markdown(
                        f'<div style="background:#111; border-left:2px solid {ag_color}; '
                        f'padding:6px 10px; margin:3px 0; border-radius:0 4px 4px 0; font-size:11px;">'
                        f'<span style="color:#ddd; font-weight:700;">{name}</span>: '
                        f'<span style="color:{ag_color}; font-weight:700;">{ag_sig}</span> '
                        f'({ag.get("confidence",0):.0%}) — {ag.get("reasoning","")[:150]}'
                        f'<br><span style="color:#555;">⚠ {ag.get("key_concern","")[:100]}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            except Exception:
                pass


def _king_alltime_stats():
    """Section F: All-time performance stats in a single metric row."""
    stats = get_all_time_stats(paper=PAPER_TRADING)
    all_trades = get_recent_trades(limit=500, paper=PAPER_TRADING)
    monthly_cost = get_monthly_api_cost()

    closed = [t for t in all_trades if t.get('pnl_usd', 0) != 0]
    wins_   = [t for t in closed if t.get('pnl_usd', 0) > 0]
    losses_ = [t for t in closed if t.get('pnl_usd', 0) < 0]
    gross_profit = sum(t.get('pnl_usd', 0) for t in wins_)
    gross_loss   = abs(sum(t.get('pnl_usd', 0) for t in losses_))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    total_fees_all = sum(t.get('fee_usd', 0) for t in all_trades)
    pnl_all = stats.get('total_pnl', 0)
    return_pct = pnl_all / ACCOUNT_SIZE * 100

    pf_label = f"{profit_factor:.2f}×" if profit_factor < 999 else "∞"

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Best Trade",        f"${stats.get('best_trade', 0):+.2f}")
    c2.metric("Worst Trade",       f"${stats.get('worst_trade', 0):+.2f}")
    c3.metric("Total Fees Paid",   f"${total_fees_all:.2f}")
    c4.metric("Profit Factor",     pf_label)
    c5.metric("Return on Account", f"{return_pct:+.1f}%")
    c6.metric("Monthly AI Cost",   f"${monthly_cost:.4f}")


def _king_milestones():
    """Section G: 10-milestone tracker. Green check = earned, progress bar = in progress, lock = not started."""
    stats = get_all_time_stats(paper=PAPER_TRADING)
    pnl_all      = stats.get('total_pnl', 0)
    win_rate     = stats.get('win_rate', 0)
    total_trades = stats.get('total', 0)

    ALL_MILESTONES = [
        ("🥇", "First Trade",        "Complete 1 trade",                     total_trades >= 1,    None),
        ("💰", "In the Black",       "Positive total P&L",                   pnl_all > 0,          None),
        ("🎯", "55% Win Rate",       "Win rate at or above 55%",             win_rate >= 0.55,     None),
        ("📊", "10 Trades",          "Complete 10 or more trades",           total_trades >= 10,   total_trades / 10),
        ("🔥", "50 Trades",          "Complete 50 or more trades",           total_trades >= 50,   total_trades / 50),
        ("🏅", "First 10% Gain",     "Account up 10% from start",           pnl_all >= ACCOUNT_SIZE * 0.10, pnl_all / (ACCOUNT_SIZE * 0.10)),
        ("🏆", "First 25% Gain",     "Account up 25% from start",           pnl_all >= ACCOUNT_SIZE * 0.25, pnl_all / (ACCOUNT_SIZE * 0.25)),
        ("👑", "First 50% Gain",     "Account up 50% from start",           pnl_all >= ACCOUNT_SIZE * 0.50, pnl_all / (ACCOUNT_SIZE * 0.50)),
        ("🌟", "100 Trades",         "Complete 100 or more trades",          total_trades >= 100,  total_trades / 100),
        ("💎", "Double the Account", "100%+ return — account fully doubled", pnl_all >= ACCOUNT_SIZE, pnl_all / ACCOUNT_SIZE),
    ]

    earned_count = sum(1 for _, _, _, cond, _ in ALL_MILESTONES if cond)
    st.caption(f"{earned_count} of {len(ALL_MILESTONES)} milestones earned")

    for trophy, title, desc, cond, prog in ALL_MILESTONES:
        if cond:
            # Earned
            st.markdown(
                f'<div style="display:flex; align-items:center; gap:10px; padding:8px 12px; '
                f'background:#0a1a0a; border:1px solid #44ff88; border-radius:6px; margin:3px 0;">'
                f'<span style="font-size:22px;">{trophy}</span>'
                f'<span style="color:#44ff88; font-size:13px; font-weight:900; '
                f'letter-spacing:1px;">✓ {title}</span>'
                f'<span style="color:#666; font-size:11px; margin-left:4px;">{desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        elif prog is not None and prog > 0:
            # In progress
            pct = min(max(prog, 0), 1.0)
            bar_w = int(pct * 100)
            st.markdown(
                f'<div style="padding:8px 12px; background:#0a0a18; border:1px solid #1D428A; '
                f'border-radius:6px; margin:3px 0;">'
                f'<div style="display:flex; align-items:center; gap:10px; margin-bottom:5px;">'
                f'<span style="font-size:22px; opacity:0.7;">{trophy}</span>'
                f'<span style="color:#FDB927; font-size:13px; font-weight:700;">{title}</span>'
                f'<span style="color:#555; font-size:11px;">{desc}</span>'
                f'</div>'
                f'<div style="background:#111; border-radius:4px; height:6px;">'
                f'<div style="background:linear-gradient(90deg,#1D428A,#FDB927); '
                f'width:{bar_w}%; height:6px; border-radius:4px;"></div></div>'
                f'<div style="color:#555; font-size:10px; margin-top:3px; text-align:right;">'
                f'{pct:.1%} complete</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            # Not started / locked
            st.markdown(
                f'<div style="display:flex; align-items:center; gap:10px; padding:6px 12px; '
                f'background:#070707; border:1px solid #1a1a1a; border-radius:6px; margin:3px 0; opacity:0.5;">'
                f'<span style="font-size:22px; filter:grayscale(1);">{trophy}</span>'
                f'<span style="color:#333; font-size:12px; font-weight:700;">🔒 {title}</span>'
                f'<span style="color:#222; font-size:11px;">{desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )




# ─── Bot status helpers ───────────────────────────────────────────────────────

_PLIST = os.path.expanduser('~/Library/LaunchAgents/com.algotrading.king.plist')


def _bot_is_running() -> bool:
    """True if main.py process is alive. Falls back to last-scan-within-2-min check."""
    import subprocess
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'main.py'],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0
    except Exception:
        pass
    # Fallback: check last scan feed entry timestamp
    try:
        entries = get_scan_feed(limit=1)
        if entries:
            last_ts = entries[0].get('ts', '')
            dt = datetime.fromisoformat(last_ts)
            import pytz as _ptz
            from datetime import timezone as _utc
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_utc.utc)
            age = (datetime.now(_utc.utc) - dt).total_seconds()
            return age < 120  # running if scan within 2 min
    except Exception:
        pass
    return False


def _start_bot() -> None:
    import subprocess, time as _t
    try:
        # Kill any existing main.py — give Python 3.14 time to release .pyc file locks
        # (launchd kickstart causes EDEADLK because it restarts before locks clear)
        subprocess.run(['pkill', '-SIGTERM', '-f', 'main.py'], capture_output=True)
        _t.sleep(6)  # Python 3.14 needs ~6s to fully release importlib file locks

        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        python = '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3'
        main_py = os.path.join(project_dir, 'main.py')
        log_path = os.path.join(project_dir, 'logs', 'service', 'bot.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'   # no .pyc writes → no lock contention
        env['TQDM_DISABLE'] = '1'
        env['TOKENIZERS_PARALLELISM'] = 'false'

        with open(log_path, 'a') as logf:
            subprocess.Popen(
                [python, main_py, '--mode', 'paper'],
                stdout=logf, stderr=logf,
                cwd=project_dir,
                env=env,
                start_new_session=True,  # detach from Streamlit's process group
            )
    except Exception:
        pass


def _stop_bot() -> None:
    import subprocess
    try:
        subprocess.run(['pkill', '-SIGTERM', '-f', 'main.py'], capture_output=True)
    except Exception:
        pass


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # Single unified view — no tab switcher.
    # THE KING view uses @st.fragment(run_every=3) for live stat hub.

    # Status bar + bot control (top)
    c1, c2 = st.columns([3, 1.4])
    with c1:
        mkt = "🟢 MARKET OPEN" if is_market_open() else "🔴 CLOSED"
        if is_in_no_trade_window():
            mkt = "🟡 NO-TRADE WINDOW"
        st.caption(f"{mkt} | {et_now()}")
    with c2:
        _running = _bot_is_running()
        if _running:
            st.markdown(
                '<div style="text-align:center; padding:2px 0 1px 0;">'
                '<span style="background:#0d2b0d; color:#44ff88; font-size:11px; font-weight:900; '
                'padding:3px 8px; border-radius:4px; border:1px solid #44ff88; '
                'letter-spacing:1px;">● SCANNING</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("⏹ Stop Bot", key="_bot_stop", use_container_width=True):
                _stop_bot()
                st.rerun()
        else:
            st.markdown(
                '<div style="text-align:center; padding:2px 0 1px 0;">'
                '<span style="background:#2b0d0d; color:#ff4444; font-size:11px; font-weight:900; '
                'padding:3px 8px; border-radius:4px; border:1px solid #ff4444; '
                'letter-spacing:1px;">● STOPPED</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("▶ Start Bot", key="_bot_start", use_container_width=True):
                _start_bot()
                st.rerun()

    render_king()


if __name__ == '__main__':
    main()
