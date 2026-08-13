"""Cerebro: deterministic hypothesis generation, archive, and falsification."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH, estimate_kalshi_fee_per_contract
from intelligence.rbi2 import get_rbi2_status
from intelligence.schema import connect, init_intelligence_db


def _fingerprint(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()


def _latest_prediction_id(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM intelligence_predictions").fetchone()
    return int(row[0] or 0)


def _json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _insert_insight(
    *,
    kind: str,
    key: str,
    title: str,
    summary: str,
    mechanism: str,
    action: str,
    confidence: float,
    evidence: dict[str, Any],
    metric_spec: dict[str, Any],
    falsification_rule: str,
    cutoff_prediction_id: int,
    horizon_count: int,
    db_path: str,
) -> str | None:
    now = datetime.now(timezone.utc).isoformat()
    fingerprint = _fingerprint(kind, key)
    insight_id = f"ci-{fingerprint[:12]}"
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT insight_id FROM cerebro_insights WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if existing:
            return None
        conn.execute(
            """INSERT INTO cerebro_insights
               (insight_id, fingerprint, created_at, updated_at, status,
                insight_type, title, summary, mechanism, action_proposed,
                confidence, evidence_json, metric_spec_json,
                cutoff_prediction_id, horizon_count, falsification_rule)
               VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                insight_id,
                fingerprint,
                now,
                now,
                kind,
                title,
                summary,
                mechanism,
                action,
                max(0.0, min(1.0, float(confidence))),
                json.dumps(evidence, sort_keys=True),
                json.dumps(metric_spec, sort_keys=True),
                cutoff_prediction_id,
                max(5, int(horizon_count)),
                falsification_rule,
            ),
        )
        conn.commit()
    return insight_id


