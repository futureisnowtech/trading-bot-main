"""
learning/pm_calibrator.py — Prediction market forecast calibrator (Lane 3).

Tracks forecast accuracy per LLM model and applies Platt scaling to correct
systematic over/under-confidence in probability estimates.

Storage: forecast_calibration table in logs/trades.db (created on first use).
Trigger: refit after every 30 resolved prediction market outcomes.

Exposes:
  calibrate_pm(raw_prob, model_name, evidence_quality, spread) → float
  record_pm_outcome(model_name, market_id, forecast_prob, actual_outcome) → None
  get_adaptive_weights() → dict[str, float]
  get_pm_calibration_stats() → dict

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/calibrator.py
(SQLite instead of JSON; our trade_logger; Lane 3 scoped).
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, MARKET_TIMEZONE, ENSEMBLE_CLAUDE_WEIGHT, ENSEMBLE_GPT_WEIGHT, ENSEMBLE_GEMINI_WEIGHT

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 30   # min resolved markets before Platt scaling activates

_INIT_SQL = [
    """CREATE TABLE IF NOT EXISTS pm_forecast_calibration (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        model_name  TEXT NOT NULL,
        market_id   TEXT NOT NULL,
        market_type TEXT DEFAULT 'UNKNOWN',
        forecast_prob   REAL NOT NULL,
        actual_outcome  REAL NOT NULL,
        brier_score     REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pm_calibration_params (
        model_name  TEXT PRIMARY KEY,
        slope       REAL DEFAULT 1.0,
        intercept   REAL DEFAULT 0.0,
        n_samples   INTEGER DEFAULT 0,
        brier_score REAL DEFAULT 1.0,
        updated_ts  TEXT NOT NULL
    )""",
]


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_schema_ready = False


def _init_once() -> None:
    global _schema_ready
    if _schema_ready:
        return
    conn = _conn()
    for sql in _INIT_SQL:
        try:
            conn.execute(sql)
        except Exception:
            pass
    conn.commit()
    conn.close()
    _schema_ready = True


# ── Platt calibrator ─────────────────────────────────────────────────────────

class _PlattCal:
    def __init__(self, model_name: str) -> None:
        self.model = model_name
        self.slope = 1.0
        self.intercept = 0.0
        self.n = 0
        self.brier = 1.0
        self._fitted = False
        self._load()

    def _load(self) -> None:
        _init_once()
        try:
            conn = _conn()
            row = conn.execute(
                "SELECT * FROM pm_calibration_params WHERE model_name=?", (self.model,)
            ).fetchone()
            conn.close()
            if row and row["n_samples"] >= _MIN_SAMPLES:
                self.slope = row["slope"]
                self.intercept = row["intercept"]
                self.n = row["n_samples"]
                self.brier = row["brier_score"]
                self._fitted = True
        except Exception:
            pass

    def fit(self) -> bool:
        _init_once()
        try:
            conn = _conn()
            rows = conn.execute(
                "SELECT forecast_prob, actual_outcome FROM pm_forecast_calibration WHERE model_name=?",
                (self.model,)
            ).fetchall()
            conn.close()

            if len(rows) < _MIN_SAMPLES:
                return False

            from sklearn.linear_model import LogisticRegression
            import numpy as np

            probs = [max(0.01, min(0.99, r["forecast_prob"])) for r in rows]
            logits = [math.log(p / (1 - p)) for p in probs]
            outcomes = [r["actual_outcome"] for r in rows]

            lr = LogisticRegression(solver="lbfgs", max_iter=1000)
            lr.fit(np.array(logits).reshape(-1, 1), np.array(outcomes))

            self.slope = float(lr.coef_[0][0])
            self.intercept = float(lr.intercept_[0])
            self.n = len(rows)
            cal = [self._apply(p) for p in probs]
            self.brier = sum((c - o) ** 2 for c, o in zip(cal, outcomes)) / len(outcomes)
            self._fitted = True

            ts = datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat()
            conn = _conn()
            conn.execute(
                "INSERT OR REPLACE INTO pm_calibration_params VALUES (?,?,?,?,?,?)",
                (self.model, self.slope, self.intercept, self.n, self.brier, ts)
            )
            conn.commit()
            conn.close()
            logger.info(f"[pm_calibrator] {self.model} fitted n={self.n} brier={self.brier:.4f}")
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.error(f"[pm_calibrator] fit {self.model}: {e}")
            return False

    def _apply(self, p: float) -> float:
        p = max(0.01, min(0.99, p))
        logit = math.log(p / (1 - p))
        return 1.0 / (1.0 + math.exp(-(self.slope * logit + self.intercept)))

    def calibrate(self, p: float) -> float:
        if self._fitted:
            return max(0.01, min(0.99, self._apply(p)))
        # Heuristic: 10% shrinkage toward 0.5
        logit = math.log(max(0.01, min(0.99, p)) / (1 - max(0.01, min(0.99, p))))
        return 1.0 / (1.0 + math.exp(-logit * 0.90))


_calibrators: dict[str, _PlattCal] = {}


def _get_cal(model: str) -> _PlattCal:
    if model not in _calibrators:
        _calibrators[model] = _PlattCal(model)
    return _calibrators[model]


# ── Public API ────────────────────────────────────────────────────────────────

def calibrate_pm(
    raw_prob: float,
    model_name: str = "claude",
    evidence_quality: float = 1.0,
    spread: float = 0.0,
    num_contradictions: int = 0,
) -> float:
    """Calibrate a raw LLM probability for a prediction market question."""
    p = _get_cal(model_name).calibrate(max(0.01, min(0.99, raw_prob)))

    if evidence_quality < 0.4:
        penalty = 0.15 * (1.0 - evidence_quality)
        p = p * (1 - penalty) + 0.5 * penalty

    if num_contradictions > 0:
        w = min(0.30, 0.10 * num_contradictions)
        p = p * (1 - w) + 0.5 * w

    if spread > 0.10:
        w = min(0.25, spread)
        p = p * (1 - w) + 0.5 * w

    return max(0.01, min(0.99, round(p, 4)))


def record_pm_outcome(
    model_name: str,
    market_id: str,
    forecast_prob: float,
    actual_outcome: float,
    market_type: str = "UNKNOWN",
) -> None:
    """Record a resolved prediction market outcome for calibration learning."""
    _init_once()
    brier = (forecast_prob - actual_outcome) ** 2
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO pm_forecast_calibration (ts,model_name,market_id,market_type,forecast_prob,actual_outcome,brier_score) VALUES (?,?,?,?,?,?,?)",
            (datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat(),
             model_name, market_id, market_type, forecast_prob, actual_outcome, brier)
        )
        count = conn.execute(
            "SELECT COUNT(*) as n FROM pm_forecast_calibration WHERE model_name=?",
            (model_name,)
        ).fetchone()["n"]
        conn.commit()
        conn.close()

        if count > 0 and count % _MIN_SAMPLES == 0:
            logger.info(f"[pm_calibrator] Refitting {model_name} ({count} samples)")
            _get_cal(model_name).fit()
    except Exception as e:
        logger.error(f"[pm_calibrator] record_pm_outcome: {e}")


def get_adaptive_weights() -> dict[str, float]:
    """Return per-model weights based on inverse Brier score."""
    defaults = {"claude": ENSEMBLE_CLAUDE_WEIGHT, "gpt-4o": ENSEMBLE_GPT_WEIGHT, "gemini": ENSEMBLE_GEMINI_WEIGHT}
    _init_once()
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT model_name, brier_score, n_samples FROM pm_calibration_params"
        ).fetchall()
        conn.close()
        valid = {r["model_name"]: r["brier_score"]
                 for r in rows if r["n_samples"] >= _MIN_SAMPLES}
        if not valid:
            return defaults
        raw = {m: 1.0 / max(0.001, b) for m, b in valid.items()}
        total = sum(raw.values())
        return {m: w / total for m, w in raw.items()}
    except Exception:
        return defaults


def get_pm_calibration_stats() -> dict:
    """Return calibration stats for MCP / dashboard."""
    _init_once()
    try:
        conn = _conn()
        params = [dict(r) for r in conn.execute(
            "SELECT * FROM pm_calibration_params ORDER BY brier_score ASC"
        ).fetchall()]
        total = conn.execute("SELECT COUNT(*) as n FROM pm_forecast_calibration").fetchone()["n"]
        conn.close()
        return {"models": params, "total_records": total}
    except Exception as e:
        return {"models": [], "total_records": 0, "error": str(e)}
