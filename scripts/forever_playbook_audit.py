#!/usr/bin/env python3
"""
scripts/forever_playbook_audit.py — Forever Playbook live audit.

Reads trades.db and price_archive.db to surface:
  - Market-type bucket for every symbol seen in the scanner
  - Governance status and policy
  - Trustworthy PnL by symbol (clean_paper_v10 source only)
  - Governance upgrade/downgrade recommendations from evidence
  - Contamination warning if dirty rows would change headline truth

Usage:
    python3 scripts/forever_playbook_audit.py
    python3 scripts/forever_playbook_audit.py --json
    python3 scripts/forever_playbook_audit.py --db logs/trades.db --price-db logs/price_archive.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from strategies.market_type_classifier import MarketType, classify
from strategies.symbol_governance import (
    GovernanceStatus,
    SymbolPolicy,
    evaluate_governance_update,
    get_policy,
)

HEADLINE_SOURCES = ("clean_paper_v10", "live_v10")
DEFAULT_DB = PROJ / "logs" / "trades.db"
DEFAULT_PRICE_DB = PROJ / "logs" / "price_archive.db"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_symbol_stats(db_path: str) -> dict:
    """
    Load per-symbol performance from trades.db.
    Returns dict keyed by symbol with keys: n, gross, fees, net, wins, exit_types.
    Separated into headline (trustworthy) and all (including dirty).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    headline: dict[str, dict] = defaultdict(
        lambda: {
            "n": 0,
            "gross": 0.0,
            "fees": 0.0,
            "net": 0.0,
            "wins": 0,
            "exit_types": defaultdict(float),
        }
    )
    dirty: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0}
    )

    # Headline trades (trustworthy sources, close legs)
    c.execute(
        """
        SELECT symbol, pnl_usd, fee_usd, exit_type
        FROM trades
        WHERE source IN ('clean_paper_v10','live_v10')
          AND action IN ('SELL','close','cover','COVER')
          AND pnl_usd IS NOT NULL
        """,
    )
    for r in c.fetchall():
        s = r["symbol"]
        headline[s]["n"] += 1
        headline[s]["gross"] += r["pnl_usd"]
        headline[s]["fees"] += r["fee_usd"] or 0.0
        headline[s]["net"] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)
        if (r["pnl_usd"] or 0.0) > 0:
            headline[s]["wins"] += 1
        et = r["exit_type"] or "none"
        headline[s]["exit_types"][et] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)

    # Dirty trades (contaminated sources)
    c.execute(
        """
        SELECT symbol, pnl_usd, fee_usd
        FROM trades
        WHERE source NOT IN ('clean_paper_v10','live_v10')
          AND action IN ('SELL','close','cover','COVER')
          AND pnl_usd IS NOT NULL
        """,
    )
    for r in c.fetchall():
        s = r["symbol"]
        dirty[s]["n"] += 1
        dirty[s]["gross"] += r["pnl_usd"] or 0.0
        dirty[s]["fees"] += r["fee_usd"] or 0.0
        dirty[s]["net"] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)

    conn.close()
    return {"headline": dict(headline), "dirty": dict(dirty)}


