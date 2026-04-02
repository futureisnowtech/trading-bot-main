"""
scripts/weekly_report.py — Generate weekly performance report.

Outputs a Markdown report at brain/06_daily_summaries/weekly_YYYY-WNN.md.

Covers:
  - Overall P&L, WR, Sharpe, drawdown
  - Per-pair breakdown
  - Top 10 most predictive features (from learning_loop)
  - RBI incubation status
  - ML model health (Brier score, trade count)
  - Kill switch events
  - Regime distribution

Usage:
    python3 scripts/weekly_report.py
    python3 scripts/weekly_report.py --days 14
    python3 scripts/weekly_report.py --output /path/to/custom.md
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# allow imports from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytz


def _db():
    from logging_db.trade_logger import get_logger
    return get_logger()


# ── Data collection ───────────────────────────────────────────────────────────

def _collect_trade_stats(db, cutoff: float) -> dict:
    rows = db.conn.execute("""
        SELECT pnl_usd, won, symbol, regime, composite_score
        FROM trade_attribution
        WHERE ts > ? AND pnl_usd IS NOT NULL
        ORDER BY ts ASC
    """, (cutoff,)).fetchall()

    if not rows:
        return {}

    pnls = [float(r[0]) for r in rows]
    wins = sum(1 for r in rows if r[1])
    n = len(rows)
    gross_win  = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    pf = gross_win / (gross_loss + 1e-9)

    # Sharpe (annualised, daily resolution approximation)
    daily: dict = {}
    for r in rows:
        day = str(r[0])[:10] if r[0] else 'unknown'
        daily.setdefault(day, []).append(float(r[0]))
    daily_pnls = np.array([sum(v) for v in daily.values()])
    sharpe = 0.0
    if len(daily_pnls) >= 5 and daily_pnls.std() > 0:
        sharpe = float((daily_pnls.mean() / daily_pnls.std()) * np.sqrt(52))

    # Max drawdown
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = float((peak - cum).max()) if len(cum) > 0 else 0.0

    # Per-symbol
    by_symbol: dict = {}
    for r in rows:
        sym = r[2] or 'UNKNOWN'
        by_symbol.setdefault(sym, {'n': 0, 'wins': 0, 'pnl': 0.0})
        by_symbol[sym]['n'] += 1
        by_symbol[sym]['wins'] += int(r[1] or 0)
        by_symbol[sym]['pnl'] += float(r[0])

    # Regime distribution
    regime_counts: dict = {}
    for r in rows:
        reg = r[3] or 'UNKNOWN'
        regime_counts[reg] = regime_counts.get(reg, 0) + 1

    return {
        'n': n,
        'wr': wins / n,
        'pf': round(pf, 3),
        'total_pnl': round(sum(pnls), 2),
        'gross_win': round(gross_win, 2),
        'gross_loss': round(gross_loss, 2),
        'max_dd': round(dd, 2),
        'sharpe': round(sharpe, 3),
        'by_symbol': by_symbol,
        'regime_counts': regime_counts,
        'avg_score': round(
            sum(float(r[4] or 0) for r in rows if r[4]) /
            max(1, sum(1 for r in rows if r[4])), 1
        ),
    }


def _collect_rbi_status(db) -> dict:
    try:
        rows = db.conn.execute("""
            SELECT status, COUNT(*) FROM rbi_incubation GROUP BY status
        """).fetchall()
        summary = {r[0]: r[1] for r in rows}

        incubating = db.conn.execute("""
            SELECT symbol, feature_combo, actual_trades, wins, backtest_mean_wr
            FROM rbi_incubation WHERE status='incubating'
        """).fetchall()

        graduated = db.conn.execute("""
            SELECT COUNT(*) FROM rbi_incubation WHERE status='graduated'
        """).fetchone()[0]

        return {
            'summary': summary,
            'graduated': graduated,
            'incubating': [
                {
                    'symbol': r[0],
                    'combo': json.loads(r[1])[:3],
                    'trades': r[2],
                    'wr': round(r[3] / max(1, r[2]), 3),
                    'bt_wr': r[4],
                }
                for r in incubating
            ],
        }
    except Exception:
        return {'summary': {}, 'graduated': 0, 'incubating': []}


def _collect_ml_health(db) -> dict:
    try:
        rows = db.conn.execute("""
            SELECT pair_key, direction, brier_score, n_samples, ts
            FROM ml_calibration
            ORDER BY ts DESC
            LIMIT 20
        """).fetchall()
        return {
            'models': [
                {'pair': r[0], 'dir': r[1], 'brier': r[2], 'n': r[3]}
                for r in rows
            ]
        }
    except Exception:
        return {'models': []}


def _collect_kill_events(db, cutoff: float) -> list:
    try:
        rows = db.conn.execute("""
            SELECT ts, trigger_type, detail
            FROM kill_switch_log WHERE ts > ?
            ORDER BY ts DESC
        """, (cutoff,)).fetchall()
        return [{'ts': r[0], 'type': r[1], 'detail': r[2]} for r in rows]
    except Exception:
        return []


# ── Report rendering ──────────────────────────────────────────────────────────

def _render(stats: dict, rbi: dict, ml: dict, kills: list,
            feat_stats: dict, days: int, week_label: str) -> str:
    lines = []
    a = lines.append

    et = pytz.timezone('US/Eastern')
    now_str = datetime.now(et).strftime('%Y-%m-%d %H:%M ET')

    a(f'# Weekly Performance Report — {week_label}')
    a(f'*Generated {now_str} · {days}-day window*')
    a('')

    # ── P&L Summary ──────────────────────────────────────────────────────────
    a('## P&L Summary')
    if not stats:
        a('*No closed trades in this period.*')
    else:
        a(f'| Metric | Value |')
        a(f'|--------|-------|')
        a(f'| Trades | {stats["n"]} |')
        a(f'| Win Rate | {stats["wr"]:.1%} |')
        a(f'| Total P&L | ${stats["total_pnl"]:+.2f} |')
        a(f'| Gross Win / Loss | ${stats["gross_win"]:.2f} / ${stats["gross_loss"]:.2f} |')
        a(f'| Profit Factor | {stats["pf"]:.2f} |')
        a(f'| Sharpe (annualised) | {stats["sharpe"]:.2f} |')
        a(f'| Max Drawdown | ${stats["max_dd"]:.2f} |')
        a(f'| Avg Signal Score | {stats["avg_score"]} |')
        a('')

        # ── Per-pair ─────────────────────────────────────────────────────────
        a('## Per-Pair Breakdown')
        a('| Symbol | Trades | WR | P&L |')
        a('|--------|--------|----|-----|')
        sym_sorted = sorted(stats['by_symbol'].items(),
                            key=lambda kv: kv[1]['pnl'], reverse=True)
        for sym, s in sym_sorted:
            wr = s['wins'] / max(1, s['n'])
            a(f'| {sym} | {s["n"]} | {wr:.0%} | ${s["pnl"]:+.2f} |')
        a('')

        # ── Regime ───────────────────────────────────────────────────────────
        a('## Regime Distribution')
        total = sum(stats['regime_counts'].values())
        a('| Regime | Trades | % |')
        a('|--------|--------|---|')
        for reg, cnt in sorted(stats['regime_counts'].items(),
                               key=lambda kv: kv[1], reverse=True):
            a(f'| {reg} | {cnt} | {cnt/total:.0%} |')
        a('')

    # ── RBI ───────────────────────────────────────────────────────────────────
    a('## RBI Incubation Status')
    a(f'- Graduated strategies: **{rbi["graduated"]}**')
    for k, v in rbi['summary'].items():
        a(f'- {k}: {v}')
    if rbi['incubating']:
        a('')
        a('### Active Incubations')
        a('| Symbol | Features | Trades | Live WR | Backtest WR |')
        a('|--------|----------|--------|---------|-------------|')
        for inc in rbi['incubating']:
            combo = ', '.join(inc['combo'])
            a(f'| {inc["symbol"]} | {combo}… | {inc["trades"]} | '
              f'{inc["wr"]:.0%} | {inc["bt_wr"]:.0%} |')
    a('')

    # ── ML ────────────────────────────────────────────────────────────────────
    a('## ML Model Health')
    if not ml['models']:
        a('*No calibration data yet (< 30 trades).*')
    else:
        a('| Pair | Dir | Brier | Samples | Status |')
        a('|------|-----|-------|---------|--------|')
        for m in ml['models']:
            status = ('✅ GOOD' if (m['brier'] or 1) < 0.20
                      else ('⚠️ WARN' if (m['brier'] or 1) < 0.22
                            else '❌ RECAL'))
            a(f'| {m["pair"]} | {m["dir"]} | {m["brier"] or "—":.3f} | '
              f'{m["n"] or 0} | {status} |')
    a('')

    # ── Feature predictiveness ────────────────────────────────────────────────
    a('## Top 10 Most Predictive Features (7-day)')
    if not feat_stats:
        a('*Insufficient data.*')
    else:
        a('| Feature | Correlation with Win |')
        a('|---------|---------------------|')
        for name, s in list(feat_stats.items())[:10]:
            direction = '↑' if s['corr'] > 0 else '↓'
            a(f'| {name} | {s["corr"]:+.3f} {direction} |')
    a('')

    # ── Kill switch events ────────────────────────────────────────────────────
    a('## Kill Switch Events')
    if not kills:
        a('*None in this period. ✅*')
    else:
        for k in kills:
            a(f'- `{k["ts"]}` **{k["type"]}**: {k["detail"]}')
    a('')

    # ── Go-live checklist ─────────────────────────────────────────────────────
    a('## Go-Live Criteria Check')
    n_trades = stats.get('n', 0)
    wr = stats.get('wr', 0)
    brier_ok = any((m['brier'] or 1) < 0.22 for m in ml['models']) if ml['models'] else False
    graduated = rbi.get('graduated', 0)

    checks = [
        ('ML Brier < 0.22', brier_ok),
        ('≥1 RBI graduate', graduated >= 1),
        ('Zero kill switches', len(kills) == 0),
        ('WR ≥ 52%', wr >= 0.52),
        ('≥50 closed trades', n_trades >= 50),
    ]
    for label, passed in checks:
        icon = '✅' if passed else '❌'
        a(f'- [{icon}] {label}')
    a('')
    a('---')
    a('*Auto-generated by `scripts/weekly_report.py`*')

    return '\n'.join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate weekly trading performance report')
    parser.add_argument('--days', type=int, default=7, help='Lookback window in days (default 7)')
    parser.add_argument('--output', type=str, default=None, help='Output path (default: auto)')
    args = parser.parse_args()

    cutoff = time.time() - args.days * 86400
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)

    # ISO week label e.g. 2026-W14
    week_label = now.strftime('%Y-W%V')

    # Output path
    output_path = args.output
    if not output_path:
        summaries_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'brain', '06_daily_summaries',
        )
        os.makedirs(summaries_dir, exist_ok=True)
        output_path = os.path.join(summaries_dir, f'weekly_{week_label}.md')

    print(f'Generating {args.days}-day report → {output_path}')

    try:
        db = _db()
    except Exception as e:
        print(f'ERROR: could not connect to DB: {e}')
        sys.exit(1)

    stats    = _collect_trade_stats(db, cutoff)
    rbi      = _collect_rbi_status(db)
    ml       = _collect_ml_health(db)
    kills    = _collect_kill_events(db, cutoff)

    # Feature predictiveness from learning_loop
    try:
        from learning_loop import get_recent_feature_stats
        feat_stats = get_recent_feature_stats(days=7)
    except Exception:
        feat_stats = {}

    report = _render(stats, rbi, ml, kills, feat_stats, args.days, week_label)

    with open(output_path, 'w') as f:
        f.write(report)

    print(f'Report written: {output_path}')
    if stats:
        print(f'  Trades: {stats["n"]}  WR: {stats["wr"]:.1%}  P&L: ${stats["total_pnl"]:+.2f}')
    else:
        print('  No trades in this period.')


if __name__ == '__main__':
    main()
