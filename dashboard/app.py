"""
dashboard/app.py — The King's War Room
4 views: THE KING | SAIYAN MODE | FILM ROOM | RING CEREMONY
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

from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    CRYPTO_PAIRS, COINBASE_TAKER_FEE_PCT, ANTHROPIC_API_KEY,
    FUTURES_ENABLED, CLAUDE_MODEL
)
from logging_db.trade_logger import (
    get_todays_trades, get_todays_signals, get_todays_pnl, get_todays_fees,
    get_daily_trade_count, get_all_time_stats, get_recent_debates,
    get_monthly_api_cost, get_win_rate, get_recent_trades, get_recent_events,
    get_recent_notifications, get_today_stats,
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

DBZ_AGENTS = {
    'buffett': 'Master Roshi', 'soros': 'Cell', 'simons': 'Android 17',
    'tudor_jones': 'Vegeta', 'druckenmiller': 'Piccolo',
    'cathie_wood': 'Bulma', 'livermore': 'Goku', 'dalio': 'Whis',
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
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%m/%d/%Y %H:%M:%S ET')


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
        f"  {t.get('ts','')[-19:-4]} | {t.get('action','')} {t.get('symbol','')} "
        f"qty={t.get('qty',0):.6f} @ ${t.get('price',0):,.4f} | P&L=${t.get('pnl_usd',0):+.2f} | "
        f"strategy={t.get('strategy','')} | notes={t.get('notes','')}"
        for t in trades
    ) or '  None yet today'

    # Recent signals
    sig_lines = '\n'.join(
        f"  {s.get('ts','')[-19:-4]} | {s.get('signal','')} {s.get('symbol','')} "
        f"conf={s.get('confidence',0):.0%} acted={bool(s.get('acted_on',0))} | {s.get('reason','')[:120]}"
        for s in signals[:15]
    ) or '  None yet'

    # Recent debates with full reasoning
    debate_lines = ''
    for d in debates:
        debate_lines += (
            f"  [{d.get('ts','')[-19:-4]}] {d.get('symbol','?')} → {d.get('final_signal','?')} "
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
        event_lines += f"  [{e.get('level','')}] {e.get('ts','')[-19:-4]} [{e.get('source','')}] {e.get('message','')}\n"
    for e in info_events[:5]:
        event_lines += f"  [INFO] {e.get('ts','')[-19:-4]} [{e.get('source','')}] {e.get('message','')}\n"
    if not event_lines:
        event_lines = '  No recent events\n'

    # All-time recent trade history for pattern analysis
    history_lines = '\n'.join(
        f"  {t.get('ts','')[-19:-4]} {t.get('action','')} {t.get('symbol','')} "
        f"P&L=${t.get('pnl_usd',0):+.2f} strategy={t.get('strategy','')}"
        for t in recent_trades
    ) or '  No trade history'

    return f"""You are Claude, the AI brain and co-pilot embedded inside this autonomous trading system.
You have FULL real-time access to the system's database — every trade, signal, debate, position, error, and cost.
Be direct. Be honest. Act like a trusted advisor who also knows this codebase inside and out.
If something looks wrong, broken, or suspicious — say so immediately and specifically.
Protect this account like it's your own money.

═══ LIVE SYSTEM STATE ({et_now()}) ═══
Mode: {'📄 PAPER TRADING' if PAPER_TRADING else '💰 LIVE TRADING'}
Account size: ${ACCOUNT_SIZE}
Today P&L: ${pnl:+.2f} ({pnl/ACCOUNT_SIZE*100:+.2f}% of account)
Today fees: ${fees:.4f}
System halted: {risk.get('halted', False)} {f"— REASON: {risk.get('halt_reason')}" if risk.get('halted') else ''}
Deployed capital: ${risk.get('deployed_usd', 0):.2f}
Equity trades today: {eq_t}/3 (PDT limit)
Crypto trades today: {cr_t}/10
Daily loss used: {abs(pnl)/ACCOUNT_SIZE*100:.2f}% / 5.00% halt threshold

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

═══ SYSTEM RULES (hardcoded, never overrideable) ═══
- 2% max account risk per trade (${ACCOUNT_SIZE*0.02:.2f} max loss per trade)
- 5% daily loss halt (${ACCOUNT_SIZE*0.05:.2f})
- 3 equity trades/day max (PDT cash account)
- 10 crypto trades/day max
- 2 max open positions per asset class
- No equity entries 9:30–10:00 ET
- Stop losses sacred — never widened after entry
- Never chase (skip if price moved >3% since signal)
- Never average down

