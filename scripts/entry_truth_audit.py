#!/usr/bin/env python3
"""
scripts/entry_truth_audit.py — Entry signal truth audit (v16).

Six sections:
  1. Funnel Summary       — scan_funnels aggregate by terminal decision
  2. Scanner EV           — theoretical vs effective position size calibration
  3. Source Quality       — win rate by exchange/source
  4. Setup Quality        — win rate by primary_setup
  5. Symbol Class Quality — win rate by execution tier (core vs research_only)
  6. Integrity Snapshot   — duplicate detection and integrity tier breakdown

Win label: hit_1r=1 AND hit_stop=0
All queries restricted to source IN ('clean_paper_v10', 'live_v10') entered candidates.

Usage:
  python3 scripts/entry_truth_audit.py
  python3 scripts/entry_truth_audit.py --days 14
  python3 scripts/entry_truth_audit.py --json
  python3 scripts/entry_truth_audit.py --days 30 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime.execution_universe import get_underlying

try:
    from config import CORE_EXECUTION_UNDERLYINGS as _CORE_UNDERLYINGS
except Exception:
    _CORE_UNDERLYINGS = set()


def _db_path() -> str:
    try:
        from config import DB_PATH as _dp

        return _dp
    except Exception:
        return os.path.join(_ROOT, "logs", "trades.db")


def _cutoff(days: int) -> str:
    """ISO timestamp cutoff string for SQLite queries."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Section 1: Funnel Summary ─────────────────────────────────────────────────


