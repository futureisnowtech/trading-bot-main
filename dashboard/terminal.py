"""
dashboard/terminal.py — Multi-column live terminal dashboard.
Renders system state using ANSI colours + box-drawing characters.
Designed for ≥ 220-column terminals.

Used by scheduler/job_runner.py (called every 5 s in the main loop).
Run standalone for a demo:
    python3 dashboard/terminal.py
"""
import os
import re
import sys
import time
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE,
    MAX_DAILY_LOSS_PCT, MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
    EQUITY_SCAN_INTERVAL_SECONDS, CRYPTO_SCAN_INTERVAL_SECONDS,
)

# ── ANSI codes ────────────────────────────────────────────────────────────────
GRN  = '\033[92m'
RED  = '\033[91m'
YLW  = '\033[93m'
CYN  = '\033[96m'
MAG  = '\033[95m'
WHT  = '\033[97m'
DIM  = '\033[2m'
BOLD = '\033[1m'
RST  = '\033[0m'
GOLD = '\033[38;5;220m'   # 256-colour gold

# ── Layout ────────────────────────────────────────────────────────────────────
W  = 220   # total terminal width (including borders)
LW = 108   # left panel inner content width
RW = W - LW - 3   # right panel inner content width  (= 109)

# ── String helpers ─────────────────────────────────────────────────────────────

def _vlen(s: str) -> int:
    """Visible length of a string (strips ANSI escape codes)."""
    return len(re.sub(r'\033\[[0-9;]*m', '', s))


def _pad(s: str, w: int) -> str:
    """Pad/clip a (possibly ANSI-coloured) string to exactly w visible chars."""
    vl = _vlen(s)
    if vl >= w:
        # Clip raw bytes; add reset to be safe
        return s[:w] + RST
    return s + ' ' * (w - vl)


def _cell(text: str, width: int, align: str = 'left') -> str:
    vl = _vlen(text)
    pad = max(0, width - vl)
    if align == 'right':
        return ' ' * pad + text
    if align == 'center':
        lpad = pad // 2
        return ' ' * lpad + text + ' ' * (pad - lpad)
    return text + ' ' * pad


# ── Box-drawing primitives ────────────────────────────────────────────────────

def _split_open() -> str:
    """Transition from full-width row into two-column section."""
    return '├' + '─' * LW + '┬' + '─' * RW + '┤'

def _bottom() -> str:
    return '└' + '─' * LW + '┴' + '─' * RW + '┘'

def _mid() -> str:
    return '├' + '─' * LW + '┼' + '─' * RW + '┤'

def _full_top() -> str:
    return '┌' + '─' * (W - 2) + '┐'

def _full_bot() -> str:
    return '└' + '─' * (W - 2) + '┘'

def _full_mid() -> str:
    return '├' + '─' * (W - 2) + '┤'

def _row(left: str, right: str) -> str:
    return '│' + _pad(left, LW) + '│' + _pad(right, RW) + '│'


def _ts(raw: str) -> str:
    """Extract HH:MM:SS from any ISO timestamp."""
    try:
        return raw.split('T')[1][:8] if 'T' in raw else raw[-8:]
    except Exception:
        return raw[-8:] if len(raw) >= 8 else raw

def _full_row(content: str) -> str:
    return '│' + _pad(content, W - 2) + '│'


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_data() -> dict:
    from logging_db.trade_logger import (
        get_todays_pnl, get_todays_fees, get_todays_trades, get_todays_signals,
        get_all_time_stats, get_today_stats, get_recent_debates, get_recent_events,
        get_daily_trade_count, get_monthly_api_cost, get_recent_trades,
    )
    from risk.risk_manager import get_risk_manager

    rm   = get_risk_manager()
    risk = rm.status_report()
    pos  = rm.get_all_positions()

    at   = get_all_time_stats(paper=PAPER_TRADING)
    td   = get_today_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + at.get('total_pnl', 0)

    return {
        'paper':        PAPER_TRADING,
        'balance':      real_balance,
        'pnl_today':    get_todays_pnl(paper=PAPER_TRADING),
        'fees_today':   get_todays_fees(paper=PAPER_TRADING),
        'halted':       risk.get('halted', False),
        'halt_reason':  risk.get('halt_reason', ''),
        'deployed_usd': risk.get('deployed_usd', 0),
        'eq_positions': pos.get('equity', {}),
        'cr_positions': pos.get('crypto', {}),
        'eq_trades_today': get_daily_trade_count('equity_momentum', PAPER_TRADING),
        'cr_trades_today': get_daily_trade_count('crypto_macd_consensus', PAPER_TRADING),
        'all_time':     at,
        'today':        td,
        'recent_trades': get_recent_trades(limit=5, paper=PAPER_TRADING),
        'signals':      get_todays_signals()[:5],
        'last_debate':  (get_recent_debates(limit=1) or [None])[0],
        'events':       get_recent_events(limit=6),
        'api_cost':     get_monthly_api_cost(),
        'now':          datetime.now(pytz.timezone(MARKET_TIMEZONE)),
    }


