"""Deterministic RBI 2.0 champion-challenger weather model optimization."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH, TRADE_DATA_START_DATE
from intelligence.schema import connect, init_intelligence_db

MIN_GLOBAL_SAMPLES = 24
MIN_SEGMENT_SAMPLES = 12
MIN_HOLDOUT_SAMPLES = 6
MIN_IMPROVEMENT = 0.002
MAX_SEGMENT_REGRESSION = 0.01
WEIGHT_FLOOR = 0.25
WEIGHT_CEILING = 0.75


def _clip(value: float) -> float:
    return max(0.01, min(0.99, float(value)))


def _brier(rows: list[dict[str, Any]], weights: dict[str, dict[str, float]]) -> float | None:
    if not rows:
        return None
    total = 0.0
    for row in rows:
        segment = str(row.get("weather_mode") or "GLOBAL")
        blend = weights.get(segment) or weights.get("GLOBAL") or {"gfs": 0.60, "ecmwf": 0.40}
        q = _clip(float(blend["gfs"]) * float(row["q_gfs"]) + float(blend["ecmwf"]) * float(row["q_ecmwf"]))
        total += (q - float(row["outcome_yes"])) ** 2
    return total / len(rows)


def _fit_weight(rows: list[dict[str, Any]], parent_weight: float = 0.60) -> float:
    if not rows:
        return parent_weight
    best_weight = parent_weight
    best_loss = float("inf")
    newest = max(datetime.fromisoformat(str(row["settled_at"]).replace("Z", "+00:00")) for row in rows)
    for step in range(51):
        weight = WEIGHT_FLOOR + step * ((WEIGHT_CEILING - WEIGHT_FLOOR) / 50.0)
        loss = 0.0
        weight_sum = 0.0
        for row in rows:
            settled = datetime.fromisoformat(str(row["settled_at"]).replace("Z", "+00:00"))
            age_days = max(0.0, (newest - settled).total_seconds() / 86400.0)
            recency = 0.5 ** (age_days / 14.0)
            q = _clip(weight * float(row["q_gfs"]) + (1.0 - weight) * float(row["q_ecmwf"]))
            loss += recency * ((q - float(row["outcome_yes"])) ** 2)
            weight_sum += recency
        objective = (loss / max(weight_sum, 1e-9)) + 0.02 * ((weight - parent_weight) ** 2)
        if objective < best_loss:
            best_loss = objective
            best_weight = weight
    return round(best_weight, 4)


def _load_samples(db_path: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """WITH ranked AS (
                 SELECT p.*,
                        ROW_NUMBER() OVER (PARTITION BY p.ticker ORDER BY p.evaluated_at DESC, p.id DESC) rn
                 FROM intelligence_predictions p
                 WHERE p.evaluated_at >= ?
                   AND p.q_gfs IS NOT NULL
                   AND p.q_ecmwf IS NOT NULL
               )
               SELECT r.id, r.ticker, r.weather_mode, r.q_gfs, r.q_ecmwf,
                      r.q_baseline, r.q_champion, r.evaluated_at,
                      o.market_result, o.settled_at
               FROM ranked r
               JOIN intelligence_outcomes o
                 ON o.ticker=r.ticker AND o.current=1 AND o.official=1
               WHERE r.rn=1
                 AND (o.settled_at IS NULL OR r.evaluated_at <= o.settled_at)
               ORDER BY COALESCE(o.settled_at, o.recorded_at), r.ticker""",
            (TRADE_DATA_START_DATE,),
        ).fetchall()
    return [
        {
            **dict(row),
            "outcome_yes": 1.0 if str(row["market_result"]).upper() == "YES" else 0.0,
            "settled_at": str(row["settled_at"] or row["evaluated_at"]),
        }
        for row in rows
    ]


def get_champion_artifact(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM intelligence_model_artifacts WHERE status='champion' LIMIT 1"
        ).fetchone()
    if not row:
        return {}
    payload = dict(row)
    payload["weights"] = json.loads(payload.get("weights_json") or "{}")
    payload["metrics"] = json.loads(payload.get("metrics_json") or "{}")
    payload["validation"] = json.loads(payload.get("validation_json") or "{}")
    return payload


def get_active_model_weights(
    mode: str,
    *,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    artifact = get_champion_artifact(db_path=db_path)
    weights = artifact.get("weights") or {}
    selected = weights.get(str(mode or "").upper()) or weights.get("GLOBAL") or {"gfs": 0.60, "ecmwf": 0.40}
    return {
        "gfs": float(selected.get("gfs", 0.60)),
        "ecmwf": float(selected.get("ecmwf", 0.40)),
        "artifact_id": str(artifact.get("artifact_id") or "rbi2-baseline-60-40"),
    }


def train_challenger(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    samples = _load_samples(db_path)
    if len(samples) < MIN_GLOBAL_SAMPLES:
        return {"status": "insufficient_evidence", "sample_size": len(samples), "required": MIN_GLOBAL_SAMPLES}

    split = max(MIN_GLOBAL_SAMPLES - MIN_HOLDOUT_SAMPLES, int(len(samples) * 0.75))
    split = min(split, len(samples) - MIN_HOLDOUT_SAMPLES)
    train = samples[:split]
    holdout = samples[split:]
    champion = get_champion_artifact(db_path=db_path)
    champion_weights = champion.get("weights") or {"GLOBAL": {"gfs": 0.60, "ecmwf": 0.40}}

    weights: dict[str, dict[str, float]] = {}
    segments = ["GLOBAL"] + sorted({str(row.get("weather_mode") or "") for row in train if row.get("weather_mode")})
    for segment in segments:
        segment_rows = train if segment == "GLOBAL" else [row for row in train if str(row.get("weather_mode")) == segment]
        parent = champion_weights.get(segment) or champion_weights.get("GLOBAL") or {"gfs": 0.60}
        if segment != "GLOBAL" and len(segment_rows) < MIN_SEGMENT_SAMPLES:
            continue
        gfs_weight = _fit_weight(segment_rows, float(parent.get("gfs", 0.60)))
        weights[segment] = {"gfs": gfs_weight, "ecmwf": round(1.0 - gfs_weight, 4)}

    train_brier = _brier(train, weights)
    holdout_brier = _brier(holdout, weights)
    champion_holdout = _brier(holdout, champion_weights)
    improvement = (champion_holdout - holdout_brier) if champion_holdout is not None and holdout_brier is not None else 0.0
    segment_metrics: dict[str, dict[str, Any]] = {}
    worst_regression = 0.0
    for segment in sorted({str(row.get("weather_mode") or "") for row in holdout if row.get("weather_mode")}):
        segment_holdout = [row for row in holdout if str(row.get("weather_mode")) == segment]
        if len(segment_holdout) < 2:
            continue
        candidate_bs = _brier(segment_holdout, weights)
        champion_bs = _brier(segment_holdout, champion_weights)
        regression = (candidate_bs - champion_bs) if candidate_bs is not None and champion_bs is not None else 0.0
        worst_regression = max(worst_regression, regression)
        segment_metrics[segment] = {
            "n": len(segment_holdout),
            "challenger_brier": candidate_bs,
            "champion_brier": champion_bs,
            "delta": -regression,
        }

    passed = bool(
        len(holdout) >= MIN_HOLDOUT_SAMPLES
        and improvement >= MIN_IMPROVEMENT
        and worst_regression <= MAX_SEGMENT_REGRESSION
    )
    cutoff = str(samples[-1]["settled_at"])
    artifact_seed = json.dumps({"cutoff": cutoff, "weights": weights, "n": len(samples)}, sort_keys=True)
    artifact_id = f"rbi2-{hashlib.sha256(artifact_seed.encode()).hexdigest()[:12]}"
    validation = {
        "passed": passed,
        "minimum_improvement": MIN_IMPROVEMENT,
        "max_segment_regression": MAX_SEGMENT_REGRESSION,
        "worst_segment_regression": worst_regression,
        "chronological_holdout": True,
        "independent_unit": "market_ticker",
        "official_outcomes_only": True,
    }
    metrics = {"segments": segment_metrics, "train_size": len(train), "holdout_size": len(holdout)}
    try:
        from runtime.build_info import get_build_info
        code_sha = str(get_build_info().get("sha") or "")
    except Exception:
        code_sha = ""
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO intelligence_model_artifacts
               (artifact_id, created_at, status, code_sha, parent_artifact_id,
                data_cutoff, sample_size, holdout_size, train_brier,
                holdout_brier, champion_holdout_brier, expected_improvement,
                weights_json, metrics_json, validation_json)
               VALUES (?, ?, 'challenger', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(artifact_id) DO UPDATE SET
                 metrics_json=excluded.metrics_json,
                 validation_json=excluded.validation_json""",
            (
                artifact_id,
                datetime.now(timezone.utc).isoformat(),
                code_sha,
                champion.get("artifact_id"),
                cutoff,
                len(samples),
                len(holdout),
                train_brier,
                holdout_brier,
                champion_holdout,
                improvement,
                json.dumps(weights, sort_keys=True),
                json.dumps(metrics, sort_keys=True),
                json.dumps(validation, sort_keys=True),
            ),
        )
        conn.commit()
    return {
        "status": "challenger_ready" if passed else "challenger_rejected_by_validation",
        "artifact_id": artifact_id,
        "data_cutoff": cutoff,
        "sample_size": len(samples),
        "holdout_size": len(holdout),
        "expected_improvement": improvement,
        "validation": validation,
        "weights": weights,
    }


