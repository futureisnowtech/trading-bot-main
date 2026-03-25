#!/usr/bin/env python3
"""
scripts/generate_daily_summary.py
Runs nightly (via launchd) to auto-generate brain/06_daily_summaries/YYYY-MM-DD.md
from the live SQLite database.

Queries:
  - Today's trades (P&L, fees, win rate, per-strategy breakdown)
  - Today's signals (which fired, acted-on rate)
  - Today's debate results (agent vote patterns)
  - Today's system events (halts, errors, notable events)
  - Open positions at end of day

Writes:
  - brain/06_daily_summaries/YYYY-MM-DD.md  (always overwrites with latest data)
  - Updates brain/01_current_system/Open Questions.md if patterns trigger an alert

Run manually: python3 scripts/generate_daily_summary.py
Run for a prior date: python3 scripts/generate_daily_summary.py --date 2026-03-24
"""
import sqlite3
import os
import sys
import json
import argparse
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH     = PROJECT_DIR / 'logs' / 'trades.db'
BRAIN_DIR   = PROJECT_DIR / 'brain'
SUMMARIES   = BRAIN_DIR / '06_daily_summaries'
OQ_PATH     = BRAIN_DIR / '01_current_system' / 'Open Questions.md'

# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn():
    if not DB_PATH.exists():
        return None
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _day_bounds(day: date):
    """Return (start_ts, end_ts) strings for a calendar day."""
    return f"{day} 00:00:00", f"{day} 23:59:59"


def query_trades(conn, day: date):
    s, e = _day_bounds(day)
    rows = conn.execute(
        "SELECT * FROM trades WHERE ts BETWEEN ? AND ? ORDER BY ts",
        (s, e)
    ).fetchall()
    return [dict(r) for r in rows]


def query_signals(conn, day: date):
    s, e = _day_bounds(day)
    rows = conn.execute(
        "SELECT * FROM signals WHERE ts BETWEEN ? AND ? ORDER BY ts",
        (s, e)
    ).fetchall()
    return [dict(r) for r in rows]


def query_debates(conn, day: date):
    s, e = _day_bounds(day)
    rows = conn.execute(
        "SELECT * FROM debate_results WHERE ts BETWEEN ? AND ? ORDER BY ts",
        (s, e)
    ).fetchall()
    return [dict(r) for r in rows]


def query_events(conn, day: date):
    s, e = _day_bounds(day)
    rows = conn.execute(
        "SELECT * FROM system_events WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 50",
        (s, e)
    ).fetchall()
    return [dict(r) for r in rows]


def query_open_positions(conn):
    rows = conn.execute("SELECT * FROM open_positions ORDER BY ts_entry").fetchall()
    return [dict(r) for r in rows]