def _resolved_samples(conn, *, after_id: int = 0, mode: str = "", limit: int = 0) -> list[dict[str, Any]]:
    params: list[Any] = [int(after_id)]
    mode_clause = ""
    if mode:
        mode_clause = "AND p.weather_mode=?"
        params.append(mode)
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(
        f"""WITH ranked AS (
              SELECT p.*,
                     ROW_NUMBER() OVER (PARTITION BY p.ticker ORDER BY p.evaluated_at DESC, p.id DESC) rn
              FROM intelligence_predictions p
              WHERE p.id > ? {mode_clause}
                AND p.q_gfs IS NOT NULL AND p.q_ecmwf IS NOT NULL
            )
            SELECT p.*, o.market_result, o.settled_at
            FROM ranked p
            JOIN intelligence_outcomes o
              ON o.ticker=p.ticker AND o.current=1 AND o.official=1
            WHERE p.rn=1
              AND (o.settled_at IS NULL OR p.evaluated_at <= o.settled_at)
            ORDER BY p.id ASC {limit_clause}""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _brier(rows: list[dict[str, Any]], weights: dict[str, dict[str, float]]) -> float:
    values = []
    for row in rows:
        segment = str(row.get("weather_mode") or "GLOBAL")
        blend = weights.get(segment) or weights.get("GLOBAL") or {"gfs": 0.60, "ecmwf": 0.40}
        q = float(blend.get("gfs", 0.60)) * float(row["q_gfs"]) + float(blend.get("ecmwf", 0.40)) * float(row["q_ecmwf"])
        y = 1.0 if str(row["market_result"]).upper() == "YES" else 0.0
        values.append((q - y) ** 2)
    return sum(values) / len(values) if values else 0.0


def score_active_insights(db_path: str = DB_PATH) -> dict[str, int]:
    init_intelligence_db(db_path)
    summary = {"scored": 0, "confirmed": 0, "falsified": 0, "inconclusive": 0}
    with connect(db_path) as conn:
        active = conn.execute(
            "SELECT * FROM cerebro_insights WHERE status='ACTIVE' ORDER BY created_at"
        ).fetchall()
        for row in active:
            spec = json.loads(row["metric_spec_json"] or "{}")
            mode = str(spec.get("mode") or "")
            samples = _resolved_samples(
                conn,
                after_id=int(row["cutoff_prediction_id"] or 0),
                mode=mode,
                limit=int(row["horizon_count"] or 20),
            )
            if len(samples) < int(row["horizon_count"] or 20):
                continue
            metric_type = str(spec.get("metric") or "")
            score = 0.0
            confirmed = False
            if metric_type == "brier_improvement":
                candidate = _brier(samples, spec.get("candidate_weights") or {})
                baseline = _brier(samples, spec.get("baseline_weights") or {})
                score = baseline - candidate
                confirmed = score >= float(spec.get("minimum", 0.002))
            elif metric_type == "model_brier_advantage":
                gfs_bs = _brier(samples, {"GLOBAL": {"gfs": 1.0, "ecmwf": 0.0}})
                ec_bs = _brier(samples, {"GLOBAL": {"gfs": 0.0, "ecmwf": 1.0}})
                preferred = str(spec.get("preferred") or "gfs")
                score = (ec_bs - gfs_bs) if preferred == "gfs" else (gfs_bs - ec_bs)
                confirmed = score >= float(spec.get("minimum", 0.01))
            elif metric_type == "veto_counterfactual":
                reason = str(spec.get("reason") or "")
                matched = [sample for sample in samples if reason in str(sample.get("decision_reason") or "")]
                if not matched:
                    status = "INCONCLUSIVE"
                    note = "No future resolved examples matched the veto reason."
                    conn.execute(
                        """UPDATE cerebro_insights SET status=?, updated_at=?, scored_count=?,
                           score_value=?, resolution_note=? WHERE insight_id=?""",
                        (status, datetime.now(timezone.utc).isoformat(), len(samples), None, note, row["insight_id"]),
                    )
                    summary["scored"] += 1
                    summary["inconclusive"] += 1
                    continue
                values = []
                for sample in matched:
                    side = str(sample.get("chosen_side") or "")
                    y = 1.0 if str(sample["market_result"]).upper() == "YES" else 0.0
                    price = float(sample.get("yes_ask") or 0.0) if side == "YES" else float(sample.get("no_ask") or 0.0)
                    payout = y if side == "YES" else 1.0 - y
                    values.append(payout - price - estimate_kalshi_fee_per_contract(price, rounded=False))
                score = sum(values) / len(values)
                protective = bool(spec.get("protective", True))
                confirmed = score <= 0.0 if protective else score > 0.0
            else:
                continue

            status = "CONFIRMED" if confirmed else "FALSIFIED"
            note = f"Prospective horizon completed with score={score:.6f}."
            conn.execute(
                """UPDATE cerebro_insights SET status=?, updated_at=?, scored_count=?,
                   score_value=?, resolution_note=? WHERE insight_id=?""",
                (
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    len(samples),
                    score,
                    note,
                    row["insight_id"],
                ),
            )
            summary["scored"] += 1
            summary["confirmed" if confirmed else "falsified"] += 1
        conn.commit()
    return summary


def generate_insights(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    scoring = score_active_insights(db_path)
    created: list[str] = []
    status = get_rbi2_status(db_path=db_path)
    challenger = status.get("challenger") or {}
    champion = status.get("champion") or {}
    with connect(db_path) as conn:
        cutoff = _latest_prediction_id(conn)
        if challenger and bool((challenger.get("validation") or {}).get("passed")):
            artifact_id = str(challenger.get("artifact_id") or "")
            insight = _insert_insight(
                kind="challenger_improvement",
                key=artifact_id,
                title="RBI 2.0 challenger predicts cleaner probabilities",
                summary=(
                    f"Artifact {artifact_id} improved chronological holdout Brier by "
                    f"{float(challenger.get('expected_improvement') or 0.0):.4f}."
                ),
                mechanism="Regularized model weighting may be adapting to persistent relative GFS/ECMWF skill without counting duplicate contract sides.",
                action="Review the artifact for promotion only after its prospective horizon confirms the improvement.",
                confidence=min(0.90, 0.55 + float(challenger.get("holdout_size") or 0) / 100.0),
                evidence={"artifact_id": artifact_id, "validation": challenger.get("validation"), "holdout_size": challenger.get("holdout_size")},
                metric_spec={
                    "metric": "brier_improvement",
                    "candidate_weights": challenger.get("weights") or {},
                    "baseline_weights": champion.get("weights") or {"GLOBAL": {"gfs": 0.60, "ecmwf": 0.40}},
                    "minimum": 0.002,
                },
                falsification_rule="Falsified if prospective Brier improvement is below 0.002 over the next 20 independently settled tickers.",
                cutoff_prediction_id=cutoff,
                horizon_count=20,
                db_path=db_path,
            )
            if insight:
                created.append(insight)

        history = _resolved_samples(conn)
        evidence_cutoff = max((int(row["id"]) for row in history), default=0)
        for mode in sorted({str(row.get("weather_mode") or "") for row in history if row.get("weather_mode")}):
            rows = [row for row in history if str(row.get("weather_mode")) == mode]
            if len(rows) < 12:
                continue
            gfs_bs = _brier(rows, {"GLOBAL": {"gfs": 1.0, "ecmwf": 0.0}})
            ec_bs = _brier(rows, {"GLOBAL": {"gfs": 0.0, "ecmwf": 1.0}})
            advantage = abs(gfs_bs - ec_bs)
            if advantage < 0.01:
                continue
            preferred = "gfs" if gfs_bs < ec_bs else "ecmwf"
            insight = _insert_insight(
                kind="model_dominance",
                key=f"{mode}:{preferred}:{evidence_cutoff}",
                title=f"{preferred.upper()} is showing a {mode} skill advantage",
                summary=f"Across {len(rows)} official {mode} outcomes, the Brier advantage is {advantage:.4f}.",
                mechanism="The model may be resolving this weather regime or lead-time mix more accurately, but historical association alone is not promotion evidence.",
                action=f"Track {preferred.upper()} prospectively in {mode}; do not change weights until the horizon resolves.",
                confidence=min(0.85, 0.50 + len(rows) / 100.0),
                evidence={"mode": mode, "n": len(rows), "gfs_brier": gfs_bs, "ecmwf_brier": ec_bs},
                metric_spec={"metric": "model_brier_advantage", "mode": mode, "preferred": preferred, "minimum": 0.01},
                falsification_rule=f"Falsified if {preferred.upper()} fails to retain at least 0.01 Brier advantage over the next 20 {mode} tickers.",
                cutoff_prediction_id=cutoff,
                horizon_count=20,
                db_path=db_path,
            )
            if insight:
                created.append(insight)

        veto_rows = conn.execute(
            """WITH ranked AS (
                 SELECT p.*, ROW_NUMBER() OVER (
                   PARTITION BY p.ticker ORDER BY p.evaluated_at DESC, p.id DESC
                 ) rn
                 FROM intelligence_predictions p
               )
               SELECT p.*, o.market_result
               FROM ranked p
               JOIN intelligence_outcomes o ON o.ticker=p.ticker AND o.current=1 AND o.official=1
               WHERE p.rn=1
                 AND (o.settled_at IS NULL OR p.evaluated_at <= o.settled_at)
                 AND p.decision NOT IN ('entered', '')
                 AND p.chosen_side IN ('YES', 'NO')
                 AND p.decision_reason != ''"""
        ).fetchall()
        grouped: dict[str, list[float]] = {}
        for raw in veto_rows:
            row = dict(raw)
            reason = str(row.get("decision_reason") or "").split(" (")[0]
            side = str(row.get("chosen_side") or "")
            price = float(row.get("yes_ask") or 0.0) if side == "YES" else float(row.get("no_ask") or 0.0)
            if price <= 0:
                continue
            y = 1.0 if str(row.get("market_result")).upper() == "YES" else 0.0
            payout = y if side == "YES" else 1.0 - y
            grouped.setdefault(reason, []).append(payout - price - estimate_kalshi_fee_per_contract(price, rounded=False))
        for reason, values in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)[:3]:
            if len(values) < 8:
                continue
            mean = sum(values) / len(values)
            protective = mean <= 0.0
            insight = _insert_insight(
                kind="veto_selectivity",
                key=f"{reason}:{protective}:{evidence_cutoff}",
                title=f"'{reason}' appears {'protective' if protective else 'over-restrictive'}",
                summary=f"Its quoted one-contract counterfactual averaged {mean:+.3f} across {len(values)} official outcomes.",
                mechanism="This is quote-level opportunity analysis, not a fill claim; maker queue position and market impact remain unknown.",
                action="Keep the veto unchanged while Cerebro scores the same hypothesis prospectively.",
                confidence=min(0.80, 0.45 + len(values) / 100.0),
                evidence={"reason": reason, "n": len(values), "mean_quoted_counterfactual": mean, "fill_assumed": False},
                metric_spec={"metric": "veto_counterfactual", "reason": reason, "protective": protective},
                falsification_rule="Falsified if the next 20 resolved opportunities reverse the sign of the quoted counterfactual.",
                cutoff_prediction_id=cutoff,
                horizon_count=20,
                db_path=db_path,
            )
            if insight:
                created.append(insight)
    return {"created": created, "created_count": len(created), "scoring": scoring}


def list_insights(
    *,
    status: str = "",
    limit: int = 50,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM cerebro_insights WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status.upper(), max(1, int(limit))),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cerebro_insights ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["evidence"] = json.loads(payload.pop("evidence_json") or "{}")
        payload["metric_spec"] = json.loads(payload.pop("metric_spec_json") or "{}")
        result.append(payload)
    return result


def _default_experiment_spec(insight: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    insight_type = str(insight.get("insight_type") or "")
    evidence = _json_dict(insight.get("evidence_json"))
    base_guardrails = {
        "shadow_only": True,
        "official_outcomes_only": True,
        "requires_human_promotion": True,
        "source_insight_id": str(insight.get("insight_id") or ""),
        "minimum_horizon_count": int(insight.get("horizon_count") or 20),
    }
    if insight_type == "challenger_improvement":
        return (
            {
                "proposal_type": "promote_rbi_artifact",
                "artifact_id": str(evidence.get("artifact_id") or ""),
            },
            {
                **base_guardrails,
                "approval_path": "cockpit_pending_approval",
                "prospective_confirmation_required": True,
            },
        )
    if insight_type == "model_dominance":
        preferred = "gfs" if float(evidence.get("gfs_brier") or 0.0) <= float(evidence.get("ecmwf_brier") or 0.0) else "ecmwf"
        return (
            {
                "proposal_type": "segment_weight_review",
                "mode": str(evidence.get("mode") or ""),
                "preferred_model": preferred,
            },
            {
                **base_guardrails,
                "minimum_segment_advantage": 0.01,
                "change_requires_code_review": True,
            },
        )
    if insight_type == "veto_selectivity":
        return (
            {
                "proposal_type": "veto_policy_review",
                "reason": str(evidence.get("reason") or ""),
                "quoted_counterfactual_mean": float(evidence.get("mean_quoted_counterfactual") or 0.0),
            },
            {
                **base_guardrails,
                "quoted_counterfactual_only": True,
                "fill_claims_forbidden": True,
            },
        )
    return (
        {
            "proposal_type": "manual_review",
            "insight_type": insight_type,
        },
        base_guardrails,
    )


def create_experiment(
    insight_id: str,
    *,
    change_spec: dict[str, Any],
    guardrails: dict[str, Any],
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    init_intelligence_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    experiment_id = f"ce-{uuid.uuid4().hex[:12]}"
    with connect(db_path) as conn:
        insight = conn.execute(
            "SELECT * FROM cerebro_insights WHERE insight_id=?",
            (insight_id,),
        ).fetchone()
        if not insight:
            raise ValueError("Unknown Cerebro insight")
        conn.execute(
            """INSERT INTO cerebro_experiments
               (experiment_id, insight_id, created_at, status, hypothesis,
                change_spec_json, guardrails_json)
               VALUES (?, ?, ?, 'PROPOSED', ?, ?, ?)""",
            (
                experiment_id,
                insight_id,
                now,
                str(insight["summary"]),
                json.dumps(change_spec, sort_keys=True),
                json.dumps(guardrails, sort_keys=True),
            ),
        )
        conn.commit()
    return {"experiment_id": experiment_id, "status": "PROPOSED", "insight_id": insight_id}


def create_experiment_from_insight(
    insight_id: str,
    *,
    approved_by: str = "",
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cerebro_insights WHERE insight_id=?",
            (insight_id,),
        ).fetchone()
    if not row:
        raise ValueError("Unknown Cerebro insight")
    insight = dict(row)
    change_spec, guardrails = _default_experiment_spec(insight)
    with connect(db_path) as conn:
        existing = conn.execute(
            """SELECT * FROM cerebro_experiments
               WHERE insight_id=? AND change_spec_json=?
               ORDER BY created_at DESC LIMIT 1""",
            (insight_id, json.dumps(change_spec, sort_keys=True)),
        ).fetchone()
    if existing:
        payload = dict(existing)
        if approved_by and str(payload.get("status") or "") == "PROPOSED":
            payload.update(
                approve_experiment(
                    str(payload["experiment_id"]),
                    approved_by=approved_by,
                    db_path=db_path,
                )
            )
        payload["change_spec"] = _json_dict(payload.pop("change_spec_json", "{}"))
        payload["guardrails"] = _json_dict(payload.pop("guardrails_json", "{}"))
        payload["result"] = _json_dict(payload.pop("result_json", "{}"))
        return payload
    result = create_experiment(
        insight_id,
        change_spec=change_spec,
        guardrails=guardrails,
        db_path=db_path,
    )
    if approved_by:
        approved = approve_experiment(
            str(result["experiment_id"]),
            approved_by=approved_by,
            db_path=db_path,
        )
        result.update(approved)
    result["change_spec"] = change_spec
    result["guardrails"] = guardrails
    return result


def approve_experiment(
    experiment_id: str,
    *,
    approved_by: str,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM cerebro_experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        if not row or str(row["status"]) != "PROPOSED":
            raise ValueError("Experiment is not awaiting approval")
        conn.execute(
            """UPDATE cerebro_experiments
               SET status='APPROVED_FOR_SHADOW', approved_at=?, approved_by=?
               WHERE experiment_id=?""",
            (now, approved_by, experiment_id),
        )
        conn.commit()
    return {"experiment_id": experiment_id, "status": "APPROVED_FOR_SHADOW"}


def list_experiments(
    *,
    status: str = "",
    limit: int = 25,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    init_intelligence_db(db_path)
    query = "SELECT * FROM cerebro_experiments"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status.upper())
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    experiments: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["change_spec"] = _json_dict(payload.pop("change_spec_json", "{}"))
        payload["guardrails"] = _json_dict(payload.pop("guardrails_json", "{}"))
        payload["result"] = _json_dict(payload.pop("result_json", "{}"))
        experiments.append(payload)
    return experiments


def list_runs(
    *,
    limit: int = 12,
    status: str = "",
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    init_intelligence_db(db_path)
    query = "SELECT * FROM intelligence_runs"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status.upper())
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["summary"] = _json_dict(payload.pop("summary_json", "{}"))
        runs.append(payload)
    return runs


def get_cerebro_status(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        counts = {
            str(row["status"]): int(row["n"])
            for row in conn.execute("SELECT status, COUNT(*) n FROM cerebro_insights GROUP BY status")
        }
        prediction_count = int(conn.execute("SELECT COUNT(*) FROM intelligence_predictions").fetchone()[0] or 0)
        official_count = int(conn.execute("SELECT COUNT(*) FROM intelligence_outcomes WHERE current=1 AND official=1").fetchone()[0] or 0)
        experiment_count = int(conn.execute("SELECT COUNT(*) FROM cerebro_experiments").fetchone()[0] or 0)
        approved_experiment_count = int(
            conn.execute("SELECT COUNT(*) FROM cerebro_experiments WHERE status='APPROVED_FOR_SHADOW'").fetchone()[0] or 0
        )
    return {
        "prediction_count": prediction_count,
        "official_outcome_count": official_count,
        "insight_counts": counts,
        "experiment_count": experiment_count,
        "approved_experiment_count": approved_experiment_count,
        "latest_insights": list_insights(limit=8, db_path=db_path),
        "latest_experiments": list_experiments(limit=8, db_path=db_path),
        "latest_runs": list_runs(limit=6, db_path=db_path),
    }