def _load_scanner_universe(db_path: str) -> list[str]:
    """All distinct symbols ever seen in scan_candidates."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT DISTINCT symbol FROM scan_candidates ORDER BY symbol")
    syms = [r[0] for r in c.fetchall()]
    conn.close()
    return syms


def _load_open_positions(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("SELECT symbol, strategy, qty, entry, stop FROM open_positions")
        rows = [dict(r) for r in c.fetchall()]
    except Exception:
        rows = []
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Audit assembly
# ---------------------------------------------------------------------------


def build_audit(db_path: str, price_db: str) -> dict:
    price_db_exists = os.path.exists(price_db)
    pdb = price_db if price_db_exists else None

    stats = _load_symbol_stats(db_path)
    headline = stats["headline"]
    dirty = stats["dirty"]
    scanner_symbols = _load_scanner_universe(db_path)
    open_positions = _load_open_positions(db_path)

    # All symbols to report on: scanner universe + any with trade evidence
    all_symbols = sorted(
        set(scanner_symbols)
        | set(headline.keys())
        | set(dirty.keys())
        | {p["symbol"] for p in open_positions}
    )

    rows = []
    gov_recommendations = []

    for sym in all_symbols:
        policy = get_policy(sym, price_db=pdb)
        h = headline.get(sym, {})
        d = dirty.get(sym, {})

        n = h.get("n", 0)
        net = h.get("net", 0.0)
        gross = h.get("gross", 0.0)
        fees = h.get("fees", 0.0)
        wins = h.get("wins", 0)
        wr = round(wins / n * 100, 1) if n > 0 else None
        exp = round(net / n, 3) if n > 0 else None

        d_n = d.get("n", 0)
        d_net = d.get("net", 0.0)

        # Governance update recommendation
        if n >= 3 and exp is not None:
            rec = evaluate_governance_update(sym, n, net, exp, policy.governance)
            if rec is not None:
                gov_recommendations.append(
                    {
                        "symbol": sym,
                        "current": policy.governance.value,
                        "recommended": rec[0].value,
                        "evidence": rec[1],
                    }
                )

        rows.append(
            {
                "symbol": sym,
                "market_type": policy.market_type.value,
                "governance": policy.governance.value,
                "can_long": policy.can_long,
                "can_short": policy.can_short,
                "max_size_pct": policy.max_size_pct,
                "notes": policy.notes,
                "headline_n": n,
                "headline_net": round(net, 2),
                "headline_gross": round(gross, 2),
                "headline_fees": round(fees, 2),
                "win_rate": wr,
                "expectancy": exp,
                "dirty_n": d_n,
                "dirty_net": round(d_net, 2),
                "exit_types": dict(h.get("exit_types", {})),
                "in_scanner": sym in scanner_symbols,
                "open_position": any(p["symbol"] == sym for p in open_positions),
            }
        )

    # Headline summary
    total_n = sum(r["headline_n"] for r in rows)
    total_net = sum(r["headline_net"] for r in rows)
    total_gross = sum(r["headline_gross"] for r in rows)
    total_fees = sum(r["headline_fees"] for r in rows)
    pf = (
        round(
            sum(r["headline_gross"] for r in rows if r["headline_gross"] > 0)
            / max(
                0.01,
                abs(sum(r["headline_gross"] for r in rows if r["headline_gross"] < 0)),
            ),
            3,
        )
        if total_gross != 0
        else None
    )

    # Contamination check
    total_dirty_n = sum(r["dirty_n"] for r in rows)
    total_dirty_net = sum(r["dirty_net"] for r in rows)

    # Bucket summary
    bucket_counts: dict[str, dict] = defaultdict(
        lambda: {"n_symbols": 0, "headline_n": 0, "headline_net": 0.0}
    )
    for r in rows:
        b = r["market_type"]
        bucket_counts[b]["n_symbols"] += 1
        bucket_counts[b]["headline_n"] += r["headline_n"]
        bucket_counts[b]["headline_net"] += r["headline_net"]

    return {
        "headline_summary": {
            "total_n": total_n,
            "total_gross": round(total_gross, 2),
            "total_fees": round(total_fees, 2),
            "total_net": round(total_net, 2),
            "profit_factor": pf,
            "source_filter": list(HEADLINE_SOURCES),
        },
        "contamination": {
            "dirty_n": total_dirty_n,
            "dirty_net": round(total_dirty_net, 2),
            "headline_would_change": abs(total_dirty_net) > 1.0,
            "warning": (
                f"Dirty rows ({total_dirty_n} closes, net={total_dirty_net:+.2f}) "
                "excluded from headline truth. Do NOT blend."
            )
            if total_dirty_n > 0
            else "No dirty rows detected.",
        },
        "bucket_summary": {
            k: {
                "n_symbols": v["n_symbols"],
                "headline_n": v["headline_n"],
                "headline_net": round(v["headline_net"], 2),
            }
            for k, v in sorted(bucket_counts.items())
        },
        "governance_recommendations": gov_recommendations,
        "open_positions": open_positions,
        "symbols": rows,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _print_text(audit: dict) -> None:
    h = audit["headline_summary"]
    print("=" * 70)
    print("FOREVER PLAYBOOK AUDIT")
    print("=" * 70)
    print(
        f"\nHeadline (clean_paper_v10 + live_v10 only): "
        f"n={h['total_n']}  net=${h['total_net']:+.2f}  "
        f"pf={h['profit_factor']}  fees=${h['total_fees']:+.2f}"
    )

    c = audit["contamination"]
    if c["dirty_n"] > 0:
        print(f"\n[WARNING] {c['warning']}")

    print("\n── Bucket Summary ──")
    for bucket, bv in audit["bucket_summary"].items():
        print(
            f"  {bucket:25s}  symbols={bv['n_symbols']:3d}  "
            f"closes={bv['headline_n']:3d}  net=${bv['headline_net']:+7.2f}"
        )

    print("\n── Governance Recommendations ──")
    recs = audit["governance_recommendations"]
    if recs:
        for r in recs:
            print(
                f"  {r['symbol']:15s}  {r['current']:12s} → {r['recommended']:12s}  ({r['evidence']})"
            )
    else:
        print("  None — all symbols within evidence thresholds")

    print("\n── Symbol Detail ──")
    print(
        f"  {'Symbol':15s}  {'Bucket':25s}  {'Gov':12s}  "
        f"{'N':>4}  {'Net':>8}  {'Exp':>7}  {'WR%':>5}  {'Open':>4}"
    )
    for r in sorted(audit["symbols"], key=lambda x: -x["headline_n"]):
        if (
            r["headline_n"] == 0
            and r["governance"] in ("research_only",)
            and not r["open_position"]
        ):
            continue  # suppress zero-evidence research symbols from verbose view
        exp_str = f"{r['expectancy']:+.3f}" if r["expectancy"] is not None else "   n/a"
        wr_str = f"{r['win_rate']:5.1f}" if r["win_rate"] is not None else "  n/a"
        open_str = " YES" if r["open_position"] else "    "
        print(
            f"  {r['symbol']:15s}  {r['market_type']:25s}  {r['governance']:12s}  "
            f"{r['headline_n']:4d}  ${r['headline_net']:+7.2f}  {exp_str}  {wr_str}  {open_str}"
        )

    print("\n── Open Positions ──")
    for p in audit["open_positions"]:
        print(f"  {p['symbol']:15s}  qty={p['qty']:.4f}  entry={p['entry']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Forever Playbook live audit")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="trades.db path")
    parser.add_argument(
        "--price-db", default=str(DEFAULT_PRICE_DB), help="price_archive.db path"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--verbose", action="store_true", help="Include research-only symbols"
    )
    args = parser.parse_args()

    audit = build_audit(args.db, args.price_db)

    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    else:
        _print_text(audit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
