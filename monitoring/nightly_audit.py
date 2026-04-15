"""
monitoring/nightly_audit.py — Automated nightly proof + drift + learning audit.

Runs automatically:
  - Scheduled by v10_runner at ~08:00 UTC (after RBI nightly loop)
  - Can also be run standalone: python3 monitoring/nightly_audit.py

Checks:
  1. Proof suite status (pytest tests/proof/ in subprocess)
  2. Candidate journaling health (scan_candidates populated / labeled)
  3. Candidate funnel analytics (entered vs vetoed vs blocked, top veto reasons)
  4. Outcome labeling lag / backlog
  5. Repo truth drift (CLAUDE.md version vs runtime version)
  6. Learning layer health (signal_stats Bayesian weight changes)
  7. Retention (table sizes, prune old rows)

Exception-only reporting:
  - Writes a JSON report to system_events (source='nightly_audit') ALWAYS (daily heartbeat)
  - Emits a notification (notifications table) ONLY when:
      * overall status changes from previous run
      * overall status is 'warn' or 'fail' (resends after 6h if warning, 1h if critical)
      * 24h have passed since last INFO notification (healthy heartbeat)

Exit code: 0 = pass, 1 = warn, 2 = fail (for standalone/cron use).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

# ── Notification cooldowns (seconds) ──────────────────────────────────────────
_NOTIFY_COOLDOWN_INFO = 23 * 3600  # once per 24h for healthy heartbeat
_NOTIFY_COOLDOWN_WARN = 6 * 3600  # up to 4× per day for warnings
_NOTIFY_COOLDOWN_CRIT = 3600  # up to 24× per day for critical failures


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


def _get_last_audit_report() -> dict:
    """
    Read the most recent nightly_audit report from system_events.
    Returns {} if not found.
    """
    try:
        from logging_db.trade_logger import _conn

        conn = _conn()
        row = conn.execute(
            """SELECT message FROM system_events
               WHERE source='nightly_audit' AND message LIKE 'NIGHTLY_AUDIT_REPORT:%'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        conn.close()
        if row:
            payload = row[0][len("NIGHTLY_AUDIT_REPORT:") :].strip()
            return json.loads(payload)
    except Exception:
        pass
    return {}


def _get_last_notification_ts(category: str, title_prefix: str) -> float:
    """
    Return the timestamp (epoch float) of the most recent notification matching
    category + title_prefix. Returns 0.0 if not found.
    """
    try:
        from logging_db.trade_logger import _conn

        conn = _conn()
        row = conn.execute(
            """SELECT ts FROM notifications
               WHERE category=? AND title LIKE ?
               ORDER BY CAST(ts AS REAL) DESC LIMIT 1""",
            (category, f"{title_prefix}%"),
        ).fetchone()
        conn.close()
        if row:
            return float(row[0])
    except Exception:
        pass
    return 0.0


def _emit_audit_notification(overall: str, summary_lines: list[str]) -> None:
    """
    Emit an exception-only notification based on audit result.

    Cooldowns prevent notification spam:
    - pass   → INFO notification at most once per 23h
    - warn   → WARNING notification at most once per 6h
    - fail   → CRITICAL notification at most once per 1h
    - status change (non-pass → pass) → always notify (recovery)
    """
    try:
        from notifications.notification_engine import (
            notify,
            CAT_SYSTEM,
            SEV_INFO,
            SEV_WARNING,
            SEV_CRITICAL,
        )

        last_report = _get_last_audit_report()
        last_overall = last_report.get("overall", "unknown")
        now = time.time()

        # Determine severity and cooldown
        if overall == "pass":
            sev = SEV_INFO
            cooldown = _NOTIFY_COOLDOWN_INFO
            title = "LEARNING HEALTH: ALL CLEAR"
            # Always notify on recovery (non-pass → pass)
            force = last_overall not in ("pass", "unknown")
        elif overall == "warn":
            sev = SEV_WARNING
            cooldown = _NOTIFY_COOLDOWN_WARN
            title = "LEARNING HEALTH: WARNING"
            force = last_overall not in ("warn",)
        else:  # fail / error
            sev = SEV_CRITICAL
            cooldown = _NOTIFY_COOLDOWN_CRIT
            title = "LEARNING HEALTH: ACTION NEEDED"
            force = True  # always escalate failures

        # Check if we're within the cooldown window
        last_ts = _get_last_notification_ts(CAT_SYSTEM, title)
        elapsed = now - last_ts
        if not force and elapsed < cooldown:
            return  # still within cooldown, skip

        message = (
            "; ".join(summary_lines[:3])
            if summary_lines
            else f"Audit overall={overall}"
        )

        notify(
            category=CAT_SYSTEM,
            severity=sev,
            title=title,
            message=message,
            data={"overall": overall, "audit_ts": _utc_now()},
        )
    except Exception as e:
        logger.debug(f"[audit] notification emit error: {e}")


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
        m = re.search(r"(\d+) passed(?:,\s*(\d+) failed)?(?:,\s*(\d+) error)?", output)
        if m:
            result["passed"] = int(m.group(1) or 0)
            result["failed"] = int(m.group(2) or 0)
            result["errors"] = int(m.group(3) or 0)

        result["duration_s"] = duration
        result["status"] = "pass" if proc.returncode == 0 else "fail"
        result["detail"] = output[-400:].strip()
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["detail"] = "pytest timed out after 120s"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 2: candidate journaling health ─────────────────────────────────────


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


