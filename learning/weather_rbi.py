"""
learning/weather_rbi.py — Research, Backtest, Incubation Loop for Weather.

The weather learner only calibrates on labeled contract resolutions. It does
not infer truth from realized PnL, because early exits can be profitable while
the underlying event still resolves the other way.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB_PATH
from forecast.db import init_forecast_db

logger = logging.getLogger(__name__)

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


def init_rbi_db() -> None:
    try:
        init_forecast_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(_DDL_CALIBRATION)
            conn.commit()
    except Exception as e:
        logger.error(f"[weather_rbi] DB Init error: {e}")


def run_weather_rbi() -> None:
    """Execute the weather calibration loop using labeled contract outcomes only."""
    logger.info("[weather_rbi] Starting calibration cycle...")
    init_rbi_db()

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT DISTINCT
                    t.symbol,
                    t.contract_side,
                    t.forecast_yes_prob,
                    t.pnl_usd,
                    r.resolved_side,
                    r.resolved_at
                FROM trades t
                JOIN forecast_contracts c
                  ON c.local_symbol = t.symbol
                JOIN forecast_resolutions r
                  ON r.contract_id = c.id
                WHERE t.broker = 'kalshi'
                  AND t.action = 'SELL'
                  AND t.contract_side IN ('YES', 'NO')
                  AND t.forecast_yes_prob IS NOT NULL
                  AND r.resolved_side IN ('YES', 'NO')
                ORDER BY r.resolved_at DESC
                """
            ).fetchall()

            labeled = []
            for row in rows:
                resolved_at = _parse_utc(row["resolved_at"])
                if resolved_at is None or resolved_at < cutoff:
                    continue

                contract_side = str(row["contract_side"]).upper()
                yes_prob = max(0.0, min(1.0, float(row["forecast_yes_prob"])))
                chosen_prob = yes_prob if contract_side == "YES" else (1.0 - yes_prob)
                outcome = 1.0 if str(row["resolved_side"]).upper() == contract_side else 0.0

                labeled.append(
                    {
                        "chosen_prob": chosen_prob,
                        "outcome": outcome,
                        "pnl_usd": float(row["pnl_usd"] or 0.0),
                    }
                )

            if not labeled:
                logger.info(
                    "[weather_rbi] No labeled resolved weather samples found in the last 7 days. "
                    "Skipping calibration."
                )
                return

            brier_sum = sum((sample["chosen_prob"] - sample["outcome"]) ** 2 for sample in labeled)
            wins = sum(int(sample["outcome"]) for sample in labeled)
            accuracy_sum = sum(1.0 - abs(sample["chosen_prob"] - sample["outcome"]) for sample in labeled)
            pnl_sum = sum(sample["pnl_usd"] for sample in labeled)

            count = len(labeled)
            avg_brier = brier_sum / count
            win_rate = wins / count
            avg_accuracy = accuracy_sum / count
            edge_decay = pnl_sum / count
            now_ts = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                INSERT OR REPLACE INTO weather_calibration
                    (ts, brier_score, win_rate, ensemble_accuracy, sample_size, edge_decay)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now_ts, avg_brier, win_rate, avg_accuracy, count, edge_decay),
            )
            conn.commit()

            logger.info(
                "[weather_rbi] Calibration complete. "
                f"Brier={avg_brier:.4f} WR={win_rate:.2%} Accuracy={avg_accuracy:.2%} "
                f"Samples={count}"
            )

    except Exception as e:
        logger.error(f"[weather_rbi] Cycle failed: {e}")


if __name__ == "__main__":
    run_weather_rbi()
