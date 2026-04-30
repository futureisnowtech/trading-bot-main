"""
learning_loop.py — Post-trade learning engine for v10.

Responsibilities:
  1. Persist 57-feature snapshot + outcome after every trade close
  2. Detect when enough new data exists to retrain ML models
  3. Feed incubating RBI strategies with live trade results
  4. Provide weekly performance report data (consumed by scripts/weekly_report.py)

DB tables (created on first import):
  ml_feature_snapshots  — 57 features per closed trade, keyed by trade_id
  ml_retrain_queue      — pair/direction pairs awaiting retrain

Called by: position_manager.py on every trade close (or scheduler calling
           `record_closed_trade()` directly).

Usage:
    from learning_loop import record_closed_trade, check_retrain_queue

    record_closed_trade(
        trade_id      = 42,
        symbol        = 'BTCUSDT',
        direction     = 'LONG',
        won           = True,
        pnl_usd       = 18.50,
        entry_price   = 68000.0,
        exit_price    = 68250.0,
        entry_score   = 71.3,
        exit_score    = 48.2,
        regime        = 'TRENDING_UP',
        features      = {...},   # dict from feature_builder.build_features()
        incubation_id = None,    # if this trade was part of RBI incubation
    )

    due = check_retrain_queue()   # returns list of (pair_key, direction) tuples
"""

import json
import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# How many new trade closes trigger a retrain per pair/direction slot
_RETRAIN_EVERY_N = 20

# Minimum trades before ML activates at all
_ML_MIN_TRADES = 30


# ── DB bootstrap ─────────────────────────────────────────────────────────────

def _db_conn():
    """Open a connection to the trades DB."""
    from logging_db.trade_logger import _conn
    return _conn()