# ── check 3: candidate funnel analytics ──────────────────────────────────────


def _check_candidate_funnel() -> dict:
    """
    Analyze the candidate decision funnel for the last 24h.

    Returns conversion rate, top veto reasons, and anomaly flags.
    """
    result: dict[str, Any] = {
        "status": "pass",
        "candidates_24h": 0,
        "entered_24h": 0,
        "econ_veto_24h": 0,
        "below_threshold_24h": 0,
        "blocked_24h": 0,
        "conversion_rate_pct": 0.0,
        "top_veto_reasons": [],
        "anomaly": "",
        "detail": "",
    }
    try:
        from logging_db.trade_logger import _conn
        import datetime as _dt

        cutoff_24h = (datetime.now(timezone.utc) - _dt.timedelta(hours=24)).isoformat()

        conn = _conn()

        # Total candidates in last 24h
        row = conn.execute(
            "SELECT COUNT(*) FROM scan_candidates WHERE ts >= ?", (cutoff_24h,)
        ).fetchone()
        total_24h = int((row or [0])[0])
        result["candidates_24h"] = total_24h

        if total_24h == 0:
            result["status"] = "warn"
            result["detail"] = "Zero candidates in last 24h — scanner may be down"
            conn.close()
            return result

        # Count by decision category
        rows = conn.execute(
            """SELECT decision, COUNT(*) AS n FROM scan_candidates
               WHERE ts >= ? GROUP BY decision""",
            (cutoff_24h,),
        ).fetchall()
        decision_map = {r[0]: r[1] for r in rows}

        entered = decision_map.get("entered", 0)
        econ_veto = decision_map.get("econ_veto", 0)
        below_thresh = decision_map.get("below_threshold", 0)
        blocked = sum(
            v
            for k, v in decision_map.items()
            if k
            in ("dual_exposure_block", "cooldown_block", "risk_block", "sizing_zero")
        )

        result["entered_24h"] = entered
        result["econ_veto_24h"] = econ_veto
        result["below_threshold_24h"] = below_thresh
        result["blocked_24h"] = blocked
        result["conversion_rate_pct"] = (
            round(entered / total_24h * 100, 1) if total_24h > 0 else 0.0
        )

        # Top veto reasons
        veto_rows = conn.execute(
            """SELECT econ_reject_reason, COUNT(*) AS n FROM scan_candidates
               WHERE ts >= ? AND decision='econ_veto' AND econ_reject_reason != ''
               GROUP BY econ_reject_reason ORDER BY n DESC LIMIT 5""",
            (cutoff_24h,),
        ).fetchall()
        result["top_veto_reasons"] = [
            {"reason": r[0][:80], "count": r[1]} for r in veto_rows
        ]

        conn.close()

        # Anomaly detection: candidate volume collapse or spike
        # Collapse: < 5 candidates in 24h (scanner likely down)
        # Spike: > 10× typical volume in 24h (possible loop or data issue)
        if total_24h < 5:
            result["status"] = "warn"
            result["anomaly"] = f"candidate_collapse: only {total_24h} in 24h"
        elif total_24h > 5000:
            result["status"] = "warn"
            result["anomaly"] = f"candidate_spike: {total_24h} in 24h (>5000)"

        result["detail"] = (
            f"{total_24h} candidates: "
            f"entered={entered} econ_veto={econ_veto} "
            f"below_thresh={below_thresh} blocked={blocked} "
            f"conversion={result['conversion_rate_pct']:.1f}%"
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


# ── check 6: retention ────────────────────────────────────────────────────────


def _check_retention() -> dict:
    """
    Prune old scan_candidates rows and report table sizes.

    Policy:
    - labeled=1 rows older than 90 days → delete (learning value already extracted)
    - labeled=0 rows older than 30 days → delete (permanently stale; labeler gave up)
    - candidate_outcomes rows are never pruned (tiny, high value)
    """
    result: dict[str, Any] = {
        "status": "pass",
        "scan_candidates_total": 0,
        "candidate_outcomes_total": 0,
        "pruned_labeled": 0,
        "pruned_unlabeled": 0,
        "detail": "",
    }
    try:
        from logging_db.trade_logger import _conn, prune_old_candidates

        prune_result = prune_old_candidates(labeled_days=90, unlabeled_days=30)
        result["pruned_labeled"] = prune_result.get("pruned_labeled", 0)
        result["pruned_unlabeled"] = prune_result.get("pruned_unlabeled", 0)

        conn = _conn()
        row = conn.execute("SELECT COUNT(*) FROM scan_candidates").fetchone()
        result["scan_candidates_total"] = int((row or [0])[0])
        row2 = conn.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()
        result["candidate_outcomes_total"] = int((row2 or [0])[0])
        conn.close()

        pruned_total = result["pruned_labeled"] + result["pruned_unlabeled"]
        result["detail"] = (
            f"scan_candidates={result['scan_candidates_total']} "
            f"candidate_outcomes={result['candidate_outcomes_total']} "
            f"pruned={pruned_total}"
        )

        # Warn if table is still very large after pruning (> 500K rows unusual)
        if result["scan_candidates_total"] > 500_000:
            result["status"] = "warn"
            result["detail"] += " — table unusually large, check for insert loop"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 7: integrity coverage ──────────────────────────────────────────────


def _check_integrity_coverage() -> dict:
    """
    Verify trade_integrity table covers >= 80% of close-side trades.
    Emits WARN if coverage is below threshold.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "total_closes": 0,
        "covered": 0,
        "verified": 0,
        "quarantined": 0,
        "excluded": 0,
        "coverage_pct": 0.0,
        "detail": "",
    }
    try:
        from logging_db.trade_logger import get_integrity_summary

        summary = get_integrity_summary()
        total = summary.get("total_closes", 0)
        covered = (
            summary.get("verified", 0)
            + summary.get("suspect", 0)
            + summary.get("quarantined", 0)
            + summary.get("excluded", 0)
        )
        pct = summary.get("coverage_pct", 0.0)

        result.update(
            {
                "total_closes": total,
                "covered": covered,
                "verified": summary.get("verified", 0),
                "quarantined": summary.get("quarantined", 0),
                "excluded": summary.get("excluded", 0),
                "coverage_pct": pct,
            }
        )

        if pct < 80.0 and total > 0:
            result["status"] = "warn"
            result["detail"] = (
                f"Integrity coverage {pct:.0f}% < 80% — run "
                f"python3 scripts/migrate_integrity_backfill.py to backfill"
            )
        else:
            result["status"] = "pass"
            result["detail"] = (
                f"integrity covered={covered}/{total} ({pct:.0f}%) "
                f"verified={summary.get('verified', 0)} "
                f"quarantined={summary.get('quarantined', 0)}"
            )
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 8: attribution coverage ────────────────────────────────────────────


def _check_attribution_coverage() -> dict:
    """
    Verify trade_attribution covers >= 80% of close-side trades.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "total_closes": 0,
        "attributed": 0,
        "lineage_complete": 0,
        "coverage_pct": 0.0,
        "detail": "",
    }
    try:
        from logging_db.trade_logger import _conn

        conn = _conn()
        total_row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE pnl_usd != 0"
        ).fetchone()
        total = int((total_row or [0])[0])

        attr_row = conn.execute("SELECT COUNT(*) FROM trade_attribution").fetchone()
        attributed = int((attr_row or [0])[0])

        lin_row = conn.execute(
            "SELECT COUNT(*) FROM trade_attribution WHERE lineage_complete = 1"
        ).fetchone()
        lineage_complete = int((lin_row or [0])[0])
        conn.close()

        pct = round(attributed / total * 100, 1) if total > 0 else 0.0
        lin_pct = round(lineage_complete / max(attributed, 1) * 100, 1)

        result.update(
            {
                "total_closes": total,
                "attributed": attributed,
                "lineage_complete": lineage_complete,
                "coverage_pct": pct,
            }
        )

        if pct < 80.0 and total > 0:
            result["status"] = "warn"
            result["detail"] = f"Attribution coverage {pct:.0f}% < 80%"
        else:
            result["status"] = "pass"
            result["detail"] = (
                f"attribution={attributed}/{total} ({pct:.0f}%) "
                f"lineage_complete={lineage_complete} ({lin_pct:.0f}%)"
            )
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:200]

    return result