# ── Top bar ───────────────────────────────────────────────────────────────────

def _top_bar(d: dict) -> list:
    mode   = f"{GOLD}👑 PAPER{RST}" if d['paper'] else f"{YLW}💰 LIVE{RST}"
    bal    = f"{BOLD}${d['balance']:,.2f}{RST}"
    at     = d['all_time']
    pnl_c  = GRN if d['pnl_today'] >= 0 else RED
    apnl_c = GRN if at['total_pnl'] >= 0 else RED
    pnl    = f"{pnl_c}{d['pnl_today']:+.2f}{RST}"
    apnl   = f"{apnl_c}{at['total_pnl']:+.2f}{RST}"
    loss_pct = abs(d['pnl_today']) / max(d['balance'], 1) * 100 if d['pnl_today'] < 0 else 0
    loss_bar = f"{RED}{loss_pct:.1f}%{RST}/{MAX_DAILY_LOSS_PCT*100:.0f}%"
    eq_rem = MAX_TRADES_PER_DAY_EQUITY - d['eq_trades_today']
    cr_rem = MAX_TRADES_PER_DAY_CRYPTO - d['cr_trades_today']
    status = f"{RED}🚨 HALTED{RST}" if d['halted'] else f"{GRN}✅ RUNNING{RST}"
    ts     = d['now'].strftime('%H:%M:%S ET')

    parts = [
        f" {mode}",
        f"Acct: {bal}",
        f"Today: {pnl}",
        f"All-time: {apnl}",
        f"Fees: ${d['fees_today']:.3f}",
        f"Loss: {loss_bar}",
        f"EQ: {d['eq_trades_today']}/{MAX_TRADES_PER_DAY_EQUITY} ({eq_rem} left)",
        f"CR: {d['cr_trades_today']}/{MAX_TRADES_PER_DAY_CRYPTO} ({cr_rem} left)",
        status,
        f"{DIM}{ts}{RST} ",
    ]
    content = f"  {'  │  '.join(parts)}"
    return [_full_top(), _full_row(content), _full_mid()]


# ── Row 2: Positions | Stats ──────────────────────────────────────────────────

def _positions_panel(d: dict, n_rows: int) -> list:
    lines = [f" {BOLD}{GOLD}⚡ OPEN POSITIONS{RST}"]
    rows = []
    for sym, p in d['eq_positions'].items():
        pnl_est = ''
        direction = p.get('direction', 'LONG')
        rows.append(
            f" {GRN}EQ{RST} {BOLD}{sym:<8}{RST} "
            f"qty={p['qty']:>6}  "
            f"entry=${p['entry']:>8.2f}  "
            f"SL=${p['stop']:>8.2f}  "
            f"TP=${p['target']:>8.2f}  "
            f"{DIM}{direction}{RST}"
        )
    for sym, p in d['cr_positions'].items():
        direction = p.get('direction', 'LONG')
        dir_c = GRN if direction == 'LONG' else RED
        rows.append(
            f" {CYN}CR{RST} {BOLD}{sym:<12}{RST} "
            f"{p['qty']:.6f}  "
            f"@ ${p['entry']:>10,.2f}  "
            f"SL ${p['stop']:>10,.2f}  "
            f"{dir_c}{direction}{RST}"
        )
    if not rows:
        rows = [f" {DIM}No open positions — patience pays 👑{RST}"]
    for r in rows[:n_rows - 1]:
        lines.append(r)
    # Pad to n_rows
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


