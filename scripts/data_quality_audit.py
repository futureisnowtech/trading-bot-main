#!/usr/bin/env python3
"""Runtime data-quality audit against the live trade ledger.

Static analysis cannot see these. q_hat was NULL on all 76,458 resolution rows
for months: the column existed, the writer existed, the code ran, and nobody
noticed because nothing asserted that the data actually arrived.

Checks:
  1. tables that have a writer in code but zero rows          (dead feedback loop)
  2. columns that are 100% NULL on recent rows                (writer never fires)
  3. columns whose NULL rate regressed vs their own history   (writer broke)
  4. taxonomy drift between columns that should share a vocabulary
  5. rotating-buffer tables reported by max(rowid) rather than count(*)

Read-only. Run with --strict to exit non-zero on any finding.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

DEFAULT_DB = "/app/logs/trades.db"

# Columns that are load-bearing: if they are NULL the feature is silently dead.
CRITICAL_COLUMNS = {
    "forecast_positions": ["q_hat", "ev_at_entry", "entry_price", "side"],
    "forecast_resolutions": ["resolved_side", "resolved_at", "q_hat"],
    "trades": ["order_type", "fee_usd", "price", "qty"],
    "scan_funnels": ["scanner_candidates_total", "entered"],
}

# Pairs that describe the same event and must not diverge into two vocabularies.
TAXONOMY_PAIRS = [
    (("forecast_positions", "exit_type"), ("trades", "notes"),
     "exit classification: the runner's label vs what the broker was actually told"),
]


def _rows(con, sql, params=()):
    try:
        return list(con.execute(sql, params))
    except sqlite3.Error:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--recent-days", type=int, default=7)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    findings: list[str] = []

    def section(title: str, msgs: list[str], why: str) -> None:
        print(f"\n\033[1m{title}\033[0m")
        if not msgs:
            print("  PASS")
            return
        for m in msgs:
            print(f"  FAIL  {m}")
        print(f"  -> {why}")
        findings.extend(msgs)

    tables = [r[0] for r in _rows(con, "SELECT name FROM sqlite_master WHERE type='table'")]

    # 1. empty tables
    msgs = []
    for t in tables:
        if t == "sqlite_sequence":
            continue
        n = _rows(con, f"SELECT COUNT(*) FROM {t}")
        if n and n[0][0] == 0:
            msgs.append(f"table '{t}' is empty -- schema exists, nothing ever landed")
    section("CHECK 1  tables with zero rows", msgs,
            "A dead feedback loop. Every threshold decision made without it is unfalsifiable.")

    # 2/3. NULL rates on critical columns
    msgs, warn = [], []
    for table, cols in CRITICAL_COLUMNS.items():
        if table not in tables:
            continue
        present = {d[1] for d in _rows(con, f"pragma table_info({table})")}
        tscol = "ts" if "ts" in present else ("opened_at" if "opened_at" in present else None)
        total = _rows(con, f"SELECT COUNT(*) FROM {table}")[0][0]
        if not total:
            continue
        for c in cols:
            if c not in present:
                msgs.append(f"{table}.{c} is declared critical but the column does not exist")
                continue
            nulls = _rows(con, f"SELECT COUNT(*) FROM {table} WHERE {c} IS NULL")[0][0]
            if nulls == total:
                msgs.append(f"{table}.{c} is NULL on all {total} rows -- the writer never fires")
                continue
            if tscol:
                rec = _rows(
                    con,
                    f"SELECT COUNT(*), SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) "
                    f"FROM {table} WHERE {tscol} >= date('now', ?)",
                    (f"-{args.recent_days} day",),
                )
                if rec and rec[0][0]:
                    rn, rnull = rec[0][0], (rec[0][1] or 0)
                    if rn >= 10 and rnull / rn > 0.5 and nulls / total < 0.5:
                        warn.append(
                            f"{table}.{c} NULL {rnull}/{rn} in the last {args.recent_days}d "
                            f"vs {nulls}/{total} lifetime -- writer may have regressed"
                        )
    section("CHECK 2  critical columns never populated", msgs,
            "This is the q_hat failure: the column shipped, the data never did.")
    section("CHECK 3  NULL-rate regression on recent rows", warn,
            "A writer that used to fire and stopped. Catches silent breakage after a deploy.")

    # 4. taxonomy divergence
    msgs = []
    for (t1, c1), (t2, c2), why in TAXONOMY_PAIRS:
        if t1 not in tables or t2 not in tables:
            continue
        v1 = {r[0] for r in _rows(con, f"SELECT DISTINCT {c1} FROM {t1} WHERE {c1} IS NOT NULL")}
        v2 = {r[0] for r in _rows(con, f"SELECT DISTINCT {c2} FROM {t2} WHERE {c2} IS NOT NULL")}
        v1 = {str(x) for x in v1 if str(x).strip()}
        v2 = {str(x) for x in v2 if str(x).strip()}
        # Partial divergence is the real failure mode: one shared value makes a
        # naive overlap test pass while every other label is silently dropped.
        for missing in sorted(v1 - v2):
            n = _rows(con, f"SELECT COUNT(*) FROM {t1} WHERE {c1} = ?", (missing,))
            cnt = n[0][0] if n else 0
            if cnt:
                msgs.append(
                    f"{t1}.{c1}='{missing}' occurs {cnt}x but never appears in {t2}.{c2} "
                    f"({why}) -- any gate keyed on it downstream is unreachable"
                )
    section("CHECK 4  taxonomy divergence between paired columns", msgs,
            "Two names for one event means a gate on one side can never match the other.")

    # 5. rotating buffers
    msgs = []
    for t in tables:
        if t == "sqlite_sequence":
            continue
        mx = _rows(con, f"SELECT COALESCE(MAX(rowid),0) FROM {t}")
        ct = _rows(con, f"SELECT COUNT(*) FROM {t}")
        if mx and ct and ct[0][0] and mx[0][0] > ct[0][0] * 50 and mx[0][0] > 10000:
            msgs.append(
                f"{t}: max(rowid)={mx[0][0]:,} but count(*)={ct[0][0]:,} -- rotating buffer; "
                f"never size it with max(rowid)"
            )
    section("CHECK 5  rotating buffers that misreport their size", msgs,
            "max(rowid) on a rotating table overstates volume by orders of magnitude.")

    print("\n" + "=" * 72)
    print(f"  {len(findings)} FINDING(S)" if findings else "  CLEAN")
    print("=" * 72)
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