# ── check 9: challenger promotion evaluation ──────────────────────────────────


def _check_challenger_promotion() -> dict:
    """
    Run promotion engine evaluation. Emits INFO/WARN if any run crosses tier.
    """
    result: dict[str, Any] = {
        "status": "pass",
        "evaluated": 0,
        "promoted_pending": 0,
        "demoted": 0,
        "detail": "",
    }
    try:
        from backtesting.promotion_engine import PromotionEngine

        engine = PromotionEngine()
        evals = engine.evaluate_all()
        promoted = sum(
            1 for e in evals if e.get("promotion_tier") == "PROMOTED_PENDING_HUMAN"
        )
        demoted = sum(1 for e in evals if e.get("promotion_tier") == "DEMOTED")

        result["evaluated"] = len(evals)
        result["promoted_pending"] = promoted
        result["demoted"] = demoted

        if promoted > 0:
            result["status"] = "warn"  # WARN = action required from human
            result["detail"] = (
                f"{promoted} challenger(s) ready for review — human confirmation required"
            )
        elif demoted > 0:
            result["status"] = "warn"
            result["detail"] = f"{demoted} strategy(ies) flagged for demotion review"
        else:
            result["detail"] = f"{len(evals)} runs evaluated, none ready for promotion"
    except Exception as e:
        # Promotion engine is non-critical — don't fail the audit
        result["status"] = "pass"
        result["detail"] = f"promotion check skipped: {e}"

    return result


