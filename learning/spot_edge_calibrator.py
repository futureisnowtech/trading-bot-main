"""
learning/spot_edge_calibrator.py — Self-calibrating spot edge conditions.

Derives per-symbol edge conditions from real closed spot trades.
Writes results to the spot_edge_conditions DB table.
Conditions are only derived when >= MIN_TRADES_PER_SYMBOL closed trades exist.
Once derived, spot_strategy.py reads them from DB instead of config.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRADES_PER_SYMBOL = 30  # minimum closed trades before any condition is derived
MIN_BUCKET_N = 8  # minimum trades in a bucket to consider a condition
MIN_PF_TO_GATE = 1.25  # baseline PF must clear this for a condition to activate
MIN_PF_IMPROVEMENT = 0.15  # condition bucket must improve PF by at least this fraction
MIN_CONFIDENCE = 0.50  # minimum confidence score (0-1) to write a condition


def _resolve_db_path() -> str:
    """Resolve DB path — respects logging_db.trade_logger.DB_PATH so tests can monkeypatch it."""
    try:
        import logging_db.trade_logger as _tl

        return str(_tl.DB_PATH)
    except Exception:
        return os.path.join(os.path.dirname(__file__), "../logs/trades.db")


# Fields the calibrator analyses per symbol
# Each entry: (field_name, field_type) where type is 'categorical' or 'numeric'
_CANDIDATE_FIELDS: list[tuple[str, str]] = [
    ("regime", "categorical"),
    ("setup_family", "categorical"),
    ("setup_score", "numeric"),
    ("vol_quality", "numeric"),
    ("structure", "numeric"),
    ("a5", "numeric"),
    ("mom_impulse", "numeric"),
]

_FIELD_REASON_MAP: dict[str, str] = {
    "regime": "edge_regime_mismatch",
    "setup_family": "edge_setup_family_mismatch",
    "setup_score": "edge_setup_score_too_low",
    "vol_quality": "edge_volatility_quality_too_low",
    "structure": "edge_structure_component_too_low",
    "a5": "edge_acceleration_too_low",
    "mom_impulse": "edge_momentum_impulse_too_low",
}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_closed_spot_trades(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    """Return closed real spot trades with entry-time lineage from ml_feature_snapshots."""
    rows = conn.execute(
        """
        SELECT
            t.ts,
            t.symbol,
            COALESCE(m.expected_net_pnl_after_fees, t.pnl_usd, 0) AS pnl_usd,
            COALESCE(NULLIF(m.spot_regime, ''), NULLIF(m.regime, ''), 'UNKNOWN') AS regime,
            COALESCE(NULLIF(m.setup_family, ''), json_extract(m.features_json, '$.setup_family'), '') AS setup_family,
            COALESCE(m.setup_score, json_extract(m.features_json, '$.setup_score'), 0) AS setup_score,
            COALESCE(json_extract(m.features_json, '$.volatility_quality'), 0) AS vol_quality,
            COALESCE(json_extract(m.features_json, '$.structure_component'), 0) AS structure,
            COALESCE(json_extract(m.features_json, '$.a5'), 0) AS a5,
            COALESCE(json_extract(m.features_json, '$.momentum_impulse'), 0) AS mom_impulse,
            COALESCE(m.route_type, '') AS route_type,
            COALESCE(m.candidate_id, 0) AS candidate_id,
            COALESCE(m.reconstructed, 0) AS reconstructed
        FROM ml_feature_snapshots m
        JOIN trades t ON t.id = m.trade_id
        WHERE t.strategy LIKE 'spot_%'
          AND t.action = 'SELL'
          AND t.paper = 0
          AND t.pnl_usd IS NOT NULL
          AND t.symbol = ?
        ORDER BY t.ts DESC
        """,
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


def _pf(wins_usd: list[float], losses_usd: list[float]) -> float:
    gross_win = sum(abs(x) for x in wins_usd)
    gross_loss = sum(abs(x) for x in losses_usd)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 1.0
    return round(gross_win / gross_loss, 4)


def _wr(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    return round(sum(1 for p in pnls if p > 0) / len(pnls), 4)


def _confidence(n: int) -> float:
    """0 at n=0, approaches 1.0 as n→∞. Reaches 0.5 at n=MIN_TRADES_PER_SYMBOL."""
    return round(min(1.0, n / (MIN_TRADES_PER_SYMBOL * 2)), 4)


def _analyse_categorical(
    trades: list[dict], field: str, baseline_pf: float
) -> dict | None:
    """Find which categorical values have meaningfully better PF than baseline."""
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        val = str(t.get(field) or "").strip()
        if val:
            buckets[val].append(float(t["pnl_usd"]))

    best_vals: list[str] = []
    for val, pnls in buckets.items():
        if len(pnls) < MIN_BUCKET_N:
            continue
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        bucket_pf = _pf(wins, losses)
        improvement = (bucket_pf - baseline_pf) / (baseline_pf + 1e-9)
        if bucket_pf >= MIN_PF_TO_GATE and improvement >= MIN_PF_IMPROVEMENT:
            best_vals.append(val)

    if not best_vals:
        return None

    # Only add condition if it excludes at least one value meaningfully
    all_vals = set(str(t.get(field) or "").strip() for t in trades if t.get(field))
    if len(best_vals) >= len(all_vals):
        return None  # all values are good — no condition needed

    in_trades = [t for t in trades if str(t.get(field) or "").strip() in best_vals]
    wins = [float(t["pnl_usd"]) for t in in_trades if float(t["pnl_usd"]) > 0]
    losses = [float(t["pnl_usd"]) for t in in_trades if float(t["pnl_usd"]) <= 0]
    bucket_pf = _pf(wins, losses)
    bucket_wr = _wr([float(t["pnl_usd"]) for t in in_trades])
    conf = _confidence(len(in_trades))

    if conf < MIN_CONFIDENCE:
        return None

    operator = "eq" if len(best_vals) == 1 else "in"
    value = best_vals[0] if operator == "eq" else best_vals

    return {
        "field": field,
        "operator": operator,
        "value": value,
        "reason": _FIELD_REASON_MAP.get(field, "edge_condition_failed"),
        "n_bucket": len(in_trades),
        "wr": bucket_wr,
        "pf": bucket_pf,
        "confidence": conf,
    }


def _analyse_numeric(trades: list[dict], field: str, baseline_pf: float) -> dict | None:
    """Find the numeric threshold (gte) that maximises PF improvement."""
    values = []
    for t in trades:
        raw = t.get(field)
        if raw is not None:
            try:
                values.append((float(raw), float(t["pnl_usd"])))
            except (TypeError, ValueError):
                pass

    if len(values) < MIN_BUCKET_N * 2:
        return None

    values.sort(key=lambda x: x[0])
    n = len(values)

    best: dict | None = None
    best_pf = baseline_pf * (1 + MIN_PF_IMPROVEMENT)

    # Sweep percentile thresholds from 20th to 75th
    for pct in [
        0.20,
        0.25,
        0.30,
        0.35,
        0.40,
        0.45,
        0.50,
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
    ]:
        idx = int(n * pct)
        if idx >= n - MIN_BUCKET_N:
            break
        threshold = values[idx][0]
        bucket_pnls = [v[1] for v in values[idx:]]
        if len(bucket_pnls) < MIN_BUCKET_N:
            continue
        wins = [p for p in bucket_pnls if p > 0]
        losses = [p for p in bucket_pnls if p <= 0]
        bucket_pf = _pf(wins, losses)
        if bucket_pf > best_pf:
            best_pf = bucket_pf
            best = {
                "threshold": threshold,
                "n_bucket": len(bucket_pnls),
                "wr": _wr(bucket_pnls),
                "pf": bucket_pf,
                "confidence": _confidence(len(bucket_pnls)),
            }

    if best is None or best["confidence"] < MIN_CONFIDENCE:
        return None

    return {
        "field": field,
        "operator": "gte",
        "value": round(float(best["threshold"]), 6),
        "reason": _FIELD_REASON_MAP.get(field, "edge_condition_failed"),
        "n_bucket": best["n_bucket"],
        "wr": best["wr"],
        "pf": best["pf"],
        "confidence": best["confidence"],
    }


def calibrate_symbol(symbol: str, conn: sqlite3.Connection) -> list[dict]:
    """
    Derive edge conditions for one symbol from real trade history.
    Returns list of derived condition dicts (may be empty if not enough data).
    """
    trades = _fetch_closed_spot_trades(conn, symbol)
    if len(trades) < MIN_TRADES_PER_SYMBOL:
        logger.debug(
            f"[calibrator] {symbol}: {len(trades)} trades < {MIN_TRADES_PER_SYMBOL} minimum — skipping"
        )
        return []

    all_pnls = [float(t["pnl_usd"]) for t in trades]
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p <= 0]
    baseline_pf = _pf(wins, losses)
    n_total = len(trades)

    logger.info(
        f"[calibrator] {symbol}: n={n_total} baseline_pf={baseline_pf:.3f} baseline_wr={_wr(all_pnls):.2%}"
    )

    derived: list[dict] = []
    for field, ftype in _CANDIDATE_FIELDS:
        try:
            if ftype == "categorical":
                result = _analyse_categorical(trades, field, baseline_pf)
            else:
                result = _analyse_numeric(trades, field, baseline_pf)
            if result:
                result["n_total"] = n_total
                result["baseline_pf"] = baseline_pf
                derived.append(result)
                logger.info(
                    f"[calibrator] {symbol}.{field}: derived {result['operator']} {result['value']} "
                    f"(pf={result['pf']:.3f} n={result['n_bucket']} conf={result['confidence']:.2f})"
                )
        except Exception as e:
            logger.warning(f"[calibrator] {symbol}.{field} error: {e}")

    return derived


def _write_conditions(
    conn: sqlite3.Connection, symbol: str, conditions: list[dict]
) -> None:
    """Upsert derived conditions into spot_edge_conditions table."""
    now = datetime.now(timezone.utc).isoformat()
    # Deactivate all existing conditions for this symbol first
    conn.execute("UPDATE spot_edge_conditions SET active=0 WHERE symbol=?", (symbol,))
    for c in conditions:
        conn.execute(
            """
            INSERT INTO spot_edge_conditions
                (symbol, field, operator, value, reason, n_total, n_bucket,
                 wr, pf, baseline_pf, confidence, active, derived_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)
            ON CONFLICT(symbol, field) DO UPDATE SET
                operator=excluded.operator,
                value=excluded.value,
                reason=excluded.reason,
                n_total=excluded.n_total,
                n_bucket=excluded.n_bucket,
                wr=excluded.wr,
                pf=excluded.pf,
                baseline_pf=excluded.baseline_pf,
                confidence=excluded.confidence,
                active=1,
                derived_at=excluded.derived_at
            """,
            (
                symbol,
                c["field"],
                c["operator"],
                json.dumps(c["value"]),
                c["reason"],
                c["n_total"],
                c["n_bucket"],
                c["wr"],
                c["pf"],
                c["baseline_pf"],
                c["confidence"],
                now,
            ),
        )


def run_calibration(symbols: list[str] | None = None) -> dict[str, int]:
    """
    Run calibration for all (or specified) spot symbols.
    Returns {symbol: conditions_derived} summary.
    """
    if symbols is None:
        try:
            from config import SPOT_SYMBOLS

            symbols = [s.upper() for s in SPOT_SYMBOLS]
        except Exception:
            symbols = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"]

    conn = _get_conn()
    summary: dict[str, int] = {}
    try:
        for sym in symbols:
            try:
                conditions = calibrate_symbol(sym, conn)
                if conditions:
                    _write_conditions(conn, sym, conditions)
                summary[sym] = len(conditions)
            except Exception as e:
                logger.warning(f"[calibrator] {sym} calibration failed: {e}")
                summary[sym] = 0
        conn.commit()
        logger.info(f"[calibrator] Calibration complete: {summary}")
        try:
            from logging_db.trade_logger import log_event

            total = sum(summary.values())
            log_event(
                "INFO",
                "spot_edge_calibrator",
                f"Calibration run: {summary} — {total} total conditions derived",
            )
        except Exception:
            pass
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_calibration()
    print(f"Calibration result: {result}")
