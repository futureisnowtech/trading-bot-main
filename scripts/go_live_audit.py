#!/usr/bin/env python3
"""
go_live_audit.py — Turn net truth into launch-night constraints.
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

from scripts.truth_audit_lib import build_go_live_audit, default_db_path


def _money(value: float) -> str:
    return f"${value:+.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch-night go-live audit")
    parser.add_argument("--db", default=default_db_path(), help="SQLite DB path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = parser.parse_args()

    audit = build_go_live_audit(args.db)
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 0

    headline = audit["headline"]
    go_live = audit["go_live"]

    print("Go-Live Audit")
    print(f"DB: {audit['db_path']}")
    print(f"Generated: {audit['generated_at']}")

    print("\nDecision")
    print(f"  status: {go_live['status']}")
    print(f"  primary recommendation: {go_live['primary_recommendation']}")
    print(
        "  headline: "
        f"n={headline['trade_count']} "
        f"net={_money(headline['net_pnl'])} "
        f"pf={headline['profit_factor_gross']:.2f} "
        f"exp={_money(headline['expectancy_net'])}"
    )

    print("\nTonight")
    for line in go_live["exact_tonight"]:
        print(f"  - {line}")

    print("\nEvidence-Backed Constraints")
    for item in go_live["recommendations"]:
        print(f"  - [{item['severity']}] {item['code']}: {item['action']}")
        print(f"    evidence: {item['evidence']}")

    print("\nDiagnostic Coverage")
    print(
        "  strict signal diagnostics: "
        f"{audit['diagnostic_coverage']['strict_signal_rows']} rows "
        f"({audit['diagnostic_coverage']['strict_signal_coverage_pct']:.1f}% of headline)"
    )
    print(
        "  relaxed signal diagnostics: "
        f"{audit['diagnostic_coverage']['relaxed_signal_rows']} rows "
        f"({audit['diagnostic_coverage']['relaxed_signal_coverage_pct']:.1f}% of headline)"
    )

    print("\nWorst Symbols")
    for row in audit["worst_symbols"]:
        print(
            f"  - {row['symbol']}: n={row['trade_count']} "
            f"net={_money(row['net_pnl'])} exp={_money(row['expectancy_net'])}"
        )

    print("\nExit Types")
    for row in audit["exit_types"][:6]:
        print(
            f"  - {row['exit_type']}: n={row['trade_count']} "
            f"net={_money(row['net_pnl'])} exp={_money(row['expectancy_net'])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
