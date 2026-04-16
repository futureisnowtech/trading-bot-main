#!/usr/bin/env python3
"""
scripts/full_audit.py — Complete system audit bundle.

Runs every existing audit script, exports key DB tables, and writes a
snapshot_context.txt into a single timestamped output directory.
Optionally zips everything for external analysts.

Usage:
    python3 scripts/full_audit.py
    python3 scripts/full_audit.py --days 30
    python3 scripts/full_audit.py --zip                  # also writes Desktop zip
    python3 scripts/full_audit.py --days 30 --zip
    python3 scripts/full_audit.py --out /path/to/dir     # custom output dir
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


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(cmd: list[str], out_path: Path, label: str) -> bool:
    """Run a subprocess, write stdout+stderr to out_path. Returns True on success."""
    print(f"  [{label}] ...", end="", flush=True)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            timeout=120,
        )
        combined = result.stdout
        if result.stderr.strip():
            combined += "\n\n── STDERR ──\n" + result.stderr
        out_path.write_text(combined, encoding="utf-8")
        status = "OK" if result.returncode == 0 else f"exit={result.returncode}"
        print(f" {status}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        out_path.write_text("TIMEOUT after 120s\n", encoding="utf-8")
        print(" TIMEOUT")
        return False
    except Exception as e:
        out_path.write_text(f"ERROR: {e}\n", encoding="utf-8")
        print(f" ERROR: {e}")
        return False


def _export_csv(
    conn: sqlite3.Connection, query: str, path: Path, headers: list[str]
) -> int:
    """Run query, write CSV. Returns row count."""
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


def _export_json(
    conn: sqlite3.Connection, query: str, path: Path, headers: list[str]
) -> int:
    try:
        rows = conn.execute(query).fetchall()
        data = [dict(zip(headers, r)) for r in rows]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return len(rows)
    except Exception as e:
        path.write_text(f'{{"error": "{e}"}}\n', encoding="utf-8")
        return 0


def _build_context(conn: sqlite3.Connection, days: int, ts_str: str) -> str:
    """Build snapshot_context.txt content dynamically."""
    lines = []
    lines.append("=" * 64)
    lines.append("  ALGO TRADING SYSTEM — FULL AUDIT SNAPSHOT")
    lines.append(f"  Generated: {ts_str}")
    lines.append(f"  Lookback:  {days} days")
    lines.append("=" * 64)

    # Git
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=str(_ROOT), text=True
        ).strip()
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(_ROOT), text=True
        ).strip()
        msg = subprocess.check_output(
            ["git", "log", "-1", "--format=%s %ai"], cwd=str(_ROOT), text=True
        ).strip()
        lines += [
            "",
            "── GIT ────────────────────────────────────────────────",
            f"  Branch:  {branch}",
            f"  Commit:  {sha}",
            f"  Message: {msg}",
        ]
    except Exception:
        lines.append("  Git info unavailable")

    # Bot process
    try:
        ps = subprocess.check_output(["ps", "aux"], text=True)
        bot_lines = [
            l
            for l in ps.splitlines()
            if ("main.py" in l or "boot.py" in l) and "grep" not in l
        ]
        lines += [
            "",
            "── PROCESS ────────────────────────────────────────────",
        ]
        if bot_lines:
            for bl in bot_lines:
                parts = bl.split()
                lines.append(f"  RUNNING  PID={parts[1]}  {' '.join(parts[10:14])}")
        else:
            lines.append("  NOT RUNNING")
    except Exception:
        pass

    # Runtime state
    try:
        row = conn.execute(
            "SELECT process_mode, active_lanes, global_status, startup_ts FROM system_runtime_state LIMIT 1"
        ).fetchone()
        if row:
            lines += [
                "",
                "── RUNTIME STATE ──────────────────────────────────────",
                f"  Mode:         {row[0]}",
                f"  Active lanes: {row[1]}",
                f"  Status:       {row[2]}",
                f"  Startup:      {row[3]}",
            ]
    except Exception:
        pass

    # Lane states
    try:
        lane_rows = conn.execute(
            "SELECT lane_id, enabled, active, mode, health, last_heartbeat_at FROM lane_runtime_state"
        ).fetchall()
        if lane_rows:
            lines += ["", "── LANES ──────────────────────────────────────────────"]
            for r in lane_rows:
                lines.append(
                    f"  {r[0]:<16} enabled={r[1]} active={r[2]} mode={r[3]} health={r[4]}"
                )
                lines.append(f"  {'':16} heartbeat: {r[5] or 'never'}")
    except Exception:
        pass

    # Kill switch
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM kill_switch_log WHERE resumed_at IS NULL"
        ).fetchone()[0]
        lines += [
            "",
            "── KILL SWITCH ────────────────────────────────────────",
            f"  Active halts: {n}",
        ]
    except Exception:
        pass

    # Open positions summary
    try:
        pos_rows = conn.execute(
            "SELECT symbol, strategy, qty, entry, ts_entry FROM open_positions"
        ).fetchall()
        lines += [
            "",
            f"── OPEN POSITIONS ({len(pos_rows)}) ───────────────────────────────",
        ]
        for r in pos_rows[:10]:
            lines.append(
                f"  {r[0]:<18} qty={r[2]:.4f}  entry={r[3]:.6g}  since={r[4][:10]}"
            )
        if len(pos_rows) > 10:
            lines.append(
                f"  ... and {len(pos_rows) - 10} more (see open_positions.csv)"
            )
    except Exception:
        pass

    # Recent scan funnel summary
    try:
        funnel_rows = conn.execute(
            """SELECT ts, scanner_candidates_total, data_unavailable, below_threshold,
                      econ_veto, research_only_block, entered
               FROM scan_funnels ORDER BY rowid DESC LIMIT 10"""
        ).fetchall()
        if funnel_rows:
            lines += [
                "",
                "── SCAN FUNNEL (last 10 cycles) ───────────────────────",
                "  ts                    cands  data_unavail  below  econ_veto  ronly  entered",
            ]
            for r in funnel_rows:
                lines.append(
                    f"  {r[0]:<22} {r[1]:<6} {r[2]:<13} {r[3]:<6} {r[4]:<10} {r[5]:<6} {r[6]}"
                )
    except Exception:
        pass

    # Recent system events
    try:
        ev_rows = conn.execute(
            """SELECT ts, source, level, message FROM system_events
               ORDER BY ts DESC LIMIT 10"""
        ).fetchall()
        if ev_rows:
            lines += ["", "── RECENT SYSTEM EVENTS ───────────────────────────────"]
            for r in ev_rows:
                lines.append(f"  [{r[0][:19]}] [{r[2]}] {r[1]}: {r[3][:90]}")
    except Exception:
        pass

    lines += [
        "",
        "── FILES IN THIS BUNDLE ───────────────────────────────",
        "  audit/                    all audit script outputs",
        "  db_exports/               CSV + JSON exports of key tables",
        "  bot_log_recent.txt        last 500 lines of bot.log",
        "  snapshot_context.txt      this file",
        "",
        "=" * 64,
    ]
    return "\n".join(lines) + "\n"


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Full system audit bundle")
    parser.add_argument(
        "--days", type=int, default=30, help="Lookback window in days (default 30)"
    )
    parser.add_argument(
        "--zip", action="store_true", help="Also write a zip to ~/Desktop"
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory (default: audit_output/audit_TIMESTAMP)",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y-%m-%dT%H:%M UTC")
    ts_tag = now.strftime("%Y%m%d_%H%M%S")

    # Output dir
    if args.out:
        out_dir = Path(args.out)
    else:
        out_dir = _ROOT / "audit_output" / f"audit_{ts_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(exist_ok=True)
    db_dir = out_dir / "db_exports"
    db_dir.mkdir(exist_ok=True)

    py = sys.executable
    print(f"\n{'=' * 60}")
    print(f"  FULL AUDIT  —  {ts_str}")
    print(f"  Output: {out_dir}")
    print(f"{'=' * 60}\n")

    # ── 1. Run audit scripts ──────────────────────────────────────
    print("1/4  Running audit scripts...")
    _run(
        [py, "scripts/live_runtime_audit.py"],
        audit_dir / "live_runtime_audit.txt",
        "live_runtime_audit",
    )
    _run(
        [py, "scripts/lane_status_audit.py"],
        audit_dir / "lane_status_audit.txt",
        "lane_status_audit",
    )
    _run(
        [py, "scripts/entry_truth_audit.py", "--days", str(args.days)],
        audit_dir / "entry_truth_audit.txt",
        "entry_truth_audit (text)",
    )
    _run(
        [py, "scripts/entry_truth_audit.py", "--days", str(args.days), "--json"],
        audit_dir / "entry_truth_audit.json",
        "entry_truth_audit (json)",
    )
    _run(
        [py, "scripts/path_truth_audit.py", "--days", str(args.days)],
        audit_dir / "path_truth_audit.txt",
        "path_truth_audit (text)",
    )
    _run(
        [py, "scripts/path_truth_audit.py", "--days", str(args.days), "--json"],
        audit_dir / "path_truth_audit.json",
        "path_truth_audit (json)",
    )
    _run(
        [py, "scripts/health_check.py", "--days", str(args.days)],
        audit_dir / "health_check.txt",
        "health_check",
    )
    _run(
        [py, "scripts/coinbase_launch_validator.py"],
        audit_dir / "coinbase_launch_validator.txt",
        "coinbase_launch_validator",
    )

    # ── 2. Export DB tables ───────────────────────────────────────
    print("\n2/4  Exporting DB tables...")
    db_path = _ROOT / "logs" / "trades.db"
    if not db_path.exists():
        print(f"  WARNING: DB not found at {db_path}")
        conn = None
    else:
        conn = sqlite3.connect(str(db_path))

    if conn:
        exports = [
            (
                "system_events",
                "SELECT ts, source, level, message FROM system_events ORDER BY ts DESC LIMIT 500",
                ["ts", "source", "level", "message"],
            ),
            (
                "scan_candidates",
                """SELECT scan_id, ts, symbol, decision, source, exchange, regime,
                          direction, composite_score, stop_pct,
                          scanner_effective_position_usd
                   FROM scan_candidates ORDER BY ts DESC LIMIT 1000""",
                [
                    "scan_id",
                    "ts",
                    "symbol",
                    "decision",
                    "source",
                    "exchange",
                    "regime",
                    "direction",
                    "composite_score",
                    "stop_pct",
                    "scanner_effective_position_usd",
                ],
            ),
            (
                "scan_funnels",
                """SELECT scan_id, ts, scanner_candidates_total, dual_exposure_block,
                          cooldown_block, risk_block, data_unavailable, below_threshold,
                          econ_veto, research_only_block, sizing_zero, execution_failed,
                          entered, scored_total, econ_passed_total, final_entryable_total
                   FROM scan_funnels ORDER BY rowid DESC LIMIT 200""",
                [
                    "scan_id",
                    "ts",
                    "scanner_candidates_total",
                    "dual_exposure_block",
                    "cooldown_block",
                    "risk_block",
                    "data_unavailable",
                    "below_threshold",
                    "econ_veto",
                    "research_only_block",
                    "sizing_zero",
                    "execution_failed",
                    "entered",
                    "scored_total",
                    "econ_passed_total",
                    "final_entryable_total",
                ],
            ),
            (
                "open_positions",
                "SELECT * FROM open_positions",
                [
                    "id",
                    "symbol",
                    "strategy",
                    "qty",
                    "entry",
                    "stop",
                    "target",
                    "high_since_entry",
                    "ts_entry",
                    "scale_33_done",
                    "scale_66_done",
                    "paper",
                    "broker",
                    "notes",
                ],
            ),
            (
                "trades_recent",
                """SELECT ts, strategy, broker, symbol, action, order_type, qty,
                          price, value_usd, fee_usd, pnl_usd, paper, order_id, notes
                   FROM trades ORDER BY ts DESC LIMIT 200""",
                [
                    "ts",
                    "strategy",
                    "broker",
                    "symbol",
                    "action",
                    "order_type",
                    "qty",
                    "price",
                    "value_usd",
                    "fee_usd",
                    "pnl_usd",
                    "paper",
                    "order_id",
                    "notes",
                ],
            ),
            (
                "candidate_outcomes",
                """SELECT co.candidate_id, sc.ts, sc.symbol, sc.direction, sc.regime,
                          co.label_status, co.hit_1r, co.hit_stop, co.hit_2r,
                          co.mfe_4h_pct, co.mae_4h_pct, co.peak_r_4h,
                          co.time_to_05r_min, co.time_to_1r_min, co.time_to_2r_min
                   FROM candidate_outcomes co
                   JOIN scan_candidates sc ON co.candidate_id = sc.id
                   ORDER BY sc.ts DESC LIMIT 300""",
                [
                    "candidate_id",
                    "ts",
                    "symbol",
                    "direction",
                    "regime",
                    "label_status",
                    "hit_1r",
                    "hit_stop",
                    "hit_2r",
                    "mfe_4h_pct",
                    "mae_4h_pct",
                    "peak_r_4h",
                    "time_to_05r_min",
                    "time_to_1r_min",
                    "time_to_2r_min",
                ],
            ),
            (
                "lane_runtime_state",
                "SELECT * FROM lane_runtime_state",
                [
                    "lane_id",
                    "enabled",
                    "active",
                    "mode",
                    "health",
                    "readiness_state",
                    "last_heartbeat_at",
                    "updated_at",
                ],
            ),
            (
                "system_runtime_state",
                "SELECT * FROM system_runtime_state LIMIT 1",
                [
                    "id",
                    "process_mode",
                    "startup_ts",
                    "active_lanes",
                    "global_status",
                    "updated_at",
                ],
            ),
            (
                "kill_switch_log",
                "SELECT * FROM kill_switch_log ORDER BY ts DESC LIMIT 50",
                [
                    "id",
                    "ts",
                    "reason",
                    "balance",
                    "peak_balance",
                    "positions_closed",
                    "resumed_at",
                    "trigger_type",
                ],
            ),
            (
                "trade_integrity",
                """SELECT close_order_id, created_at, tier, reason, override_by
                   FROM trade_integrity ORDER BY created_at DESC LIMIT 200""",
                ["close_order_id", "created_at", "tier", "reason", "override_by"],
            ),
            (
                "exit_evaluations",
                """SELECT close_order_id, created_at, opportunity_loss_pct,
                          stop_overshoot_pct, mfe_at_exit, path_label
                   FROM exit_evaluations ORDER BY created_at DESC LIMIT 200""",
                [
                    "close_order_id",
                    "created_at",
                    "opportunity_loss_pct",
                    "stop_overshoot_pct",
                    "mfe_at_exit",
                    "path_label",
                ],
            ),
        ]

        for name, query, headers in exports:
            n = _export_csv(conn, query, db_dir / f"{name}.csv", headers)
            print(f"  [{name}] {n} rows")

    # ── 3. Bot log ────────────────────────────────────────────────
    print("\n3/4  Capturing bot log...")
    log_path = _ROOT / "logs" / "bot.log"
    if log_path.exists():
        lines_all = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        recent = "\n".join(lines_all[-500:])
        (out_dir / "bot_log_recent.txt").write_text(recent, encoding="utf-8")
        print(f"  bot_log_recent.txt — last {min(500, len(lines_all))} lines")
    else:
        (out_dir / "bot_log_recent.txt").write_text(
            "No bot.log found.\n", encoding="utf-8"
        )
        print("  WARNING: no bot.log found")

    # ── 4. Snapshot context ───────────────────────────────────────
    print("\n4/4  Writing snapshot_context.txt...")
    ctx = _build_context(
        conn if conn else sqlite3.connect(":memory:"), args.days, ts_str
    )
    (out_dir / "snapshot_context.txt").write_text(ctx, encoding="utf-8")
    if conn:
        conn.close()
    print("  OK")

    # ── Summary ───────────────────────────────────────────────────
    total_files = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    total_bytes = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print(f"\n{'=' * 60}")
    print(f"  Done — {total_files} files  ({total_bytes / 1024:.0f} KB)")
    print(f"  {out_dir}")

    # ── Optional zip ─────────────────────────────────────────────
    if args.zip:
        zip_name = f"algo_audit_{ts_tag}.zip"
        zip_path = Path.home() / "Desktop" / zip_name
        with zipfile.ZipFile(
            zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            for fpath in sorted(out_dir.rglob("*")):
                if fpath.is_file():
                    arcname = f"audit_{ts_tag}/" + str(fpath.relative_to(out_dir))
                    zf.write(fpath, arcname)
        zip_mb = zip_path.stat().st_size / 1_048_576
        print(f"  ZIP → {zip_path}  ({zip_mb:.1f} MB)")

    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
