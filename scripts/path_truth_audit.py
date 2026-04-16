#!/usr/bin/env python3
"""
scripts/path_truth_audit.py — Trade path truth audit (v16).

Four sections:
  1. R-Multiple Reach    — % of entered candidates hitting 0.5R / 1R / 2R within 4h
  2. Timing to Threshold — median / mean minutes to reach each R level
  3. Path by Group       — R-reach and timing broken down by regime and direction
  4. Exit Quality        — opportunity loss, stop overshoot, MFE-at-exit summary

All sections restricted to source IN ('clean_paper_v10', 'live_v10').
Timing columns require that the labeler has run with path-timing support (v16).

Usage:
  python3 scripts/path_truth_audit.py
  python3 scripts/path_truth_audit.py --days 14
  python3 scripts/path_truth_audit.py --json
  python3 scripts/path_truth_audit.py --days 30 --json
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


def _db_path() -> str:
    try:
        from config import DB_PATH as _dp

        return _dp
    except Exception:
        return os.path.join(_ROOT, "logs", "trades.db")


def _cutoff(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Section 1: R-Multiple Reach ───────────────────────────────────────────────


def r_multiple_reach(conn: sqlite3.Connection, days: int) -> dict:
    """Percentage of entered candidates reaching 0.5R / 1R / 2R within 4h."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            COUNT(*)  AS n,
            SUM(CASE WHEN co.mfe_4h_pct >= sc.stop_pct * 0.5 THEN 1 ELSE 0 END) AS hit_05r,
            SUM(co.hit_1r)  AS hit_1r,
            SUM(co.hit_2r)  AS hit_2r,
            SUM(co.hit_stop) AS hit_stop,
            AVG(co.peak_r_4h) AS avg_peak_r,
            AVG(co.mfe_4h_pct) AS avg_mfe,
            AVG(co.mae_4h_pct) AS avg_mae
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.decision = 'entered'
          AND sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )
    row = rows[0] if rows else {}
    n = row.get("n") or 0

    def pct(x):
        v = row.get(x) or 0
        return round(v / n * 100, 1) if n > 0 else 0.0

    return {
        "n": n,
        "hit_05r_pct": pct("hit_05r"),
        "hit_1r_pct": pct("hit_1r"),
        "hit_2r_pct": pct("hit_2r"),
        "hit_stop_pct": pct("hit_stop"),
        "avg_peak_r_4h": round(row.get("avg_peak_r") or 0, 3),
        "avg_mfe_pct": round(row.get("avg_mfe") or 0, 3),
        "avg_mae_pct": round(row.get("avg_mae") or 0, 3),
    }


# ── Section 2: Timing to Threshold ────────────────────────────────────────────


