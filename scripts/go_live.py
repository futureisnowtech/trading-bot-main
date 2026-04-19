#!/usr/bin/env python3
"""
scripts/go_live.py — controlled live launch path for Claude/Codex.

This is the only sanctioned automated transition into live mode.
It performs a narrow preflight, stops the paper launchd bot, starts the live
bot through boot.py, and waits for runtime state to confirm the transition.

Crypto live is the hard blocker here. Forecast readiness is reported but does
not block launch, because the forecast lane can truthfully remain
NO_TRADABLE_CONTRACTS_RIGHT_NOW while crypto is live.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PYTHON = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
PAPER_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.algotrading.king.plist"
LIVE_LOG = ROOT / "logs" / "service" / "manual_live_bot.log"
LIVE_PID = ROOT / "logs" / "service" / "manual_live_bot.pid"
DB_PATH = ROOT / "logs" / "trades.db"


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=check,
    )


def _boot_processes() -> tuple[list[int], list[int]]:
    """Return (paper_pids, live_pids) for boot.py processes in this repo."""
    out = _run(["ps", "-ax", "-o", "pid=", "-o", "command="]).stdout.splitlines()
    paper: list[int] = []
    live: list[int] = []
    marker = str(ROOT / "scripts" / "boot.py")
    for line in out:
        if marker not in line:
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1] if len(parts) > 1 else ""
        if "--mode live" in command:
            live.append(pid)
        else:
            paper.append(pid)
    return paper, live


def _terminate(pids: list[int], label: str) -> None:
    if not pids:
        return
    print(f"[go_live] Stopping {label}: {', '.join(str(p) for p in pids)}")
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
            f"Timed out waiting for {label} to exit: {', '.join(str(p) for p in sorted(remaining))}"
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


def _load_crypto_lane() -> tuple[int, float, str]:
    """Return (connected, buying_power_usd, readiness_state) for the crypto lane."""
    if not DB_PATH.exists():
        return 0, 0.0, ""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT connected, buying_power_usd, readiness_state "
            "FROM lane_runtime_state WHERE lane_id='crypto' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return 0, 0.0, ""
        return int(row[0] or 0), float(row[1] or 0.0), str(row[2] or "")
    finally:
        conn.close()


def _coinbase_live_ready() -> None:
    print("[go_live] Verifying Coinbase live auth...")
    from execution.coinbase_broker import CoinbaseBroker

    broker = CoinbaseBroker(paper=False)
    if not broker.connect():
        raise RuntimeError(
            "Coinbase LIVE auth/connect() failed. Fix CDP auth/network first."
        )


def _forecast_status() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT readiness_state, connected FROM lane_runtime_state "
            "WHERE lane_id='forecast' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            readiness, connected = row
            print(
                "[go_live] Forecast lane status: "
                f"readiness={readiness}, connected={connected} "
                "(informational — does not block crypto live)"
            )
    finally:
        conn.close()


def main() -> int:
    print("[go_live] Starting controlled live transition...")
    os.makedirs(LIVE_LOG.parent, exist_ok=True)

    _coinbase_live_ready()
    _forecast_status()

    paper_pids, live_pids = _boot_processes()
    if live_pids:
        raise RuntimeError(
            f"Live bot already appears to be running: {', '.join(str(p) for p in live_pids)}"
        )

    proc: subprocess.Popen[bytes] | None = None
    paper_was_stopped = False
    try:
        if PAPER_PLIST.exists():
            print(f"[go_live] Unloading paper launchd service: {PAPER_PLIST}")
            _run(["launchctl", "unload", str(PAPER_PLIST)])

        _terminate(paper_pids, "paper boot process(es)")
        paper_was_stopped = True

        env = os.environ.copy()
        env.update(
            {
                "ALGO_BOOT_MODE": "live",
                "ALGO_LIVE_CONFIRM": "I UNDERSTAND",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TQDM_DISABLE": "1",
                "TOKENIZERS_PARALLELISM": "false",
            }
        )

        print(f"[go_live] Launching live bot via {ROOT / 'scripts' / 'boot.py'}")
        with LIVE_LOG.open("ab") as logf:
            proc = subprocess.Popen(
                [
                    PYTHON,
                    "-B",
                    str(ROOT / "scripts" / "boot.py"),
                    "--mode",
                    "live",
                    "--confirm-live",
                ],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )

        LIVE_PID.write_text(f"{proc.pid}\n", encoding="utf-8")
        print(f"[go_live] Live bot PID: {proc.pid}")

        deadline = time.time() + 20
        while time.time() < deadline:
            mode = _load_mode()
            connected, buying_power, readiness = _load_crypto_lane()
            if mode == "live" and connected and buying_power > 0:
                print(
                    "[go_live] Runtime state confirms mode=live "
                    f"and crypto connected=1 buying_power=${buying_power:,.2f} "
                    f"readiness={readiness or 'UNKNOWN'}"
                )
                print(f"[go_live] Log: {LIVE_LOG}")
                return 0
            time.sleep(1)

        raise RuntimeError(
            "Live bot did not confirm mode=live with a connected crypto lane and non-zero buying power "
            "in runtime state within 20 seconds. "
            f"Check {LIVE_LOG}"
        )
    except Exception:
        if proc is not None:
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if paper_was_stopped and PAPER_PLIST.exists():
            print("[go_live] Restoring paper launchd bot after failed live launch...")
            _run(["launchctl", "load", str(PAPER_PLIST)])
            _run(
                [
                    "launchctl",
                    "kickstart",
                    "-k",
                    f"gui/{os.getuid()}/com.algotrading.king",
                ]
            )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[go_live] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