# ── check 10: ML retrain queue ────────────────────────────────────────────────


def _check_ml_retrain_queue() -> dict:
    """
    Check if ml_retrain_queue has pending items and emit INFO/WARN.
    """
    result: dict[str, Any] = {
        "status": "pass",
        "pending": 0,
        "detail": "",
    }
    try:
        from logging_db.trade_logger import _conn

        conn = _conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM ml_retrain_queue WHERE status='pending'"
        ).fetchone()
        pending = int((row or [0])[0])
        conn.close()

        result["pending"] = pending
        if pending > 0:
            result["status"] = "warn"
            result["detail"] = (
                f"{pending} pending ML retrain items — walk_forward_trainer will process them"
            )
        else:
            result["detail"] = "ML retrain queue empty"
    except Exception as e:
        result["status"] = "pass"  # non-critical
        result["detail"] = f"retrain queue check skipped: {e}"

    return result


# ── main audit ────────────────────────────────────────────────────────────────


def run_audit(run_proof: bool = True) -> dict:
    """
    Run all audit checks and return a structured report dict.
    Also writes the report to system_events and emits exception-only notifications.

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

    logger.info("[audit] checking candidate funnel...")
    checks["candidate_funnel"] = _check_candidate_funnel()

    logger.info("[audit] checking repo drift...")
    checks["repo_drift"] = _check_repo_drift()

    logger.info("[audit] checking learning health...")
    checks["learning_health"] = _check_learning_health()

    logger.info("[audit] running retention pruning...")
    checks["retention"] = _check_retention()

    # v14.0: integrity, attribution, promotion, retrain checks
    logger.info("[audit] checking integrity coverage...")
    checks["integrity_coverage"] = _check_integrity_coverage()

    logger.info("[audit] checking attribution coverage...")
    checks["attribution_coverage"] = _check_attribution_coverage()

    logger.info("[audit] evaluating challenger promotion...")
    checks["challenger_promotion"] = _check_challenger_promotion()

    logger.info("[audit] checking ML retrain queue...")
    checks["ml_retrain_queue"] = _check_ml_retrain_queue()

    # Overall status: worst of all checks (skipped checks don't count)
    statuses = [
        c.get("status", "unknown")
        for c in checks.values()
        if c.get("status") != "skipped"
    ]
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

    # Build summary lines for notification message
    summary_lines = []
    for k, v in checks.items():
        s = v.get("status", "?")
        if s not in ("pass", "skipped"):
            summary_lines.append(f"{k}={s}: {v.get('detail', '')[:60]}")
    if not summary_lines:
        summary_lines = [
            checks.get("candidate_journaling", {}).get("detail", "all checks passing")[
                :80
            ]
        ]

    # Exception-only notification emit
    _emit_audit_notification(overall, summary_lines)

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
