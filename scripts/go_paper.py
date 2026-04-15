#!/usr/bin/env python3
"""
scripts/go_paper.py — controlled return to the paper launchd bot.

Stops any live boot.py process started via scripts/go_live.py, then reloads the
paper launchd service and waits for runtime state to return to paper mode.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.algotrading.king.plist"
LIVE_PID = ROOT / "logs" / "service" / "manual_live_bot.pid"
DB_PATH = ROOT / "logs" / "trades.db"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )


def _live_pids() -> list[int]:
    out = _run(["ps", "-ax", "-o", "pid=", "-o", "command="]).stdout.splitlines()
    marker = str(ROOT / "scripts" / "boot.py")
    found: list[int] = []
    for line in out:
        if marker not in line or "--mode live" not in line:
            continue
        parts = line.strip().split(None, 1)
        try:
            found.append(int(parts[0]))
        except (ValueError, IndexError):
            continue
    return found


def _terminate(pids: list[int]) -> None:
    if not pids:
        return
    print(f"[go_paper] Stopping live boot process(es): {', '.join(str(p) for p in pids)}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 15
    remaining = set(pids)
    while remaining and time.time() < deadline:
        time.sleep(0.5)
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.discard(pid)
    if remaining:
        raise RuntimeError(
            f"Timed out waiting for live bot to exit: {', '.join(str(p) for p in sorted(remaining))}"
        )


def _load_mode() -> str | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT process_mode FROM system_runtime_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def main() -> int:
    _terminate(_live_pids())
    if LIVE_PID.exists():
        LIVE_PID.unlink()

    if not PAPER_PLIST.exists():
        raise RuntimeError(f"Paper launchd plist not found: {PAPER_PLIST}")

    print(f"[go_paper] Loading paper launchd service: {PAPER_PLIST}")
    _run(["launchctl", "load", str(PAPER_PLIST)])
    _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.algotrading.king"])

    deadline = time.time() + 20
    while time.time() < deadline:
        mode = _load_mode()
        if mode == "paper":
            print("[go_paper] Runtime state confirms mode=paper")
            return 0
        time.sleep(1)

    raise RuntimeError("Paper bot did not confirm mode=paper within 20 seconds.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[go_paper] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
