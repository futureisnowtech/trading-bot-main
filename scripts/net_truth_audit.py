#!/usr/bin/env python3
"""
net_truth_audit.py — Decision-grade, trust-aware net performance audit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJ_ROOT not in sys.path:
    sys.path.insert(0, PROJ_ROOT)

from scripts.truth_audit_lib import build_net_truth_audit, default_db_path


def _money(value: float) -> str:
    return f"${value:+.2f}"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _row(label: str, metrics: dict) -> str:
    return (
        f"{label:<22} n={metrics['trade_count']:>4} "
        f"wr={_pct(metrics['win_rate_net']):>7} "
        f"gross={_money(metrics['gross_pnl']):>9} "
        f"fees={_money(-metrics['fees']):>9} "
        f"net={_money(metrics['net_pnl']):>9} "
        f"exp={_money(metrics['expectancy_net']):>8}"
    )


def _print_group(title: str, rows: list[dict], key_name: str, limit: int = 10) -> None:
    print(f"\n{title}")
    if not rows:
        print("  none")
        return
    for row in rows[:limit]:
        print(f"  {_row(str(row[key_name]), row)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Trust-aware net performance audit")
    parser.add_argument("--db", default=default_db_path(), help="SQLite DB path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = parser.parse_args()

    audit = build_net_truth_audit(args.db)
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 0

    headline = audit["headline"]
    overall = headline["overall"]
    recent = headline["recent_windows"]
    relaxed = audit["diagnostics"]["relaxed_signal_diagnostics"]
    strict = audit["diagnostics"]["strict_signal_diagnostics"]

    print("Net Truth Audit")
    print(f"DB: {audit['db_path']}")
    print(f"Generated: {audit['generated_at']}")
    print(
        "Headline filter: sources="
        + ",".join(audit["filters"]["headline_sources"])
        + " | exclude force_test_close | exclude dirty/replay sources"
    )

    print("\nHeadline Truth")
    print("  " + _row("trustworthy close ledger", overall))
    print("  " + _row("raw comparable closes", headline["raw_trade_surface"]))
    print(f"  contamination delta net: {_money(headline['contamination_delta_net'])}")

    print("\nRecency")
    print("  " + _row("24h", recent["24h"]))
    print("  " + _row("7d", recent["7d"]))
    print("  " + _row("since clean epoch", recent["all"]))

    _print_group("By Direction", headline["by_direction"], "direction")
    _print_group("By Exit Type", headline["by_exit_type"], "exit_type")
    _print_group("Worst Symbols", headline["by_symbol_negative"], "symbol")
    _print_group("Best Symbols", headline["by_symbol_positive"], "symbol")
    _print_group("Named Setups", headline["by_setup"], "setup_name")

    print("\nAttribution Truth Coverage")
    print(
        "  relaxed diagnostics: "
        + _row("usable relaxed", relaxed["overall"])
        + f" | coverage={relaxed['coverage_vs_headline_pct']:.1f}%"
    )
    print(
        "  strict diagnostics:  "
        + _row("usable strict", strict["overall"])
        + f" | coverage={strict['coverage_vs_headline_pct']:.1f}%"
    )
    print(
        "  raw comparable attribution: "
        + _row("raw attr", audit["diagnostics"]["raw_attribution_surface"]["overall"])
    )

    _print_group(
        "Relaxed Hold Buckets", relaxed["by_hold_bucket"], "hold_bucket", limit=10
    )
    _print_group(
        "Strict Primary Signals", strict["by_primary_signal"], "primary_signal", limit=10
    )

    print("\nTrust Counts")
    for bucket, count in sorted(audit["trust_counts"]["closed_trades"].items()):
        print(f"  closed_trades.{bucket}: {count}")
    for bucket, count in sorted(audit["trust_counts"]["trade_attribution"].items()):
        print(f"  trade_attribution.{bucket}: {count}")

    print("\nTakeaways")
    if overall["net_pnl"] > 0:
        print("  Trustworthy close ledger is net positive after fees.")
    else:
        print("  Trustworthy close ledger is not net positive after fees.")
    if strict["count"] < 20:
        print(
            f"  Strict setup/exit attribution is under-covered ({strict['count']} rows); "
            "treat setup-level conclusions as provisional."
        )
    if headline["by_direction"]:
        print(
            "  Direction split is one of the highest-signal truth surfaces in this report."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