def _stats_panel(d: dict, n_rows: int) -> list:
    td = d['today']
    at = d['all_time']
    wr_c  = lambda r: GRN if r >= 0.5 else (YLW if r >= 0.4 else RED)
    pnl_c = lambda p: GRN if p >= 0 else RED

    def stat_row(label, t, w, l, wr, gross, fees, net):
        wr_col = wr_c(wr)
        net_col = pnl_c(net)
        return (
            f" {BOLD}{label:<10}{RST}"
            f"Trades:{t:>4}  "
            f"{GRN}{w}W{RST}/{RED}{l}L{RST}  "
            f"{wr_col}{wr:.0%}{RST}  "
            f"Gross:{pnl_c(gross)}{gross:>+7.2f}{RST}  "
            f"Fees:{RED}-${fees:.3f}{RST}  "
            f"Net:{net_col}{net:>+7.2f}{RST}"
        )

    hdr  = f" {BOLD}{GOLD}📊 STATS{RST}"
    hdr2 = f"  {'TODAY':<10}{'All-Time':>20}"
    lines = [
        hdr,
        stat_row('TODAY',    td['total'], td['wins'], td['losses'], td['win_rate'],
                 td['gross_pnl'], td['fees'], td['net_pnl']),
        stat_row('ALL-TIME', at['total'], at['wins'], at['losses'], at['win_rate'],
                 at['total_pnl'], 0, at['total_pnl']),
        f" {DIM}Best trade: {pnl_c(at.get('best_trade',0))}{at.get('best_trade',0):>+7.2f}{RST}   "
        f"Worst: {pnl_c(at.get('worst_trade',0))}{at.get('worst_trade',0):>+7.2f}{RST}   "
        f"Deployed: ${d['deployed_usd']:,.2f}",
    ]
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


# ── Row 3: Recent trades | Recent signals ─────────────────────────────────────

def _trades_panel(d: dict, n_rows: int) -> list:
    lines = [f" {BOLD}📋 RECENT TRADES{RST}"]
    for t in d['recent_trades']:
        act    = t.get('action', '')
        pnl    = t.get('pnl_usd', 0)
        ts     = _ts(t.get('ts', ''))
        sym    = t.get('symbol', '')
        strat  = t.get('strategy', '')[:12]
        if act == 'SELL' or (act == 'BUY' and pnl != 0):
            icon = f"{GRN}✅ CLOSE{RST}" if pnl >= 0 else f"{RED}❌ CLOSE{RST}"
            pnl_s = f"{GRN if pnl>=0 else RED}{pnl:>+7.2f}{RST}"
        else:
            icon = f"{GRN}🟢 OPEN {RST}" if act == 'BUY' else f"{RED}🔴 OPEN {RST}"
            pnl_s = f"{DIM}  entry {RST}"
        lines.append(f" {DIM}{ts}{RST}  {icon}  {BOLD}{sym:<12}{RST}  {pnl_s}  {DIM}{strat}{RST}")
    if len(lines) == 1:
        lines.append(f" {DIM}No trades yet today{RST}")
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


def _signals_panel(d: dict, n_rows: int) -> list:
    lines = [f" {BOLD}📡 RECENT SIGNALS{RST}"]
    for s in d['signals']:
        sig  = s.get('signal', 'HOLD')
        sym  = s.get('symbol', '')
        conf = s.get('confidence', 0)
        ts   = _ts(s.get('ts', ''))
        rsn  = s.get('reason', '')[:55]
        acted = '✓' if s.get('acted_on') else ' '
        if sig == 'BUY':
            icon = f"{GRN}🟢 BUY {RST}"
        elif sig == 'SELL':
            icon = f"{RED}🔴 SELL{RST}"
        else:
            icon = f"{DIM}⚪ HOLD{RST}"
        conf_c = GRN if conf >= 0.7 else (YLW if conf >= 0.5 else DIM)
        lines.append(
            f" {DIM}{ts}{RST} {icon} {BOLD}{sym:<12}{RST} "
            f"{conf_c}{conf:.0%}{RST} {acted}  {DIM}{rsn}{RST}"
        )
    if len(lines) == 1:
        lines.append(f" {DIM}No signals yet today{RST}")
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


# ── Row 4: Last debate | System events ───────────────────────────────────────

def _debate_panel(d: dict, n_rows: int) -> list:
    lines = [f" {BOLD}🤖 LAST AI DEBATE{RST}"]
    db = d['last_debate']
    if db:
        sig    = db.get('final_signal', '?')
        sym    = db.get('symbol', '?')
        ts     = _ts(db.get('ts', ''))
        bv     = db.get('buy_votes', 0)
        hv     = db.get('hold_votes', 0)
        sv     = db.get('sell_votes', 0)
        conf   = db.get('confidence', 0)
        regime = db.get('regime', '')
        sig_c  = GRN if sig == 'BUY' else (RED if sig == 'SELL' else DIM)
        lines.append(
            f" {BOLD}{sym}{RST}  {DIM}{ts}{RST}  "
            f"{sig_c}{BOLD}{sig}{RST}  "
            f"{GRN}{bv}B{RST}/{DIM}{hv}H{RST}/{RED}{sv}S{RST}  "
            f"conf={GRN if conf>=0.6 else YLW}{conf:.0%}{RST}  "
            f"{DIM}{regime}{RST}"
        )
        for label, key in [('Bull', 'bull_case'), ('Bear', 'bear_case'), ('Risk', 'key_risk')]:
            val = (db.get(key) or '')[:90]
            if val:
                col = GRN if label == 'Bull' else (RED if label == 'Bear' else YLW)
                lines.append(f" {col}{label}:{RST} {DIM}{val}{RST}")
    else:
        lines.append(f" {DIM}No debates yet — waiting for first signal{RST}")
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


