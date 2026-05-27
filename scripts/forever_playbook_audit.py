#!/usr/bin/env python3
"""
scripts/forever_playbook_audit.py — Forever Playbook live audit (v19.1 Ledgerless).
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

def _load_symbol_stats(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    headline = defaultdict(lambda: {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0, "wins": 0, "exit_types": defaultdict(float)})
    dirty = defaultdict(lambda: {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})

    c.execute("SELECT symbol, pnl_usd, fee_usd, exit_type FROM trades WHERE source IN ('clean_paper_v10','live_v10') AND action IN ('SELL','close','cover','COVER') AND pnl_usd IS NOT NULL")
    for r in c.fetchall():
        s = r["symbol"]
        headline[s]["n"] += 1
        headline[s]["gross"] += r["pnl_usd"]
        headline[s]["fees"] += r["fee_usd"] or 0.0
        headline[s]["net"] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)
        if (r["pnl_usd"] or 0.0) > 0: headline[s]["wins"] += 1
        headline[s]["exit_types"][r["exit_type"] or "none"] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)

    c.execute("SELECT symbol, pnl_usd, fee_usd FROM trades WHERE source NOT IN ('clean_paper_v10','live_v10') AND action IN ('SELL','close','cover','COVER') AND pnl_usd IS NOT NULL")
    for r in c.fetchall():
        s = r["symbol"]
        dirty[s]["n"] += 1
        dirty[s]["gross"] += r["pnl_usd"] or 0.0
        dirty[s]["fees"] += r["fee_usd"] or 0.0
        dirty[s]["net"] += (r["pnl_usd"] or 0.0) - (r["fee_usd"] or 0.0)
    conn.close()
    return {"headline": dict(headline), "dirty": dict(dirty)}

def _load_scanner_universe(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT DISTINCT symbol FROM scan_candidates ORDER BY symbol")
    syms = [r[0] for r in c.fetchall()]
    conn.close()
    return syms

def build_audit(db_path: str, price_db: str) -> dict:
    price_db_exists = os.path.exists(price_db)
    pdb = price_db if price_db_exists else None
    stats = _load_symbol_stats(db_path)
    headline, dirty = stats["headline"], stats["dirty"]
    scanner_symbols = _load_scanner_universe(db_path)

    all_symbols = sorted(set(scanner_symbols) | set(headline.keys()) | set(dirty.keys()))
    rows, gov_recommendations = [], []

    for sym in all_symbols:
        policy = get_policy(sym, price_db=pdb)
        h, d = headline.get(sym, {}), dirty.get(sym, {})
        n, net = h.get("n", 0), h.get("net", 0.0)
        wr = round(h.get("wins", 0) / n * 100, 1) if n > 0 else None
        exp = round(net / n, 3) if n > 0 else None

        if n >= 3 and exp is not None:
            rec = evaluate_governance_update(sym, n, net, exp, policy.governance)
            if rec: gov_recommendations.append({"symbol": sym, "current": policy.governance.value, "recommended": rec[0].value, "evidence": rec[1]})

        rows.append({
            "symbol": sym, "market_type": policy.market_type.value, "governance": policy.governance.value,
            "can_long": policy.can_long, "can_short": policy.can_short, "max_size_pct": policy.max_size_pct,
            "headline_n": n, "headline_net": round(net, 2), "win_rate": wr, "expectancy": exp,
            "dirty_n": d.get("n", 0), "dirty_net": round(d.get("net", 0.0), 2),
            "exit_types": dict(h.get("exit_types", {})), "in_scanner": sym in scanner_symbols,
        })

    total_n = sum(r["headline_n"] for r in rows)
    total_net = sum(r["headline_net"] for r in rows)
    return {
        "headline_summary": {"total_n": total_n, "total_net": round(total_net, 2), "source_filter": list(HEADLINE_SOURCES)},
        "bucket_summary": {k: {"n_symbols": len([r for r in rows if r["market_type"] == k]), "headline_n": sum(r["headline_n"] for r in rows if r["market_type"] == k), "headline_net": round(sum(r["headline_net"] for r in rows if r["market_type"] == k), 2)} for k in sorted(set(r["market_type"] for r in rows))},
        "governance_recommendations": gov_recommendations,
        "symbols": rows,
    }

def _print_text(audit: dict) -> None:
    h = audit["headline_summary"]
    print("=" * 70 + "\nFOREVER PLAYBOOK AUDIT\n" + "=" * 70)
    print(f"\nHeadline: n={h['total_n']}  net=${h['total_net']:+.2f}")
    print("\n── Bucket Summary ──")
    for b, bv in audit["bucket_summary"].items():
        print(f"  {b:25s}  symbols={bv['n_symbols']:3d}  closes={bv['headline_n']:3d}  net=${bv['headline_net']:+7.2f}")
    print("\n── Governance Recommendations ──")
    if audit["governance_recommendations"]:
        for r in audit["governance_recommendations"]: print(f"  {r['symbol']:15s}  {r['current']:12s} → {r['recommended']:12s}  ({r['evidence']})")
    else: print("  None")
    print("\n── Symbol Detail ──")
    print(f"  {'Symbol':15s}  {'Bucket':25s}  {'Gov':12s}  {'N':>4}  {'Net':>8}  {'WR%':>5}")
    for r in sorted(audit["symbols"], key=lambda x: -x["headline_n"]):
        if r["headline_n"] == 0 and r["governance"] in ("research_only",): continue
        exp_str = f"{r['expectancy']:+.3f}" if r["expectancy"] is not None else "   n/a"
        wr_str = f"{r['win_rate']:5.1f}" if r["win_rate"] is not None else "  n/a"
        print(f"  {r['symbol']:15s}  {r['market_type']:25s}  {r['governance']:12s}  {r['headline_n']:4d}  ${r['headline_net']:+7.2f}  {wr_str}")

def main() -> int:
    parser = argparse.ArgumentParser(description="Forever Playbook live audit")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="trades.db path")
    parser.add_argument("--price-db", default=str(DEFAULT_PRICE_DB), help="price_archive.db path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()
    audit = build_audit(args.db, args.price_db)
    if args.json: print(json.dumps(audit, indent=2, default=str))
    else: _print_text(audit)
    return 0

if __name__ == "__main__": sys.exit(main())