def query_todays_attribution(conn, day: date):
    """Return signal attribution rows for today's closed trades."""
    s, e = _day_bounds(day)
    try:
        rows = conn.execute(
            """SELECT ta.signal_name, ta.regime, ta.won, ta.pnl_usd, ta.lesson
               FROM trade_attribution ta
               WHERE ta.ts BETWEEN ? AND ?
               ORDER BY ta.ts""",
            (s, e)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_signal_leaderboard(conn):
    """Current Bayesian signal stats (cumulative, not just today)."""
    try:
        rows = conn.execute(
            """SELECT signal_name, regime, fires, wins, losses,
                      win_rate, avg_pnl, bayesian_pts, prior_pts
               FROM signal_stats
               WHERE fires >= 3
               ORDER BY bayesian_pts DESC
               LIMIT 20"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_agent_stats(conn):
    """Per-agent accuracy stats."""
    try:
        rows = conn.execute(
            """SELECT agent_name, regime, votes, correct_calls, accuracy
               FROM agent_stats
               WHERE votes >= 5
               ORDER BY accuracy DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_trades(trades):
    if not trades:
        return {
            'count': 0, 'buy_count': 0, 'sell_count': 0,
            'wins': 0, 'losses': 0, 'win_rate': None,
            'total_pnl': 0.0, 'total_fees': 0.0, 'net_pnl': 0.0,
            'best_trade': None, 'worst_trade': None,
            'by_strategy': {}, 'by_symbol': {},
            'paper_count': 0, 'live_count': 0,
        }

    closes = [t for t in trades if t['action'] in ('SELL', 'CLOSE', 'SELL_SHORT')]
    wins   = [t for t in closes if (t['pnl_usd'] or 0) > 0]
    losses = [t for t in closes if (t['pnl_usd'] or 0) < 0]

    total_pnl  = sum((t['pnl_usd'] or 0) for t in closes)
    total_fees = sum((t['fee_usd'] or 0) for t in trades)
    net_pnl    = total_pnl - total_fees

    best  = max(closes, key=lambda t: t['pnl_usd'] or 0) if closes else None
    worst = min(closes, key=lambda t: t['pnl_usd'] or 0) if closes else None

    by_strategy = defaultdict(lambda: {'count': 0, 'pnl': 0.0, 'fees': 0.0})
    for t in trades:
        s = t['strategy']
        by_strategy[s]['count'] += 1
        by_strategy[s]['pnl']   += t['pnl_usd'] or 0
        by_strategy[s]['fees']  += t['fee_usd'] or 0

    by_symbol = defaultdict(lambda: {'count': 0, 'pnl': 0.0})
    for t in closes:
        sym = t['symbol']
        by_symbol[sym]['count'] += 1
        by_symbol[sym]['pnl']   += t['pnl_usd'] or 0

    return {
        'count':      len(trades),
        'buy_count':  len([t for t in trades if t['action'] in ('BUY', 'BUY_LONG')]),
        'sell_count': len(closes),
        'wins':       len(wins),
        'losses':     len(losses),
        'win_rate':   len(wins) / len(closes) if closes else None,
        'total_pnl':  total_pnl,
        'total_fees': total_fees,
        'net_pnl':    net_pnl,
        'best_trade': best,
        'worst_trade': worst,
        'by_strategy': dict(by_strategy),
        'by_symbol':   dict(by_symbol),
        'paper_count': len([t for t in trades if t.get('paper')]),
        'live_count':  len([t for t in trades if not t.get('paper')]),
    }


def analyze_signals(signals):
    if not signals:
        return {'count': 0, 'acted_on': 0, 'by_strategy': {}}
    acted = [s for s in signals if s.get('acted_on')]
    by_strategy = defaultdict(int)
    for s in signals:
        by_strategy[s['strategy']] += 1
    return {
        'count':       len(signals),
        'acted_on':    len(acted),
        'by_strategy': dict(by_strategy),
    }


def analyze_debates(debates):
    if not debates:
        return {'count': 0, 'buy_count': 0, 'hold_count': 0, 'sell_count': 0}
    buy_count  = len([d for d in debates if d.get('final_signal') == 'BUY'])
    hold_count = len([d for d in debates if d.get('final_signal') == 'HOLD'])
    sell_count = len([d for d in debates if d.get('final_signal') == 'SELL'])

    regimes = defaultdict(int)
    for d in debates:
        r = d.get('regime') or 'unknown'
        regimes[r] += 1

    return {
        'count':      len(debates),
        'buy_count':  buy_count,
        'hold_count': hold_count,
        'sell_count': sell_count,
        'regimes':    dict(regimes),
    }


def detect_alerts(ta, events):
    """Return list of alert strings for the open questions update."""
    alerts = []
    # Fee drag alert
    if ta['total_fees'] > 40:
        alerts.append(f"Fee drag high today: ${ta['total_fees']:.2f} (limit $50)")
    # Loss streak alert
    if ta['losses'] >= 4 and (ta['wins'] == 0 or ta['losses'] / max(ta['wins'], 1) >= 4):
        alerts.append(f"Heavy loss day: {ta['losses']} losses, {ta['wins']} wins")
    # No trades (might indicate bot is down)
    if ta['count'] == 0:
        alerts.append("No trades today — bot may not be running or no signals fired")
    # System halts
    halts = [e for e in events if 'halt' in e.get('message', '').lower() or
             e.get('level') == 'ERROR']
    if halts:
        alerts.append(f"{len(halts)} system halts/errors logged today")
    return alerts


# ── Note writers ──────────────────────────────────────────────────────────────

def _pct(val):
    return f"{val*100:.1f}%" if val is not None else "N/A"


def _usd(val):
    return f"${val:+.2f}" if val is not None else "N/A"


def _trade_line(t):
    if not t:
        return "None"
    return (f"{t['symbol']} {t['action']} "
            f"{_usd(t['pnl_usd'])} pnl / "
            f"${t['fee_usd']:.2f} fee @ {t['ts'][:16]}")


def build_signal_intelligence_section(attribution_rows, signal_leaderboard, agent_stats):
    """Build the What to Keep/Change/Test/Stop section from real signal data."""
    lines = []

    # Today's winners — signals that fired on winning trades
    if attribution_rows:
        winner_signals = defaultdict(lambda: {'count': 0, 'pnl': 0.0})
        loser_signals  = defaultdict(lambda: {'count': 0, 'pnl': 0.0})
        for row in attribution_rows:
            sig = row['signal_name']
            if row['won']:
                winner_signals[sig]['count'] += 1
                winner_signals[sig]['pnl']   += row['pnl_usd'] or 0
            else:
                loser_signals[sig]['count'] += 1
                loser_signals[sig]['pnl']   += row['pnl_usd'] or 0

        keep_signals = sorted(winner_signals.items(), key=lambda x: x[1]['pnl'], reverse=True)[:5]
        watch_signals = sorted(loser_signals.items(), key=lambda x: x[1]['pnl'])[:5]

        lines.append("**Keep** (signals on winning trades today):")
        for sig, stats in keep_signals:
            lines.append(f"- `{sig}`: {stats['count']} wins | +${stats['pnl']:.2f} P&L")
        if not keep_signals:
            lines.append("- No winning trades today")

        lines.append("\n**Watch** (signals on losing trades today):")
        for sig, stats in watch_signals:
            lines.append(f"- `{sig}`: {stats['count']} losses | ${stats['pnl']:.2f} P&L")
        if not watch_signals:
            lines.append("- No losing trades today")

        # Lessons from attribution
        lessons = [r['lesson'] for r in attribution_rows if r.get('lesson')]
        if lessons:
            lines.append("\n**Lessons extracted today**:")
            seen = set()
            for lesson in lessons[:5]:
                key = lesson[:60]
                if key not in seen:
                    seen.add(key)
                    lines.append(f"- {lesson}")
    else:
        lines.append("**Keep / Change / Test / Stop**: No attribution data yet for today.\n")
        lines.append("*Signals will populate after trades close and are attributed.*")

    # Top signals by Bayesian weight (cumulative)
    if signal_leaderboard:
        lines.append("\n**Signal Leaderboard** (cumulative Bayesian pts, ≥3 fires):")
        lines.append(f"| Signal | Regime | Fires | Win% | Avg P&L | Bayes Pts |")
        lines.append(f"|--------|--------|-------|------|---------|-----------|")
        for s in signal_leaderboard[:10]:
            wr  = f"{s['win_rate']*100:.0f}%" if s['win_rate'] is not None else "N/A"
            ap  = f"${s['avg_pnl']:+.3f}" if s['avg_pnl'] is not None else "N/A"
            bp  = f"{s['bayesian_pts']:.1f}" if s['bayesian_pts'] is not None else "N/A"
            lines.append(
                f"| {s['signal_name']} | {s['regime'] or 'all'} | "
                f"{s['fires']} | {wr} | {ap} | {bp} |"
            )

    # Agent accuracy
    if agent_stats:
        lines.append("\n**Agent Accuracy** (≥5 votes):")
        for a in agent_stats[:8]:
            acc = f"{a['accuracy']*100:.0f}%" if a['accuracy'] is not None else "N/A"
            lines.append(
                f"- {a['agent_name']} | {a['regime'] or 'all'} | "
                f"{a['votes']} votes | {acc} accuracy"
            )

    return "\n".join(lines)


def _get_tax_snapshot() -> str:
    """Pull YTD tax summary from tax_tracker. Silent on error."""
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from learning.tax_tracker import format_tax_summary_for_brain
        return format_tax_summary_for_brain()
    except Exception as e:
        return f"*Tax snapshot unavailable: {e}*"


def write_daily_summary(day: date, ta, sa, da, events, open_positions,
                        attribution_rows=None, signal_leaderboard=None,
                        agent_stats=None, no_db=False):
    SUMMARIES.mkdir(parents=True, exist_ok=True)
    out_path = SUMMARIES / f"{day}.md"

    mode_label = "Paper" if not any(
        not t for t in (ta.get('live_count', 0),)
    ) else "Paper + Live"
    if ta.get('live_count', 0) > 0 and ta.get('paper_count', 0) == 0:
        mode_label = "LIVE"
    elif ta.get('paper_count', 0) > 0 and ta.get('live_count', 0) == 0:
        mode_label = "Paper"
    else:
        mode_label = "Mixed"

    win_rate_str = _pct(ta['win_rate'])
    total_trades = ta['sell_count']

    # Strategy breakdown
    strat_lines = []
    for strat, stats in ta.get('by_strategy', {}).items():
        strat_lines.append(
            f"  - {strat}: {stats['count']} trades | "
            f"pnl {_usd(stats['pnl'])} | fees ${stats['fees']:.2f}"
        )
    strat_section = "\n".join(strat_lines) if strat_lines else "  - No trades"

    # Symbol breakdown (top 5 by |pnl|)
    sym_sorted = sorted(
        ta.get('by_symbol', {}).items(),
        key=lambda x: abs(x[1]['pnl']), reverse=True
    )[:5]
    sym_lines = [
        f"  - {sym}: {s['count']} closes | {_usd(s['pnl'])}"
        for sym, s in sym_sorted
    ] or ["  - No closed trades"]

    # Debate summary
    debate_summary = (
        f"{da['count']} debates → "
        f"{da['buy_count']} BUY / {da['hold_count']} HOLD / {da['sell_count']} SELL"
        if da['count'] > 0 else "No debates run"
    )

    # Regime
    regime_str = "Unknown (no debate data)"
    if da.get('regimes'):
        top_regime = max(da['regimes'], key=da['regimes'].get)
        regime_str = f"{top_regime} (dominant across {da['regimes'][top_regime]} debates)"

    # Notable events
    event_lines = []
    for e in events[:10]:
        lvl = e.get('level', 'INFO')
        msg = e.get('message', '')[:120]
        event_lines.append(f"  - [{lvl}] {e['ts'][:16]}: {msg}")
    events_section = "\n".join(event_lines) if event_lines else "  - No notable events"

    # Open positions
    pos_lines = []
    for p in open_positions:
        entry  = p.get('entry', 0)
        target = p.get('target', 0)
        stop   = p.get('stop', 0)
        prog   = ((target - entry) / max(target - stop, 0.0001)) if target > stop else 0
        pos_lines.append(
            f"  - {p['symbol']} ({p['strategy']}) | "
            f"entry ${entry:.4f} | stop ${stop:.4f} | target ${target:.4f}"
        )
    pos_section = "\n".join(pos_lines) if pos_lines else "  - No open positions"

    # Fee analysis
    fee_pct_of_account = ta['total_fees'] / 500.0 * 100  # $500 account
    fee_ok = "YES" if ta['total_fees'] < 40 else ("WARN > $40" if ta['total_fees'] < 50 else "BREACHED $50 LIMIT")

    db_note = "\n> **WARNING**: Database not found — this is a template with no real data.\n" if no_db else ""

    signal_intel_section = build_signal_intelligence_section(
        attribution_rows or [], signal_leaderboard or [], agent_stats or []
    )

    content = f"""# {day}

#daily-summary
{db_note}
**System version**: BELIEVED v4.3
**Mode**: {mode_label}
**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (auto)

---

## SUMMARY

| Metric | Value |
|--------|-------|
| Total trade actions | {ta['count']} |
| Completed trades (closes) | {total_trades} |
| Wins | {ta['wins']} |
| Losses | {ta['losses']} |
| Win rate | {win_rate_str} |
| Gross P&L | {_usd(ta['total_pnl'])} |
| Total fees | ${ta['total_fees']:.2f} |
| Net P&L (after fees) | {_usd(ta['net_pnl'])} |
| Signals generated | {sa['count']} |
| Signals acted on | {sa['acted_on']} |
| {debate_summary} | |

---

## CONFIRMED TODAY

What was directly confirmed (trades executed, DB rows present):

- Trades logged: {ta['count']}
- {'Paper trades only' if mode_label == 'Paper' else 'LIVE trades included'}
- Database queried successfully: {'NO — DB not found' if no_db else 'YES'}

---

## REGIME

- Dominant regime: {regime_str}
- Notable events: see below
- Fear & Greed: (check Alternative.me — not auto-logged)
- SPY direction: (check manually if needed)

---

## BY STRATEGY

{strat_section}

---

## BY SYMBOL (top 5 by P&L impact)

{"\n".join(sym_lines)}

---

## BEST / WORST TRADE

- Best:  {_trade_line(ta['best_trade'])}
- Worst: {_trade_line(ta['worst_trade'])}

---

## OPEN POSITIONS AT END OF DAY

{pos_section}

---

## SYSTEM EVENTS (latest 10)

{events_section}

---

## FEE ANALYSIS

- Total fees: ${ta['total_fees']:.2f}
- Fee drag: {fee_pct_of_account:.1f}% of $500 account
- Daily limit ($50 = 10%): {fee_ok}

---

## SETUPS THAT MADE MONEY AFTER FEES

{chr(10).join(f"- {t['symbol']}: {_usd(t['pnl_usd'])} ({t['strategy']}) @ {t['ts'][:16]}" for t in ([ta['best_trade']] if ta['best_trade'] and (ta['best_trade']['pnl_usd'] or 0) > 0 else [])) or '- None confirmed today'}

---

## SETUPS THAT LOST AFTER FEES

{chr(10).join(f"- {t['symbol']}: {_usd(t['pnl_usd'])} ({t['strategy']}) @ {t['ts'][:16]}" for t in ([ta['worst_trade']] if ta['worst_trade'] and (ta['worst_trade']['pnl_usd'] or 0) < 0 else [])) or '- None confirmed today'}

---

## WHAT TO KEEP / CHANGE / TEST / STOP

{signal_intel_section}

---

## TAX SNAPSHOT (YTD)

{_get_tax_snapshot()}

---

## LINKS

- Parameter set: [[03_parameter_sets/Param Set - v4.3 Active.md]]
- Open Questions: [[01_current_system/Open Questions.md]]
"""

    out_path.write_text(content)
    return out_path


def maybe_update_open_questions(alerts, day: date):
    """Append a dated alert block to Open Questions if anything notable happened."""
    if not alerts or not OQ_PATH.exists():
        return

    existing = OQ_PATH.read_text()
    block = f"\n---\n\n## AUTO-ALERT — {day}\n\n"
    for a in alerts:
        block += f"- {a}\n"
    block += f"\n*Generated by generate_daily_summary.py — review and resolve or dismiss.*\n"

    # Only append if this date's alert isn't already there
    if f"AUTO-ALERT — {day}" not in existing:
        OQ_PATH.write_text(existing + block)
        print(f"  → Updated Open Questions with {len(alerts)} alert(s)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None,
                        help='Date to summarise (YYYY-MM-DD). Defaults to today.')
    args = parser.parse_args()

    if args.date:
        try:
            target_day = date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: Invalid date '{args.date}'. Use YYYY-MM-DD format.")
            sys.exit(1)
    else:
        target_day = date.today()

    print(f"generate_daily_summary.py — {target_day}")

    conn = _conn()
    no_db = conn is None

    if no_db:
        print("  WARNING: Database not found at", DB_PATH)
        trades, signals, debates, events, positions = [], [], [], [], []
        attribution_rows, signal_leaderboard, agent_stats_rows = [], [], []
    else:
        print("  Querying database...")
        trades    = query_trades(conn, target_day)
        signals   = query_signals(conn, target_day)
        debates   = query_debates(conn, target_day)
        events    = query_events(conn, target_day)
        positions = query_open_positions(conn)
        attribution_rows   = query_todays_attribution(conn, target_day)
        signal_leaderboard = query_signal_leaderboard(conn)
        agent_stats_rows   = query_agent_stats(conn)
        conn.close()

    ta = analyze_trades(trades)
    sa = analyze_signals(signals)
    da = analyze_debates(debates)

    print(f"  Trades: {ta['count']} | Closes: {ta['sell_count']} | "
          f"Wins: {ta['wins']} | Losses: {ta['losses']} | "
          f"Net P&L: {_usd(ta['net_pnl'])}")
    print(f"  Attribution rows: {len(attribution_rows)} | "
          f"Signal leaderboard: {len(signal_leaderboard)} | "
          f"Agent stats: {len(agent_stats_rows)}")

    out_path = write_daily_summary(
        target_day, ta, sa, da, events, positions,
        attribution_rows=attribution_rows,
        signal_leaderboard=signal_leaderboard,
        agent_stats=agent_stats_rows,
        no_db=no_db,
    )
    print(f"  Written → {out_path.relative_to(PROJECT_DIR)}")

    alerts = detect_alerts(ta, events)
    maybe_update_open_questions(alerts, target_day)

    print("  Done.")


if __name__ == '__main__':
    main()
