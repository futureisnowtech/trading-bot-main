"""Deterministic RBI 2.0 champion-challenger weather model optimization."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from config import (
    DB_PATH,
    RBI_LEARNING_EPOCH,
    RBI_MIN_DAYS,
    RBI_MIN_NEW_CLEAN_TRADES,
    TRADE_DATA_START_DATE,
)
from intelligence.schema import connect, init_intelligence_db

MIN_GLOBAL_SAMPLES = 24
MIN_SEGMENT_SAMPLES = 12
MIN_HOLDOUT_SAMPLES = 6
MIN_IMPROVEMENT = 0.002
MAX_SEGMENT_REGRESSION = 0.01
WEIGHT_FLOOR = 0.25
WEIGHT_CEILING = 0.75
BASELINE_ARTIFACT_ID = "rbi2-baseline-60-40"
BASELINE_WEIGHTS = {"GLOBAL": {"gfs": 0.60, "ecmwf": 0.40}}


def _clip(value: float) -> float:
    return max(0.01, min(0.99, float(value)))


def _utc_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _production_probability(
    row: dict[str, Any],
    blend: dict[str, float],
) -> float:
    """Replay the exact probability transform used by live pricing.

    ``q_hrrr`` is only usable with the point-in-time lead time because the live
    HRRR splice weight is a function of hours to resolution.  `_load_samples`
    rejects legacy rows that recorded HRRR without that required input.
    """
    from forecast.pricing_engine import log_odds_blend

    return _clip(
        log_odds_blend(
            _float_or_none(row.get("q_gfs")),
            _float_or_none(row.get("q_ecmwf")),
            _float_or_none(row.get("q_hrrr")),
            {
                "gfs": float(blend.get("gfs", 0.60)),
                "ecmwf": float(blend.get("ecmwf", 0.40)),
            },
            float(row.get("hours_to_resolution") or 0.0),
        )
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _group_by_event(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_key = str(row.get("event_key") or row.get("ticker") or "")
        groups.setdefault(event_key, []).append(row)
    return groups


def _brier(rows: list[dict[str, Any]], weights: dict[str, dict[str, float]]) -> float | None:
    if not rows:
        return None
    event_losses: list[float] = []
    for event_rows in _group_by_event(rows).values():
        market_losses: list[float] = []
        for row in event_rows:
            segment = str(row.get("weather_mode") or "GLOBAL")
            blend = (
                weights.get(segment)
                or weights.get("GLOBAL")
                or {"gfs": 0.60, "ecmwf": 0.40}
            )
            q = _production_probability(row, blend)
            market_losses.append((q - float(row["outcome_yes"])) ** 2)
        event_losses.append(sum(market_losses) / len(market_losses))
    return sum(event_losses) / len(event_losses)


def _fit_weight(rows: list[dict[str, Any]], parent_weight: float = 0.60) -> float:
    if not rows:
        return parent_weight
    best_weight = parent_weight
    best_loss = float("inf")
    event_groups = _group_by_event(rows)
    newest = max(
        _utc_datetime(row["settled_at"])
        for event_rows in event_groups.values()
        for row in event_rows
    )
    for step in range(51):
        weight = WEIGHT_FLOOR + step * ((WEIGHT_CEILING - WEIGHT_FLOOR) / 50.0)
        loss = 0.0
        weight_sum = 0.0
        for event_rows in event_groups.values():
            settled = max(_utc_datetime(row["settled_at"]) for row in event_rows)
            age_days = max(0.0, (newest - settled).total_seconds() / 86400.0)
            recency = 0.5 ** (age_days / 14.0)
            market_losses = [
                (
                    _production_probability(
                        row,
                        {"gfs": weight, "ecmwf": 1.0 - weight},
                    )
                    - float(row["outcome_yes"])
                )
                ** 2
                for row in event_rows
            ]
            loss += recency * (
                sum(market_losses) / max(1, len(market_losses))
            )
            weight_sum += recency
        objective = (loss / max(weight_sum, 1e-9)) + 0.02 * ((weight - parent_weight) ** 2)
        if objective < best_loss:
            best_loss = objective
            best_weight = weight
    return round(best_weight, 4)


def _load_samples(db_path: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """WITH eligible AS (
                 SELECT p.*, o.market_result, o.settled_at,
                        COALESCE(o.settled_at, o.recorded_at) outcome_at
                 FROM intelligence_predictions p
                 JOIN intelligence_outcomes o
                   ON o.ticker=p.ticker AND o.current=1 AND o.official=1
                 WHERE p.evaluated_at >= ?
                   AND p.learning_epoch = ?
                   AND p.q_gfs IS NOT NULL
                   AND p.q_ecmwf IS NOT NULL
                   AND (o.settled_at IS NULL OR p.evaluated_at <= o.settled_at)
               ), ranked AS (
                 SELECT e.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY e.ticker
                            ORDER BY e.evaluated_at DESC, e.id DESC
                        ) rn
                 FROM eligible e
               )
               SELECT r.id, r.ticker, r.event_key, r.weather_mode,
                      r.q_gfs, r.q_ecmwf, r.q_hrrr,
                      r.q_baseline, r.q_champion, r.features_json,
                      r.evaluated_at, r.market_result, r.settled_at
               FROM ranked r
               WHERE r.rn=1
               ORDER BY r.outcome_at, r.event_key, r.ticker""",
            (TRADE_DATA_START_DATE, RBI_LEARNING_EPOCH),
        ).fetchall()
    samples: list[dict[str, Any]] = []
    for row in rows:
        sample = dict(row)
        try:
            features = json.loads(sample.get("features_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            features = {}
        hours_to_resolution = _float_or_none(features.get("hours_to_resolution"))
        # An HRRR probability without its original lead time cannot replay the
        # production splice and is therefore not clean learning evidence.
        if sample.get("q_hrrr") is not None and hours_to_resolution is None:
            continue
        sample.update(
            {
                "event_key": str(sample.get("event_key") or sample.get("ticker") or ""),
                "hours_to_resolution": hours_to_resolution or 0.0,
                "outcome_yes": (
                    1.0 if str(sample["market_result"]).upper() == "YES" else 0.0
                ),
                "settled_at": str(sample["settled_at"] or sample["evaluated_at"]),
            }
        )
        samples.append(sample)
    return samples


def _ordered_event_keys(samples: list[dict[str, Any]]) -> list[str]:
    event_times: dict[str, datetime] = {}
    for row in samples:
        event_key = str(row.get("event_key") or row.get("ticker") or "")
        timestamp = _utc_datetime(row.get("settled_at") or row.get("evaluated_at"))
        previous = event_times.get(event_key)
        if previous is None or timestamp > previous:
            event_times[event_key] = timestamp
    return [
        event_key
        for event_key, _ in sorted(event_times.items(), key=lambda item: (item[1], item[0]))
    ]


def _chronological_event_split(
    samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    """Split whole events so sibling strikes can never cross the boundary."""
    event_keys = _ordered_event_keys(samples)
    split = max(
        MIN_GLOBAL_SAMPLES - MIN_HOLDOUT_SAMPLES,
        int(len(event_keys) * 0.75),
    )
    split = min(split, len(event_keys) - MIN_HOLDOUT_SAMPLES)
    train_keys = event_keys[:split]
    holdout_keys = event_keys[split:]
    train_set = set(train_keys)
    holdout_set = set(holdout_keys)
    train = [row for row in samples if str(row.get("event_key")) in train_set]
    holdout = [row for row in samples if str(row.get("event_key")) in holdout_set]
    return train, holdout, train_keys, holdout_keys


def _learning_gate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = sorted(
        _utc_datetime(row["evaluated_at"])
        for row in samples
        if row.get("evaluated_at")
    )
    earliest = evaluated[0] if evaluated else None
    latest = evaluated[-1] if evaluated else None
    observed_days = (
        max(0.0, (latest - earliest).total_seconds() / 86400.0)
        if earliest is not None and latest is not None
        else 0.0
    )
    minimum_days = max(0.0, float(RBI_MIN_DAYS))
    required_events = max(MIN_GLOBAL_SAMPLES, int(RBI_MIN_NEW_CLEAN_TRADES))
    sample_count = len(samples)
    event_count = len(_ordered_event_keys(samples))
    return {
        "passed": bool(
            event_count >= required_events
            and observed_days >= minimum_days
        ),
        "learning_epoch": RBI_LEARNING_EPOCH,
        "minimum_days": minimum_days,
        "observed_days": observed_days,
        "required_samples": required_events,
        "required_independent_events": required_events,
        "sample_count": sample_count,
        "independent_event_count": event_count,
        "earliest_evaluated_at": earliest.isoformat() if earliest else "",
        "latest_evaluated_at": latest.isoformat() if latest else "",
    }


def _artifact_passes_current_learning_gate(artifact: dict[str, Any]) -> bool:
    if str(artifact.get("artifact_id") or "") == BASELINE_ARTIFACT_ID:
        return True
    validation = artifact.get("validation") or {}
    return bool(
        validation.get("learning_period_passed")
        and str(validation.get("learning_epoch") or "") == RBI_LEARNING_EPOCH
        and float(validation.get("observed_learning_days") or 0.0) >= max(0.0, float(RBI_MIN_DAYS))
        and int(validation.get("independent_event_count") or 0)
        >= max(MIN_GLOBAL_SAMPLES, int(RBI_MIN_NEW_CLEAN_TRADES))
    )


def _effective_champion(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact and _artifact_passes_current_learning_gate(artifact):
        return artifact
    return {
        "artifact_id": BASELINE_ARTIFACT_ID,
        "weights": BASELINE_WEIGHTS,
        "sample_size": 0,
        "validation": {
            "passed": True,
            "reason": "current_learning_epoch_not_yet_promoted",
            "learning_epoch": RBI_LEARNING_EPOCH,
        },
    }


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
    artifact = _effective_champion(get_champion_artifact(db_path=db_path))
    weights = artifact.get("weights") or {}
    selected = weights.get(str(mode or "").upper()) or weights.get("GLOBAL") or {"gfs": 0.60, "ecmwf": 0.40}
    return {
        "gfs": float(selected.get("gfs", 0.60)),
        "ecmwf": float(selected.get("ecmwf", 0.40)),
        "artifact_id": str(artifact.get("artifact_id") or BASELINE_ARTIFACT_ID),
    }


def train_challenger(db_path: str = DB_PATH) -> dict[str, Any]:
    init_intelligence_db(db_path)
    samples = _load_samples(db_path)
    learning_gate = _learning_gate(samples)
    if not learning_gate["passed"]:
        return {
            "status": "learning_period_active",
            "sample_size": len(samples),
            "required": learning_gate["required_samples"],
            "learning_gate": learning_gate,
        }

    train, holdout, train_event_keys, holdout_event_keys = _chronological_event_split(samples)
    champion = _effective_champion(get_champion_artifact(db_path=db_path))
    champion_weights = champion.get("weights") or BASELINE_WEIGHTS

    weights: dict[str, dict[str, float]] = {}
    segments = ["GLOBAL"] + sorted({str(row.get("weather_mode") or "") for row in train if row.get("weather_mode")})
    for segment in segments:
        segment_rows = train if segment == "GLOBAL" else [row for row in train if str(row.get("weather_mode")) == segment]
        parent = champion_weights.get(segment) or champion_weights.get("GLOBAL") or {"gfs": 0.60}
        if (
            segment != "GLOBAL"
            and len(_group_by_event(segment_rows)) < MIN_SEGMENT_SAMPLES
        ):
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
        segment_event_count = len(_group_by_event(segment_holdout))
        if segment_event_count < 2:
            continue
        candidate_bs = _brier(segment_holdout, weights)
        champion_bs = _brier(segment_holdout, champion_weights)
        regression = (candidate_bs - champion_bs) if candidate_bs is not None and champion_bs is not None else 0.0
        worst_regression = max(worst_regression, regression)
        segment_metrics[segment] = {
            "n": segment_event_count,
            "market_n": len(segment_holdout),
            "challenger_brier": candidate_bs,
            "champion_brier": champion_bs,
            "delta": -regression,
        }

    passed = bool(
        len(holdout_event_keys) >= MIN_HOLDOUT_SAMPLES
        and improvement >= MIN_IMPROVEMENT
        and worst_regression <= MAX_SEGMENT_REGRESSION
    )
    cutoff = max(_utc_datetime(row["settled_at"]) for row in samples).isoformat()
    artifact_seed = json.dumps(
        {
            "cutoff": cutoff,
            "weights": weights,
            "n_events": learning_gate["independent_event_count"],
            "evidence_hash": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "id": row.get("id"),
                            "ticker": row.get("ticker"),
                            "event_key": row.get("event_key"),
                            "q_gfs": row.get("q_gfs"),
                            "q_ecmwf": row.get("q_ecmwf"),
                            "q_hrrr": row.get("q_hrrr"),
                            "hours": row.get("hours_to_resolution"),
                            "outcome": row.get("outcome_yes"),
                        }
                        for row in samples
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "learning_epoch": RBI_LEARNING_EPOCH,
        },
        sort_keys=True,
    )
    artifact_id = f"rbi2-{hashlib.sha256(artifact_seed.encode()).hexdigest()[:12]}"
    validation = {
        "passed": passed,
        "minimum_improvement": MIN_IMPROVEMENT,
        "max_segment_regression": MAX_SEGMENT_REGRESSION,
        "worst_segment_regression": worst_regression,
        "chronological_holdout": True,
        "independent_unit": "event_key",
        "sibling_strike_leakage": False,
        "event_weighting": "equal",
        "probability_transform": "forecast.pricing_engine.log_odds_blend",
        "hrrr_lead_time_splice_replayed": True,
        "official_outcomes_only": True,
        "learning_period_passed": True,
        "learning_epoch": RBI_LEARNING_EPOCH,
        "minimum_learning_days": learning_gate["minimum_days"],
        "observed_learning_days": learning_gate["observed_days"],
        "minimum_clean_samples": learning_gate["required_samples"],
        "independent_event_count": learning_gate["independent_event_count"],
        "holdout_event_count": len(holdout_event_keys),
    }
    metrics = {
        "segments": segment_metrics,
        "train_market_count": len(train),
        "holdout_market_count": len(holdout),
        "train_event_count": len(train_event_keys),
        "holdout_event_count": len(holdout_event_keys),
        "train_event_keys": train_event_keys,
        "holdout_event_keys": holdout_event_keys,
        "learning_gate": learning_gate,
    }
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
                learning_gate["independent_event_count"],
                len(holdout_event_keys),
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
        "sample_size": learning_gate["independent_event_count"],
        "market_sample_size": len(samples),
        "holdout_size": len(holdout_event_keys),
        "expected_improvement": improvement,
        "validation": validation,
        "weights": weights,
        "learning_gate": learning_gate,
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
        if not bool(validation.get("learning_period_passed")):
            raise ValueError("Challenger did not complete the RBI learning period")
        if str(validation.get("learning_epoch") or "") != RBI_LEARNING_EPOCH:
            raise ValueError("Challenger belongs to a stale RBI learning epoch")
        if float(validation.get("observed_learning_days") or 0.0) < max(0.0, float(RBI_MIN_DAYS)):
            raise ValueError("Challenger has fewer than the required RBI learning days")
        if int(validation.get("independent_event_count") or 0) < max(
            MIN_GLOBAL_SAMPLES, int(RBI_MIN_NEW_CLEAN_TRADES)
        ):
            raise ValueError("Challenger has fewer than the required independent RBI events")
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
    stored_champion = get_champion_artifact(db_path=db_path)
    champion = _effective_champion(stored_champion)
    samples = _load_samples(db_path)
    learning_gate = _learning_gate(samples)
    with connect(db_path) as conn:
        challenger = conn.execute(
            "SELECT * FROM intelligence_model_artifacts WHERE status='challenger' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    challenger_payload = dict(challenger) if challenger else {}
    if challenger_payload:
        challenger_payload["weights"] = json.loads(challenger_payload.get("weights_json") or "{}")
        challenger_payload["validation"] = json.loads(challenger_payload.get("validation_json") or "{}")
    return {
        "champion": champion,
        "stored_champion": stored_champion,
        "challenger": challenger_payload,
        "official_sample_count": len(samples),
        "official_event_count": learning_gate["independent_event_count"],
        "learning_gate": learning_gate,
        "promotion_mode": "human_approved",
    }