def _events_panel(d: dict, n_rows: int) -> list:
    lines = [f" {BOLD}📟 SYSTEM EVENTS{RST}"]
    for e in d['events']:
        lvl = e.get('level', 'INFO')
        src = e.get('source', '')[:14]
        msg = e.get('message', '')[:72]
        ts  = _ts(e.get('ts', ''))
        if lvl in ('ERROR', 'HALT'):
            icon = f"{RED}❌{RST}"
        elif lvl == 'WARNING':
            icon = f"{YLW}⚠ {RST}"
        else:
            icon = f"{DIM}ℹ {RST}"
        lines.append(f" {icon} {DIM}{ts}  {src:<14}{RST} {msg}")
    if len(lines) == 1:
        lines.append(f" {DIM}No events yet{RST}")
    while len(lines) < n_rows:
        lines.append('')
    return lines[:n_rows]


# ── Bottom bar ────────────────────────────────────────────────────────────────

def _bottom_bar(d: dict) -> list:
    ts   = d['now'].strftime('%Y-%m-%d %H:%M:%S ET')
    cost = d['api_cost']
    parts = [
        f"{DIM}{ts}{RST}",
        f"equity_momentum + crypto_macd_consensus",
        f"Claude API: ${cost:.4f}/mo",
        f"EQ scan: {EQUITY_SCAN_INTERVAL_SECONDS}s  CR scan: {CRYPTO_SCAN_INTERVAL_SECONDS}s",
        f"{DIM}Press Ctrl+C to stop{RST}",
    ]
    content = f"  {'   │   '.join(parts)}  "
    return [_full_row(content), _full_bot()]


# ── Main render ───────────────────────────────────────────────────────────────

def render(data: dict = None) -> None:
    """Clear screen and draw the full dashboard. Accepts pre-loaded data dict."""
    if data is None:
        try:
            data = _load_data()
        except Exception as e:
            print(f"\033[2J\033[H[terminal] Data load error: {e}")
            return

    lines = ['\033[2J\033[H']   # clear screen, home cursor

    # Top bar (3 lines)
    lines += _top_bar(data)

    # Row 2: Positions (left) | Stats (right) — 6 content rows
    N2 = 6
    lines.append(_split_open())
    for l, r in zip(_positions_panel(data, N2), _stats_panel(data, N2)):
        lines.append(_row(l, r))

    # Row 3: Recent trades | Signals — 7 content rows
    N3 = 7
    lines.append(_mid())   # ├──────────┼──────────┤
    for l, r in zip(_trades_panel(data, N3), _signals_panel(data, N3)):
        lines.append(_row(l, r))

    # Row 4: Debate | Events — 6 content rows
    N4 = 6
    lines.append(_mid())   # ├──────────┼──────────┤
    for l, r in zip(_debate_panel(data, N4), _events_panel(data, N4)):
        lines.append(_row(l, r))

    # Close two-column section, then bottom bar (full-width again)
    lines.append('├' + '─' * LW + '┴' + '─' * RW + '┤')
    lines += _bottom_bar(data)

    sys.stdout.write('\n'.join(lines) + '\n')
    sys.stdout.flush()


# ── Demo mode (python3 dashboard/terminal.py) ────────────────────────────────

