"""
learning/forecast_calibrator.py — Confidence calibration for conviction scores.

Problem: When the system says "conviction = 85", does that actually mean
85% of the time we win? Probably not. Calibration measures the gap between
stated confidence and observed win rate, then surfaces it to agents so they
can self-correct.

How it works:
  1. Reads trade_attribution: conviction score + won (0/1) per closed trade
  2. Bins conviction into brackets (30-40, 40-50, ..., 90+)
  3. For each bin: count trades and compute observed win rate
  4. Returns a formatted context string injected into each debate prompt
  5. Also checks agent_stats accuracy vs their stated confidence to surface
     per-agent calibration drift

The calibration string gets injected into every debate via _build_market_data()
in scheduler/_helpers.py (same pattern as other context strings).

No sklearn. No models. Just bin counting — the minimum sufficient approach
for our 100-trade sample size.
"""
import os
import sys
import sqlite3
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'logs', 'trades.db')

# Minimum trades in a bin before we report that bin (avoids noisy single-trade bins)
_MIN_BIN_TRADES = 5

# Conviction bins: (label, low_inclusive, high_exclusive)
_BINS = [
    ('30-40', 30, 40),
    ('40-50', 40, 50),
    ('50-60', 50, 60),
    ('60-70', 60, 70),
    ('70-80', 70, 80),
    ('80-90', 80, 90),
    ('90+',   90, 999),
]


def get_calibration_data(paper: bool = True, min_trades: int = 30) -> dict:
    """
    Read trade_attribution and compute calibration bins.

    Returns dict:
      bins        : list of {label, n, win_rate, stated_center} — only bins with ≥ _MIN_BIN_TRADES
      total_trades: int
      calibrated  : bool — True when total_trades >= min_trades
      summary     : str — one-line calibration health description
    """
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT conviction, won FROM trade_attribution WHERE paper=? AND conviction IS NOT NULL",
            (1 if paper else 0,)
        ).fetchall()
        conn.close()
    except Exception:
        return {'bins': [], 'total_trades': 0, 'calibrated': False,
                'summary': 'No calibration data yet'}

    if not rows:
        return {'bins': [], 'total_trades': 0, 'calibrated': False,
                'summary': 'No calibration data yet'}

    total = len(rows)
    bin_data = []

    for label, lo, hi in _BINS:
        bucket = [r for r in rows if lo <= (r['conviction'] or 0) < hi]
        if len(bucket) < _MIN_BIN_TRADES:
            continue
        win_rate = sum(r['won'] for r in bucket) / len(bucket)
        center = (lo + min(hi, 99)) / 2  # stated confidence center
        bin_data.append({
            'label': label,
            'n': len(bucket),
            'win_rate': win_rate,
            'stated_center': center,
            'drift': win_rate - center / 100.0,  # positive = overperforming vs stated
        })

    calibrated = total >= min_trades

    if not bin_data:
        summary = f"{total} trades recorded — need {_MIN_BIN_TRADES}+ per bin for calibration"
    else:
        # Find worst-calibrated bin
        worst = max(bin_data, key=lambda b: abs(b['drift']))
        direction = "over" if worst['drift'] > 0 else "under"
        summary = (f"{total} trades. Worst-calibrated: conviction {worst['label']} "
                   f"states ~{worst['stated_center']:.0f}% but achieves {worst['win_rate']:.0%} "
                   f"({direction}performing by {abs(worst['drift']):.0%})")

    return {
        'bins': bin_data,
        'total_trades': total,
        'calibrated': calibrated,
        'summary': summary,
    }


def get_calibration_context(paper: bool = True) -> str:
    """
    Returns a formatted string for injection into debate prompts.
    Shows agents how well the system's conviction scores predict outcomes.

    Returns empty string if < 30 trades (no signal yet).
    """
    data = get_calibration_data(paper=paper)

    if not data['calibrated'] or not data['bins']:
        return ""

    lines = [
        f"CONVICTION CALIBRATION ({data['total_trades']} closed trades):",
        "  How well conviction scores predict wins:",
    ]

    for b in data['bins']:
        drift_str = f"+{b['drift']:.0%}" if b['drift'] >= 0 else f"{b['drift']:.0%}"
        lines.append(
            f"  conviction {b['label']:5s}: {b['n']:3d} trades, "
            f"actual WR={b['win_rate']:.0%} "
            f"(stated ~{b['stated_center']:.0f}%, drift {drift_str})"
        )

    lines.append(
        "  → Use this when judging whether a conviction score justifies entry."
    )
    return "\n".join(lines)


def get_agent_calibration_context(paper: bool = True) -> str:
    """
    Returns per-agent accuracy vs typical confidence for injection into debate prompts.
    Reads agent_stats table.
    """
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT agent_name, votes_buy, correct_buy, accuracy
               FROM agent_stats
               WHERE regime='any' AND votes_buy >= 5
               ORDER BY agent_name"""
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    if not rows:
        return ""

    lines = ["AGENT BUY-CALL ACCURACY (lifetime):"]
    for r in rows:
        acc = r['accuracy'] or (r['correct_buy'] / max(r['votes_buy'], 1))
        lines.append(
            f"  {r['agent_name']}: {r['correct_buy']}/{r['votes_buy']} correct ({acc:.0%})"
        )
    lines.append(
        "  → If your accuracy is below 50%, be more selective before voting BUY."
    )
    return "\n".join(lines)


def get_full_calibration_context(paper: bool = True) -> str:
    """
    Combines conviction calibration + agent accuracy into one context block.
    Called once per scan cycle from _helpers.py.
    """
    parts = []
    cv = get_calibration_context(paper=paper)
    if cv:
        parts.append(cv)
    ag = get_agent_calibration_context(paper=paper)
    if ag:
        parts.append(ag)
    return "\n\n".join(parts)


def print_calibration_report(paper: bool = True) -> None:
    """CLI diagnostic: print full calibration report to stdout."""
    data = get_calibration_data(paper=paper)
    print(f"\n{'='*60}")
    print(f"CONVICTION CALIBRATION REPORT")
    print(f"{'='*60}")
    print(f"Total trades: {data['total_trades']}")
    print(f"Calibrated:   {data['calibrated']}")
    print(f"Summary:      {data['summary']}")
    print()

    if data['bins']:
        print(f"{'Bin':<10} {'N':>6} {'Act WR':>8} {'Stated':>8} {'Drift':>8}")
        print("-" * 45)
        for b in data['bins']:
            drift_str = f"{b['drift']:+.1%}"
            print(f"{b['label']:<10} {b['n']:>6} {b['win_rate']:>8.1%} "
                  f"{b['stated_center']:>7.0f}% {drift_str:>8}")

    print()
    print(get_agent_calibration_context(paper=paper))
    print()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Use live trades (not paper)')
    args = parser.parse_args()
    print_calibration_report(paper=not args.live)