def funnel_summary(conn: sqlite3.Connection, days: int) -> dict:
    """Aggregate scan_funnels over the window.

    Column mapping (actual scan_funnels schema):
      scanner_candidates_total → scanned
      scored_total             → above_threshold (scored above composite threshold)
      econ_passed_total        → econ_passed (research_only + sizing + exec_fail + entered)
    """
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            SUM(scanner_candidates_total) AS scanned,
            SUM(scored_total)             AS above_threshold,
            SUM(econ_passed_total)        AS econ_passed,
            SUM(econ_veto)                AS econ_veto,
            SUM(research_only_block)      AS research_only_block,
            SUM(sizing_zero)              AS sizing_zero,
            SUM(execution_failed)         AS execution_failed,
            SUM(entered)                  AS entered,
            COUNT(*)                      AS cycles
        FROM scan_funnels
        WHERE datetime(replace(substr(ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )
    row = rows[0] if rows else {}

    scanned = row.get("scanned") or 0
    above = row.get("above_threshold") or 0
    econ = row.get("econ_passed") or 0
    entered = row.get("entered") or 0
    econ_veto = row.get("econ_veto") or 0

    conversion_rate = round(entered / above * 100, 1) if above > 0 else 0.0
    econ_veto_rate = round(econ_veto / above * 100, 1) if above > 0 else 0.0

    return {
        "days": days,
        "cycles": row.get("cycles") or 0,
        "scanned": scanned,
        "above_threshold": above,
        "econ_passed": econ,
        "econ_veto": econ_veto,
        "research_only_block": row.get("research_only_block") or 0,
        "sizing_zero": row.get("sizing_zero") or 0,
        "execution_failed": row.get("execution_failed") or 0,
        "entered": entered,
        "conversion_rate_pct": conversion_rate,
        "econ_veto_rate_pct": econ_veto_rate,
    }


# ── Section 2: Scanner EV Calibration ────────────────────────────────────────


def scanner_ev_calibration(conn: sqlite3.Connection, days: int) -> dict:
    """Compare theoretical vs effective position sizes from scan_candidates."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            AVG(scanner_theoretical_position_usd) AS avg_theoretical,
            AVG(scanner_effective_position_usd)   AS avg_effective,
            MIN(scanner_theoretical_position_usd) AS min_theoretical,
            MAX(scanner_theoretical_position_usd) AS max_theoretical,
            MIN(scanner_effective_position_usd)   AS min_effective,
            MAX(scanner_effective_position_usd)   AS max_effective,
            COUNT(*) AS n
        FROM scan_candidates
        WHERE scanner_theoretical_position_usd IS NOT NULL
          AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )
    row = rows[0] if rows else {}

    avg_theor = row.get("avg_theoretical") or 0.0
    avg_eff = row.get("avg_effective") or 0.0
    cap_rate = round((1 - avg_eff / avg_theor) * 100, 1) if avg_theor > 0 else 0.0

    return {
        "n": row.get("n") or 0,
        "avg_theoretical_usd": round(avg_theor, 2),
        "avg_effective_usd": round(avg_eff, 2),
        "min_theoretical_usd": round(row.get("min_theoretical") or 0, 2),
        "max_theoretical_usd": round(row.get("max_theoretical") or 0, 2),
        "min_effective_usd": round(row.get("min_effective") or 0, 2),
        "max_effective_usd": round(row.get("max_effective") or 0, 2),
        "effective_cap_rate_pct": cap_rate,
        "note": "effective = min(theoretical, $100) to prevent unrealistic EV",
    }


# ── Section 3: Source Quality ─────────────────────────────────────────────────


def source_quality(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Win rate by exchange/source for clean entered candidates."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            sc.exchange,
            sc.source,
            COUNT(*) AS n,
            SUM(CASE WHEN co.hit_1r=1 AND co.hit_stop=0 THEN 1 ELSE 0 END) AS wins,
            AVG(CASE WHEN co.hit_1r=1 AND co.hit_stop=0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(co.mfe_4h_pct) AS avg_mfe
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.decision = 'entered'
          AND sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        GROUP BY sc.exchange, sc.source
        ORDER BY n DESC
        """,
        (cut,),
    )
    return [
        {
            "exchange": r.get("exchange") or "unknown",
            "source": r.get("source") or "unknown",
            "n": r["n"],
            "wins": r["wins"] or 0,
            "win_rate_pct": round((r.get("win_rate") or 0) * 100, 1),
            "avg_mfe_pct": round(r.get("avg_mfe") or 0, 3),
        }
        for r in rows
    ]


# ── Section 4: Setup Quality ──────────────────────────────────────────────────


def setup_quality(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Win rate by primary_setup for clean entered candidates."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            sc.primary_setup,
            sc.regime,
            COUNT(*) AS n,
            SUM(CASE WHEN co.hit_1r=1 AND co.hit_stop=0 THEN 1 ELSE 0 END) AS wins,
            AVG(CASE WHEN co.hit_1r=1 AND co.hit_stop=0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(co.mfe_4h_pct) AS avg_mfe
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.decision = 'entered'
          AND sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        GROUP BY sc.primary_setup, sc.regime
        ORDER BY n DESC
        LIMIT 20
        """,
        (cut,),
    )
    return [
        {
            "primary_setup": r.get("primary_setup") or "unknown",
            "regime": r.get("regime") or "unknown",
            "n": r["n"],
            "wins": r["wins"] or 0,
            "win_rate_pct": round((r.get("win_rate") or 0) * 100, 1),
            "avg_mfe_pct": round(r.get("avg_mfe") or 0, 3),
        }
        for r in rows
    ]


# ── Section 5: Symbol Class Quality ──────────────────────────────────────────


def symbol_class_quality(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Win rate by execution tier, classified from the actual underlying."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            sc.symbol,
            sc.decision,
            co.hit_1r,
            co.hit_stop,
            co.mfe_4h_pct,
            co.mae_4h_pct
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND sc.decision IN ('entered', 'research_only_block')
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )

    _core_upper = {u.upper() for u in _CORE_UNDERLYINGS}
    buckets: dict[str, dict[str, float]] = {
        "core": {"n": 0, "wins": 0, "mfe_sum": 0.0, "mae_sum": 0.0},
        "research_only": {"n": 0, "wins": 0, "mfe_sum": 0.0, "mae_sum": 0.0},
    }

    for row in rows:
        underlying = get_underlying(str(row.get("symbol") or ""))
        tier = "core" if underlying.upper() in _core_upper else "research_only"
        bucket = buckets[tier]
        bucket["n"] += 1
        bucket["wins"] += (
            1
            if int(row.get("hit_1r") or 0) == 1 and int(row.get("hit_stop") or 0) == 0
            else 0
        )
        bucket["mfe_sum"] += float(row.get("mfe_4h_pct") or 0.0)
        bucket["mae_sum"] += float(row.get("mae_4h_pct") or 0.0)

    result = []
    for tier, bucket in buckets.items():
        n = int(bucket["n"])
        if n == 0:
            continue
        wins = int(bucket["wins"])
        result.append(
            {
                "tier": tier,
                "n": n,
                "wins": wins,
                "win_rate_pct": round(wins / n * 100, 1),
                "avg_mfe_pct": round(bucket["mfe_sum"] / n, 3),
                "avg_mae_pct": round(bucket["mae_sum"] / n, 3),
            }
        )

    return sorted(result, key=lambda r: r["tier"])


# ── Section 6: Integrity Snapshot ────────────────────────────────────────────


def integrity_snapshot(conn: sqlite3.Connection, days: int) -> dict:
    """Integrity tier breakdown, duplicate-close events, and top non-verified reasons."""
    cut = _cutoff(days)

    tier_rows = _query(
        conn,
        """
        SELECT tier, COUNT(*) AS n
        FROM trade_integrity
        WHERE datetime(replace(substr(created_at,1,19),'T',' ')) >= datetime(?)
        GROUP BY tier
        ORDER BY n DESC
        """,
        (cut,),
    )

    dup_rows = _query(
        conn,
        """
        SELECT ts
        FROM system_events
        WHERE message LIKE '%duplicate close%'
          AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )

    reason_rows = _query(
        conn,
        """
        SELECT tier, reason, COUNT(*) AS n
        FROM trade_integrity
        WHERE datetime(replace(substr(created_at,1,19),'T',' ')) >= datetime(?)
          AND tier != 'verified'
          AND reason != ''
        GROUP BY tier, reason
        ORDER BY n DESC
        LIMIT 5
        """,
        (cut,),
    )

    tiers = {r["tier"]: r["n"] for r in tier_rows}
    total = sum(tiers.values())
    trusted = tiers.get("verified", 0)
    trust_rate = round(trusted / total * 100, 1) if total > 0 else 0.0

    return {
        "total_closes": total,
        "tiers": tiers,
        "trust_rate_pct": trust_rate,
        "duplicate_close_event_count": len(dup_rows),
        "top_non_verified_reasons": [
            {"tier": r["tier"], "reason": r["reason"], "count": r["n"]}
            for r in reason_rows
        ],
    }


# ── Printing ──────────────────────────────────────────────────────────────────


def _print_report(report: dict) -> None:
    days = report["days"]
    print(f"\n{'=' * 60}")
    print(f"  ENTRY TRUTH AUDIT  (last {days} days)")
    print(f"{'=' * 60}")

    # Section 1
    f = report["funnel"]
    print(f"\n── 1. Funnel Summary ({f['cycles']} scan cycles) ─────────────────")
    print(f"   Scanned:          {f['scanned']:>7}")
    print(f"   Above threshold:  {f['above_threshold']:>7}")
    print(f"   Econ vetoed:      {f['econ_veto']:>7}  ({f['econ_veto_rate_pct']}%)")
    print(f"   Research-only:    {f['research_only_block']:>7}")
    print(f"   Sizing zero:      {f['sizing_zero']:>7}")
    print(f"   Execution failed: {f['execution_failed']:>7}")
    print(
        f"   ENTERED:          {f['entered']:>7}  (conversion {f['conversion_rate_pct']}%)"
    )

    # Section 2
    ev = report["scanner_ev"]
    print(f"\n── 2. Scanner EV Calibration (n={ev['n']}) ──────────────────────")
    print(
        f"   Avg theoretical pos: ${ev['avg_theoretical_usd']:.2f}  (range ${ev['min_theoretical_usd']:.0f}–${ev['max_theoretical_usd']:.0f})"
    )
    print(f"   Avg effective pos:   ${ev['avg_effective_usd']:.2f}  (capped at $100)")
    print(
        f"   Capped rate:         {ev['effective_cap_rate_pct']}% of candidates were capped"
    )

    # Section 3
    print(f"\n── 3. Source Quality ─────────────────────────────────────────────")
    srcs = report["source_quality"]
    if not srcs:
        print("   No labeled entered candidates yet.")
    else:
        print(f"   {'Exchange':<16} {'Source':<20} {'n':>5} {'WR%':>6} {'AvgMFE':>8}")
        print(f"   {'-' * 16} {'-' * 20} {'---':>5} {'---':>6} {'------':>8}")
        for r in srcs:
            print(
                f"   {r['exchange']:<16} {r['source']:<20} {r['n']:>5} "
                f"{r['win_rate_pct']:>5.1f}% {r['avg_mfe_pct']:>7.3f}%"
            )

    # Section 4
    print(f"\n── 4. Setup Quality (top 10) ─────────────────────────────────────")
    setups = report["setup_quality"][:10]
    if not setups:
        print("   No labeled entered candidates yet.")
    else:
        print(f"   {'Setup':<22} {'Regime':<14} {'n':>5} {'WR%':>6}")
        print(f"   {'-' * 22} {'-' * 14} {'---':>5} {'---':>6}")
        for r in setups:
            print(
                f"   {r['primary_setup']:<22} {r['regime']:<14} "
                f"{r['n']:>5} {r['win_rate_pct']:>5.1f}%"
            )

    # Section 5
    print(f"\n── 5. Symbol Class Quality ───────────────────────────────────────")
    classes = report["symbol_class_quality"]
    if not classes:
        print("   No labeled candidates yet.")
    else:
        print(f"   {'Tier':<16} {'n':>5} {'WR%':>6} {'AvgMFE':>8} {'AvgMAE':>8}")
        print(f"   {'-' * 16} {'---':>5} {'---':>6} {'------':>8} {'------':>8}")
        for r in classes:
            print(
                f"   {r['tier']:<16} {r['n']:>5} {r['win_rate_pct']:>5.1f}% "
                f"{r['avg_mfe_pct']:>7.3f}% {r['avg_mae_pct']:>7.3f}%"
            )

    # Section 6
    ig = report["integrity"]
    print(f"\n── 6. Integrity Snapshot ─────────────────────────────────────────")
    print(f"   Total closes: {ig['total_closes']}  trust rate: {ig['trust_rate_pct']}%")
    for tier, n in sorted(ig["tiers"].items()):
        print(f"     {tier:<16}: {n}")
    if ig["duplicate_close_event_count"]:
        print(f"   ⚠  Duplicate close events: {ig['duplicate_close_event_count']}")
    else:
        print("   No duplicate close events detected.")
    for row in ig.get("top_non_verified_reasons", [])[:3]:
        print(f"     {row['tier']:<16}: {row['reason']} ({row['count']})")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────


def main(args=None) -> int:
    parser = argparse.ArgumentParser(description="Entry truth audit")
    parser.add_argument(
        "--days", type=int, default=7, help="Lookback window (default 7)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    opts = parser.parse_args(args)

    db = _db_path()
    if not os.path.exists(db):
        print(f"DB not found: {db}", file=sys.stderr)
        return 1

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        report = {
            "days": opts.days,
            "funnel": funnel_summary(conn, opts.days),
            "scanner_ev": scanner_ev_calibration(conn, opts.days),
            "source_quality": source_quality(conn, opts.days),
            "setup_quality": setup_quality(conn, opts.days),
            "symbol_class_quality": symbol_class_quality(conn, opts.days),
            "integrity": integrity_snapshot(conn, opts.days),
        }

    if opts.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
