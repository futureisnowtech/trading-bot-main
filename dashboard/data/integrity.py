"""
dashboard/data/integrity.py — Truth-tiered integrity metrics for the dashboard.

All functions are fail-safe: return empty/zero structures on any error.
These metrics surface data quality and attribution coverage, not trading signals.

Functions:
  get_integrity_summary()         — verified/suspect/quarantined/excluded counts + coverage%
  get_attribution_coverage()      — attribution + lineage completeness rates
  get_candidate_labeling_coverage(days) — labeling rate for scan_candidates
  get_promotion_state()           — current challenger promotion state
  get_exit_quality_summary()      — exit quality metrics from exit_evaluations
  get_integrity_issues(hours)     — recent non-verified trades for audit panel
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Dashboard modules use the shared db shim
try:
    from db import _q, _q1
except ImportError:
    # Fallback for standalone use
    import sqlite3
    from config import DB_PATH

    def _q(sql, params=()):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows

    def _q1(sql, params=()):
        rows = _q(sql, params)
        return rows[0] if rows else {}


def get_integrity_summary() -> dict:
    """
    Return counts by integrity tier and coverage percentage.

    Returns:
        verified, suspect, quarantined, excluded, total_closes, coverage_pct
    """
    defaults = {
        "verified": 0,
        "suspect": 0,
        "quarantined": 0,
        "excluded": 0,
        "total_closes": 0,
        "coverage_pct": 0.0,
    }
    try:
        total_row = _q1("SELECT COUNT(*) as n FROM trades WHERE pnl_usd != 0")
        total_closes = int(total_row.get("n", 0))

        tier_rows = _q("SELECT tier, COUNT(*) as n FROM trade_integrity GROUP BY tier")
        tier_counts = {r["tier"]: int(r["n"]) for r in tier_rows}
        covered = sum(tier_counts.values())

        return {
            "verified": tier_counts.get("verified", 0),
            "suspect": tier_counts.get("suspect", 0),
            "quarantined": tier_counts.get("quarantined", 0),
            "excluded": tier_counts.get("excluded", 0),
            "total_closes": total_closes,
            "coverage_pct": round(covered / total_closes * 100, 1)
            if total_closes > 0
            else 0.0,
        }
    except Exception:
        return defaults


def get_attribution_coverage() -> dict:
    """
    Return attribution and lineage completeness metrics.

    Returns:
        total_closes, attributed, lineage_complete, coverage_pct, lineage_pct
    """
    defaults = {
        "total_closes": 0,
        "attributed": 0,
        "lineage_complete": 0,
        "coverage_pct": 0.0,
        "lineage_pct": 0.0,
    }
    try:
        total_row = _q1("SELECT COUNT(*) as n FROM trades WHERE pnl_usd != 0")
        total = int(total_row.get("n", 0))

        attr_row = _q1("SELECT COUNT(*) as n FROM trade_attribution")
        attributed = int(attr_row.get("n", 0))

        lin_row = _q1(
            "SELECT COUNT(*) as n FROM trade_attribution WHERE lineage_complete = 1"
        )
        lineage_complete = int(lin_row.get("n", 0))

        return {
            "total_closes": total,
            "attributed": attributed,
            "lineage_complete": lineage_complete,
            "coverage_pct": round(attributed / total * 100, 1) if total > 0 else 0.0,
            "lineage_pct": round(lineage_complete / max(attributed, 1) * 100, 1),
        }
    except Exception:
        return defaults


def get_candidate_labeling_coverage(days: int = 7) -> dict:
    """
    Return candidate labeling coverage for the past N days.

    Returns:
        total_candidates, labeled, coverage_pct, backlog
    """
    defaults = {
        "total_candidates": 0,
        "labeled": 0,
        "coverage_pct": 0.0,
        "backlog": 0,
    }
    try:
        total_row = _q1(
            f"SELECT COUNT(*) as n FROM scan_candidates "
            f"WHERE ts >= datetime('now', '-{days} days')"
        )
        total = int(total_row.get("n", 0))

        labeled_row = _q1(
            f"SELECT COUNT(*) as n FROM scan_candidates "
            f"WHERE ts >= datetime('now', '-{days} days') AND labeled = 1"
        )
        labeled = int(labeled_row.get("n", 0))

        # Backlog: unlabeled candidates old enough to label (> 4 hours)
        backlog_row = _q1(
            "SELECT COUNT(*) as n FROM scan_candidates "
            "WHERE labeled = 0 AND ts <= datetime('now', '-4 hours')"
        )
        backlog = int(backlog_row.get("n", 0))

        return {
            "total_candidates": total,
            "labeled": labeled,
            "coverage_pct": round(labeled / total * 100, 1) if total > 0 else 0.0,
            "backlog": backlog,
        }
    except Exception:
        return defaults


def get_promotion_state() -> list[dict]:
    """Return current challenger_state rows, most recent first."""
    try:
        rows = _q("SELECT * FROM challenger_state ORDER BY created_at DESC LIMIT 20")
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_exit_quality_summary(days: int = 30) -> dict:
    """Return aggregate exit quality metrics from exit_evaluations."""
    defaults = {
        "count": 0,
        "avg_opportunity_loss_pct": 0.0,
        "avg_stop_overshoot_pct": 0.0,
        "path_label_counts": {},
        "exit_type_counts": {},
    }
    try:
        rows = _q(
            f"SELECT exit_type, opportunity_loss_pct, stop_overshoot_pct, path_label "
            f"FROM exit_evaluations "
            f"WHERE created_at >= datetime('now', '-{days} days')"
        )
        if not rows:
            return defaults

        count = len(rows)
        opp_losses = [
            r["opportunity_loss_pct"]
            for r in rows
            if r["opportunity_loss_pct"] is not None
        ]
        overshots = [
            r["stop_overshoot_pct"] for r in rows if r["stop_overshoot_pct"] is not None
        ]
        path_labels: dict = {}
        exit_types: dict = {}
        for r in rows:
            lbl = r.get("path_label") or "unknown"
            et = r.get("exit_type") or "unknown"
            path_labels[lbl] = path_labels.get(lbl, 0) + 1
            exit_types[et] = exit_types.get(et, 0) + 1

        return {
            "count": count,
            "avg_opportunity_loss_pct": round(sum(opp_losses) / len(opp_losses), 3)
            if opp_losses
            else 0.0,
            "avg_stop_overshoot_pct": round(sum(overshots) / len(overshots), 3)
            if overshots
            else 0.0,
            "path_label_counts": path_labels,
            "exit_type_counts": exit_types,
        }
    except Exception:
        return defaults


def get_integrity_issues(hours: int = 48) -> list[dict]:
    """
    Return recent trades with non-verified integrity tier.
    Used by the audit panel to surface data quality problems.
    """
    try:
        rows = _q(
            f"""
            SELECT ti.trade_id, ti.close_order_id, ti.tier, ti.reason,
                   ti.source_check, ti.created_at, ti.notes
            FROM trade_integrity ti
            WHERE ti.tier != 'verified'
              AND ti.created_at >= datetime('now', '-{hours} hours')
            ORDER BY ti.created_at DESC
            LIMIT 50
            """
        )
        return [dict(r) for r in rows]
    except Exception:
        return []
