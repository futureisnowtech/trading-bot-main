#!/usr/bin/env python3
"""
scripts/full_audit.py — Complete system audit bundle (v19.1 Ledgerless).
"""

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

def _run(cmd: list[str], out_path: Path, label: str) -> bool:
    print(f"  [{label}] ...", end="", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=120)
        combined = result.stdout
        if result.stderr.strip(): combined += "\n\n── STDERR ──\n" + result.stderr
        out_path.write_text(combined, encoding="utf-8")
        status = "OK" if result.returncode == 0 else f"exit={result.returncode}"
        print(f" {status}")
        return result.returncode == 0
    except Exception as e:
        out_path.write_text(f"ERROR: {e}\n", encoding="utf-8")
        print(f" ERROR: {e}")
        return False

def _export_csv(conn: sqlite3.Connection, query: str, path: Path, headers: list[str]) -> int:
    try:
        rows = conn.execute(query).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        return len(rows)
    except Exception as e:
        path.write_text(f"ERROR: {e}\n", encoding="utf-8")
        return 0

def _build_context(conn: sqlite3.Connection, days: int, ts_str: str) -> str:
    lines = ["=" * 64, "  ALGO TRADING SYSTEM — FULL AUDIT SNAPSHOT", f"  Generated: {ts_str}", f"  Lookback:  {days} days", "=" * 64]
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(_ROOT), text=True).strip()
        lines += ["", "── GIT ────────────────────────────────────────────────", f"  Commit:  {sha}"]
    except Exception: pass
    try:
        ev_rows = conn.execute("SELECT ts, source, level, message FROM system_events ORDER BY ts DESC LIMIT 10").fetchall()
        if ev_rows:
            lines += ["", "── RECENT SYSTEM EVENTS ───────────────────────────────"]
            for r in ev_rows: lines.append(f"  [{r[0][:19]}] [{r[2]}] {r[1]}: {r[3][:90]}")
    except Exception: pass
    return "\n".join(lines) + "\n"

def main() -> None:
    parser = argparse.ArgumentParser(description="Full system audit bundle")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--out", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    ts_str, ts_tag = now.strftime("%Y-%m-%dT%H:%M UTC"), now.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else _ROOT / "audit_output" / f"audit_{ts_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_dir, db_dir = out_dir / "audit", out_dir / "db_exports"
    audit_dir.mkdir(exist_ok=True); db_dir.mkdir(exist_ok=True)

    py = sys.executable
    print(f"\n{'=' * 60}\n  FULL AUDIT  —  {ts_str}\n  Output: {out_dir}\n{'=' * 60}\n")

    print("1/3  Running audit scripts...")
    _run([py, "scripts/health_check.py", "--days", str(args.days)], audit_dir / "health_check.txt", "health_check")
    _run([py, "scripts/coinbase_launch_validator.py"], audit_dir / "coinbase_launch_validator.txt", "coinbase_launch_validator")

    print("\n2/3  Exporting DB tables...")
    db_path = _ROOT / "logs" / "trades.db"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        exports = [
            ("system_events", "SELECT ts, source, level, message FROM system_events ORDER BY ts DESC LIMIT 500", ["ts", "source", "level", "message"]),
            ("trades_recent", "SELECT ts, strategy, broker, symbol, action, qty, price, pnl_usd FROM trades ORDER BY ts DESC LIMIT 500", ["ts", "strategy", "broker", "symbol", "action", "qty", "price", "pnl_usd"]),
        ]
        for name, query, headers in exports:
            n = _export_csv(conn, query, db_dir / f"{name}.csv", headers)
            print(f"  [{name}] {n} rows")
        (out_dir / "snapshot_context.txt").write_text(_build_context(conn, args.days, ts_str), encoding="utf-8")
        conn.close()

    print("\n3/3  Capturing bot log...")
    log_path = _ROOT / "logs" / "bot.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        (out_dir / "bot_log_recent.txt").write_text("\n".join(lines[-500:]), encoding="utf-8")
    print(f"\nDone — {out_dir}")

if __name__ == "__main__": main()
