#!/usr/bin/env python3
"""Unattended watchdog: alert on silent failure, stay quiet otherwise.

Built because the bot can fail in ways that look exactly like calm: it stops
entering, a feature flag is on but inert, an order orphans on the book, a writer
stops writing. None of those raise. All of them are invisible until someone goes
looking, and going looking is the thing that does not happen while you sleep.

Alerts are edge-triggered. A problem pages once when it appears and once when it
clears; a steady state is silent. That is the difference between a watchdog and
a source of noise nobody reads.

Cron:  */15 * * * * docker exec execution-engine python3 /app/scripts/watchdog.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DB = os.getenv("WATCHDOG_DB", "/app/logs/trades.db")
STATE = pathlib.Path(os.getenv("WATCHDOG_STATE", "/app/logs/watchdog_state.json"))
LOG = os.getenv("WATCHDOG_LOG", "/app/logs/bot.log")

# No entries for this long is a stall, not a quiet market. Tuned to the observed
# ~10min scan cadence and the deliberately selective EV_THRESHOLD.
STALL_HOURS = 18
DISK_PCT_ALERT = 88
ERROR_WINDOW_MIN = 90
ERROR_COUNT_ALERT = 5


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _q(con, sql, params=()):
    try:
        return list(con.execute(sql, params))
    except sqlite3.Error:
        return []


def collect() -> dict[str, str]:
    """Return {check_id: human message} for every currently-failing check."""
    bad: dict[str, str] = {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    now = _utcnow()

    # 1. Has the bot stopped trading entirely?
    row = _q(con, "SELECT MAX(opened_at) FROM forecast_positions")
    last = row[0][0] if row and row[0][0] else None
    if last:
        try:
            age_h = (now - dt.datetime.fromisoformat(last)).total_seconds() / 3600.0
            if age_h > STALL_HOURS:
                bad["stalled"] = f"No entry for {age_h:.1f}h (last {last[:16]}Z)."
        except ValueError:
            pass
    else:
        bad["stalled"] = "No entries recorded at all."

    # 2. Firewall halted entries for the day.
    try:
        sys.path.insert(0, "/app")
        from forecast.firewall import is_entries_allowed_today

        allowed, why = is_entries_allowed_today()
        if not allowed:
            bad["firewall"] = f"Entries halted: {why}"
    except Exception as exc:
        bad["firewall_check"] = f"Could not read firewall state: {exc}"

    # 2b. The release gate is closed. This is the fastest-moving silent halt we
    #     have seen: the runner keeps scanning and logging normally while
    #     entering nothing, so "no entries" only becomes visible hours later.
    #     It has fired for real on a transient missing_weather_data burst right
    #     after a container restart, and on a stale artifact left behind by an
    #     operator running release_audit.py --local by hand (that mode PERSISTS
    #     its verdict; use --no-persist for diagnostics).
    try:
        from runtime.release_gate import load_release_audit_artifact

        art = load_release_audit_artifact() or {}
        verdict = str(art.get("verdict") or "")
        # Gate on entries_allowed, not on the verdict string. The runner itself
        # checks entries_allowed (forecast/runner.py), and PASS_WITH_WARNINGS is
        # a healthy steady state that allows entries -- alerting on any verdict
        # that is not READY_FOR_LIVE would page every 15 minutes forever, which
        # is how a watchdog gets muted and stops being a watchdog.
        if art and art.get("entries_allowed") is False:
            blockers = art.get("blockers") or []
            bad["gate_blocked"] = (
                f"Release gate {verdict or 'BLOCKED'}: "
                f"{', '.join(map(str, blockers))[:140] or 'no reason given'}. "
                f"The bot scans but enters nothing."
            )
    except Exception as exc:
        bad["gate_check"] = f"Could not read the release artifact: {exc}"

    # 3. Enabled-but-inert: maker routing on, but never attempted.
    #    This is the failure that cost 313% of gross edge for months.
    try:
        import config

        if getattr(config, "MAKER_ENTRY_ENABLED", False):
            cutoff = (now - dt.timedelta(days=3)).isoformat()
            entries = _q(
                con, "SELECT COUNT(*) FROM trades WHERE action='BUY' AND ts > ?", (cutoff,)
            )
            n = entries[0][0] if entries else 0
            if n >= 10:
                attempts = 0
                try:
                    with open(LOG, errors="ignore") as fh:
                        attempts = sum(1 for ln in fh if "[Maker]" in ln)
                except OSError:
                    attempts = -1
                if attempts == 0:
                    bad["maker_inert"] = (
                        f"MAKER_ENTRY_ENABLED is on and {n} entries ran in 3d, "
                        f"but zero maker attempts were logged."
                    )
    except Exception:
        pass

    # 4. Orphaned resting orders.
    try:
        from execution.kalshi_broker import KalshiBroker

        b = KalshiBroker()
        b.connect()
        if b.is_connected():
            resting = b.list_resting_orders()
            if len(resting) > 2:
                bad["orphans"] = (
                    f"{len(resting)} resting orders on the book "
                    f"(a rest window holds at most ~1-2)."
                )
    except Exception as exc:
        bad["broker"] = f"Broker unreachable: {exc}"

    # 5. Error burst in the log.
    try:
        cutoff = (now - dt.timedelta(minutes=ERROR_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M")
        n = 0
        with open(LOG, errors="ignore") as fh:
            for ln in fh:
                if ln[:16] >= cutoff and ("Traceback" in ln or "CRITICAL" in ln):
                    n += 1
        if n >= ERROR_COUNT_ALERT:
            bad["errors"] = f"{n} errors/criticals in the last {ERROR_WINDOW_MIN}m."
    except OSError:
        pass

    # 6. Disk.
    try:
        usage = shutil.disk_usage("/app/logs")
        pct = 100.0 * usage.used / usage.total
        if pct >= DISK_PCT_ALERT:
            bad["disk"] = f"Disk {pct:.0f}% full ({usage.free/1e9:.1f}GB free)."
    except Exception:
        pass

    # 7. Boundary contract audit regressed.
    audit = pathlib.Path("/app/scripts/contract_audit.py")
    if audit.exists():
        r = subprocess.run(
            [sys.executable, str(audit), "--strict"],
            capture_output=True, text=True, cwd="/app",
        )
        if r.returncode != 0:
            fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            bad["contract_audit"] = "Boundary audit failing: " + (fails[0][:160] if fails else "see output")

    return bad


def notify(text: str) -> None:
    try:
        sys.path.insert(0, "/app")
        from notifications.telegram_bot import send_message

        send_message(text)
    except Exception as exc:  # pragma: no cover
        print(f"[watchdog] telegram send failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="report even if unchanged")
    ap.add_argument("--dry-run", action="store_true", help="print, never send")
    args = ap.parse_args()

    bad = collect()
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text()).get("open", {})
        except Exception:
            prev = {}

    new = {k: v for k, v in bad.items() if k not in prev}
    cleared = [k for k in prev if k not in bad]

    lines: list[str] = []
    if new:
        lines.append("🔴 <b>Weatherman watchdog</b>")
        lines += [f"• {v}" for v in new.values()]
    if cleared:
        lines.append("🟢 <b>Recovered</b>: " + ", ".join(cleared))

    if args.force and not lines:
        lines = ["🟢 <b>Weatherman watchdog</b>: all checks pass."]

    msg = "\n".join(lines)
    if msg:
        print(msg)
        if not args.dry_run:
            notify(msg)
    else:
        print(f"[watchdog] ok ({len(bad)} open, unchanged)")

    try:
        STATE.write_text(json.dumps({"open": bad, "ts": _utcnow().isoformat()}, indent=1))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