def promote_challenger(
    artifact_id: str,
    *,
    promoted_by: str,
    reason: str,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    init_intelligence_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM intelligence_model_artifacts WHERE artifact_id=? AND status='challenger'",
            (artifact_id,),
        ).fetchone()
        if not row:
            raise ValueError("Artifact is not an available challenger")
        validation = json.loads(row["validation_json"] or "{}")
        if not bool(validation.get("passed")):
            raise ValueError("Challenger did not pass deterministic validation")
        conn.execute("UPDATE intelligence_model_artifacts SET status='retired' WHERE status='champion'")
        conn.execute(
            """UPDATE intelligence_model_artifacts
               SET status='champion', promoted_at=?, promoted_by=?, promotion_reason=?
               WHERE artifact_id=?""",
            (now, promoted_by, reason, artifact_id),
        )
        conn.commit()
    return {"artifact_id": artifact_id, "status": "champion", "promoted_at": now}


def get_rbi2_status(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    champion = get_champion_artifact(db_path=db_path)
    with connect(db_path) as conn:
        challenger = conn.execute(
            "SELECT * FROM intelligence_model_artifacts WHERE status='challenger' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        sample_count = conn.execute(
            """SELECT COUNT(DISTINCT p.ticker)
               FROM intelligence_predictions p
               JOIN intelligence_outcomes o ON o.ticker=p.ticker AND o.current=1 AND o.official=1
               WHERE p.q_gfs IS NOT NULL AND p.q_ecmwf IS NOT NULL"""
        ).fetchone()[0]
    challenger_payload = dict(challenger) if challenger else {}
    if challenger_payload:
        challenger_payload["weights"] = json.loads(challenger_payload.get("weights_json") or "{}")
        challenger_payload["validation"] = json.loads(challenger_payload.get("validation_json") or "{}")
    return {
        "champion": champion,
        "challenger": challenger_payload,
        "official_sample_count": int(sample_count or 0),
        "promotion_mode": "human_approved",
    }