def _ensure_tables():
    """Create learning tables if they don't exist (idempotent)."""
    try:
        conn = _db_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ml_feature_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        INTEGER NOT NULL,
                symbol          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                won             INTEGER NOT NULL,
                pnl_usd         REAL    NOT NULL,
                entry_price     REAL    NOT NULL,
                exit_price      REAL    NOT NULL,
                entry_score     REAL    DEFAULT 0,
                exit_score      REAL    DEFAULT 0,
                regime          TEXT    DEFAULT 'UNKNOWN',
                incubation_id   INTEGER,
                features_json   TEXT    NOT NULL,
                candidate_id    INTEGER DEFAULT 0,
                scan_id         TEXT DEFAULT '',
                raw_scanner_symbol TEXT DEFAULT '',
                base_asset      TEXT DEFAULT '',
                executed_symbol TEXT DEFAULT '',
                trade_ref       TEXT DEFAULT '',
                route_type      TEXT DEFAULT '',
                setup_family    TEXT DEFAULT '',
                setup_score     REAL DEFAULT 0,
                spot_regime     TEXT DEFAULT '',
                tv_profile_name TEXT DEFAULT '',
                tv_htf_bias     TEXT DEFAULT '',
                tv_signal_age_sec REAL DEFAULT 0,
                tv_veto_state   TEXT DEFAULT '',
                fast_follow_through INTEGER DEFAULT 0,
                thesis_decay_risk INTEGER DEFAULT 0,
                expected_net_pnl_after_fees REAL DEFAULT 0,
                route_conditional_value REAL DEFAULT 0,
                reconstructed   INTEGER DEFAULT 0,
                ts              REAL    NOT NULL
            )
        """)
        for migration in [
            "ALTER TABLE ml_feature_snapshots ADD COLUMN candidate_id INTEGER DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN scan_id TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN raw_scanner_symbol TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN base_asset TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN executed_symbol TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN trade_ref TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN route_type TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN setup_family TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN setup_score REAL DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN spot_regime TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN tv_profile_name TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN tv_htf_bias TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN tv_signal_age_sec REAL DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN tv_veto_state TEXT DEFAULT ''",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN fast_follow_through INTEGER DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN thesis_decay_risk INTEGER DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN expected_net_pnl_after_fees REAL DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN route_conditional_value REAL DEFAULT 0",
            "ALTER TABLE ml_feature_snapshots ADD COLUMN reconstructed INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mlfs_symbol_dir
            ON ml_feature_snapshots(symbol, direction, ts DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mlfs_ts
            ON ml_feature_snapshots(ts DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ml_retrain_queue (
                pair_key    TEXT NOT NULL,
                direction   TEXT NOT NULL,
                pending     INTEGER DEFAULT 0,
                last_queued REAL    DEFAULT 0,
                PRIMARY KEY (pair_key, direction)
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f'[learning] table init error: {e}')


def _load_trade_strategy(trade_id: int) -> str:
    try:
        conn = _db_conn()
        row = conn.execute(
            "SELECT strategy FROM trades WHERE id=? LIMIT 1", (int(trade_id or 0),)
        ).fetchone()
        conn.close()
        return str(row[0] if row else "")
    except Exception:
        return ""


def _load_candidate_metrics(candidate_id: int) -> dict:
    if int(candidate_id or 0) <= 0:
        return {}
    try:
        conn = _db_conn()
        row = conn.execute(
            """
            SELECT
                sc.spread_pct,
                sc.execution_route,
                co.mfe_4h_pct,
                co.time_to_05r_min,
                co.hit_1r,
                co.hit_stop,
                co.peak_r_4h
            FROM scan_candidates sc
            LEFT JOIN candidate_outcomes co ON co.candidate_id = sc.id
            WHERE sc.id=?
            LIMIT 1
            """,
            (int(candidate_id),),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


# ── Core API ──────────────────────────────────────────────────────────────────

def record_closed_trade(
    trade_id:       int,
    symbol:         str,
    direction:      str,
    won:            bool,
    pnl_usd:        float,
    entry_price:    float,
    exit_price:     float,
    entry_score:    float        = 0.0,
    exit_score:     float        = 0.0,
    regime:         str          = 'UNKNOWN',
    features:       Optional[Dict] = None,
    incubation_id:  Optional[int]  = None,
    exit_reason:    str          = "",
    trade_ref:      str          = "",
    reconstructed:  bool         = False,
) -> None:
    """
    Main entry point: record one closed trade with its feature snapshot.

    Triggers:
      - Feature snapshot persistence
      - Retrain queue increment
      - RBI incubation update (if incubation_id provided)
    """
    _ensure_tables()

    features = features or {}
    ts = time.time()
    candidate_id = int(features.get("candidate_id") or 0)
    scan_id = str(features.get("scan_id") or "")
    raw_scanner_symbol = str(features.get("raw_scanner_symbol") or "")
    base_asset = str(features.get("base_asset") or symbol)
    executed_symbol = str(features.get("executed_symbol") or symbol)
    route_type = str(features.get("route_type") or features.get("execution_route") or "")
    setup_family = str(features.get("setup_family") or "")
    setup_score = float(features.get("setup_score") or 0.0)
    spot_regime = str(features.get("spot_regime") or regime or "UNKNOWN")
    tv_profile_name = str(features.get("tv_profile_name") or "")
    tv_htf_bias = str(features.get("tv_htf_bias") or "")
    tv_signal_age_sec = float(features.get("tv_signal_age_sec") or 0.0)
    tv_veto_state = str(features.get("tv_veto_state") or "")
    trade_strategy = _load_trade_strategy(trade_id)
    candidate_metrics = _load_candidate_metrics(candidate_id)
    spread_pct = float(candidate_metrics.get("spread_pct") or 0.0)
    route_hint = str(candidate_metrics.get("execution_route") or route_type or "")
    entry_fee_pct = (0.0 if entry_price <= 0 else max(0.0, float(features.get("entry_fee_usd") or 0.0) / max(abs(entry_price), 1e-9)))
    exit_fee_pct = 0.0 if exit_price <= 0 else max(0.0, 0.0)
    assumed_slippage_pct = 0.0005 if str(route_hint or route_type).lower() == "maker_first" else 0.0010
    total_cost_pct = entry_fee_pct + exit_fee_pct + max(0.0, spread_pct / 2.0) + assumed_slippage_pct
    mfe_pct = float(candidate_metrics.get("mfe_4h_pct") or 0.0)
    time_to_05r_min = float(candidate_metrics.get("time_to_05r_min") or 0.0)
    fast_follow_through = int(
        bool(
            (time_to_05r_min > 0 and time_to_05r_min <= 15.0)
            or mfe_pct >= (1.25 * total_cost_pct)
        )
    ) if str(trade_strategy).startswith("spot_") else int(bool(pnl_usd > 0))
    thesis_decay_risk = int(
        str(exit_reason or "").strip().lower() in {"thesis_decay", "stagnation_exit"}
        or (
            str(trade_strategy).startswith("spot_")
            and float(exit_score or 0.0) < float(entry_score or 0.0)
            and pnl_usd <= 0
        )
    )
    expected_net_pnl_after_fees = round(float(pnl_usd or 0.0), 4)
    route_conditional_value = round(float(pnl_usd or 0.0), 4)

    try:
        conn = _db_conn()

        # 1. Persist 57-feature snapshot
        conn.execute("""
            INSERT INTO ml_feature_snapshots
            (trade_id, symbol, direction, won, pnl_usd,
             entry_price, exit_price, entry_score, exit_score,
             regime, incubation_id, features_json,
             candidate_id, scan_id, raw_scanner_symbol, base_asset, executed_symbol,
             trade_ref, route_type, setup_family, setup_score, spot_regime,
             tv_profile_name, tv_htf_bias, tv_signal_age_sec, tv_veto_state,
             fast_follow_through, thesis_decay_risk,
             expected_net_pnl_after_fees, route_conditional_value,
             reconstructed, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, symbol, direction,
            1 if won else 0,
            round(pnl_usd, 4),
            round(entry_price, 4), round(exit_price, 4),
            round(entry_score, 4), round(exit_score, 4),
            regime, incubation_id,
            json.dumps(features),
            candidate_id,
            scan_id,
            raw_scanner_symbol,
            base_asset,
            executed_symbol,
            str(trade_ref or ""),
            route_type,
            setup_family,
            setup_score,
            spot_regime,
            tv_profile_name,
            tv_htf_bias,
            tv_signal_age_sec,
            tv_veto_state,
            fast_follow_through,
            thesis_decay_risk,
            expected_net_pnl_after_fees,
            route_conditional_value,
            int(bool(reconstructed)),
            ts,
        ))

        # 2. Patch trade_attribution with v10 scores (non-fatal if columns absent)
        try:
            conn.execute("""
                UPDATE trade_attribution
                SET composite_score=?, regime=?, entry_thesis_score=?, exit_thesis_score=?
                WHERE id=?
            """, (entry_score, regime, entry_score, exit_score, trade_id))
        except Exception:
            pass

        conn.commit()
        conn.close()
        logger.debug(f'[learning] snapshot saved trade_id={trade_id} {symbol} '
                     f'{direction} won={won} pnl=${pnl_usd:.2f}')

    except Exception as e:
        logger.warning(f'[learning] snapshot error: {e}')
        return

    # 3. Increment retrain queue
    _increment_retrain_queue(symbol, direction)

    # 4. Feed RBI incubation if this trade belongs to an incubating strategy
    if incubation_id is not None:
        _forward_to_rbi(incubation_id, won, pnl_usd)


