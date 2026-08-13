"""SQLite schema for point-in-time evidence, model artifacts, and insights."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH


DDL = """
CREATE TABLE IF NOT EXISTS intelligence_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_key TEXT NOT NULL UNIQUE,
    scan_id TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_key TEXT NOT NULL,
    contract_name TEXT,
    weather_mode TEXT,
    city_key TEXT,
    strike REAL,
    comparator TEXT,
    lower_bound REAL,
    upper_bound REAL,
    threshold REAL,
    market_close_at TEXT,
    chosen_side TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT,
    q_gfs REAL,
    q_ecmwf REAL,
    q_hrrr REAL,
    q_baseline REAL,
    q_champion REAL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    quote_at TEXT,
    provider_at TEXT,
    provider_payload_hash TEXT,
    rule_hash TEXT,
    code_sha TEXT,
    config_hash TEXT,
    artifact_id TEXT,
    features_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intel_predictions_ticker
    ON intelligence_predictions(ticker, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_predictions_mode
    ON intelligence_predictions(weather_mode, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS intelligence_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    revision INTEGER NOT NULL,
    market_result TEXT NOT NULL CHECK(market_result IN ('YES', 'NO')),
    market_status TEXT NOT NULL,
    official INTEGER NOT NULL DEFAULT 0,
    observed_value REAL,
    source TEXT NOT NULL,
    source_payload_hash TEXT,
    determined_at TEXT,
    settled_at TEXT,
    recorded_at TEXT NOT NULL,
    current INTEGER NOT NULL DEFAULT 1,
    UNIQUE(ticker, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_outcome_current
    ON intelligence_outcomes(ticker) WHERE current=1;

CREATE TABLE IF NOT EXISTS intelligence_model_artifacts (
    artifact_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    code_sha TEXT,
    parent_artifact_id TEXT,
    data_cutoff TEXT,
    sample_size INTEGER NOT NULL DEFAULT 0,
    holdout_size INTEGER NOT NULL DEFAULT 0,
    train_brier REAL,
    holdout_brier REAL,
    champion_holdout_brier REAL,
    expected_improvement REAL,
    weights_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    validation_json TEXT NOT NULL DEFAULT '{}',
    promoted_at TEXT,
    promoted_by TEXT,
    promotion_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_one_champion
    ON intelligence_model_artifacts(status) WHERE status='champion';

CREATE TABLE IF NOT EXISTS cerebro_insights (
    insight_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    mechanism TEXT,
    action_proposed TEXT,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    metric_spec_json TEXT NOT NULL,
    cutoff_prediction_id INTEGER NOT NULL DEFAULT 0,
    horizon_count INTEGER NOT NULL DEFAULT 20,
    scored_count INTEGER NOT NULL DEFAULT 0,
    score_value REAL,
    falsification_rule TEXT NOT NULL,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cerebro_insights_status
    ON cerebro_insights(status, created_at DESC);

CREATE TABLE IF NOT EXISTS cerebro_experiments (
    experiment_id TEXT PRIMARY KEY,
    insight_id TEXT NOT NULL REFERENCES cerebro_insights(insight_id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    change_spec_json TEXT NOT NULL,
    guardrails_json TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS intelligence_runs (
    run_id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    data_cutoff TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_intelligence_db(db_path: str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(DDL)
        baseline = conn.execute(
            "SELECT 1 FROM intelligence_model_artifacts WHERE status='champion'"
        ).fetchone()
        if baseline is None:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO intelligence_model_artifacts
                   (artifact_id, created_at, status, code_sha, data_cutoff,
                    sample_size, holdout_size, weights_json, metrics_json,
                    validation_json, promoted_at, promoted_by, promotion_reason)
                   VALUES (?, ?, 'champion', '', '', 0, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    "rbi2-baseline-60-40",
                    now,
                    json.dumps({"GLOBAL": {"gfs": 0.60, "ecmwf": 0.40}}),
                    json.dumps({"kind": "static_baseline"}),
                    json.dumps({"passed": True, "reason": "safe_bootstrap"}),
                    now,
                    "system",
                    "Safe baseline installed during schema initialization.",
                ),
            )
        conn.commit()