def _demo_data() -> dict:
    """Generate plausible fake data so the layout can be previewed."""
    from datetime import timezone, timedelta
    tz  = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)

    return {
        'paper':        True,
        'balance':      501.27,
        'pnl_today':    1.27,
        'fees_today':   0.019,
        'halted':       False,
        'halt_reason':  '',
        'deployed_usd': 145.00,
        'eq_positions': {
            'AAPL': {'qty': 1, 'entry': 185.20, 'stop': 175.94, 'target': 204.72, 'direction': 'LONG'},
        },
        'cr_positions': {
            'BTC-USDC': {'qty': 0.000265, 'entry': 94800.00, 'stop': 91956.00,
                         'target': 100188.00, 'direction': 'LONG'},
        },
        'eq_trades_today': 1,
        'cr_trades_today': 4,
        'all_time': {
            'total': 17, 'wins': 10, 'losses': 7,
            'win_rate': 0.588, 'total_pnl': 1.27,
            'best_trade': 2.14, 'worst_trade': -1.05,
        },
        'today': {
            'total': 3, 'wins': 2, 'losses': 1,
            'win_rate': 0.667, 'gross_pnl': 1.29,
            'fees': 0.019, 'net_pnl': 1.271,
        },
        'recent_trades': [
            {'ts': '2026-03-22T10:12:03', 'action': 'SELL', 'symbol': 'BTC-USDC',
             'pnl_usd': 0.43, 'strategy': 'crypto_macd'},
            {'ts': '2026-03-22T10:08:21', 'action': 'BUY',  'symbol': 'BTC-USDC',
             'pnl_usd': 0,    'strategy': 'crypto_macd'},
            {'ts': '2026-03-22T09:55:10', 'action': 'SELL', 'symbol': 'ETH-USDC',
             'pnl_usd': -0.18, 'strategy': 'crypto_macd'},
            {'ts': '2026-03-22T09:40:00', 'action': 'BUY',  'symbol': 'ETH-USDC',
             'pnl_usd': 0,    'strategy': 'crypto_macd'},
            {'ts': '2026-03-22T09:35:00', 'action': 'SELL', 'symbol': 'AAPL',
             'pnl_usd': 1.02, 'strategy': 'equity_momentum'},
        ],
        'signals': [
            {'ts': '2026-03-22T10:14:52', 'signal': 'BUY',  'symbol': 'AAPL',
             'confidence': 0.82, 'reason': '3/3 agents BUY: funding neutral + squeeze fired + fee math clear', 'acted_on': 1},
            {'ts': '2026-03-22T10:13:11', 'signal': 'HOLD', 'symbol': 'ETH-USDC',
             'confidence': 0.45, 'reason': 'ADX < 20, choppy market — skipping', 'acted_on': 0},
            {'ts': '2026-03-22T10:10:03', 'signal': 'SELL', 'symbol': 'BTC-USDC',
             'confidence': 0.65, 'reason': 'RSI overbought + MACD histogram crossed below 0', 'acted_on': 1},
            {'ts': '2026-03-22T09:58:00', 'signal': 'BUY',  'symbol': 'ETH-USDC',
             'confidence': 0.70, 'reason': 'Workhorse + Classic MACD in consensus', 'acted_on': 1},
            {'ts': '2026-03-22T09:45:00', 'signal': 'BUY',  'symbol': 'AAPL',
             'confidence': 0.78, 'reason': 'Volume spike 220%, price > VWAP, KST zero-cross', 'acted_on': 1},
        ],
        'last_debate': {
            'ts': '2026-03-22T10:10:03', 'symbol': 'BTC-USDC',
            'final_signal': 'BUY', 'buy_votes': 5, 'hold_votes': 1, 'sell_votes': 2,
            'confidence': 0.72, 'regime': 'trending_up',
            'bull_case': 'Strong momentum continuation — MACD hist positive, price > VWAP.',
            'bear_case': 'RSI at 68, approaching overbought. Watch for reversal on next candle.',
            'key_risk': 'Fee drag at 0.3% of account vs 1.5% daily limit. High-frequency risk.',
        },
        'events': [
            {'ts': '2026-03-22T10:15:20', 'level': 'INFO',    'source': 'equity_scan',   'message': 'AAPL signal BUY conf=82% — entering'},
            {'ts': '2026-03-22T10:10:45', 'level': 'INFO',    'source': 'crypto_scan',   'message': 'BTC-USDC debate 5/8 BUY — risk check passed'},
            {'ts': '2026-03-22T10:08:33', 'level': 'WARNING', 'source': 'exit_monitor',  'message': 'BTC-USDC trailing stop check — holding (3% profit cushion needed)'},
            {'ts': '2026-03-22T09:35:10', 'level': 'INFO',    'source': 'equity_scan',   'message': 'AAPL SELL — take profit hit $204.72 | P&L +$1.02'},
            {'ts': '2026-03-22T09:00:01', 'level': 'INFO',    'source': 'scheduler',     'message': 'System started — paper mode | AI debate: ON'},
            {'ts': '2026-03-22T08:30:00', 'level': 'INFO',    'source': 'premarket',     'message': 'SPY HTF=bullish ADX=28.4 — trend day expected'},
        ],
        'api_cost': 0.1247,
        'now':      now,
    }


if __name__ == '__main__':
    print("Rendering demo state (no live data)...\n")
    time.sleep(0.3)
    render(_demo_data())
    print()   # trailing newline after dashboard
