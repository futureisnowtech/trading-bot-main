"""
scripts/backfill_agent_stats.py — Reconstruct agent_stats from historical debate_results.

Joins debate_results to trades by symbol + timestamp to determine trade outcome (won/lost),
then calls record_agent_votes() for each matched debate.

Handles two agent_details formats:
  - New (v8.0+): JSON array of objects each having "agent_key" and "signal" fields
  - Old (pre-v8.0): JSON array of objects with a "signal" field but no "agent_key"
    (old rows are skipped — no stable agent identity to attribute)

Only the 3 canonical agent keys are processed:
  funding_regime, momentum_structure, risk_economics

Usage:
    python3 scripts/backfill_agent_stats.py
    python3 scripts/backfill_agent_stats.py --dry-run
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from learning.signal_performance import record_agent_votes

AGENT_KEYS = {"funding_regime", "momentum_structure", "risk_economics"}
MATCH_WINDOW_HOURS = 24  # look up to 24h after debate for a matching trade close


# ── Helpers ──────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def parse_ts(ts_str: str) -> datetime | None:
    """Parse an ISO timestamp string (with or without timezone offset) into a UTC datetime."""
    if not ts_str:
        return None
    try:
        # datetime.fromisoformat handles +HH:MM offsets in Python 3.11+
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def extract_agent_votes(agent_details_json: str) -> dict[str, str] | None:
    """
    Parse agent_details JSON and return {agent_key: vote} for new-format rows.
    Returns None if the row is old-format (no agent_key) or unparseable.
    Vote is the string value of the "signal" field (BUY / HOLD / SELL).
    """
    if not agent_details_json:
        return None
    try:
        data = json.loads(agent_details_json)
    except (json.JSONDecodeError, ValueError) as exc:
        return None

    if not isinstance(data, list):
        return None

    votes: dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        agent_key = entry.get("agent_key")
        if agent_key not in AGENT_KEYS:
            # Old format row or unknown agent — skip the entire debate
            continue
        vote = str(entry.get("signal", "HOLD")).upper()
        votes[agent_key] = vote

    # Only return if we got at least one recognised agent_key
    if not votes:
        return None
    return votes


def find_matching_trade_close(
    conn: sqlite3.Connection,
    symbol: str,
    debate_ts: datetime,
) -> bool | None:
    """
    Find the first trade close (pnl_usd != 0) for `symbol` with ts > debate_ts
    and ts <= debate_ts + MATCH_WINDOW_HOURS.

    Returns:
        True  — trade found and won (pnl_usd > 0)
        False — trade found and lost (pnl_usd <= 0)
        None  — no matching trade close found within window
    """
    window_end = debate_ts + timedelta(hours=MATCH_WINDOW_HOURS)

    # SQLite stores ts as ISO strings; we compare lexicographically which works
    # for ISO 8601 timestamps that share the same timezone offset format.
    # To be safe we fetch candidates and filter in Python.
    rows = conn.execute(
        """
        SELECT ts, pnl_usd FROM trades
        WHERE symbol = ?
          AND pnl_usd != 0
        ORDER BY ts ASC
        """,
        (symbol,),
    ).fetchall()

    for row in rows:
        trade_ts = parse_ts(row["ts"])
        if trade_ts is None:
            continue
        if trade_ts > debate_ts and trade_ts <= window_end:
            return row["pnl_usd"] > 0

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    label = "[DRY-RUN] " if dry_run else ""
    print(f"{label}Backfilling agent_stats from debate_results...")
    print(f"  DB: {config.DB_PATH}")
    print(f"  Match window: {MATCH_WINDOW_HOURS}h after each debate")
    print()

    conn = _conn()

    # Fetch all debate rows that have agent_details populated
    debate_rows = conn.execute(
        """
        SELECT id, ts, symbol, agent_details, regime
        FROM debate_results
        WHERE agent_details IS NOT NULL
        ORDER BY ts ASC
        """
    ).fetchall()

    total_debates = len(debate_rows)
    print(f"Found {total_debates} debate_results rows with agent_details.\n")

    stats = {
        "skipped_old_format": 0,
        "skipped_json_error": 0,
        "skipped_no_trade_match": 0,
        "skipped_ts_parse_error": 0,
        "processed": 0,
    }

    for i, row in enumerate(debate_rows, 1):
        debate_id = row["id"]
        symbol = row["symbol"]
        regime = row["regime"] or "any"

        # Parse debate timestamp
        debate_ts = parse_ts(row["ts"])
        if debate_ts is None:
            if i % 50 == 0 or i == total_debates:
                print(f"  [{i}/{total_debates}] id={debate_id} {symbol} — ts parse error, skipping")
            stats["skipped_ts_parse_error"] += 1
            continue

        # Parse agent votes
        agent_votes = extract_agent_votes(row["agent_details"])
        if agent_votes is None:
            stats["skipped_old_format"] += 1
            continue

        # Find matching trade close
        won = find_matching_trade_close(conn, symbol, debate_ts)
        if won is None:
            stats["skipped_no_trade_match"] += 1
            continue

        # Report what we found
        vote_summary = ", ".join(f"{k}={v}" for k, v in agent_votes.items())
        outcome = "WIN" if won else "LOSS"
        print(
            f"  [{i}/{total_debates}] id={debate_id} {symbol} @ {row['ts'][:19]} "
            f"| regime={regime} | {outcome} | {vote_summary}"
        )

        if not dry_run:
            record_agent_votes(agent_votes, regime, won)

        stats["processed"] += 1

    # Summary
    print()
    print("=" * 60)
    print(f"{label}Backfill complete.")
    print(f"  Total debate rows examined : {total_debates}")
    print(f"  Debates processed          : {stats['processed']}")
    print(f"  Skipped — old format       : {stats['skipped_old_format']}")
    print(f"  Skipped — no trade match   : {stats['skipped_no_trade_match']}")
    print(f"  Skipped — JSON error       : {stats['skipped_json_error']}")
    print(f"  Skipped — ts parse error   : {stats['skipped_ts_parse_error']}")
    print()

    if dry_run:
        print("DRY-RUN: No writes made. Re-run without --dry-run to apply.")
        conn.close()
        return

    # Print resulting agent_stats table
    print("Current agent_stats table after backfill:")
    print("-" * 72)
    header = f"  {'agent_name':<24} {'regime':<12} {'buy_votes':>9} {'correct':>7} {'accuracy':>9}"
    print(header)
    print("  " + "-" * 68)

    rows = conn.execute(
        """
        SELECT agent_name, regime, votes_buy, correct_buy, accuracy
        FROM agent_stats
        ORDER BY agent_name, regime
        """
    ).fetchall()

    if not rows:
        print("  (no rows — agent_stats is still empty)")
    else:
        for r in rows:
            acc_str = f"{r['accuracy']:.1%}" if r["accuracy"] is not None else "  N/A"
            print(
                f"  {r['agent_name']:<24} {r['regime']:<12} "
                f"{r['votes_buy']:>9} {r['correct_buy']:>7} {acc_str:>9}"
            )

    print("-" * 72)
    conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill agent_stats from historical debate_results rows."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to the database.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
