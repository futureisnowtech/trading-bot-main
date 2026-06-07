"""
learning/weather_rbi.py — Research, Backtest, Incubation Loop for Weather.

The weather learner only calibrates on labeled contract resolutions. It does
not infer truth from realized PnL, because early exits can be profitable while
the underlying event still resolves the other way.
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from config import DB_PATH
from forecast.db import get_system_cooldown_ts, init_forecast_db, set_system_cooldown_ts

logger = logging.getLogger(__name__)

BASE_GFS_WEIGHT = 0.60
BASE_ECMWF_WEIGHT = 0.40
RBI_CALIBRATION_LOOKBACK_DAYS = 7
RBI_MODEL_LOOKBACK_DAYS = 30
RBI_MODEL_HALF_LIFE_DAYS = 10.0
RBI_RUN_COOLDOWN_HOURS = 18
RBI_MIN_GLOBAL_SAMPLES = 50
RBI_MIN_MODE_SAMPLES = 25
RBI_MIN_MODEL_WEIGHT = 0.25
RBI_MAX_MODEL_WEIGHT = 0.75
RBI_CACHE_TTL_SECONDS = 300.0
RBI_PROCESS_NAME = "weather_rbi"

_MODEL_SKILL_CACHE = {"loaded_at": 0.0, "rows": {}}

_DDL_CALIBRATION = """
CREATE TABLE IF NOT EXISTS weather_calibration (
    ts TEXT PRIMARY KEY,
    brier_score REAL,
    win_rate REAL,
    ensemble_accuracy REAL,
    sample_size INTEGER,
    edge_decay REAL
)
"""

_DDL_MODEL_SKILL_STATE = """
CREATE TABLE IF NOT EXISTS weather_model_skill_state (
    segment TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    effective_weight REAL NOT NULL,
    gfs_brier REAL,
    ecmwf_brier REAL,
    gfs_weight REAL NOT NULL,
    ecmwf_weight REAL NOT NULL,
    shrinkage REAL NOT NULL,
    lookback_days INTEGER NOT NULL
)
"""

_DDL_SYSTEM_COOLDOWNS = """
CREATE TABLE IF NOT EXISTS system_cooldowns (
    process_name TEXT PRIMARY KEY,
    last_executed_ts INTEGER NOT NULL
)
"""


def _parse_utc(value) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clip_prob(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.01, min(0.99, float(value)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _age_decay_weight(resolved_at: datetime, now_utc: datetime) -> float:
    age_days = max(0.0, (now_utc - resolved_at).total_seconds() / 86400.0)
    return 0.5 ** (age_days / RBI_MODEL_HALF_LIFE_DAYS)


def _reset_model_skill_cache() -> None:
    _MODEL_SKILL_CACHE["loaded_at"] = 0.0
    _MODEL_SKILL_CACHE["rows"] = {}


def _latest_calibration_ts(conn: sqlite3.Connection) -> datetime | None:
    try:
        row = conn.execute(
            "SELECT ts FROM weather_calibration ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return _parse_utc(row[0] if not isinstance(row, sqlite3.Row) else row["ts"])


def _calculate_segment_weights(
    samples: list[dict],
    *,
    min_samples: int,
) -> dict:
    if not samples:
        return {
            "sample_size": 0,
            "effective_weight": 0.0,
            "gfs_brier": None,
            "ecmwf_brier": None,
            "gfs_weight": BASE_GFS_WEIGHT,
            "ecmwf_weight": BASE_ECMWF_WEIGHT,
            "shrinkage": 0.0,
        }

    weighted_total = sum(float(sample["decay_weight"]) for sample in samples)
    if weighted_total <= 0:
        weighted_total = float(len(samples))

    gfs_brier = sum(
        float(sample["decay_weight"]) * (float(sample["gfs_prob"]) - float(sample["outcome_yes"])) ** 2
        for sample in samples
    ) / weighted_total
    ecmwf_brier = sum(
        float(sample["decay_weight"]) * (float(sample["ecmwf_prob"]) - float(sample["outcome_yes"])) ** 2
        for sample in samples
    ) / weighted_total

    gfs_skill = 1.0 / max(0.02, gfs_brier)
    ecmwf_skill = 1.0 / max(0.02, ecmwf_brier)
    skill_total = gfs_skill + ecmwf_skill
    raw_gfs_weight = gfs_skill / skill_total if skill_total > 0 else BASE_GFS_WEIGHT

    shrinkage = min(1.0, len(samples) / float(max(1, min_samples)))
    gfs_weight = (BASE_GFS_WEIGHT * (1.0 - shrinkage)) + (raw_gfs_weight * shrinkage)
    gfs_weight = _clamp(gfs_weight, RBI_MIN_MODEL_WEIGHT, RBI_MAX_MODEL_WEIGHT)
    ecmwf_weight = 1.0 - gfs_weight

    return {
        "sample_size": len(samples),
        "effective_weight": round(weighted_total, 4),
        "gfs_brier": round(gfs_brier, 6),
        "ecmwf_brier": round(ecmwf_brier, 6),
        "gfs_weight": round(gfs_weight, 6),
        "ecmwf_weight": round(ecmwf_weight, 6),
        "shrinkage": round(shrinkage, 6),
    }


def _load_model_skill_rows(db_path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            fetched = conn.execute(
                """
                SELECT segment, ts, sample_size, effective_weight, gfs_brier, ecmwf_brier,
                       gfs_weight, ecmwf_weight, shrinkage, lookback_days
                FROM weather_model_skill_state
                """
            ).fetchall()
    except Exception:
        return rows

    for row in fetched:
        segment = str(row["segment"] or "").upper()
        if not segment:
            continue
        rows[segment] = dict(row)
    return rows


def get_weather_model_blend(
    mode: str | None = None,
    *,
    db_path: str | None = None,
    refresh: bool = False,
) -> dict:
    selected_db = db_path or DB_PATH
    now_ts = time.time()
    if refresh or (now_ts - float(_MODEL_SKILL_CACHE["loaded_at"] or 0.0)) > RBI_CACHE_TTL_SECONDS:
        _MODEL_SKILL_CACHE["rows"] = _load_model_skill_rows(selected_db)
        _MODEL_SKILL_CACHE["loaded_at"] = now_ts

    rows = _MODEL_SKILL_CACHE["rows"]
    segment = str(mode or "").upper()
    row = rows.get(segment) if segment else None
    if row is None:
        row = rows.get("GLOBAL")

    if row is None:
        return {
            "segment": "STATIC",
            "sample_size": 0,
            "effective_weight": 0.0,
            "gfs_brier": None,
            "ecmwf_brier": None,
            "gfs_weight": BASE_GFS_WEIGHT,
            "ecmwf_weight": BASE_ECMWF_WEIGHT,
            "shrinkage": 0.0,
            "lookback_days": RBI_MODEL_LOOKBACK_DAYS,
        }

    return {
        "segment": str(row.get("segment") or "GLOBAL"),
        "sample_size": int(row.get("sample_size") or 0),
        "effective_weight": float(row.get("effective_weight") or 0.0),
        "gfs_brier": row.get("gfs_brier"),
        "ecmwf_brier": row.get("ecmwf_brier"),
        "gfs_weight": float(row.get("gfs_weight") or BASE_GFS_WEIGHT),
        "ecmwf_weight": float(row.get("ecmwf_weight") or BASE_ECMWF_WEIGHT),
        "shrinkage": float(row.get("shrinkage") or 0.0),
        "lookback_days": int(row.get("lookback_days") or RBI_MODEL_LOOKBACK_DAYS),
    }


def init_rbi_db() -> None:
    try:
        init_forecast_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(_DDL_CALIBRATION)
            conn.execute(_DDL_MODEL_SKILL_STATE)
            conn.execute(_DDL_SYSTEM_COOLDOWNS)
            conn.commit()
    except Exception as e:
        logger.error(f"[weather_rbi] DB Init error: {e}")


def run_weather_rbi(force: bool = False) -> None:
    """Execute the weather calibration loop using labeled contract outcomes only."""
    logger.info("[weather_rbi] Starting calibration cycle...")
    init_rbi_db()

    now_utc = datetime.now(timezone.utc)
    now_ts_int = int(now_utc.timestamp())
    calibration_cutoff = now_utc - timedelta(days=RBI_CALIBRATION_LOOKBACK_DAYS)
    model_cutoff = now_utc - timedelta(days=RBI_MODEL_LOOKBACK_DAYS)

    try:
        latest_run_ts = get_system_cooldown_ts(RBI_PROCESS_NAME, db_path=DB_PATH)
        if (
            not force
            and latest_run_ts is not None
            and (now_ts_int - latest_run_ts) < int(RBI_RUN_COOLDOWN_HOURS * 3600)
        ):
            latest_run = datetime.fromtimestamp(latest_run_ts, tz=timezone.utc)
            logger.info(
                "[weather_rbi] Skipping calibration; latest run at %s is inside the %sh cooldown.",
                latest_run.isoformat(),
                RBI_RUN_COOLDOWN_HOURS,
            )
            return

        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT
                    t.id,
                    t.symbol,
                    t.contract_side,
                    t.forecast_yes_prob,
                    t.model_prob_gfs,
                    t.model_prob_ecmwf,
                    t.weather_mode,
                    t.forecast_hours_to_resolution,
                    COALESCE(pnl.realized_pnl_usd, 0.0) AS pnl_usd,
                    r.resolved_side,
                    r.resolved_at
                FROM trades t
                JOIN forecast_contracts c
                  ON c.local_symbol = t.symbol
                JOIN forecast_resolutions r
                  ON r.contract_id = c.id
                LEFT JOIN (
                    SELECT
                        symbol,
                        contract_side,
                        SUM(pnl_usd) AS realized_pnl_usd
                    FROM trades
                    WHERE broker = 'kalshi'
                      AND action = 'SELL'
                    GROUP BY symbol, contract_side
                ) pnl
                  ON pnl.symbol = t.symbol
                 AND pnl.contract_side = t.contract_side
                WHERE t.broker = 'kalshi'
                  AND t.action = 'BUY'
                  AND t.contract_side IN ('YES', 'NO')
                  AND r.resolved_side IN ('YES', 'NO')
                ORDER BY r.resolved_at DESC, t.id DESC
                """
            ).fetchall()

            labeled = []
            model_samples_global: list[dict] = []
            model_samples_by_mode: dict[str, list[dict]] = {}
            seen_symbols: set[str] = set()

            for row in rows:
                symbol = str(row["symbol"] or "").strip()
                if not symbol or symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)

                resolved_at = _parse_utc(row["resolved_at"])
                if resolved_at is None or resolved_at < model_cutoff:
                    continue

                contract_side = str(row["contract_side"]).upper()
                resolved_side = str(row["resolved_side"]).upper()
                outcome_yes = 1.0 if resolved_side == "YES" else 0.0

                forecast_yes_prob = _clip_prob(row["forecast_yes_prob"])
                if forecast_yes_prob is not None and resolved_at >= calibration_cutoff:
                    chosen_outcome = 1.0 if contract_side == resolved_side else 0.0
                    labeled.append(
                        {
                            "forecast_yes_prob": forecast_yes_prob,
                            "outcome_yes": outcome_yes,
                            "chosen_outcome": chosen_outcome,
                            "pnl_usd": float(row["pnl_usd"] or 0.0),
                        }
                    )

                gfs_prob = _clip_prob(row["model_prob_gfs"])
                ecmwf_prob = _clip_prob(row["model_prob_ecmwf"])
                if gfs_prob is None or ecmwf_prob is None:
                    continue

                weather_mode = str(row["weather_mode"] or "").upper() or "UNKNOWN"
                sample = {
                    "symbol": symbol,
                    "weather_mode": weather_mode,
                    "outcome_yes": outcome_yes,
                    "gfs_prob": gfs_prob,
                    "ecmwf_prob": ecmwf_prob,
                    "decay_weight": _age_decay_weight(resolved_at, now_utc),
                }
                model_samples_global.append(sample)
                model_samples_by_mode.setdefault(weather_mode, []).append(sample)

            if not labeled and not model_samples_global:
                logger.info(
                    "[weather_rbi] No labeled resolved weather samples found in the active windows. "
                    "Skipping calibration."
                )
                set_system_cooldown_ts(RBI_PROCESS_NAME, now_ts_int, db_path=DB_PATH)
                return

            now_ts = datetime.now(timezone.utc).isoformat()

            if labeled:
                brier_sum = sum(
                    (sample["forecast_yes_prob"] - sample["outcome_yes"]) ** 2
                    for sample in labeled
                )
                wins = sum(int(sample["chosen_outcome"]) for sample in labeled)
                accuracy_sum = sum(
                    1.0 - abs(sample["forecast_yes_prob"] - sample["outcome_yes"])
                    for sample in labeled
                )
                pnl_sum = sum(sample["pnl_usd"] for sample in labeled)

                count = len(labeled)
                avg_brier = brier_sum / count
                win_rate = wins / count
                avg_accuracy = accuracy_sum / count
                edge_decay = pnl_sum / count

                conn.execute(
                    """
                    INSERT OR REPLACE INTO weather_calibration
                        (ts, brier_score, win_rate, ensemble_accuracy, sample_size, edge_decay)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now_ts, avg_brier, win_rate, avg_accuracy, count, edge_decay),
                )

                logger.info(
                    "[weather_rbi] Calibration complete. "
                    f"Brier={avg_brier:.4f} WR={win_rate:.2%} Accuracy={avg_accuracy:.2%} "
                    f"Samples={count}"
                )

            segment_payloads = {"GLOBAL": model_samples_global}
            segment_payloads.update(model_samples_by_mode)
            for segment, samples in segment_payloads.items():
                metrics = _calculate_segment_weights(
                    samples,
                    min_samples=(
                        RBI_MIN_GLOBAL_SAMPLES if segment == "GLOBAL" else RBI_MIN_MODE_SAMPLES
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO weather_model_skill_state
                        (segment, ts, sample_size, effective_weight, gfs_brier, ecmwf_brier,
                         gfs_weight, ecmwf_weight, shrinkage, lookback_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        segment,
                        now_ts,
                        metrics["sample_size"],
                        metrics["effective_weight"],
                        metrics["gfs_brier"],
                        metrics["ecmwf_brier"],
                        metrics["gfs_weight"],
                        metrics["ecmwf_weight"],
                        metrics["shrinkage"],
                        RBI_MODEL_LOOKBACK_DAYS,
                    ),
                )
            conn.commit()
            _reset_model_skill_cache()
            set_system_cooldown_ts(RBI_PROCESS_NAME, now_ts_int, db_path=DB_PATH)
            logger.info(
                "[weather_rbi] Model skill update complete. global_samples=%s segments=%s",
                len(model_samples_global),
                sorted(segment_payloads),
            )

    except Exception as e:
        logger.error(f"[weather_rbi] Cycle failed: {e}")


if __name__ == "__main__":
    run_weather_rbi()