═══ STRATEGIES ═══
Equity: 8-agent AI debate (5/8 must agree) | KST+MACD+VWAP signals | Finviz+Yahoo auto-screener
Crypto: 3-agent quick debate (Tudor Jones, Simons, Livermore) | 3-variant MACD | BTC-USDC, ETH-USDC
Exits: Extended thinking AI review (any 1 of 3 exit agents saying EXIT → we exit)
Memory: LanceDB vector store learns from every completed trade

═══ BROKERS ═══
Equity: Webull | Crypto: Coinbase (Advanced Trade API) | Futures: DISABLED (no App ID)"""


def render_chat_column(theme: str):
    st.subheader("🤖 Ask Claude")
    st.caption("Ask anything about your trades, positions, strategy, costs, or market conditions.")

    if 'chat' not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat[-10:]:
        cls = 'chat-user' if msg['role'] == 'user' else 'chat-bot'
        icon = '👤' if msg['role'] == 'user' else '👑'
        st.markdown(f'<div class="{cls}">{icon} {msg["content"]}</div>',
                    unsafe_allow_html=True)

    st.caption("Quick questions:")
    qc = st.columns(2)
    quick_qs = ["Why did it trade today?", "What's my win rate?",
                "Any risks right now?", "How are costs trending?"]
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
    """Write key=value pairs into .env."""
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
- Debate depth: quick (3 agents, ~$0.04/debate) vs full (8 agents, ~$0.12/debate)
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
    """
    Interactive cost lab: sliders → live estimate → apply button + AI advisor.
    theme: 'king' | 'saiyan' | 'filmroom' | 'ring'
    """
    accent = {'king': '#FDB927', 'saiyan': '#00ffff',
              'filmroom': '#ff8c00', 'ring': '#FFD700'}.get(theme, '#FDB927')

    st.subheader("💰 Cost Lab")
    st.caption("Tune your AI spend. Changes write to .env and take effect on next bot restart.")

    monthly_cost = get_monthly_api_cost()
    st.metric("Actual spend this month", f"${monthly_cost:.4f}")
    st.divider()

    # ── Sliders ───────────────────────────────────────────────────────────────
    st.markdown("**⚙️ Parameters**")

    debate_depth = st.radio(
        "Debate depth",
        ["quick (3 agents — saves cost)", "full (8 agents — best signals)"],
        index=0,
        key=f"cl_depth_{theme}",
        horizontal=True,
    )
    depth_val = 'quick' if debate_depth.startswith('quick') else 'full'

    col1, col2 = st.columns(2)
    with col1:
        debate_tokens = st.slider(
            "Debate max tokens", 100, 600, 300, step=50,
            key=f"cl_dtok_{theme}",
            help="Tokens per agent per debate. Lower = cheaper, less reasoning depth."
        )
        crypto_trades = st.slider(
            "Max crypto trades/day", 1, 20, 10, step=1,
            key=f"cl_ctrades_{theme}",
            help="Caps daily crypto debates."
        )
    with col2:
        exit_tokens = st.slider(
            "Exit review max tokens", 200, 1200, 800, step=100,
            key=f"cl_etok_{theme}",
            help="Tokens for extended thinking exit reviews. Higher = smarter exits."
        )
        crypto_interval = st.slider(
            "Crypto scan interval (min)", 1, 15, 5, step=1,
            key=f"cl_cint_{theme}",
            help="How often crypto is scanned. More frequent = more potential debates."
        )

    equity_trades = st.slider(
        "Max equity trades/day", 1, 3, 3, step=1,
        key=f"cl_etrades_{theme}",
        help="PDT rule caps this at 3 on a cash account."
    )

    # ── Live estimate ─────────────────────────────────────────────────────────
    est = _est_monthly_cost(depth_val, debate_tokens, exit_tokens,
                            crypto_trades, equity_trades, crypto_interval)

    delta_pct = ((est - monthly_cost) / monthly_cost * 100) if monthly_cost > 0 else 0
    col_a, col_b = st.columns(2)
    col_a.metric("Estimated monthly cost", f"${est:.2f}",
                 delta=f"{delta_pct:+.1f}% vs actual" if monthly_cost > 0 else None)
    col_b.metric("Est. annual cost", f"${est * 12:.2f}")

    # Cost breakdown bar
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


# ─── VIEW RENDERERS ───────────────────────────────────────────────────────────

def render_king():
    st.markdown(f"<style>{THEME_CSS['king']}</style>", unsafe_allow_html=True)

    quote, _ = get_quote_for_hour()

    st.markdown(f"""
    <div class="king-header">
        <div style="text-align:center; color:#FDB927; font-size:28px; font-weight:900; letter-spacing:4px;">
            👑 THE KING'S WAR ROOM 👑
        </div>
        <div class="quote-box" style="margin-top:10px;">"{quote}" — LeBron James</div>
    </div>
    """, unsafe_allow_html=True)

    rm = get_risk_manager()
    pnl = get_todays_pnl(paper=PAPER_TRADING)
    fees = get_todays_fees(paper=PAPER_TRADING)
    trades = get_todays_trades(paper=PAPER_TRADING)
    risk = rm.status_report()

    if PAPER_TRADING:
        st.markdown(f'<div class="paper-banner">📄 PAPER TRAINING — {LEBRON_MESSAGES["paper"]}</div>',
                    unsafe_allow_html=True)
    if rm.is_halted:
        st.markdown(f'<div class="halt-banner">🚨 {LEBRON_MESSAGES["halt"]}<br>{rm.halt_reason}</div>',
                    unsafe_allow_html=True)

    # Scoreboard P&L
    pnl_class = "scoreboard" if pnl >= 0 else "scoreboard scoreboard-neg"
    sign = "+" if pnl >= 0 else ""
    pnl_emoji = "🔥" if pnl > 20 else "📈" if pnl > 0 else "📉"
    st.markdown(f'<div class="{pnl_class}">{pnl_emoji} {sign}${pnl:.2f} {pnl_emoji}</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div style="text-align:center; color:#aaa; letter-spacing:4px; margin-bottom:20px;">TODAY\'S P&L — {et_now()}</div>',
                unsafe_allow_html=True)

    # Win flash animation if recent winning trade
    recent = get_recent_trades(limit=3, paper=PAPER_TRADING)
    if recent and recent[0].get('pnl_usd', 0) > 0:
        st.markdown(f"""
        <div class="win-flash" style="text-align:center; color:#FDB927; font-size:20px; 
             font-weight:900; padding:10px; background:#1D428A33; border-radius:8px; margin:10px 0;">
            🏆 {LEBRON_MESSAGES['win']} +${recent[0].get('pnl_usd',0):.2f} 🏆
        </div>""", unsafe_allow_html=True)

    # Top metrics
    stats = get_all_time_stats(paper=PAPER_TRADING)
    today_s = get_today_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + stats.get('total_pnl', 0)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Account", f"${real_balance:,.2f}",
              delta=f"{stats.get('total_pnl',0):+.2f} realized")
    c2.metric("📈 All-Time P&L", f"${stats.get('total_pnl',0):+.2f}",
              delta=f"{stats.get('win_rate',0):.1%} win rate ({stats.get('total',0)} trades)")
    c3.metric("🎯 Today Closed", f"{today_s['total']} trades",
              delta=f"{today_s['wins']}W / {today_s['losses']}L  {today_s['win_rate']:.0%}")
    c4.metric("📊 Today Net P&L", f"${today_s['net_pnl']:+.2f}",
              delta=f"gross {today_s['gross_pnl']:+.2f}  fees −${today_s['fees']:.3f}")
    c5.metric("💸 Fees Today", f"${fees:.4f}")

    st.divider()

    left, mid, right = st.columns([1.3, 1, 1.2])

    with left:
        st.subheader("⚡ Positions")
        pos = rm.get_all_positions()
        eq_pos = pos.get('equity', {})
        cr_pos = pos.get('crypto', {})

        if not eq_pos and not cr_pos:
            st.markdown('<div style="color:#666; text-align:center; padding:20px;">No open positions<br>👑 Patience pays</div>',
                        unsafe_allow_html=True)
        else:
            if eq_pos:
                st.caption("**EQUITY**")
                rows = [{'Symbol': s, 'Qty': p['qty'], 'Entry': f"${p['entry']:.2f}",
                          'Stop': f"${p['stop']:.2f}", 'Target': f"${p['target']:.2f}"}
                         for s, p in eq_pos.items()]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if cr_pos:
                st.caption("**CRYPTO**")
                rows = [{'Pair': s, 'Qty': f"{p['qty']:.6f}", 'Entry': f"${p['entry']:,.2f}",
                          'Stop': f"${p['stop']:,.4f}", 'Target': f"${p['target']:,.4f}"}
                         for s, p in cr_pos.items()]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📋 Today's Trades")
        if trades:
            df_t = pd.DataFrame(trades)
            cols = [c for c in ['ts','symbol','action','qty','price','pnl_usd','strategy'] if c in df_t.columns]
            st.dataframe(df_t[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No trades yet today. Waiting for the right setup. 👑")

        st.divider()
        st.subheader("🔔 Notifications")
        notifs = get_recent_notifications(limit=25)
        if notifs:
            for n in notifs[:20]:
                level = n.get('level', 'INFO')
                msg   = n.get('message', '')
                ts    = n.get('ts', '')

                # Subject is before first " | ", body is everything after
                parts   = msg.split(' | ', 1)
                subject = parts[0]
                body    = parts[1] if len(parts) > 1 else ''

                # Icon + colour by content
                if 'HALT' in subject or level == 'ERROR':
                    icon, color = '🚨', '#ff4444'
                elif 'WIN' in subject:
                    icon, color = '✅', '#44ff88'
                elif 'LOSS' in subject or level == 'WARNING':
                    icon, color = '❌', '#ff8844'
                elif 'BUY' in subject or 'OPENED' in subject:
                    icon, color = '💰', '#FDB927'
                elif 'CLOSED' in subject:
                    icon, color = '🏁', '#FDB927'
                elif 'SIGNAL' in subject:
                    icon, color = '📡', '#aaa'
                elif 'READY' in subject:
                    icon, color = '🏆', '#FDB927'
                else:
                    icon, color = 'ℹ️', '#666'

                time_str = ts[5:19] if len(ts) >= 19 else ts  # MM-DD HH:MM:SS
                st.markdown(
                    f'<div style="background:#111; border-left:3px solid {color}; '
                    f'padding:8px 12px; margin:3px 0; border-radius:0 6px 6px 0;">'
                    f'<span style="color:{color}; font-weight:700; font-size:13px;">'
                    f'{icon} {subject}</span>'
                    f'<span style="color:#555; font-size:11px; float:right;">{time_str}</span>'
                    f'<br><span style="color:#888; font-size:11px;">{body[:110]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No notifications yet. System is watching. 👑")

    with mid:
        st.subheader("🏀 Live Signals")
        sigs = get_todays_signals()
        if sigs:
            for s in sigs[:12]:
                act = s.get('signal', 'HOLD')
                e = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(act, '⚪')
                ts = s.get('ts', '')[-8:-3]
                st.markdown(
                    f'<span class="signal-{"buy" if act=="BUY" else "sell" if act=="SELL" else "hold"}">'
                    f'`{ts}` {e} **{act}** `{s.get("symbol","")}` @ ${s.get("price",0):,.2f} '
                    f'— {s.get("confidence",0):.0%}</span>', unsafe_allow_html=True)
                if s.get('reason'):
                    st.caption(f"  ↳ {s.get('reason','')[:55]}")
        else:
            st.info("Scanning the market... 👀")

        st.divider()
        st.subheader("🛡️ Risk Status")
        daily_loss_pct = abs(pnl) / ACCOUNT_SIZE * 100 if pnl < 0 else 0
        st.progress(min(daily_loss_pct / 5, 1.0),
                    text=f"Daily loss: {daily_loss_pct:.2f}% / 5% halt limit")
        eq_t = get_daily_trade_count('equity_momentum', PAPER_TRADING)
        st.progress(min(eq_t / 3, 1.0), text=f"Equity trades: {eq_t}/3 (PDT)")
        cr_t = get_daily_trade_count('crypto_macd_consensus', PAPER_TRADING)
        st.progress(min(cr_t / 10, 1.0), text=f"Crypto trades: {cr_t}/10")

        st.divider()
        render_cost_lab(theme='king')

    with right:
        render_chat_column('king')


def render_saiyan():
    st.markdown(f"<style>{THEME_CSS['saiyan']}</style>", unsafe_allow_html=True)

    rm = get_risk_manager()
    pnl = get_todays_pnl(paper=PAPER_TRADING)
    trades = get_todays_trades(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)
    win_rate = stats.get('win_rate', 0)
    power_level = int(abs(pnl) * 100 + len(trades) * 500 + win_rate * 5000 + 1337)

    st.markdown("""
    <div class="saiyan-header">
        <div style="text-align:center; color:#00ffff; font-size:24px; font-weight:900;
             letter-spacing:6px; font-family:monospace;">
            ⚡ SAIYAN TRADING SYSTEM ⚡
        </div>
    </div>""", unsafe_allow_html=True)

    over9k = power_level > 9000
    if over9k:
        st.markdown(f'<div class="over9k" style="text-align:center;">⚡ ITS POWER LEVEL IS OVER 9,000!!! ⚡</div>',
                    unsafe_allow_html=True)

    st.markdown(f'<div class="power-level">POWER LEVEL: {power_level:,}</div>', unsafe_allow_html=True)
    sign = "+" if pnl >= 0 else ""
    st.markdown(f'<div style="text-align:center; color:#00ffff; font-size:18px; font-family:monospace; margin-bottom:20px;">KI BALANCE: {sign}${pnl:.2f} TODAY</div>',
                unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔋 Account", f"${ACCOUNT_SIZE:,.0f}")
    c2.metric("⚡ Win Rate", f"{win_rate:.1%}")
    c3.metric("🐉 Total Trades", stats.get('total', 0))
    c4.metric("💥 Best Trade", f"${stats.get('best_trade',0):+.2f}")

    st.divider()

    left, right = st.columns([1.5, 1])

    with left:
        st.subheader("🐉 Z-Fighter Debate Log (Recent)")
        debates = get_recent_debates(limit=5)
        if debates:
            for d in debates:
                buy_v = d.get('buy_votes', 0)
                hold_v = d.get('hold_votes', 0)
                sell_v = d.get('sell_votes', 0)
                total_v = buy_v + hold_v + sell_v
                conf = d.get('confidence', 0)

                conf_level = int(conf * 10000) + random.randint(100, 999)
                signal = d.get('final_signal', 'HOLD')
                color = '#44ff88' if signal == 'BUY' else '#ff4444' if signal == 'SELL' else '#888'

                st.markdown(f"""
                <div class="saiyan-border" style="border-color:{color};">
                    <div style="color:#00ffff; font-family:monospace; font-size:11px;">
                        {d.get('ts','')[-19:-9]} — {d.get('symbol','?')}
                    </div>
                    <div style="color:{color}; font-weight:900; font-size:16px;">
                        {signal} | POWER LEVEL: {conf_level:,}
                    </div>
                    <div style="color:#888; font-size:12px;">
                        Z-Fighters: {buy_v} attack | {hold_v} defend | {sell_v} retreat
                    </div>
                    <div style="color:#aaa; font-size:11px; margin-top:4px;">
                        {d.get('reasoning','')[:80]}
                    </div>
                </div>""", unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#888; font-family:monospace; text-align:center; padding:20px;">Z-Fighters standing by... training mode active</div>',
                        unsafe_allow_html=True)

        st.divider()
        st.subheader("💥 Ki Energy Gauges")
        daily_loss_pct = abs(pnl) / ACCOUNT_SIZE * 100 if pnl < 0 else 0
        st.progress(min(daily_loss_pct / 5, 1.0), text=f"⚡ KI DRAIN: {daily_loss_pct:.2f}% / 5%")

        pos = rm.get_all_positions()
        eq_count = len(pos.get('equity', {}))
        cr_count = len(pos.get('crypto', {}))
        st.progress(eq_count / 2, text=f"🔥 EQUITY KI: {eq_count}/2 positions charged")
        st.progress(cr_count / 2, text=f"⚡ CRYPTO KI: {cr_count}/2 positions charged")

    with right:
        render_chat_column('saiyan')


def render_filmroom():
    st.markdown(f"<style>{THEME_CSS['filmroom']}</style>", unsafe_allow_html=True)

    st.markdown("""
    <div class="chalk-header">
        <div style="color:#ff8c00; font-family:'Courier New'; font-size:20px; font-weight:bold;">
            🎬 FILM ROOM — Breaking Down Every Play
        </div>
        <div style="color:#888; font-size:12px; font-family:monospace;">
            Full debate reasoning | Extended thinking chains | No animations. Pure signal.
        </div>
    </div>""", unsafe_allow_html=True)

    rm = get_risk_manager()
    pnl = get_todays_pnl(paper=PAPER_TRADING)
    trades = get_todays_trades(paper=PAPER_TRADING)
    stats = get_all_time_stats(paper=PAPER_TRADING)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("P&L Today", f"${pnl:+.2f}")
    c2.metric("Win Rate", f"{stats.get('win_rate',0):.1%}")
    c3.metric("Total Trades", stats.get('total', 0))
    c4.metric("Deployed", f"${rm._get_deployed():.2f}")

    st.divider()
    left, right = st.columns([1.5, 1])

    with left:
        st.subheader("🎬 Full Debate Transcripts")
        debates = get_recent_debates(limit=8)
        if debates:
            for d in debates:
                signal = d.get('final_signal', 'HOLD')
                border = 'buy' if signal == 'BUY' else 'sell' if signal == 'SELL' else ''
                buy_v = d.get('buy_votes', 0)
                hold_v = d.get('hold_votes', 0)
                sell_v = d.get('sell_votes', 0)

                with st.expander(
                    f"[{d.get('ts','')[-19:-9]}] {d.get('symbol','?')} → {signal} "
                    f"({buy_v}B/{hold_v}H/{sell_v}S | {d.get('confidence',0):.0%} conf) | {d.get('regime','?')}"
                ):
                    st.markdown(f"""
                    <div class="agent-card {border}-card">
                        <b>SYNTHESIS:</b> {d.get('reasoning','')}<br>
                        <b>BULL CASE:</b> {d.get('bull_case','')}<br>
                        <b>BEAR CASE:</b> {d.get('bear_case','')}<br>
                        <b>KEY RISK:</b> {d.get('key_risk','')}
                    </div>""", unsafe_allow_html=True)

                    try:
                        agents = json.loads(d.get('agent_details', '[]'))
                        if agents:
                            st.caption("Individual agent votes:")
                            for ag in agents:
                                ag_signal = ag.get('signal', 'HOLD')
                                ac = 'buy' if ag_signal == 'BUY' else 'sell' if ag_signal == 'SELL' else 'hold'
                                st.markdown(
                                    f'<div class="agent-card {ac}-card" style="font-size:12px;">'
                                    f'<b>{ag.get("agent","?")}</b>: {ag_signal} ({ag.get("confidence",0):.0%}) '
                                    f'— {ag.get("reasoning","")}<br>'
                                    f'<span style="color:#888">Risk: {ag.get("key_concern","")}</span></div>',
                                    unsafe_allow_html=True)
                    except Exception:
                        pass
        else:
            st.info("No debates recorded yet. System is warming up.")

        st.divider()
        st.subheader("📊 Today's Trades")
        if trades:
            st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)

    with right:
        st.subheader("📈 Performance Breakdown")
        all_trades = get_recent_trades(limit=50, paper=PAPER_TRADING)
        if all_trades:
            wins = [t for t in all_trades if t.get('pnl_usd', 0) > 0 and t.get('action') == 'SELL']
            losses = [t for t in all_trades if t.get('pnl_usd', 0) < 0 and t.get('action') == 'SELL']
            st.metric("Wins (last 50)", len(wins))
            st.metric("Losses (last 50)", len(losses))
            if wins:
                avg_win = sum(t['pnl_usd'] for t in wins) / len(wins)
                st.metric("Avg Win", f"${avg_win:+.2f}")
            if losses:
                avg_loss = sum(t['pnl_usd'] for t in losses) / len(losses)
                st.metric("Avg Loss", f"${avg_loss:+.2f}")

        st.divider()
        render_chat_column('filmroom')


def render_ring():
    st.markdown(f"<style>{THEME_CSS['ring']}</style>", unsafe_allow_html=True)

    stats = get_all_time_stats(paper=PAPER_TRADING)
    pnl_all = stats.get('total_pnl', 0)
    win_rate = stats.get('win_rate', 0)
    total_trades = stats.get('total', 0)
    wins = stats.get('wins', 0)

    # Determine milestones unlocked
    milestones = []
    if total_trades >= 1:
        milestones.append(("🥇", "FIRST TRADE", "The journey begins."))
    if pnl_all > 0:
        milestones.append(("💰", "FIRST PROFIT", "The system works."))
    if win_rate >= 0.55:
        milestones.append(("🎯", "55% WIN RATE", "Consistent edge."))
    if pnl_all >= ACCOUNT_SIZE * 0.10:
        milestones.append(("🏆", "10% GAIN", "First ring."))
    if pnl_all >= ACCOUNT_SIZE * 0.50:
        milestones.append(("👑", "50% GAIN", "The King."))

    st.markdown("""
    <div class="trophy-header">
        <div style="text-align:center; color:#FFD700; font-size:26px; font-weight:900; letter-spacing:4px;">
            🏆 THE RING CEREMONY 🏆
        </div>
        <div style="text-align:center; color:#888; font-size:13px; margin-top:6px;">
            "Nothing is given. Everything is earned." — LeBron James
        </div>
    </div>""", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👑 Total P&L", f"${pnl_all:+.2f}")
    c2.metric("🎯 Win Rate", f"{win_rate:.1%}")
    c3.metric("🏀 Total Trades", total_trades)
    c4.metric("🏆 Wins", wins)

    st.divider()
    st.subheader("🏅 Milestones Earned")

    if not milestones:
        st.markdown('<div style="text-align:center; color:#888; padding:40px; font-size:16px;">No milestones yet. The grind is just beginning. Keep working. 👑</div>',
                    unsafe_allow_html=True)
    else:
        cols = st.columns(min(len(milestones), 4))
        for i, (trophy, title, desc) in enumerate(milestones):
            with cols[i % len(cols)]:
                st.markdown(f"""
                <div style="text-align:center; padding:20px; background:#1a1200;
                     border:2px solid #FFD700; border-radius:12px; margin:8px 0;">
                    <div style="font-size:40px;">{trophy}</div>
                    <div style="color:#FFD700; font-weight:900; font-size:14px; letter-spacing:2px;">{title}</div>
                    <div style="color:#888; font-size:11px; margin-top:4px;">{desc}</div>
                </div>""", unsafe_allow_html=True)

    st.divider()
    left, right = st.columns([1, 1])

    with left:
        st.subheader("📊 Championship Stats")
        st.metric("Best Single Trade", f"${stats.get('best_trade',0):+.2f}")
        st.metric("Worst Single Trade", f"${stats.get('worst_trade',0):+.2f}")
        monthly_cost = get_monthly_api_cost()
        st.metric("Claude API Spend (month)", f"${monthly_cost:.4f}")
        st.caption('"Every champion was once a contender that refused to give up." — Rocky (for the culture)')

    with right:
        render_chat_column('ring')


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if 'view' not in st.session_state:
        st.session_state.view = 'king'

    # View switcher
    c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
    with c1:
        mkt = "🟢 MARKET OPEN" if is_market_open() else "🔴 CLOSED"
        if is_in_no_trade_window():
            mkt = "🟡 NO-TRADE WINDOW"
        st.caption(f"{mkt} | {et_now()}")
    with c2:
        if st.button("👑 THE KING", use_container_width=True,
                     type='primary' if st.session_state.view == 'king' else 'secondary'):
            st.session_state.view = 'king'; st.rerun()
    with c3:
        if st.button("⚡ SAIYAN", use_container_width=True,
                     type='primary' if st.session_state.view == 'saiyan' else 'secondary'):
            st.session_state.view = 'saiyan'; st.rerun()
    with c4:
        if st.button("🎬 FILM ROOM", use_container_width=True,
                     type='primary' if st.session_state.view == 'filmroom' else 'secondary'):
            st.session_state.view = 'filmroom'; st.rerun()
    with c5:
        if st.button("🏆 RING", use_container_width=True,
                     type='primary' if st.session_state.view == 'ring' else 'secondary'):
            st.session_state.view = 'ring'; st.rerun()

    view = st.session_state.view
    if view == 'king':
        render_king()
    elif view == 'saiyan':
        render_saiyan()
    elif view == 'filmroom':
        render_filmroom()
    elif view == 'ring':
        render_ring()


if __name__ == '__main__':
    main()
