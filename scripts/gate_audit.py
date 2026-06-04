#!/usr/bin/env python3
"""
scripts/gate_audit.py — one-command Kalshi market-reality gate audit.

Default behavior:
  1. Run one fresh shadow-mode sniper cycle.
  2. Read the exact ForecastRunner veto events written during that cycle.
  3. Summarize the dominant gate families and any shadow-blocked buy attempts.

Use --skip-run to inspect recent veto history without launching a new cycle.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_DB_PATH = REPO_ROOT / "logs" / "trades.db"

VETO_RE = re.compile(r"\[ForecastRunner\]\s+(?P<ticker>\S+)\s+vetoed:\s+(?P<reason>.+)")
SHADOW_RE = re.compile(r"SHADOW MODE: Blocked POST .* body=(?P<body>\{.*\})")


@dataclass
class VetoRecord:
    ts: float
    ticker: str
    reason: str

    @property
    def family(self) -> str:
        return reason_family(self.reason)


@dataclass
class ShadowAttempt:
    ticker: str
    action: str
    side: str
    count: int
    order_type: str
    raw_line: str


@dataclass
class RunWindow:
    started_at: float
    ended_at: float
    return_code: int | None
    output: str
    mode: str


def reason_family(reason: str) -> str:
    token = (reason or "").strip()
    if not token:
        return "unknown"
    for sep in (" ", "("):
        if sep in token:
            token = token.split(sep, 1)[0]
    return token


def parse_veto_message(ts: float, message: str) -> VetoRecord | None:
    match = VETO_RE.search(message or "")
    if not match:
        return None
    return VetoRecord(
        ts=ts,
        ticker=match.group("ticker"),
        reason=match.group("reason").strip(),
    )


def parse_shadow_block_line(line: str) -> ShadowAttempt | None:
    match = SHADOW_RE.search(line or "")
    if not match:
        return None
    try:
        body = ast.literal_eval(match.group("body"))
    except Exception:
        return None

    return ShadowAttempt(
        ticker=str(body.get("ticker") or "UNKNOWN"),
        action=str(body.get("action") or "unknown").upper(),
        side=str(body.get("side") or "unknown").upper(),
        count=int(body.get("count") or 0),
        order_type=str(body.get("type") or "unknown").upper(),
        raw_line=line.rstrip(),
    )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _coerce_event_ts(raw_ts: object) -> float | None:
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        return float(raw_ts)

    text = str(raw_ts).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _query_veto_records(db_path: Path, started_at: float) -> list[VetoRecord]:
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path, timeout=30.0) as conn:
        rows = conn.execute(
            """
            SELECT ts, message
            FROM system_events
            WHERE source='ForecastRunner'
              AND message LIKE '% vetoed: %'
            ORDER BY ts ASC
            """
        ).fetchall()

    records: list[VetoRecord] = []
    for raw_ts, message in rows:
        event_ts = _coerce_event_ts(raw_ts)
        if event_ts is None or event_ts < started_at:
            continue
        parsed = parse_veto_message(event_ts, str(message))
        if parsed is not None:
            records.append(parsed)
    return records


def _run_shadow_cycle(timeout_seconds: int) -> RunWindow:
    env = os.environ.copy()
    env["SHADOW_EXECUTION"] = "true"
    env["PYTHONUNBUFFERED"] = "1"

    started_at = time.time()
    proc = subprocess.run(
        [sys.executable, "sniper_cron.py"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
    )
    ended_at = time.time()
    return RunWindow(
        started_at=started_at,
        ended_at=ended_at,
        return_code=proc.returncode,
        output=proc.stdout or "",
        mode="fresh_shadow_cycle",
    )


def _history_window(lookback_minutes: int) -> RunWindow:
    ended_at = time.time()
    started_at = ended_at - (lookback_minutes * 60)
    return RunWindow(
        started_at=started_at,
        ended_at=ended_at,
        return_code=None,
        output="",
        mode=f"history_only_last_{lookback_minutes}m",
    )


def _parse_shadow_attempts(output: str) -> list[ShadowAttempt]:
    attempts: list[ShadowAttempt] = []
    for line in (output or "").splitlines():
        parsed = parse_shadow_block_line(line)
        if parsed is not None:
            attempts.append(parsed)
    return attempts


def _format_counts(counter: Counter[str], limit: int) -> list[str]:
    lines: list[str] = []
    for key, count in counter.most_common(limit):
        lines.append(f"  {count:>3}  {key}")
    return lines


def _build_report(
    window: RunWindow,
    veto_records: list[VetoRecord],
    shadow_attempts: list[ShadowAttempt],
    sample_limit: int,
) -> str:
    veto_family_counts = Counter(record.family for record in veto_records)
    veto_exact_counts = Counter(record.reason for record in veto_records)
    buy_attempts = [attempt for attempt in shadow_attempts if attempt.action == "BUY"]
    sell_attempts = [attempt for attempt in shadow_attempts if attempt.action == "SELL"]

    lines = [
        "KALSHI GATE AUDIT",
        f"Mode: {window.mode}",
        f"Window UTC: {_iso(window.started_at)} -> {_iso(window.ended_at)}",
    ]
    if window.return_code is not None:
        lines.append(f"Shadow cycle exit code: {window.return_code}")
    lines.extend(
        [
            f"Vetoed candidates: {len(veto_records)}",
            f"Shadow-blocked BUY attempts: {len(buy_attempts)}",
            f"Shadow-blocked SELL attempts: {len(sell_attempts)}",
        ]
    )

    if veto_family_counts:
        lines.extend(["", "Top Veto Families:"])
        lines.extend(_format_counts(veto_family_counts, sample_limit))
    else:
        lines.extend(["", "Top Veto Families:", "  none"])

    if veto_exact_counts:
        lines.extend(["", "Top Exact Veto Reasons:"])
        lines.extend(_format_counts(veto_exact_counts, sample_limit))

    if veto_records:
        lines.extend(["", "Sample Vetoed Contracts:"])
        for record in veto_records[:sample_limit]:
            lines.append(f"  - {record.ticker} -> {record.reason}")

    if buy_attempts:
        lines.extend(["", "Gate Passes Reaching Shadow Order Placement:"])
        for attempt in buy_attempts[:sample_limit]:
            lines.append(
                f"  - {attempt.ticker} {attempt.side} x{attempt.count} [{attempt.order_type}]"
            )
    elif window.return_code is not None:
        lines.extend(
            [
                "",
                "Gate Passes Reaching Shadow Order Placement:",
                "  none in this shadow cycle",
            ]
        )

    if sell_attempts:
        lines.extend(["", "Shadow Exit Attempts:"])
        for attempt in sell_attempts[:sample_limit]:
            lines.append(
                f"  - {attempt.ticker} {attempt.side} x{attempt.count} [{attempt.order_type}]"
            )

    if window.output:
        shadow_lines = [line for line in window.output.splitlines() if "SHADOW MODE: Blocked POST" in line]
        if shadow_lines:
            lines.extend(["", "Raw Shadow Order Lines:"])
            for line in shadow_lines[:sample_limit]:
                lines.append(f"  - {line.strip()}")

    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit current Kalshi market-reality gate outcomes.",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Do not launch a fresh shadow sniper cycle; summarize recent DB history only.",
    )
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=180,
        help="History lookback when using --skip-run. Default: 180.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=240,
        help="Timeout for the shadow sniper cycle. Default: 240.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=8,
        help="How many top reasons/contracts to print. Default: 8.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite DB path. Default: {DEFAULT_DB_PATH}",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path)

    try:
        if args.skip_run:
            window = _history_window(max(1, args.lookback_minutes))
        else:
            window = _run_shadow_cycle(max(30, args.timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        print("KALSHI GATE AUDIT", file=sys.stderr)
        print(f"Shadow cycle timed out after {exc.timeout}s.", file=sys.stderr)
        return 2

    veto_records = _query_veto_records(db_path, window.started_at)
    shadow_attempts = _parse_shadow_attempts(window.output)
    report = _build_report(window, veto_records, shadow_attempts, max(1, args.sample_limit))
    print(report)

    if window.return_code not in (None, 0):
        print("", file=sys.stderr)
        print("Shadow cycle output tail:", file=sys.stderr)
        tail = "\n".join(window.output.splitlines()[-20:])
        if tail:
            print(tail, file=sys.stderr)
        return int(window.return_code)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
