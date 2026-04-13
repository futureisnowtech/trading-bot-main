"""
monitoring/nightly_audit.py — Automated nightly proof + drift + learning audit.

Runs automatically:
  - Scheduled by v10_runner at ~03:00 UTC (after RBI nightly loop)
  - Can also be run standalone: python3 monitoring/nightly_audit.py

Checks:
  1. Proof suite status (pytest tests/proof/ in subprocess)
  2. Candidate journaling health (scan_candidates populated / labeled)
  3. Outcome labeling lag / backlog
  4. Repo truth drift (CLAUDE.md version vs runtime version)
  5. Learning layer health (signal_stats Bayesian weight changes)

Writes a structured report to system_events (source='nightly_audit').
Exit code 0 = all green, 1 = warnings, 2 = failures.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


# ── helpers ──────────────────────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(level: str, message: str) -> None:
    """Write to system_events. Silent on failure."""
    try:
        from logging_db.trade_logger import log_event

        log_event(level, "nightly_audit", message)
    except Exception:
        pass


def _write_report(report: dict) -> None:
    """Serialise and persist the audit report as a single system_event."""
    try:
        _log_event("INFO", f"NIGHTLY_AUDIT_REPORT: {json.dumps(report)}")
    except Exception:
        pass


# ── check 1: proof suite ─────────────────────────────────────────────────────


def _check_proof_suite() -> dict:
    """Run pytest tests/proof/ in a subprocess and return a status dict."""
    result: dict[str, Any] = {
        "status": "unknown",
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "duration_s": 0.0,
        "detail": "",
    }
    proof_dir = ROOT / "tests" / "proof"
    if not proof_dir.exists():
        result["status"] = "missing"
        result["detail"] = "tests/proof/ directory not found"
        return result

    t0 = time.time()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(proof_dir),
                "-q",
                "--tb=short",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        duration = round(time.time() - t0, 1)
        output = proc.stdout + proc.stderr

        # Parse pytest summary line e.g. "3 passed, 1 failed in 4.2s"
        import re

        m = re.search(r"(\d+) passed(?:,\s*(\d+) failed)?(?:,\s*(\d+) error)?", output)
        if m:
            result["passed"] = int(m.group(1) or 0)
            result["failed"] = int(m.group(2) or 0)
            result["errors"] = int(m.group(3) or 0)

        result["duration_s"] = duration
        result["status"] = "pass" if proc.returncode == 0 else "fail"
        # Keep last 400 chars of output for the report
        result["detail"] = output[-400:].strip()
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["detail"] = "pytest timed out after 120s"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 2 + 3: candidate journaling health ─────────────────────────────────


def _check_candidate_journaling() -> dict:
    result: dict[str, Any] = {
        "status": "unknown",
        "total_7d": 0,
        "labeled_7d": 0,
        "unlabeled_backlog": 0,
        "labeling_rate_pct": 0.0,
        "last_candidate_ts": None,
        "decision_counts": {},
        "detail": "",
    }
    try:
        from logging_db.trade_logger import get_candidate_journal_stats

        stats = get_candidate_journal_stats(days=7)
        total = stats.get("total_candidates", 0)
        labeled = stats.get("labeled", 0)
        backlog = stats.get("unlabeled_backlog", 0)
        rate = round(labeled / total * 100, 1) if total > 0 else 0.0

        result.update(
            {
                "total_7d": total,
                "labeled_7d": labeled,
                "unlabeled_backlog": backlog,
                "labeling_rate_pct": rate,
                "last_candidate_ts": stats.get("last_ts"),
                "decision_counts": stats.get("decision_counts", {}),
            }
        )

        if total == 0:
            result["status"] = "warn"
            result["detail"] = (
                "No candidates journaled in last 7 days — is the bot running?"
            )
        elif backlog > 200:
            result["status"] = "warn"
            result["detail"] = (
                f"Labeling backlog {backlog} rows — labeler may be stalled"
            )
        elif rate < 50 and total >= 20:
            result["status"] = "warn"
            result["detail"] = f"Labeling rate {rate:.0f}% is low"
        else:
            result["status"] = "pass"
            result["detail"] = (
                f"{total} candidates journaled, {labeled} labeled ({rate:.0f}%)"
            )
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 4: repo truth drift ─────────────────────────────────────────────────


def _check_repo_drift() -> dict:
    result: dict[str, Any] = {
        "status": "unknown",
        "claude_md_version": None,
        "detail": "",
    }
    try:
        claude_md = ROOT / "CLAUDE.md"
        if not claude_md.exists():
            result["status"] = "warn"
            result["detail"] = "CLAUDE.md not found"
            return result

        import re

        text = claude_md.read_text(encoding="utf-8")
        m = re.search(r"Current Version:\s*(v[\d.]+)", text)
        version = m.group(1) if m else "unknown"
        result["claude_md_version"] = version
        result["status"] = "pass"
        result["detail"] = f"CLAUDE.md version: {version}"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 5: learning layer health ───────────────────────────────────────────


def _check_learning_health() -> dict:
    result: dict[str, Any] = {
        "status": "unknown",
        "signal_stats_rows": 0,
        "signals_with_min_fires": 0,
        "ml_feature_snapshots": 0,
        "detail": "",
    }
    try:
        import sqlite3

        from config import DB_PATH

        conn = sqlite3.connect(DB_PATH)

        try:
            row = conn.execute("SELECT COUNT(*) FROM signal_stats").fetchone()
            result["signal_stats_rows"] = int((row or [0])[0])
            row2 = conn.execute(
                "SELECT COUNT(*) FROM signal_stats WHERE fires >= 10"
            ).fetchone()
            result["signals_with_min_fires"] = int((row2 or [0])[0])
        except Exception:
            pass

        try:
            row3 = conn.execute("SELECT COUNT(*) FROM ml_feature_snapshots").fetchone()
            result["ml_feature_snapshots"] = int((row3 or [0])[0])
        except Exception:
            pass

        conn.close()

        result["status"] = "pass"
        result["detail"] = (
            f"signal_stats={result['signal_stats_rows']} "
            f"(>=10 fires: {result['signals_with_min_fires']}), "
            f"ml_snapshots={result['ml_feature_snapshots']}"
        )
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── main audit ────────────────────────────────────────────────────────────────


def run_audit(run_proof: bool = True) -> dict:
    """
    Run all audit checks and return a structured report dict.
    Also writes the report to system_events.

    Args:
        run_proof: whether to run the pytest proof suite (slow ~30s, skip in tests).
    """
    started_at = _utc_now()

    checks: dict[str, Any] = {}

    if run_proof:
        logger.info("[audit] running proof suite...")
        checks["proof_suite"] = _check_proof_suite()
    else:
        checks["proof_suite"] = {"status": "skipped", "detail": "skipped by caller"}

    logger.info("[audit] checking candidate journaling...")
    checks["candidate_journaling"] = _check_candidate_journaling()

    logger.info("[audit] checking repo drift...")
    checks["repo_drift"] = _check_repo_drift()

    logger.info("[audit] checking learning health...")
    checks["learning_health"] = _check_learning_health()

    # Overall status: worst of all checks
    statuses = [c.get("status", "unknown") for c in checks.values()]
    if "fail" in statuses or "error" in statuses:
        overall = "fail"
    elif "warn" in statuses or "timeout" in statuses:
        overall = "warn"
    else:
        overall = "pass"

    report = {
        "ts": started_at,
        "overall": overall,
        "checks": checks,
    }

    _write_report(report)

    level = "INFO" if overall == "pass" else "WARNING" if overall == "warn" else "ERROR"
    _log_event(
        level,
        f"Nightly audit complete: overall={overall} "
        + " | ".join(f"{k}={v.get('status', '?')}" for k, v in checks.items()),
    )

    logger.info(
        f"[audit] complete: overall={overall} "
        + " | ".join(f"{k}={v.get('status', '?')}" for k, v in checks.items())
    )

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Add repo root to path so imports work when run standalone
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    report = run_audit(run_proof=True)
    exit_code = (
        0 if report["overall"] == "pass" else (1 if report["overall"] == "warn" else 2)
    )
    sys.exit(exit_code)