def check_retrain_queue() -> List[Tuple[str, str]]:
    """
    Return list of (pair_key, direction) slots that have >= _RETRAIN_EVERY_N
    pending trades and are ready for a walk-forward retrain.

    Calling code should trigger walk_forward_trainer.train_walk_forward()
    for each returned pair and then call mark_retrain_done() to reset the counter.
    """
    _ensure_tables()
    try:
        conn = _db_conn()
        rows = conn.execute("""
            SELECT pair_key, direction, pending
            FROM ml_retrain_queue
            WHERE pending >= ?
        """, (_RETRAIN_EVERY_N,)).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.debug(f'[learning] queue check error: {e}')
        return []


def mark_retrain_done(pair_key: str, direction: str) -> None:
    """Reset pending counter after a successful retrain."""
    try:
        conn = _db_conn()
        conn.execute("""
            UPDATE ml_retrain_queue
            SET pending = 0, last_queued = ?
            WHERE pair_key=? AND direction=?
        """, (time.time(), pair_key, direction))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f'[learning] mark_done error: {e}')


def get_training_data(pair_key: str, direction: str,
                      days: int = 60) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Build (X, y) arrays from feature snapshots for walk-forward training.

    Returns (None, None) if insufficient data (< _ML_MIN_TRADES rows).
    """
    _ensure_tables()
    try:
        from ml.feature_builder import FEATURE_NAMES
        conn = _db_conn()
        cutoff = time.time() - days * 86400
        rows = conn.execute("""
            SELECT features_json, won
            FROM ml_feature_snapshots
            WHERE symbol LIKE ? AND direction=? AND ts > ?
            ORDER BY ts ASC
        """, (f'%{pair_key.replace("USDT","")}%', direction, cutoff)).fetchall()
        conn.close()

        if len(rows) < _ML_MIN_TRADES:
            logger.debug(f'[learning] {pair_key}/{direction}: only {len(rows)} rows '
                         f'(need {_ML_MIN_TRADES})')
            return None, None

        X_rows, y_rows = [], []
        for row in rows:
            try:
                f = json.loads(row[0])
                arr = np.array([float(f.get(name, 0.0)) for name in FEATURE_NAMES],
                               dtype=np.float32)
                X_rows.append(arr)
                y_rows.append(int(row[1]))
            except Exception:
                continue

        if len(X_rows) < _ML_MIN_TRADES:
            return None, None

        return np.array(X_rows), np.array(y_rows)

    except Exception as e:
        logger.debug(f'[learning] training data error: {e}')
        return None, None


def get_ml_readiness() -> Dict:
    """
    Check how many feature snapshots exist per slot and overall ML readiness.
    Used by the dashboard and go-live criteria checker.
    """
    _ensure_tables()
    try:
        conn = _db_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM ml_feature_snapshots"
        ).fetchone()[0]

        by_slot = conn.execute("""
            SELECT symbol, direction, COUNT(*) as n,
                   AVG(won) as wr,
                   SUM(pnl_usd) as total_pnl
            FROM ml_feature_snapshots
            GROUP BY symbol, direction
            ORDER BY n DESC
        """).fetchall()

        live_days = 0
        if total > 0:
            oldest = conn.execute(
                "SELECT MIN(ts) FROM ml_feature_snapshots"
            ).fetchone()[0] or time.time()
            live_days = (time.time() - oldest) / 86400
        conn.close()

        return {
            'total_snapshots': total,
            'live_days': round(live_days, 1),
            'ml_active': total >= _ML_MIN_TRADES,
            'by_slot': [
                {
                    'symbol': r[0], 'direction': r[1],
                    'n': r[2], 'wr': round(r[3] or 0, 3),
                    'total_pnl': round(r[4] or 0, 2),
                }
                for r in by_slot
            ],
        }
    except Exception as e:
        logger.debug(f'[learning] readiness error: {e}')
        return {'total_snapshots': 0, 'live_days': 0, 'ml_active': False, 'by_slot': []}


def get_recent_feature_stats(days: int = 7) -> Dict:
    """
    Compute per-feature win rate correlation for the last N days.
    Used by the weekly report to surface which features are most predictive.

    Returns: {feature_name: {'corr': float, 'n': int}}
    """
    _ensure_tables()
    try:
        from ml.feature_builder import FEATURE_NAMES
        conn = _db_conn()
        cutoff = time.time() - days * 86400
        rows = conn.execute("""
            SELECT features_json, won
            FROM ml_feature_snapshots
            WHERE ts > ?
            ORDER BY ts DESC
            LIMIT 500
        """, (cutoff,)).fetchall()
        conn.close()

        if len(rows) < 10:
            return {}

        X_rows, y_rows = [], []
        for row in rows:
            try:
                f = json.loads(row[0])
                arr = [float(f.get(name, 0.0)) for name in FEATURE_NAMES]
                X_rows.append(arr)
                y_rows.append(int(row[1]))
            except Exception:
                continue

        if len(X_rows) < 10:
            return {}

        X = np.array(X_rows)
        y = np.array(y_rows, dtype=float)
        result = {}
        for i, name in enumerate(FEATURE_NAMES):
            col = X[:, i]
            if np.std(col) < 1e-9:
                continue
            corr = float(np.corrcoef(col, y)[0, 1])
            if not np.isnan(corr):
                result[name] = {'corr': round(corr, 4), 'n': len(y_rows)}

        return dict(sorted(result.items(), key=lambda kv: abs(kv[1]['corr']), reverse=True))

    except Exception as e:
        logger.debug(f'[learning] feature stats error: {e}')
        return {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _increment_retrain_queue(symbol: str, direction: str):
    """Add 1 to the pending counter for this pair/direction slot."""
    try:
        # Normalize pair key: BTCUSDT → BTC, ETHUSDT → ETH, etc.
        pair_key = symbol.replace('USDT', '').replace('BUSD', '').replace('-', '')
        conn = _db_conn()
        conn.execute("""
            INSERT INTO ml_retrain_queue (pair_key, direction, pending, last_queued)
            VALUES (?, ?, 1, 0)
            ON CONFLICT(pair_key, direction) DO UPDATE SET
                pending = pending + 1
        """, (pair_key, direction))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f'[learning] queue increment error: {e}')


def _forward_to_rbi(incubation_id: int, won: bool, pnl_usd: float):
    """Forward trade result to RBI incubation manager."""
    try:
        from rbi.incubation_manager import record_incubation_trade
        record_incubation_trade(incubation_id, won, pnl_usd)
        logger.debug(f'[learning] RBI incubation {incubation_id} updated: won={won}')
    except Exception as e:
        logger.debug(f'[learning] RBI forward error: {e}')


# ── Retrain trigger (called by scheduler) ────────────────────────────────────

def maybe_trigger_retrains(paper: bool = True) -> List[str]:
    """
    Check retrain queue and launch walk-forward retrains for due slots.
    Returns list of pair/direction strings that were triggered.

    Designed to be called from a low-frequency scheduler (e.g., every 6 hours).
    """
    due = check_retrain_queue()
    triggered = []

    for pair_key, direction in due:
        try:
            from ml.walk_forward_trainer import train_walk_forward
            logger.info(f'[learning] triggering retrain: {pair_key}/{direction}')
            result = train_walk_forward(pair_key, direction, paper=paper, optimize=False)
            mark_retrain_done(pair_key, direction)
            if result.get('passed'):
                triggered.append(f'{pair_key}/{direction}')
                logger.info(f'[learning] retrain passed: {pair_key}/{direction} '
                            f'WR={result.get("mean_wr", 0):.1%}')
            else:
                logger.info(f'[learning] retrain did not pass: {pair_key}/{direction} '
                            f'reason={result.get("reason", "unknown")[:60]}')
        except Exception as e:
            logger.warning(f'[learning] retrain error {pair_key}/{direction}: {e}')

    return triggered


# ── Nightly RBI research trigger ──────────────────────────────────────────────

def run_nightly_rbi(symbol: str = 'BTCUSDT', paper: bool = True) -> Dict:
    """
    Run the full RBI pipeline for one symbol:
      1. Research loop (test 575 combos)
      2. Backtest all promoted combos
      3. Return summary counts

    Intended to run at 2am ET via scheduler.
    """
    results = {'promoted': 0, 'backtested': 0, 'passed': 0, 'error': None}
    try:
        from rbi.research_loop import run_research
        from rbi.backtest_loop import run_all_pending

        promoted = run_research(symbol, paper=paper)
        results['promoted'] = len(promoted)
        logger.info(f'[learning/rbi] {symbol}: {len(promoted)} combos promoted')

        passed = run_all_pending(symbol)
        results['backtested'] = passed
        results['passed'] = passed
        logger.info(f'[learning/rbi] {symbol}: {passed} combos queued for incubation')

    except Exception as e:
        results['error'] = str(e)
        logger.error(f'[learning/rbi] nightly error: {e}', exc_info=True)

    return results
