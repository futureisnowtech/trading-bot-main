"""
scripts/migrate_integrity_backfill.py — Idempotent integrity tier backfill.

Assigns a trust tier to every close-side trade not yet in trade_integrity.
Safe to run multiple times (INSERT OR IGNORE).

Tier assignment rules (applied in order):
  1. source contains 'contaminated'/'synthetic'/'replay'/'backtest'/'bootstrap'
     → excluded
  2. notes contains those same tags → excluded
  3. |pnl_usd| > 50% of ACCOUNT_SIZE → quarantined (suspect_pnl_magnitude)
  4. price <= 0 or qty <= 0 → quarantined (invalid_price_or_qty)
  5. has trade_attribution row + trade_features row → verified (lineage_complete)
  6. else → suspect (lineage_incomplete)

Usage:
    python3 scripts/migrate_integrity_backfill.py
    python3 scripts/migrate_integrity_backfill.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill trade_integrity table for all close-side trades."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching the DB.",
    )
    args = parser.parse_args()

    from logging_db.trade_logger import init_db, bulk_backfill_integrity

    # Ensure new tables exist (idempotent)
    print("[migrate] Running init_db() to ensure trade_integrity table exists...")
    init_db()

    if args.dry_run:
        # Dry-run: just show what bulk_backfill_integrity would find
        import sqlite3
        from config import DB_PATH, ACCOUNT_SIZE

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT t.id, t.order_id, t.pnl_usd, t.price, t.qty, t.source, t.notes
            FROM trades t
            LEFT JOIN trade_integrity ti
              ON ti.close_order_id = COALESCE(t.order_id, CAST(t.id AS TEXT))
            WHERE t.pnl_usd != 0 AND ti.id IS NULL
        """)
        rows = cur.fetchall()
        conn.close()
        print(
            f"[dry-run] Found {len(rows)} close-side trades without integrity records."
        )
        half_account = ACCOUNT_SIZE * 0.5
        preview: dict[str, int] = {
            "excluded": 0,
            "quarantined": 0,
            "verified": 0,
            "suspect": 0,
        }
        for row in rows:
            src = (row["source"] or "").lower()
            note_str = (row["notes"] or "").lower()
            if any(
                t in src
                for t in (
                    "contaminated",
                    "synthetic",
                    "replay",
                    "backtest",
                    "bootstrap",
                )
            ):
                preview["excluded"] += 1
            elif any(t in note_str for t in ("contaminated", "synthetic", "replay")):
                preview["excluded"] += 1
            elif abs(row["pnl_usd"] or 0) > half_account:
                preview["quarantined"] += 1
            elif (row["price"] or 0) <= 0 or (row["qty"] or 0) <= 0:
                preview["quarantined"] += 1
            else:
                # Lineage check requires actual DB queries — approximate as suspect
                preview["suspect"] += 1
        print(f"[dry-run] Would assign: {preview}")
        print("[dry-run] No changes made. Re-run without --dry-run to apply.")
        return

    print("[migrate] Running bulk_backfill_integrity()...")
    result = bulk_backfill_integrity()

    total_new = sum(v for k, v in result.items() if k != "skipped")
    print(f"\n[migrate] Backfill complete:")
    print(f"  verified    : {result.get('verified', 0)}")
    print(f"  suspect     : {result.get('suspect', 0)}")
    print(f"  quarantined : {result.get('quarantined', 0)}")
    print(f"  excluded    : {result.get('excluded', 0)}")
    print(f"  skipped     : {result.get('skipped', 0)}  (already had a record)")
    print(f"  total new   : {total_new}")

    # Print coverage summary
    from logging_db.trade_logger import get_integrity_summary

    summary = get_integrity_summary()
    print(
        f"\n[migrate] Current integrity coverage: "
        f"{summary.get('coverage_pct', 0):.1f}% "
        f"({sum([summary.get('verified', 0), summary.get('suspect', 0), summary.get('quarantined', 0), summary.get('excluded', 0)])} / "
        f"{summary.get('total_closes', 0)} close-side trades)"
    )

    if summary.get("quarantined", 0) > 0:
        print(
            f"\n[migrate] WARNING: {summary['quarantined']} quarantined trades found."
        )
        print("  These will NOT influence Bayesian weights or Kelly sizing.")
        print("  Review them in the dashboard Integrity & Truth panel.")

    if summary.get("excluded", 0) > 0:
        print(
            f"[migrate] INFO: {summary['excluded']} excluded trades "
            "(pre-v10 contaminated / synthetic). These are kept for reference only."
        )


if __name__ == "__main__":
    main()
