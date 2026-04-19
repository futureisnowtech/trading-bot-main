"""
dashboard/data/journal_health.py — Candidate journaling + labeling health queries.

Powers the "Learning & Journaling Health" expander in the SYSTEM SETTINGS tab.
All queries are read-only and fail silently so they never crash the dashboard.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from db import _q, _q1

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def get_journal_health() -> dict:
    """
    Return a comprehensive health snapshot for the candidate learning subsystem.

    Covers:
    - 24h and 7d candidate counts
    - Labeling rate and backlog
    - Decision funnel breakdown
    - Top economics gate veto reasons
    - Last nightly audit result
    - Outcome label quality (% complete vs data_unavailable)
    """
    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_label = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")

    # ── Candidate counts ──────────────────────────────────────────────────────
    r_24h = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))",
        (cutoff_24h,),
    )
    r_7d = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))",
        (cutoff_7d,),
    )
    r_labeled = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' ')) AND labeled=1",
        (cutoff_7d,),
    )
    r_backlog = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE labeled=0 AND {_TS_NORM} <= datetime(replace(substr(?,1,19),'T',' '))",
        (cutoff_label,),
    )

    candidates_24h = r_24h.get("n") or 0
    candidates_7d = r_7d.get("n") or 0
    labeled_7d = r_labeled.get("n") or 0
    backlog = r_backlog.get("n") or 0
    labeling_rate_pct = (
        round(labeled_7d / candidates_7d * 100, 1) if candidates_7d else 0.0
    )

    # ── Decision funnel (24h) ─────────────────────────────────────────────────
    funnel_rows = _q(
        f"""SELECT decision, COUNT(*) AS n FROM scan_candidates
           WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' ')) GROUP BY decision ORDER BY n DESC""",
        (cutoff_24h,),
    )
    funnel = {r["decision"]: r["n"] for r in funnel_rows if r.get("decision")}

    entered = funnel.get("entered", 0)
    conversion_pct = round(entered / candidates_24h * 100, 1) if candidates_24h else 0.0

    # ── research_only_block counts ────────────────────────────────────────────
    research_block_24h_row = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' ')) AND decision='research_only_block'",
        (cutoff_24h,),
    )
    research_block_7d_row = _q1(
        f"SELECT COUNT(*) AS n FROM scan_candidates WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' ')) AND decision='research_only_block'",
        (cutoff_7d,),
    )
    research_only_blocks_24h = research_block_24h_row.get("n") or 0
    research_only_blocks_7d = research_block_7d_row.get("n") or 0

    # ── Top veto reasons (24h) ────────────────────────────────────────────────
    veto_rows = _q(
        f"""SELECT econ_reject_reason AS reason, COUNT(*) AS n
           FROM scan_candidates
           WHERE {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
             AND decision='econ_veto' AND econ_reject_reason != ''
           GROUP BY econ_reject_reason ORDER BY n DESC LIMIT 5""",
        (cutoff_24h,),
    )

    # ── Outcome label quality (7d) ────────────────────────────────────────────
    label_q = _q1(
        """SELECT
               COUNT(*) AS total,
               SUM(CASE WHEN label_status='complete' THEN 1 ELSE 0 END) AS complete,
               SUM(CASE WHEN label_status='data_unavailable' THEN 1 ELSE 0 END) AS unavailable
           FROM candidate_outcomes co
           JOIN scan_candidates sc ON co.candidate_id = sc.id
           WHERE datetime(replace(substr(sc.ts,1,19),'T',' ')) >= datetime(replace(substr(?,1,19),'T',' '))""",
        (cutoff_7d,),
    )
    outcomes_total = label_q.get("total") or 0
    outcomes_complete = label_q.get("complete") or 0
    outcomes_unavailable = label_q.get("unavailable") or 0
    outcome_quality_pct = (
        round(outcomes_complete / outcomes_total * 100, 1) if outcomes_total else 0.0
    )

    # ── Last nightly audit ────────────────────────────────────────────────────
    last_audit = _q1(
        """SELECT ts, message FROM system_events
           WHERE source='nightly_audit' AND message LIKE 'NIGHTLY_AUDIT_REPORT:%'
           ORDER BY id DESC LIMIT 1"""
    )
    last_audit_ts = last_audit.get("ts", "")
    last_audit_overall = "unknown"
    if last_audit.get("message"):
        try:
            import json

            payload = last_audit["message"][len("NIGHTLY_AUDIT_REPORT:") :].strip()
            parsed = json.loads(payload)
            last_audit_overall = parsed.get("overall", "unknown")
        except Exception:
            pass

    # ── Overall health status ─────────────────────────────────────────────────
    if candidates_24h == 0:
        health_status = "WARNING"
        health_detail = "No candidates in last 24h — scanner may be down"
    elif backlog > 200:
        health_status = "WARNING"
        health_detail = f"Labeling backlog {backlog} rows"
    elif labeling_rate_pct < 40 and candidates_7d >= 20:
        health_status = "WARNING"
        health_detail = f"Labeling rate {labeling_rate_pct:.0f}% is low"
    else:
        health_status = "OK"
        health_detail = (
            f"{candidates_24h} candidates/24h · {labeling_rate_pct:.0f}% labeled (7d)"
        )

    return {
        "health_status": health_status,
        "health_detail": health_detail,
        "candidates_24h": candidates_24h,
        "candidates_7d": candidates_7d,
        "labeled_7d": labeled_7d,
        "labeling_rate_pct": labeling_rate_pct,
        "backlog": backlog,
        "conversion_pct": conversion_pct,
        "funnel": funnel,
        "top_veto_reasons": veto_rows,
        "outcomes_total": outcomes_total,
        "outcomes_complete": outcomes_complete,
        "outcomes_unavailable": outcomes_unavailable,
        "outcome_quality_pct": outcome_quality_pct,
        "last_audit_ts": last_audit_ts,
        "last_audit_overall": last_audit_overall,
        "research_only_blocks_24h": research_only_blocks_24h,
        "research_only_blocks_7d": research_only_blocks_7d,
    }