def timing_to_threshold(conn: sqlite3.Connection, days: int) -> dict:
    """Median and mean minutes to reach each R level (from path timing columns)."""
    cut = _cutoff(days)

    def _stats(col: str) -> dict:
        rows = _query(
            conn,
            f"""
            SELECT
                COUNT({col}) AS n_reached,
                AVG({col})   AS mean_min,
                MIN({col})   AS min_min,
                MAX({col})   AS max_min
            FROM scan_candidates sc
            JOIN candidate_outcomes co ON co.candidate_id = sc.id
            WHERE sc.decision = 'entered'
              AND sc.source IN ('clean_paper_v10', 'live_v10')
              AND co.label_status = 'complete'
              AND {col} IS NOT NULL
              AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
            """,
            (cut,),
        )
        r = rows[0] if rows else {}
        # Approximate median via SQLite window (not available in all versions) — use sorted midpoint
        median_rows = _query(
            conn,
            f"""
            SELECT {col} AS v
            FROM scan_candidates sc
            JOIN candidate_outcomes co ON co.candidate_id = sc.id
            WHERE sc.decision = 'entered'
              AND sc.source IN ('clean_paper_v10', 'live_v10')
              AND co.label_status = 'complete'
              AND {col} IS NOT NULL
              AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
            ORDER BY {col}
            """,
            (cut,),
        )
        vals = [mr["v"] for mr in median_rows if mr["v"] is not None]
        median = None
        if vals:
            mid = len(vals) // 2
            median = (
                vals[mid] if len(vals) % 2 == 1 else (vals[mid - 1] + vals[mid]) / 2
            )

        return {
            "n_reached": r.get("n_reached") or 0,
            "median_min": median,
            "mean_min": round(r.get("mean_min") or 0, 1) if r.get("mean_min") else None,
            "min_min": r.get("min_min"),
            "max_min": r.get("max_min"),
        }

    # Total entered for reach-rate context
    total_rows = _query(
        conn,
        """
        SELECT COUNT(*) AS n
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.decision = 'entered'
          AND sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )
    total_n = (total_rows[0].get("n") or 0) if total_rows else 0

    s05 = _stats("co.time_to_05r_min")
    s1r = _stats("co.time_to_1r_min")
    s2r = _stats("co.time_to_2r_min")

    def _reach_pct(n_reached: int) -> float:
        return round(n_reached / total_n * 100, 1) if total_n > 0 else 0.0

    return {
        "total_entered_labeled": total_n,
        "time_to_05r": {**s05, "reach_pct": _reach_pct(s05["n_reached"])},
        "time_to_1r": {**s1r, "reach_pct": _reach_pct(s1r["n_reached"])},
        "time_to_2r": {**s2r, "reach_pct": _reach_pct(s2r["n_reached"])},
        "note": (
            "NULL timing = threshold not reached within available bars. "
            "reach_pct based on total labeled entered candidates."
        ),
    }


# ── Section 3: Path by Group ──────────────────────────────────────────────────


def path_by_group(conn: sqlite3.Connection, days: int) -> list[dict]:
    """R-reach and avg timing broken down by regime × direction."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            sc.regime,
            sc.direction,
            COUNT(*) AS n,
            SUM(co.hit_1r) AS hit_1r,
            SUM(co.hit_2r) AS hit_2r,
            AVG(co.peak_r_4h) AS avg_peak_r,
            AVG(co.time_to_1r_min) AS avg_time_to_1r,
            AVG(co.time_to_2r_min) AS avg_time_to_2r,
            AVG(co.mfe_4h_pct) AS avg_mfe
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE sc.decision = 'entered'
          AND sc.source IN ('clean_paper_v10', 'live_v10')
          AND co.label_status = 'complete'
          AND datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(?)
        GROUP BY sc.regime, sc.direction
        ORDER BY n DESC
        LIMIT 16
        """,
        (cut,),
    )
    return [
        {
            "regime": r.get("regime") or "UNKNOWN",
            "direction": r.get("direction") or "LONG",
            "n": r["n"],
            "hit_1r_pct": round((r.get("hit_1r") or 0) / r["n"] * 100, 1),
            "hit_2r_pct": round((r.get("hit_2r") or 0) / r["n"] * 100, 1),
            "avg_peak_r": round(r.get("avg_peak_r") or 0, 3),
            "avg_time_to_1r_min": (
                round(r["avg_time_to_1r"], 0) if r.get("avg_time_to_1r") else None
            ),
            "avg_time_to_2r_min": (
                round(r["avg_time_to_2r"], 0) if r.get("avg_time_to_2r") else None
            ),
            "avg_mfe_pct": round(r.get("avg_mfe") or 0, 3),
        }
        for r in rows
    ]


# ── Section 4: Exit Quality Context ──────────────────────────────────────────


def exit_quality_context(conn: sqlite3.Connection, days: int) -> dict:
    """Opportunity loss, stop overshoot, and MFE-at-exit from exit_evaluations."""
    cut = _cutoff(days)
    rows = _query(
        conn,
        """
        SELECT
            COUNT(*) AS n,
            AVG(opportunity_loss_pct) AS avg_opp_loss,
            AVG(stop_overshoot_pct)   AS avg_overshoot,
            AVG(mfe_at_exit)          AS avg_mfe_at_exit,
            SUM(CASE WHEN opportunity_loss_pct > 1.0 THEN 1 ELSE 0 END) AS high_opp_loss,
            SUM(CASE WHEN stop_overshoot_pct > 0.5 THEN 1 ELSE 0 END)   AS high_overshoot
        FROM exit_evaluations
        WHERE datetime(replace(substr(created_at,1,19),'T',' ')) >= datetime(?)
        """,
        (cut,),
    )
    row = rows[0] if rows else {}
    n = row.get("n") or 0

    path_label_rows = _query(
        conn,
        """
        SELECT path_label, COUNT(*) AS n
        FROM exit_evaluations
        WHERE datetime(replace(substr(created_at,1,19),'T',' ')) >= datetime(?)
        GROUP BY path_label
        ORDER BY n DESC
        """,
        (cut,),
    )

    return {
        "n": n,
        "avg_opportunity_loss_pct": round(row.get("avg_opp_loss") or 0, 3),
        "avg_stop_overshoot_pct": round(row.get("avg_overshoot") or 0, 3),
        "avg_mfe_at_exit_pct": round(row.get("avg_mfe_at_exit") or 0, 3),
        "high_opportunity_loss_count": row.get("high_opp_loss") or 0,
        "high_overshoot_count": row.get("high_overshoot") or 0,
        "path_labels": {
            r["path_label"]: r["n"] for r in path_label_rows if r.get("path_label")
        },
    }


# ── Printing ──────────────────────────────────────────────────────────────────


def _print_report(report: dict) -> None:
    days = report["days"]
    print(f"\n{'=' * 60}")
    print(f"  PATH TRUTH AUDIT  (last {days} days)")
    print(f"{'=' * 60}")

    # Section 1
    rm = report["r_multiple_reach"]
    print(f"\n── 1. R-Multiple Reach (n={rm['n']} labeled entered) ────────────")
    print(f"   Hit 0.5R:  {rm['hit_05r_pct']:>5.1f}%")
    print(f"   Hit 1R:    {rm['hit_1r_pct']:>5.1f}%")
    print(f"   Hit 2R:    {rm['hit_2r_pct']:>5.1f}%")
    print(f"   Hit Stop:  {rm['hit_stop_pct']:>5.1f}%")
    print(f"   Avg peak R (4h): {rm['avg_peak_r_4h']:.3f}R")
    print(f"   Avg MFE: {rm['avg_mfe_pct']:.3f}%  Avg MAE: {rm['avg_mae_pct']:.3f}%")

    # Section 2
    tm = report["timing"]
    total = tm["total_entered_labeled"]
    print(f"\n── 2. Timing to Threshold (n={total} total labeled) ─────────────")
    for key, label in [
        ("time_to_05r", "0.5R"),
        ("time_to_1r", "1R"),
        ("time_to_2r", "2R"),
    ]:
        t = tm[key]
        med = f"{t['median_min']:.0f}m" if t["median_min"] is not None else "N/A"
        mean = f"{t['mean_min']:.0f}m" if t.get("mean_min") is not None else "N/A"
        print(
            f"   {label:<5}: reached={t['n_reached']} ({t['reach_pct']}%)  "
            f"median={med}  mean={mean}"
        )
    print(f"   Note: {tm['note']}")

    # Section 3
    groups = report["path_by_group"]
    print(f"\n── 3. Path by Group (regime × direction) ─────────────────────────")
    if not groups:
        print("   No labeled entered candidates yet.")
    else:
        print(
            f"   {'Regime':<16} {'Dir':<6} {'n':>4} {'1R%':>5} {'2R%':>5} {'AvgPeakR':>9} {'Avg→1R':>8}"
        )
        print(
            f"   {'-' * 16} {'-' * 6} {'---':>4} {'---':>5} {'---':>5} {'-' * 9} {'-' * 8}"
        )
        for r in groups:
            t1r = (
                f"{r['avg_time_to_1r_min']:.0f}m"
                if r.get("avg_time_to_1r_min")
                else "N/A"
            )
            print(
                f"   {r['regime']:<16} {r['direction']:<6} {r['n']:>4} "
                f"{r['hit_1r_pct']:>4.1f}% {r['hit_2r_pct']:>4.1f}% "
                f"{r['avg_peak_r']:>8.3f}R {t1r:>7}"
            )

    # Section 4
    eq = report["exit_quality"]
    print(f"\n── 4. Exit Quality Context (n={eq['n']} closes) ──────────────────")
    print(f"   Avg opp loss:    {eq['avg_opportunity_loss_pct']:.3f}%")
    print(f"   Avg stop overshoot: {eq['avg_stop_overshoot_pct']:.3f}%")
    print(f"   Avg MFE at exit: {eq['avg_mfe_at_exit_pct']:.3f}%")
    print(f"   High opp loss (>1%): {eq['high_opportunity_loss_count']}")
    print(f"   High overshoot (>0.5%): {eq['high_overshoot_count']}")
    if eq["path_labels"]:
        print("   Path labels:")
        for lbl, cnt in sorted(eq["path_labels"].items(), key=lambda x: -x[1]):
            print(f"     {lbl:<20}: {cnt}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────


def main(args=None) -> int:
    parser = argparse.ArgumentParser(description="Path truth audit")
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
            "r_multiple_reach": r_multiple_reach(conn, opts.days),
            "timing": timing_to_threshold(conn, opts.days),
            "path_by_group": path_by_group(conn, opts.days),
            "exit_quality": exit_quality_context(conn, opts.days),
        }

    if opts.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
