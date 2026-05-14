"""
logging_db/trade_logger.py
SQLite trade log + position persistence + CSV export.
Positions are written to disk on every open/close so a restart never loses state.
"""

import sqlite3
import csv
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import pytz

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH, CSV_LOG_DIR, MARKET_TIMEZONE


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    # WAL mode: writes survive crashes without corrupting existing data.
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    os.makedirs(CSV_LOG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _conn()
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, strategy TEXT NOT NULL, broker TEXT NOT NULL,
        symbol TEXT NOT NULL, action TEXT NOT NULL, order_type TEXT NOT NULL,
        qty REAL NOT NULL, price REAL NOT NULL, value_usd REAL NOT NULL,
        fee_usd REAL DEFAULT 0, pnl_usd REAL DEFAULT 0,
        paper INTEGER NOT NULL, order_id TEXT, notes TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS open_positions (
        symbol TEXT NOT NULL, strategy TEXT NOT NULL,
        qty REAL NOT NULL, entry REAL NOT NULL,
        stop REAL NOT NULL, target REAL NOT NULL,
        high_since_entry REAL NOT NULL, ts_entry TEXT NOT NULL,
        paper INTEGER NOT NULL, direction TEXT DEFAULT 'LONG',
        entry_reason TEXT DEFAULT '',
        spot_regime TEXT DEFAULT '',
        setup_family TEXT DEFAULT '',
        setup_score REAL DEFAULT 0,
        setup_preference TEXT DEFAULT '',
        tf_5m_state TEXT DEFAULT '',
        tf_30m_state TEXT DEFAULT '',
        tf_4h_state TEXT DEFAULT '',
        tf_1d_state TEXT DEFAULT '',
        structural_confirms TEXT DEFAULT '',
        execution_route TEXT DEFAULT '',
        cooldown_until TEXT DEFAULT '',
        microstructure_veto TEXT DEFAULT '',
        stop_model_version TEXT DEFAULT '',
        target_model_version TEXT DEFAULT '',
        target_r REAL DEFAULT 0,
        trail_arm_r REAL DEFAULT 0,
        risk_dollars REAL DEFAULT 0,
        entry_fee_usd REAL DEFAULT 0,
        exit_reason TEXT DEFAULT '',
        PRIMARY KEY (symbol, strategy, paper)
    )""")
    for migration in [
        "ALTER TABLE open_positions ADD COLUMN direction TEXT DEFAULT 'LONG'",
        "ALTER TABLE open_positions ADD COLUMN entry_reason TEXT DEFAULT ''",
        # v9.0 Sprint 2: lane tag for 3-lane architecture (lane1=stocks, lane2=crypto, lane3=prediction)
        "ALTER TABLE trades ADD COLUMN lane TEXT DEFAULT 'lane2'",
        "ALTER TABLE open_positions ADD COLUMN lane TEXT DEFAULT 'lane2'",
        # v9.1 audit builds: MAE/MFE tracking, exit classification, ML gate visibility
        "ALTER TABLE open_positions ADD COLUMN low_since_entry REAL",
        "ALTER TABLE trade_attribution ADD COLUMN mae_pct REAL DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN mfe_pct REAL DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN exit_type TEXT DEFAULT 'unknown'",
        "ALTER TABLE trade_attribution ADD COLUMN is_fee_trap INTEGER DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN ml_p_win REAL DEFAULT 0",
        # v9.1 super score: unified 0-100 composite intelligence per trade
        "ALTER TABLE trade_attribution ADD COLUMN super_score REAL DEFAULT 0",
        # v10.1: won flag (1=profitable, 0=loss) and source tag for ML training filters.
        # walk_forward_trainer and position_manager._get_kelly_fraction both query these columns.
        "ALTER TABLE trades ADD COLUMN won INTEGER DEFAULT NULL",
        "ALTER TABLE trades ADD COLUMN source TEXT DEFAULT 'paper'",
        "ALTER TABLE trades ADD COLUMN pnl_pct REAL DEFAULT 0",
        # v10.2: position state persistence — survive restarts without losing exit logic state.
        # These are required to correctly restore trailing stops and scale-out flags.
        "ALTER TABLE open_positions ADD COLUMN atr_at_entry REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN composite_score REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN trailing_active INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN trailing_stop_price REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN scale_33_done INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN scale_66_done INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN leverage INTEGER DEFAULT 3",
        # v13.7: 15-minute forward outcome fields for candidate_outcomes
        "ALTER TABLE candidate_outcomes ADD COLUMN price_15m REAL DEFAULT 0",
        "ALTER TABLE candidate_outcomes ADD COLUMN ret_15m_pct REAL DEFAULT 0",
        # v13.7: funding rate at scan time for gate-quality analytics
        "ALTER TABLE scan_candidates ADD COLUMN funding_rate REAL DEFAULT NULL",
        # v16: scanner EV calibration — theoretical vs capped effective position
        "ALTER TABLE scan_candidates ADD COLUMN scanner_theoretical_position_usd REAL",
        "ALTER TABLE scan_candidates ADD COLUMN scanner_effective_position_usd REAL",
        # v16.14: shared tradeability engine fields
        "ALTER TABLE scan_candidates ADD COLUMN recommended_lane TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN tradeability_status TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN trade_blocked_reason TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN trade_size_block_reason TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN trade_source_reason TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN manual_executable INTEGER DEFAULT 0",
        "ALTER TABLE scan_candidates ADD COLUMN auto_executable INTEGER DEFAULT 0",
        "ALTER TABLE scan_candidates ADD COLUMN spot_regime TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN setup_family TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN setup_score REAL DEFAULT 0",
        "ALTER TABLE scan_candidates ADD COLUMN setup_preference TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN tf_5m_state TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN tf_30m_state TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN tf_4h_state TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN tf_1d_state TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN structural_confirms TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN execution_route TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN cooldown_until TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN microstructure_veto TEXT DEFAULT ''",
        "ALTER TABLE scan_candidates ADD COLUMN final_spot_score REAL DEFAULT 0",
        "ALTER TABLE scan_candidates ADD COLUMN regime_floor REAL DEFAULT 0",
        "ALTER TABLE scan_candidates ADD COLUMN actual_stop_pct REAL DEFAULT NULL",
        "ALTER TABLE scan_candidates ADD COLUMN actual_target_pct REAL DEFAULT NULL",
        "ALTER TABLE scan_candidates ADD COLUMN net_rr REAL DEFAULT NULL",
        "ALTER TABLE scan_candidates ADD COLUMN net_win_usd REAL DEFAULT NULL",
        "ALTER TABLE scan_candidates ADD COLUMN econ_gate_class TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN spot_regime TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN setup_family TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN setup_score REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN setup_preference TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tf_5m_state TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tf_30m_state TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tf_4h_state TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tf_1d_state TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN structural_confirms TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN execution_route TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN cooldown_until TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN microstructure_veto TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN stop_model_version TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN target_model_version TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN target_r REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN trail_arm_r REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN risk_dollars REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN entry_fee_usd REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN exit_reason TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN entry_trade_id INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN entry_order_id TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN entry_feature_snapshot_id INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN tv_profile_name TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tv_signal_bias TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tv_signal_ts TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tv_signal_age_sec REAL DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN tv_indicator_name TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tv_signal_strength TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN candidate_id INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN candidate_scan_id TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN raw_scanner_symbol TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN base_asset TEXT DEFAULT ''",
        "ALTER TABLE open_positions ADD COLUMN tv_veto_state TEXT DEFAULT ''",
        # v18.19: sell-failure halt (SOL ghost cure Layer C). After 3 consecutive
        # broker rejections with the same error code, sell_blocked=1 stops the
        # retry loop and requires human reconciliation.
        "ALTER TABLE open_positions ADD COLUMN sell_failure_count INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN sell_blocked INTEGER DEFAULT 0",
        "ALTER TABLE open_positions ADD COLUMN sell_blocked_reason TEXT DEFAULT ''",
        "CREATE TABLE IF NOT EXISTS api_telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, module TEXT NOT NULL, prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0)",
    ]:
        try:
            cur.execute(migration)
        except Exception:
            pass

    cur.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
        signal TEXT NOT NULL, confidence REAL NOT NULL,
        reason TEXT, acted_on INTEGER DEFAULT 0, price REAL
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS debate_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, symbol TEXT NOT NULL,
        buy_votes INTEGER, hold_votes INTEGER, sell_votes INTEGER,
        final_signal TEXT, confidence REAL,
        reasoning TEXT, bull_case TEXT, bear_case TEXT, key_risk TEXT,
        agent_details TEXT, regime TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, level TEXT NOT NULL,
        source TEXT NOT NULL, message TEXT NOT NULL
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS tv_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        symbol TEXT NOT NULL,
        action_raw TEXT NOT NULL,
        direction TEXT NOT NULL,
        htf_bias TEXT NOT NULL,
        price REAL DEFAULT 0,
        tf_min TEXT DEFAULT '',
        indicator_name TEXT DEFAULT '',
        profile_name TEXT DEFAULT '',
        strength TEXT DEFAULT '',
        signal_desc TEXT DEFAULT '',
        secret_validated INTEGER DEFAULT 0,
        raw_payload_json TEXT DEFAULT ''
    )""")
    cur.execute(
        """CREATE INDEX IF NOT EXISTS idx_tv_signals_symbol_ts
           ON tv_signals(symbol, ts DESC)"""
    )
    cur.execute("""CREATE TABLE IF NOT EXISTS spot_holding_classifications (
        symbol TEXT PRIMARY KEY,
        classification TEXT NOT NULL,
        note TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""")

    # v18.19: sticky regime state — survives bot restart. classify_spot_regime
    # reads prior regime here to apply the NEUTRAL→CHOP hysteresis band.
    cur.execute("""CREATE TABLE IF NOT EXISTS spot_regime_state (
        symbol TEXT PRIMARY KEY,
        last_regime TEXT NOT NULL,
        ts INTEGER NOT NULL
    )""")

    # v18.19: per-symbol cooldown timestamps. check_spot_entry_cooldown reads
    # last_exit_ts to enforce SPOT_SCALP_SYMBOL_CONFIG[symbol]['cooldown_min'].
    cur.execute("""CREATE TABLE IF NOT EXISTS spot_cooldown_state (
        symbol TEXT PRIMARY KEY,
        last_exit_ts INTEGER NOT NULL
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS api_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, call_type TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0, symbol TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS api_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        module TEXT NOT NULL,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS edge_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        market TEXT NOT NULL,
        symbol TEXT NOT NULL,
        v_score REAL,
        e_score REAL,
        d_factor REAL,
        t_multiplier REAL,
        k_factor REAL,
        m_score REAL,
        final_size_usd REAL,
        debate_type TEXT,
        notes TEXT
    )""")

    # v10.1: 57-feature snapshots keyed to each trade.
    # Enables walk_forward_trainer to train on real features instead of 3-proxy scores.
    # One row per trade entry (trade_id → BUY trade in trades table).
    cur.execute("""CREATE TABLE IF NOT EXISTS trade_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,
        ts REAL NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        features_json TEXT NOT NULL
    )""")

    # v13.6: Candidate journaling — one row per decision-grade candidate per scan cycle.
    # Captures the full decision set (entered + vetoed + blocked) so the learning layer
    # can analyse selection bias and attribute outcomes to decisions, not just filled trades.
    cur.execute("""CREATE TABLE IF NOT EXISTS scan_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT,
        ts REAL NOT NULL,
        symbol TEXT NOT NULL,
        exchange TEXT,
        base_asset TEXT,
        direction TEXT,
        primary_setup TEXT,
        scan_setups_json TEXT,
        price REAL,
        volume_24h_usd REAL,
        spread_pct REAL,
        bid_depth_usd REAL,
        ask_depth_usd REAL,
        atr_15m REAL,
        stop_pct REAL,
        target_pct REAL,
        scanner_expected_profit REAL,
        regime TEXT,
        technical_score REAL,
        ml_score REAL,
        composite_score REAL,
        entry_threshold REAL,
        should_enter_signal INTEGER,
        econ_approved INTEGER,
        econ_tier TEXT,
        econ_reject_reason TEXT,
        edge_score REAL,
        size_usd REAL,
        leverage INTEGER,
        entry_block_reason TEXT,
        decision TEXT,
        paper INTEGER,
        source TEXT,
        labeled INTEGER DEFAULT 0,
        scanner_theoretical_position_usd REAL,
        scanner_effective_position_usd REAL,
        recommended_lane TEXT DEFAULT '',
        tradeability_status TEXT DEFAULT '',
        trade_blocked_reason TEXT DEFAULT '',
        trade_size_block_reason TEXT DEFAULT '',
        trade_source_reason TEXT DEFAULT '',
        manual_executable INTEGER DEFAULT 0,
        auto_executable INTEGER DEFAULT 0,
        spot_regime TEXT DEFAULT '',
        setup_family TEXT DEFAULT '',
        setup_score REAL DEFAULT 0,
        setup_preference TEXT DEFAULT '',
        tf_5m_state TEXT DEFAULT '',
        tf_30m_state TEXT DEFAULT '',
        tf_4h_state TEXT DEFAULT '',
        tf_1d_state TEXT DEFAULT '',
        structural_confirms TEXT DEFAULT '',
        execution_route TEXT DEFAULT '',
        cooldown_until TEXT DEFAULT '',
        microstructure_veto TEXT DEFAULT '',
        final_spot_score REAL DEFAULT 0,
        regime_floor REAL DEFAULT 0,
        actual_stop_pct REAL DEFAULT NULL,
        actual_target_pct REAL DEFAULT NULL,
        net_rr REAL DEFAULT NULL,
        net_win_usd REAL DEFAULT NULL,
        econ_gate_class TEXT DEFAULT ''
    )""")

    # v13.6: Forward-outcome labels for each journaled candidate.
    # Populated asynchronously by learning/candidate_labeler.py after the
    # minimum look-forward window (4 h) has elapsed.
    # v13.7: price_15m / ret_15m_pct columns included in DDL so fresh DBs
    # get them without relying on the ALTER TABLE migration path.
    cur.execute("""CREATE TABLE IF NOT EXISTS candidate_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL,
        label_status TEXT,
        entry_ref_price REAL,
        price_1h REAL,
        price_4h REAL,
        ret_1h_pct REAL,
        ret_4h_pct REAL,
        mfe_4h_pct REAL,
        mae_4h_pct REAL,
        hit_1r INTEGER,
        hit_2r INTEGER,
        hit_stop INTEGER,
        best_exit_pct REAL,
        worst_drawdown_pct REAL,
        labeled_at TEXT,
        price_15m REAL DEFAULT 0,
        ret_15m_pct REAL DEFAULT 0,
        path_timing_evaluated INTEGER DEFAULT 0,
        time_to_05r_min REAL,
        time_to_1r_min REAL,
        time_to_2r_min REAL,
        peak_r_4h REAL,
        FOREIGN KEY (candidate_id) REFERENCES scan_candidates(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS kill_switch_log (
        id              TEXT PRIMARY KEY,
        ts              TEXT NOT NULL,
        reason          TEXT NOT NULL,
        balance         REAL,
        peak_balance    REAL,
        positions_closed INTEGER,
        resumed_at      TEXT,
        trigger_type    TEXT DEFAULT 'trigger'
    )""")

    # v14.0: Trade integrity — durable trust tier for every close-side trade.
    # Tier: 'verified' | 'suspect' | 'quarantined' | 'excluded'
    # No Bayesian or Kelly update proceeds if tier is 'quarantined' or 'excluded'.
    cur.execute("""CREATE TABLE IF NOT EXISTS trade_integrity (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id       INTEGER,
        close_order_id TEXT,
        tier           TEXT NOT NULL DEFAULT 'suspect',
        reason         TEXT NOT NULL DEFAULT '',
        source_check   TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL,
        notes          TEXT,
        UNIQUE(close_order_id)
    )""")

    # v14.0: Exit evaluation substrate — research-grade quality tracking per exit.
    # Populated after close; used to surface exit improvement opportunities.
    cur.execute("""CREATE TABLE IF NOT EXISTS exit_evaluations (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id               TEXT,
        close_order_id         TEXT,
        exit_type              TEXT,
        actual_exit_price      REAL,
        actual_exit_pct        REAL,
        optimal_exit_price     REAL,
        opportunity_loss_pct   REAL,
        stop_overshoot_pct     REAL,
        regime                 TEXT,
        composite_score_at_exit REAL,
        mfe_at_exit            REAL,
        mae_at_exit            REAL,
        path_label             TEXT,
        created_at             TEXT,
        UNIQUE(close_order_id)
    )""")

    # v16.0: Scan funnels — exact per-scan-cycle funnel counters.
    cur.execute("""CREATE TABLE IF NOT EXISTS scan_funnels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id TEXT,
        ts REAL NOT NULL,
        scanner_candidates_total INTEGER DEFAULT 0,
        dual_exposure_block INTEGER DEFAULT 0,
        cooldown_block INTEGER DEFAULT 0,
        risk_block INTEGER DEFAULT 0,
        data_unavailable INTEGER DEFAULT 0,
        below_threshold INTEGER DEFAULT 0,
        econ_veto INTEGER DEFAULT 0,
        research_only_block INTEGER DEFAULT 0,
        sizing_zero INTEGER DEFAULT 0,
        execution_failed INTEGER DEFAULT 0,
        entered INTEGER DEFAULT 0,
        scored_total INTEGER DEFAULT 0,
        econ_passed_total INTEGER DEFAULT 0,
        final_entryable_total INTEGER DEFAULT 0
    )""")

    # v14.0: Challenger state — promotion/demotion tracking for backtested strategies.
    cur.execute("""CREATE TABLE IF NOT EXISTS challenger_state (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy         TEXT NOT NULL,
        run_id           TEXT NOT NULL,
        promotion_tier   TEXT NOT NULL DEFAULT 'CANDIDATE',
        criteria_met_json TEXT,
        promoted_at      TEXT,
        demoted_at       TEXT,
        notes            TEXT,
        created_at       TEXT NOT NULL,
        UNIQUE(strategy, run_id)
    )""")

    # v14.0: Trade lineage — extended attribution lineage columns.
    # Added as migrations to trade_attribution to avoid creating a new table.
    for migration in [
        "ALTER TABLE trade_attribution ADD COLUMN entry_order_id TEXT",
        "ALTER TABLE trade_attribution ADD COLUMN feature_snapshot_id INTEGER",
        "ALTER TABLE trade_attribution ADD COLUMN lineage_complete INTEGER DEFAULT 0",
        "ALTER TABLE trade_attribution ADD COLUMN lineage_notes TEXT",
        "ALTER TABLE trade_attribution ADD COLUMN integrity_tier TEXT DEFAULT 'suspect'",
    ]:
        try:
            cur.execute(migration)
        except Exception:
            pass

    # v15.8: kill_switch_log schema migration — align with new column names.
    # Production DBs may have the old schema (balance_at_trigger, resolved, etc.).
    for migration in [
        "ALTER TABLE kill_switch_log ADD COLUMN balance REAL",
        "ALTER TABLE kill_switch_log ADD COLUMN peak_balance REAL",
        "ALTER TABLE kill_switch_log ADD COLUMN positions_closed INTEGER",
        "ALTER TABLE kill_switch_log ADD COLUMN resumed_at TEXT",
        "ALTER TABLE kill_switch_log ADD COLUMN trigger_type TEXT DEFAULT 'trigger'",
    ]:
        try:
            cur.execute(migration)
        except Exception:
            pass

    # v16.0: candidate_outcomes — path timing columns.
    for _migration in [
        "ALTER TABLE candidate_outcomes ADD COLUMN path_timing_evaluated INTEGER DEFAULT 0",
        "ALTER TABLE candidate_outcomes ADD COLUMN time_to_05r_min REAL",
        "ALTER TABLE candidate_outcomes ADD COLUMN time_to_1r_min REAL",
        "ALTER TABLE candidate_outcomes ADD COLUMN time_to_2r_min REAL",
        "ALTER TABLE candidate_outcomes ADD COLUMN peak_r_4h REAL",
    ]:
        try:
            cur.execute(_migration)
        except Exception:
            pass
    try:
        cur.execute(
            """
            UPDATE candidate_outcomes
            SET path_timing_evaluated = 1
            WHERE COALESCE(path_timing_evaluated, 0) = 0
              AND peak_r_4h IS NOT NULL
            """
        )
    except Exception:
        pass

    try:
        _backfill_tradeability_truth(cur)
    except Exception:
        pass

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS spot_edge_conditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                field TEXT NOT NULL,
                operator TEXT NOT NULL,
                value TEXT NOT NULL,
                reason TEXT NOT NULL,
                n_total INTEGER NOT NULL DEFAULT 0,
                n_bucket INTEGER NOT NULL DEFAULT 0,
                wr REAL NOT NULL DEFAULT 0.0,
                pf REAL NOT NULL DEFAULT 0.0,
                baseline_pf REAL NOT NULL DEFAULT 0.0,
                confidence REAL NOT NULL DEFAULT 0.0,
                active INTEGER NOT NULL DEFAULT 1,
                derived_at TEXT NOT NULL,
                UNIQUE(symbol, field)
            )
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()


def _backfill_tradeability_truth(cur) -> None:
    """
    Backfill route hints for older scan_candidates rows created before the
    shared tradeability engine wrote canonical fields.

    We only write stable policy-level hints here:
      - recommended_lane
      - tradeability_status = 'not_evaluated'
      - trade_source_reason = 'not_applicable'

    Existing non-empty rows are preserved.
    """
    try:
        from runtime.crypto_tradeability import get_recommended_crypto_lane
    except Exception:
        return

    try:
        rows = cur.execute(
            """
            SELECT id, symbol, direction
            FROM scan_candidates
            WHERE COALESCE(recommended_lane, '') = ''
            ORDER BY id DESC
            LIMIT 25000
            """
        ).fetchall()
    except Exception:
        return

    for row in rows:
        try:
            row_id = row[0]
            symbol = row[1] or ""
            direction = row[2] or "LONG"
            if not symbol:
                continue
            lane = get_recommended_crypto_lane(
                symbol,
                direction,
                live=True,
            )
            cur.execute(
                """
                UPDATE scan_candidates
                SET recommended_lane = ?,
                    tradeability_status = CASE
                        WHEN COALESCE(tradeability_status, '') = '' THEN 'not_evaluated'
                        ELSE tradeability_status
                    END,
                    trade_source_reason = CASE
                        WHEN COALESCE(trade_source_reason, '') = '' THEN 'not_applicable'
                        ELSE trade_source_reason
                    END
                WHERE id = ?
                """,
                (lane, row_id),
            )
        except Exception:
            continue


# ── Logger handle (singleton per process) ────────────────────────────────────

_LOGGER_HANDLE = None


class _TradeLoggerHandle:
    """Thin wrapper holding a persistent sqlite3 connection.
    Used by risk_engine, kill_switch, position_manager, and RBI modules."""

    def __init__(self) -> None:
        self.conn = _conn()


def get_logger() -> _TradeLoggerHandle:
    """Return the module-level logger handle, creating it on first call."""
    global _LOGGER_HANDLE
    if _LOGGER_HANDLE is None:
        _LOGGER_HANDLE = _TradeLoggerHandle()
    return _LOGGER_HANDLE


def _ts() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).isoformat()


def log_trade(
    strategy,
    broker,
    symbol,
    action,
    order_type,
    qty,
    price,
    fee_usd=0.0,
    pnl_usd=0.0,
    order_id="",
    notes="",
    won=None,
    source=None,
    pnl_pct=0.0,
    paper: int = 0,
) -> int:
    """
    Log a trade to the SQLite trades table.

    Args:
        won:    1 if trade was profitable, 0 if loss, None for open legs.
                Used by walk_forward_trainer and _get_kelly_fraction.
        source: Trade source tag (e.g. 'paper_v10', 'live_v10', 'backtest').
                Used to filter ML training data to prevent contamination.
        pnl_pct: P&L as fraction of position size.
    """
    ts = _ts()
    value_usd = qty * price
    # Infer won from pnl_usd if not supplied explicitly
    if won is None and pnl_usd != 0:
        won = 1 if pnl_usd > 0 else 0
    if source is None:
        source = "live_v10" if paper == 0 else "paper_v10"
    conn = _conn()
    cur = conn.cursor()

    # Dedup guard: if an identical close (SELL/BUY with P&L) for this symbol+strategy
    # was already logged within the last 90 seconds, skip. Prevents double-logging
    # caused by the kill window between log_trade and delete_position on restart.
    if pnl_usd != 0:
        cur.execute(
            """
            SELECT id FROM trades
            WHERE symbol=? AND strategy=? AND action=? AND paper=?
              AND ABS(qty - ?) < 0.000001
              AND ts >= datetime('now', '-90 seconds')
            LIMIT 1
        """,
            (symbol, strategy, action, paper, qty),
        )
        if cur.fetchone():
            conn.close()
            return -1  # silently skip duplicate

    cur.execute(
        """INSERT INTO trades
        (ts,strategy,broker,symbol,action,order_type,qty,price,value_usd,
         fee_usd,pnl_usd,paper,order_id,notes,won,source,pnl_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ts,
            strategy,
            broker,
            symbol,
            action,
            order_type,
            qty,
            price,
            value_usd,
            fee_usd,
            pnl_usd,
            paper,
            order_id or (f"LIVE_{uuid.uuid4().hex[:8]}" if paper == 0 else f"PAPER_{uuid.uuid4().hex[:8]}"),
            notes,
            won,
            source,
            pnl_pct,
        ),
    )
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    _csv_append(
        ts,
        strategy,
        broker,
        symbol,
        action,
        order_type,
        qty,
        price,
        value_usd,
        fee_usd,
        pnl_usd,
        order_id,
        notes,
    )
    return trade_id


def log_signal(
    strategy, symbol, signal, confidence, reason="", acted_on=False, price=0.0
) -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO signals (ts,strategy,symbol,signal,confidence,reason,acted_on,price) VALUES (?,?,?,?,?,?,?,?)",
        (_ts(), strategy, symbol, signal, confidence, reason, int(acted_on), price),
    )
    conn.commit()
    conn.close()


def log_debate(
    symbol,
    buy_votes,
    hold_votes,
    sell_votes,
    final_signal,
    confidence,
    reasoning,
    bull_case,
    bear_case,
    key_risk,
    agent_details,
    regime="",
) -> None:
    import json

    conn = _conn()
    conn.cursor().execute(
        """INSERT INTO debate_results
        (ts,symbol,buy_votes,hold_votes,sell_votes,final_signal,confidence,
         reasoning,bull_case,bear_case,key_risk,agent_details,regime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _ts(),
            symbol,
            buy_votes,
            hold_votes,
            sell_votes,
            final_signal,
            confidence,
            reasoning,
            bull_case,
            bear_case,
            key_risk,
            json.dumps(agent_details)
            if not isinstance(agent_details, str)
            else agent_details,
            regime,
        ),
    )
    conn.commit()
    conn.close()


def log_event(level, source, message) -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO system_events (ts,level,source,message) VALUES (?,?,?,?)",
        (_ts(), level, source, message),
    )
    conn.commit()
    conn.close()


def log_api_cost(call_type, input_tokens, output_tokens, cost_usd, symbol="") -> None:
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO api_costs (ts,call_type,input_tokens,output_tokens,cost_usd,symbol) VALUES (?,?,?,?,?,?)",
        (_ts(), call_type, input_tokens, output_tokens, cost_usd, symbol),
    )
    conn.commit()
    conn.close()


def log_edge_snapshot(
    market: str,
    symbol: str,
    v_score: float = 0.0,
    e_score: float = 0.0,
    d_factor: float = 1.0,
    t_multiplier: float = 1.0,
    k_factor: float = 1.0,
    m_score: float = 0.0,
    final_size_usd: float = 0.0,
    debate_type: str = "agents",
    notes: str = "",
) -> None:
    """Log a sizing edge snapshot for post-trade attribution and reporting."""
    conn = _conn()
    conn.cursor().execute(
        "INSERT INTO edge_snapshots "
        "(ts,market,symbol,v_score,e_score,d_factor,t_multiplier,k_factor,m_score,final_size_usd,debate_type,notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            _ts(),
            market,
            symbol,
            v_score,
            e_score,
            d_factor,
            t_multiplier,
            k_factor,
            m_score,
            final_size_usd,
            debate_type,
            notes,
        ),
    )
    conn.commit()
    conn.close()


def log_trade_features(
    trade_id: int, symbol: str, direction: str, features: dict
) -> int:
    """
    Persist a 57-feature snapshot for a trade entry.

    Called immediately after a successful perps_engine.open_long/open_short so
    walk_forward_trainer._load_training_data() can join features to outcomes and
    train on the real 57-column feature matrix instead of 3-proxy scores.

    Args:
        trade_id:  The id returned by log_trade() for the BUY/SELL entry leg.
        symbol:    Trading pair (e.g. 'BTCUSDT').
        direction: 'LONG' or 'SHORT'.
        features:  Dict produced by feature_builder.build_features() + injections
                   in v10_runner._attempt_entry.  All 57 FEATURE_NAMES keys should
                   be present; extras are serialised and ignored at training time.
    """
    import json

    if not trade_id or trade_id <= 0:
        return 0
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trade_features (trade_id, ts, symbol, direction, features_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_id, time.time(), symbol, direction, json.dumps(features)),
        )
        row_id = int(cur.lastrowid or 0)
        conn.commit()
        conn.close()
        return row_id
    except Exception:
        return 0  # feature snapshot is best-effort; never block trade execution


# ─── Candidate journaling (v13.6) ────────────────────────────────────────────


def log_scan_candidate(
    scan_id: str,
    symbol: str,
    exchange: str,
    base_asset: str,
    direction: str,
    primary_setup: str,
    scan_setups_json: str,
    price: float,
    volume_24h_usd: float,
    spread_pct: float,
    bid_depth_usd: float,
    ask_depth_usd: float,
    atr_15m: float,
    stop_pct: float,
    target_pct: float,
    scanner_expected_profit: float,
    regime: str,
    technical_score: float,
    ml_score: float,
    composite_score: float,
    entry_threshold: float,
    should_enter_signal: int,
    econ_approved: int,
    econ_tier: str,
    econ_reject_reason: str,
    edge_score: float,
    size_usd: float,
    leverage: int,
    entry_block_reason: str,
    decision: str,
    source: str,
    paper: int = 0,
    scanner_theoretical_position_usd: float | None = None,
    scanner_effective_position_usd: float | None = None,
    recommended_lane: str = "",
    tradeability_status: str = "",
    trade_blocked_reason: str = "",
    trade_size_block_reason: str = "",
    trade_source_reason: str = "",
    manual_executable: int = 0,
    auto_executable: int = 0,
    spot_regime: str = "",
    setup_family: str = "",
    setup_score: float = 0.0,
    setup_preference: str = "",
    tf_5m_state: str = "",
    tf_30m_state: str = "",
    tf_4h_state: str = "",
    tf_1d_state: str = "",
    structural_confirms: str = "",
    execution_route: str = "",
    cooldown_until: str = "",
    microstructure_veto: str = "",
    final_spot_score: float = 0.0,
    regime_floor: float = 0.0,
    actual_stop_pct: float | None = None,
    actual_target_pct: float | None = None,
    net_rr: float | None = None,
    net_win_usd: float | None = None,
    econ_gate_class: str = "",
) -> int:
    """
    Persist one candidate decision to scan_candidates.

    Called at every meaningful gate outcome — entered, econ_veto,
    below_threshold, dual_exposure_block, cooldown_block, etc.
    Returns the row id (0 on error — never raises).
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO scan_candidates (
                scan_id, ts, symbol, exchange, base_asset, direction,
                primary_setup, scan_setups_json, price, volume_24h_usd,
                spread_pct, bid_depth_usd, ask_depth_usd, atr_15m,
                stop_pct, target_pct, scanner_expected_profit,
                regime, technical_score, ml_score, composite_score,
                entry_threshold, should_enter_signal, econ_approved,
                econ_tier, econ_reject_reason, edge_score, size_usd,
                leverage, entry_block_reason, decision, paper, source, labeled,
                scanner_theoretical_position_usd, scanner_effective_position_usd,
                recommended_lane, tradeability_status, trade_blocked_reason,
                trade_size_block_reason, trade_source_reason,
                manual_executable, auto_executable,
                spot_regime, setup_family, setup_score, setup_preference,
                tf_5m_state, tf_30m_state, tf_4h_state,
                tf_1d_state, structural_confirms, execution_route, cooldown_until,
                microstructure_veto, final_spot_score, regime_floor,
                actual_stop_pct, actual_target_pct, net_rr, net_win_usd, econ_gate_class
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                scan_id,
                _ts(),
                symbol,
                exchange,
                base_asset,
                direction,
                primary_setup,
                scan_setups_json,
                price,
                volume_24h_usd,
                spread_pct,
                bid_depth_usd,
                ask_depth_usd,
                atr_15m,
                stop_pct,
                target_pct,
                scanner_expected_profit,
                regime,
                technical_score,
                ml_score,
                composite_score,
                entry_threshold,
                int(should_enter_signal),
                int(econ_approved),
                econ_tier,
                econ_reject_reason,
                edge_score,
                size_usd,
                int(leverage),
                entry_block_reason,
                decision,
                int(paper),
                source,
                0,
                scanner_theoretical_position_usd,
                scanner_effective_position_usd,
                recommended_lane,
                tradeability_status,
                trade_blocked_reason,
                trade_size_block_reason,
                trade_source_reason,
                int(manual_executable),
                int(auto_executable),
                spot_regime,
                setup_family,
                float(setup_score or 0.0),
                str(setup_preference or ""),
                tf_5m_state,
                tf_30m_state,
                tf_4h_state,
                tf_1d_state,
                structural_confirms,
                execution_route,
                cooldown_until,
                microstructure_veto,
                final_spot_score,
                regime_floor,
                actual_stop_pct,
                actual_target_pct,
                net_rr,
                net_win_usd,
                econ_gate_class,
            ),
        )
        row_id = cur.lastrowid or 0
        conn.commit()
        conn.close()
        return row_id
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[trade_logger] log_scan_candidate error: {e}")
        return 0


def update_scan_candidate_result(candidate_id: int, **updates) -> bool:
    """
    Patch an existing scan_candidates row after execution.

    Used by the spot lane to preserve one canonical candidate row across:
      admitted -> entered / execution_failed
    instead of inserting a second near-duplicate row.
    """
    if int(candidate_id or 0) <= 0:
        return False

    allowed = {
        "decision",
        "entry_block_reason",
        "execution_route",
        "cooldown_until",
        "microstructure_veto",
        "final_spot_score",
        "regime_floor",
        "actual_stop_pct",
        "actual_target_pct",
        "net_rr",
        "net_win_usd",
        "econ_gate_class",
        "trade_blocked_reason",
        "trade_size_block_reason",
        "trade_source_reason",
        "spot_regime",
        "setup_family",
        "setup_score",
        "setup_preference",
        "tf_5m_state",
        "tf_30m_state",
        "tf_4h_state",
        "tf_1d_state",
        "structural_confirms",
        "size_usd",
        "econ_approved",
    }
    payload = {k: v for k, v in updates.items() if k in allowed}
    if not payload:
        return False

    try:
        conn = _conn()
        cur = conn.cursor()
        assignments = ", ".join(f"{col}=?" for col in payload)
        values = list(payload.values()) + [int(candidate_id)]
        cur.execute(
            f"UPDATE scan_candidates SET {assignments} WHERE id=?",
            values,
        )
        conn.commit()
        changed = cur.rowcount > 0
        conn.close()
        return changed
    except Exception:
        return False


def log_scan_funnel(
    scan_id: str,
    scanner_candidates_total: int = 0,
    dual_exposure_block: int = 0,
    cooldown_block: int = 0,
    risk_block: int = 0,
    data_unavailable: int = 0,
    below_threshold: int = 0,
    econ_veto: int = 0,
    research_only_block: int = 0,
    sizing_zero: int = 0,
    execution_failed: int = 0,
    entered: int = 0,
) -> int:
    """Persist one exact scan funnel row for the given scan_id. Returns row id or 0 on error."""
    scored_total = (
        below_threshold
        + econ_veto
        + research_only_block
        + sizing_zero
        + execution_failed
        + entered
    )
    econ_passed_total = research_only_block + sizing_zero + execution_failed + entered
    final_entryable_total = sizing_zero + execution_failed + entered

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO scan_funnels (
                scan_id, ts, scanner_candidates_total,
                dual_exposure_block, cooldown_block, risk_block,
                data_unavailable, below_threshold, econ_veto,
                research_only_block, sizing_zero, execution_failed, entered,
                scored_total, econ_passed_total, final_entryable_total
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                scan_id,
                ts,
                scanner_candidates_total,
                dual_exposure_block,
                cooldown_block,
                risk_block,
                data_unavailable,
                below_threshold,
                econ_veto,
                research_only_block,
                sizing_zero,
                execution_failed,
                entered,
                scored_total,
                econ_passed_total,
                final_entryable_total,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid or 0
        conn.close()
        return row_id
    except Exception as e:
        logger.debug(f"[trade_logger] log_scan_funnel error: {e}")
        return 0


def get_unlabeled_candidates(min_age_hours: float = 4.0, limit: int = 100) -> list:
    """
    Return scan_candidates rows that are old enough to label (labeled=0).
    Uses a conservative 4-hour look-forward window so candle data exists.
    """
    import datetime as _dt

    try:
        cutoff = (
            datetime.now(pytz.utc) - _dt.timedelta(hours=min_age_hours)
        ).isoformat()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """SELECT id, symbol, direction, price, stop_pct, atr_15m, ts
               FROM scan_candidates
               WHERE labeled=0 AND ts <= ?
               ORDER BY ts ASC
               LIMIT ?""",
            (cutoff, limit),
        )
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def log_candidate_outcome(
    candidate_id: int,
    label_status: str,
    entry_ref_price: float,
    price_1h: float,
    price_4h: float,
    ret_1h_pct: float,
    ret_4h_pct: float,
    mfe_4h_pct: float,
    mae_4h_pct: float,
    hit_1r: int,
    hit_2r: int,
    hit_stop: int,
    best_exit_pct: float,
    worst_drawdown_pct: float,
    price_15m: float = 0.0,
    ret_15m_pct: float = 0.0,
    path_timing_evaluated: int = 0,
    time_to_05r_min: float | None = None,
    time_to_1r_min: float | None = None,
    time_to_2r_min: float | None = None,
    peak_r_4h: float | None = None,
) -> None:
    """Insert a candidate outcome row and mark the candidate as labeled=1."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO candidate_outcomes (
                candidate_id, label_status, entry_ref_price,
                price_1h, price_4h, ret_1h_pct, ret_4h_pct,
                mfe_4h_pct, mae_4h_pct, hit_1r, hit_2r, hit_stop,
                best_exit_pct, worst_drawdown_pct, labeled_at,
                price_15m, ret_15m_pct, path_timing_evaluated,
                time_to_05r_min, time_to_1r_min, time_to_2r_min, peak_r_4h
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id,
                label_status,
                entry_ref_price,
                price_1h,
                price_4h,
                ret_1h_pct,
                ret_4h_pct,
                mfe_4h_pct,
                mae_4h_pct,
                int(hit_1r),
                int(hit_2r),
                int(hit_stop),
                best_exit_pct,
                worst_drawdown_pct,
                _ts(),
                price_15m,
                ret_15m_pct,
                int(path_timing_evaluated),
                time_to_05r_min,
                time_to_1r_min,
                time_to_2r_min,
                peak_r_4h,
            ),
        )
        cur.execute("UPDATE scan_candidates SET labeled=1 WHERE id=?", (candidate_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass  # best-effort; never block the labeler loop


def get_candidate_journal_stats(days: int = 7) -> dict:
    """
    Return summary stats for the candidate journaling health check.

    Returns:
        total_candidates  — rows logged in last `days`
        labeled           — rows with labeled=1
        unlabeled_backlog — rows with labeled=0 that are old enough to label (>4h)
        decision_counts   — dict of decision → count
        last_ts           — ISO timestamp of most recent candidate row
    """
    import datetime as _dt

    defaults: dict = {
        "total_candidates": 0,
        "labeled": 0,
        "unlabeled_backlog": 0,
        "decision_counts": {},
        "last_ts": None,
    }
    try:
        cutoff_week = (datetime.now(pytz.utc) - _dt.timedelta(days=days)).isoformat()
        cutoff_label = (datetime.now(pytz.utc) - _dt.timedelta(hours=4)).isoformat()
        conn = _conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM scan_candidates WHERE ts >= ?", (cutoff_week,)
        )
        total = (cur.fetchone() or [0])[0]

        cur.execute(
            "SELECT COUNT(*) FROM scan_candidates WHERE ts >= ? AND labeled=1",
            (cutoff_week,),
        )
        labeled = (cur.fetchone() or [0])[0]

        cur.execute(
            "SELECT COUNT(*) FROM scan_candidates WHERE labeled=0 AND ts <= ?",
            (cutoff_label,),
        )
        backlog = (cur.fetchone() or [0])[0]

        cur.execute(
            "SELECT decision, COUNT(*) FROM scan_candidates WHERE ts >= ? GROUP BY decision",
            (cutoff_week,),
        )
        decision_counts = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute("SELECT MAX(ts) FROM scan_candidates")
        last_ts_row = cur.fetchone()
        last_ts = last_ts_row[0] if last_ts_row else None

        conn.close()
        return {
            "total_candidates": total,
            "labeled": labeled,
            "unlabeled_backlog": backlog,
            "decision_counts": decision_counts,
            "last_ts": last_ts,
        }
    except Exception:
        return defaults


def prune_old_candidates(
    labeled_days: int = 90,
    unlabeled_days: int = 30,
) -> dict:
    """
    Retention pruning for scan_candidates.

    Keeps the table bounded without destroying learning value:
    - labeled=1 rows older than `labeled_days` are pruned (default 90 days).
    - labeled=0 rows older than `unlabeled_days` are pruned as permanently stale
      (the labeler had ample time; data is unavailable).

    candidate_outcomes rows are never pruned here — they are linked by FK and
    are tiny compared to scan_candidates.  If scan_candidates rows are pruned,
    the orphaned candidate_outcomes rows remain harmless.

    Returns:
        {"pruned_labeled": int, "pruned_unlabeled": int, "remaining": int}
    """
    import datetime as _dt

    result = {"pruned_labeled": 0, "pruned_unlabeled": 0, "remaining": 0}
    try:
        now = datetime.now(pytz.utc)
        cutoff_labeled = (now - _dt.timedelta(days=labeled_days)).isoformat()
        cutoff_unlabeled = (now - _dt.timedelta(days=unlabeled_days)).isoformat()

        conn = _conn()
        cur = conn.cursor()

        cur.execute(
            "DELETE FROM scan_candidates WHERE labeled=1 AND ts < ?",
            (cutoff_labeled,),
        )
        result["pruned_labeled"] = cur.rowcount

        cur.execute(
            "DELETE FROM scan_candidates WHERE labeled=0 AND ts < ?",
            (cutoff_unlabeled,),
        )
        result["pruned_unlabeled"] = cur.rowcount

        cur.execute("SELECT COUNT(*) FROM scan_candidates")
        result["remaining"] = (cur.fetchone() or [0])[0]

        conn.commit()
        conn.close()
    except Exception:
        pass
    return result


# ─── Trade integrity (v14.0) ──────────────────────────────────────────────────

_VALID_TIERS = frozenset({"verified", "suspect", "quarantined", "excluded"})


def log_trade_integrity(
    close_order_id: str,
    tier: str,
    reason: str,
    source_check: str,
    trade_id: int = None,
    notes: str = None,
) -> bool:
    """
    Write an integrity record for a close-side trade.

    INSERT OR IGNORE so calling this multiple times is idempotent.
    Returns True if a new row was inserted, False if already present.
    """
    if tier not in _VALID_TIERS:
        tier = "suspect"
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT OR IGNORE INTO trade_integrity
               (trade_id, close_order_id, tier, reason, source_check, created_at, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (trade_id, close_order_id or "", tier, reason, source_check, _ts(), notes),
        )
        inserted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return inserted
    except Exception:
        return False


def get_integrity_tier(close_order_id: str) -> str:
    """
    Return the integrity tier for a close_order_id.
    Returns 'suspect' if no record exists (fail-closed).
    """
    if not close_order_id:
        return "suspect"
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT tier FROM trade_integrity WHERE close_order_id=? LIMIT 1",
            (close_order_id,),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else "suspect"
    except Exception:
        return "suspect"


def is_integrity_trusted(close_order_id: str) -> bool:
    """Return True only if the trade is 'verified'. Used as gate for Bayesian/Kelly updates."""
    tier = get_integrity_tier(close_order_id)
    return tier == "verified"


def get_integrity_summary() -> dict:
    """Return counts by tier and coverage percentage."""
    defaults = {
        "verified": 0,
        "suspect": 0,
        "quarantined": 0,
        "excluded": 0,
        "total_closes": 0,
        "coverage_pct": 0.0,
    }
    try:
        conn = _conn()
        cur = conn.cursor()
        # Count close-side trades (pnl_usd != 0)
        cur.execute("SELECT COUNT(*) FROM trades WHERE pnl_usd != 0")
        total_closes = (cur.fetchone() or [0])[0]
        cur.execute("SELECT tier, COUNT(*) FROM trade_integrity GROUP BY tier")
        tier_counts = {r[0]: r[1] for r in cur.fetchall()}
        conn.close()
        covered = sum(tier_counts.values())
        return {
            "verified": tier_counts.get("verified", 0),
            "suspect": tier_counts.get("suspect", 0),
            "quarantined": tier_counts.get("quarantined", 0),
            "excluded": tier_counts.get("excluded", 0),
            "total_closes": total_closes,
            "coverage_pct": round(covered / total_closes * 100, 1)
            if total_closes > 0
            else 0.0,
        }
    except Exception:
        return defaults


def bulk_backfill_integrity() -> dict:
    """
    Idempotent backfill: assigns a tier to every close-side trade not yet in trade_integrity.

    Rules applied in order:
      1. source contains 'contaminated'/'synthetic'/'replay'/'backtest'/'bootstrap' → excluded
      2. |pnl_usd| > 50% of ACCOUNT_SIZE → quarantined (suspect_pnl_magnitude)
      3. price <= 0 or qty <= 0 → quarantined (invalid_price_or_qty)
      4. has trade_attribution + trade_features → verified (lineage_complete)
      5. else → suspect (lineage_incomplete)

    INSERT OR IGNORE so running multiple times is safe.
    Returns counts by tier of newly inserted rows.
    """
    from config import ACCOUNT_SIZE

    result = {
        "verified": 0,
        "suspect": 0,
        "quarantined": 0,
        "excluded": 0,
        "skipped": 0,
    }
    try:
        conn = _conn()
        cur = conn.cursor()
        # Load all close-side trades not yet in trade_integrity
        cur.execute("""
            SELECT t.id, t.order_id, t.pnl_usd, t.price, t.qty, t.source, t.notes
            FROM trades t
            LEFT JOIN trade_integrity ti ON ti.close_order_id = COALESCE(t.order_id, CAST(t.id AS TEXT))
            WHERE t.pnl_usd != 0 AND ti.id IS NULL
        """)
        rows = cur.fetchall()
        half_account = ACCOUNT_SIZE * 0.5

        for row in rows:
            trade_id, order_id, pnl_usd, price, qty, source, notes = row
            close_key = order_id or str(trade_id)
            src = (source or "").lower()
            note_str = (notes or "").lower()

            # Rule 1: contaminated / synthetic / replay
            if any(
                tag in src
                for tag in (
                    "contaminated",
                    "synthetic",
                    "replay",
                    "backtest",
                    "bootstrap",
                )
            ):
                tier, reason = "excluded", f"source_tag:{source}"
            elif any(
                tag in note_str for tag in ("contaminated", "synthetic", "replay")
            ):
                tier, reason = "excluded", "notes_tag:contaminated_or_synthetic"
            # Rule 2: suspect PnL magnitude
            elif abs(pnl_usd or 0) > half_account:
                tier, reason = "quarantined", "suspect_pnl_magnitude"
            # Rule 3: invalid price / qty
            elif (price or 0) <= 0 or (qty or 0) <= 0:
                tier, reason = "quarantined", "invalid_price_or_qty"
            else:
                # Rule 4: lineage check
                cur2 = conn.cursor()
                cur2.execute(
                    "SELECT id FROM trade_attribution WHERE trade_ref=? LIMIT 1",
                    (close_key,),
                )
                has_attr = cur2.fetchone() is not None
                cur2.execute(
                    "SELECT id FROM trade_features WHERE trade_id=? LIMIT 1",
                    (trade_id,),
                )
                has_features = cur2.fetchone() is not None
                if has_attr and has_features:
                    tier, reason = "verified", "lineage_complete"
                else:
                    tier, reason = "suspect", "lineage_incomplete"

            try:
                cur.execute(
                    """INSERT OR IGNORE INTO trade_integrity
                       (trade_id, close_order_id, tier, reason, source_check, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (trade_id, close_key, tier, reason, "bulk_backfill", _ts()),
                )
                if cur.rowcount > 0:
                    result[tier] = result.get(tier, 0) + 1
                else:
                    result["skipped"] = result.get("skipped", 0) + 1
            except Exception:
                pass

        conn.commit()
        conn.close()
    except Exception:
        pass
    return result


# ─── Exit evaluations (v14.0) ─────────────────────────────────────────────────


def log_exit_evaluation(
    close_order_id: str,
    exit_type: str,
    actual_exit_price: float,
    actual_exit_pct: float,
    optimal_exit_price: float = None,
    opportunity_loss_pct: float = None,
    stop_overshoot_pct: float = 0.0,
    regime: str = "",
    composite_score_at_exit: float = 0.0,
    mfe_at_exit: float = 0.0,
    mae_at_exit: float = 0.0,
    path_label: str = "",
    trade_id: str = None,
) -> bool:
    """
    Record an exit quality evaluation. INSERT OR IGNORE for idempotency.
    Returns True if inserted.
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT OR IGNORE INTO exit_evaluations (
                trade_id, close_order_id, exit_type,
                actual_exit_price, actual_exit_pct,
                optimal_exit_price, opportunity_loss_pct,
                stop_overshoot_pct, regime,
                composite_score_at_exit, mfe_at_exit, mae_at_exit,
                path_label, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(trade_id) if trade_id else None,
                close_order_id or "",
                exit_type,
                actual_exit_price,
                actual_exit_pct,
                optimal_exit_price,
                opportunity_loss_pct,
                stop_overshoot_pct,
                regime,
                composite_score_at_exit,
                mfe_at_exit,
                mae_at_exit,
                path_label,
                _ts(),
            ),
        )
        inserted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return inserted
    except Exception:
        return False


def get_exit_quality_summary(
    strategy: str = None,
    regime: str = None,
    exit_type: str = None,
    days: int = 30,
) -> dict:
    """Aggregate exit quality metrics from exit_evaluations."""
    import datetime as _dt

    defaults = {
        "count": 0,
        "avg_opportunity_loss_pct": 0.0,
        "avg_stop_overshoot_pct": 0.0,
        "path_label_counts": {},
        "exit_type_counts": {},
    }
    try:
        cutoff = (datetime.now(pytz.utc) - _dt.timedelta(days=days)).isoformat()
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """SELECT exit_type, opportunity_loss_pct, stop_overshoot_pct, path_label
               FROM exit_evaluations
               WHERE created_at >= ?""",
            (cutoff,),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return defaults
        count = len(rows)
        opp_losses = [r[1] for r in rows if r[1] is not None]
        overshots = [r[2] for r in rows if r[2] is not None]
        path_labels = {}
        exit_types = {}
        for r in rows:
            path_labels[r[3] or "unknown"] = path_labels.get(r[3] or "unknown", 0) + 1
            exit_types[r[0] or "unknown"] = exit_types.get(r[0] or "unknown", 0) + 1
        return {
            "count": count,
            "avg_opportunity_loss_pct": sum(opp_losses) / len(opp_losses)
            if opp_losses
            else 0.0,
            "avg_stop_overshoot_pct": sum(overshots) / len(overshots)
            if overshots
            else 0.0,
            "path_label_counts": path_labels,
            "exit_type_counts": exit_types,
        }
    except Exception:
        return defaults


# ─── Challenger / promotion state (v18.16) ─────────────────────────────────────


def upsert_challenger_state(
    strategy: str,
    run_id: str,
    promotion_tier: str,
    criteria_met_json: str = None,
    notes: str = None,
) -> None:
    """Insert or update a challenger promotion state row."""
    try:
        conn = _conn()
        cur = conn.cursor()
        now = _ts()
        cur.execute(
            """INSERT INTO challenger_state
               (strategy, run_id, promotion_tier, criteria_met_json, notes, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(strategy, run_id) DO UPDATE SET
                 promotion_tier=excluded.promotion_tier,
                 criteria_met_json=excluded.criteria_met_json,
                 notes=excluded.notes""",
            (strategy, run_id, promotion_tier, criteria_met_json, notes, now),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_promotion_state() -> list:
    """Return all challenger_state rows ordered by created_at desc."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM challenger_state ORDER BY created_at DESC LIMIT 50")
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ─── Position persistence ─────────────────────────────────────────────────────


def persist_position(
    symbol,
    strategy,
    qty,
    entry,
    stop,
    target,
    high_since_entry,
    ts_entry,
    direction="LONG",
    entry_reason="",
    low_since_entry=None,
    atr_at_entry=0.0,
    composite_score=0.0,
    trailing_active=False,
    trailing_stop_price=0.0,
    scale_33_done=False,
    scale_66_done=False,
    leverage=3,
    spot_regime="",
    setup_family="",
    setup_score=0.0,
    setup_preference="",
    tf_5m_state="",
    tf_30m_state="",
    tf_4h_state="",
    tf_1d_state="",
    structural_confirms="",
    execution_route="",
    cooldown_until="",
    microstructure_veto="",
    stop_model_version="",
    target_model_version="",
    target_r=0.0,
    trail_arm_r=0.0,
    risk_dollars=0.0,
    entry_fee_usd=0.0,
    exit_reason="",
    entry_trade_id=0,
    entry_order_id="",
    entry_feature_snapshot_id=0,
    tv_profile_name="",
    tv_signal_bias="",
    tv_signal_ts="",
    tv_signal_age_sec=0.0,
    tv_indicator_name="",
    tv_signal_strength="",
    candidate_id=0,
    candidate_scan_id="",
    raw_scanner_symbol="",
    base_asset="",
    tv_veto_state="",
    paper=0,
) -> None:
    """Write open position to DB so restarts can recover it (including exit state)."""
    _low = low_since_entry if low_since_entry is not None else entry
    values = (
        symbol,
        strategy,
        qty,
        entry,
        stop,
        target,
        high_since_entry,
        _low,
        ts_entry,
        paper,
        direction,
        entry_reason or "",
        float(atr_at_entry),
        float(composite_score),
        int(trailing_active),
        float(trailing_stop_price),
        int(scale_33_done),
        int(scale_66_done),
        int(leverage),
        str(spot_regime or ""),
        str(setup_family or ""),
        float(setup_score or 0.0),
        str(setup_preference or ""),
        str(tf_5m_state or ""),
        str(tf_30m_state or ""),
        str(tf_4h_state or ""),
        str(tf_1d_state or ""),
        str(structural_confirms or ""),
        str(execution_route or ""),
        str(cooldown_until or ""),
        str(microstructure_veto or ""),
        str(stop_model_version or ""),
        str(target_model_version or ""),
        float(target_r or 0.0),
        float(trail_arm_r or 0.0),
        float(risk_dollars or 0.0),
        float(entry_fee_usd or 0.0),
        str(exit_reason or ""),
        int(entry_trade_id or 0),
        str(entry_order_id or ""),
        int(entry_feature_snapshot_id or 0),
        str(tv_profile_name or ""),
        str(tv_signal_bias or ""),
        str(tv_signal_ts or ""),
        float(tv_signal_age_sec or 0.0),
        str(tv_indicator_name or ""),
        str(tv_signal_strength or ""),
        int(candidate_id or 0),
        str(candidate_scan_id or ""),
        str(raw_scanner_symbol or ""),
        str(base_asset or ""),
        str(tv_veto_state or ""),
    )
    conn = _conn()
    placeholders = ",".join(["?"] * len(values))
    conn.cursor().execute(
        f"""INSERT OR REPLACE INTO open_positions
        (symbol,strategy,qty,entry,stop,target,high_since_entry,low_since_entry,ts_entry,paper,
         direction,entry_reason,atr_at_entry,composite_score,
         trailing_active,trailing_stop_price,scale_33_done,scale_66_done,leverage,
         spot_regime,setup_family,setup_score,setup_preference,
         tf_5m_state,tf_30m_state,tf_4h_state,tf_1d_state,
         structural_confirms,execution_route,cooldown_until,microstructure_veto,
         stop_model_version,target_model_version,target_r,trail_arm_r,risk_dollars,
         entry_fee_usd,exit_reason,entry_trade_id,entry_order_id,entry_feature_snapshot_id,
         tv_profile_name,tv_signal_bias,tv_signal_ts,tv_signal_age_sec,
         tv_indicator_name,tv_signal_strength,candidate_id,candidate_scan_id,
         raw_scanner_symbol,base_asset,tv_veto_state)
        VALUES ({placeholders})""",
        values,
    )
    conn.commit()
    conn.close()


def delete_position(symbol, strategy, paper: int = 0) -> None:
    conn = _conn()
    conn.execute(
        "DELETE FROM open_positions WHERE symbol=? AND strategy=? AND paper=?",
        (symbol, strategy, paper),
    )
    conn.commit()
    conn.close()


# ─── v18.19 sticky regime state ─────────────────────────────────────────────


def load_spot_regime_state(symbol: str) -> Optional[str]:
    """Return last persisted regime for symbol, or None if no prior entry."""
    if not symbol:
        return None
    sym = str(symbol).upper()
    try:
        conn = _conn()
        cur = conn.execute(
            "SELECT last_regime FROM spot_regime_state WHERE symbol=?", (sym,)
        )
        row = cur.fetchone()
        conn.close()
        return str(row["last_regime"]) if row else None
    except Exception:
        return None


def save_spot_regime_state(symbol: str, regime: str) -> None:
    if not symbol or not regime:
        return
    sym = str(symbol).upper()
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO spot_regime_state(symbol,last_regime,ts) VALUES(?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET last_regime=excluded.last_regime, ts=excluded.ts",
            (sym, str(regime).upper(), int(time.time())),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─── v18.19 per-symbol cooldown state ───────────────────────────────────────


def load_spot_cooldown_state(symbol: str) -> Optional[int]:
    """Return last_exit_ts (unix seconds) for symbol, or None."""
    if not symbol:
        return None
    sym = str(symbol).upper()
    try:
        conn = _conn()
        cur = conn.execute(
            "SELECT last_exit_ts FROM spot_cooldown_state WHERE symbol=?", (sym,)
        )
        row = cur.fetchone()
        conn.close()
        return int(row["last_exit_ts"]) if row else None
    except Exception:
        return None


def save_spot_cooldown_state(symbol: str, last_exit_ts: Optional[int] = None) -> None:
    if not symbol:
        return
    sym = str(symbol).upper()
    ts = int(last_exit_ts if last_exit_ts is not None else time.time())
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO spot_cooldown_state(symbol,last_exit_ts) VALUES(?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET last_exit_ts=excluded.last_exit_ts",
            (sym, ts),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─── v18.19 sell-failure halt helpers ────────────────────────────────────────


def increment_sell_failure(symbol: str, strategy: str, paper: int = 0) -> int:
    """Bump sell_failure_count for the row; return the new value."""
    sym = str(symbol).upper()
    try:
        conn = _conn()
        conn.execute(
            "UPDATE open_positions SET sell_failure_count = COALESCE(sell_failure_count,0) + 1 "
            "WHERE symbol=? AND strategy=? AND paper=?",
            (sym, strategy, paper),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT sell_failure_count FROM open_positions WHERE symbol=? AND strategy=? AND paper=?",
            (sym, strategy, paper),
        )
        row = cur.fetchone()
        conn.close()
        return int(row["sell_failure_count"]) if row else 0
    except Exception:
        return 0


def mark_sell_blocked(symbol: str, strategy: str, reason: str, paper: int = 0) -> None:
    sym = str(symbol).upper()
    try:
        conn = _conn()
        conn.execute(
            "UPDATE open_positions SET sell_blocked=1, sell_blocked_reason=? "
            "WHERE symbol=? AND strategy=? AND paper=?",
            (str(reason or ""), sym, strategy, paper),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def clear_sell_failure(symbol: str, strategy: str, paper: int = 0) -> None:
    sym = str(symbol).upper()
    try:
        conn = _conn()
        conn.execute(
            "UPDATE open_positions SET sell_failure_count=0, sell_blocked=0, sell_blocked_reason='' "
            "WHERE symbol=? AND strategy=? AND paper=?",
            (sym, strategy, paper),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def load_open_positions(paper: int = 0) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM open_positions WHERE paper=?", (paper,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ─── Query helpers ────────────────────────────────────────────────────────────


def get_todays_trades() -> list:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM trades WHERE ts LIKE ? AND paper=0 ORDER BY ts DESC",
        (f"{today}%",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_todays_signals() -> list:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM signals WHERE ts LIKE ? ORDER BY ts DESC LIMIT 50",
        (f"{today}%",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_debates(limit=10) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM debate_results ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_todays_pnl() -> float:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(pnl_usd),0) FROM trades WHERE ts LIKE ? AND paper=0", (f"{today}%",)
    )
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_todays_fees() -> float:
    """Returns total cost today: trading fees + Gemini API costs."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(fee_usd),0) FROM trades WHERE ts LIKE ? AND paper=0""", (f"{today}%",)
    )
    trade_fees = float(cur.fetchone()[0])
    cur.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts LIKE ?",
        (f"{today}%",),
    )
    api_fees = float(cur.fetchone()[0])
    conn.close()
    return trade_fees + api_fees


def get_todays_trade_fees() -> float:
    """Trading exchange fees only (excludes API costs)."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(fee_usd),0) FROM trades WHERE ts LIKE ? AND paper=0""", (f"{today}%",)
    )
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_todays_api_cost() -> float:
    """Gemini API cost today only."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts LIKE ?",
        (f"{today}%",),
    )
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_daily_trade_count(strategy) -> int:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM trades WHERE ts LIKE ? AND strategy=? AND paper=0 AND action='BUY'",
        (f"{today}%", strategy),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_win_rate(strategy=None, lookback_days=14) -> float:
    # Use pnl_usd != 0 so SHORT exits (logged as action='BUY') are counted.
    conn = _conn()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE strategy=? AND paper=0 AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
            (strategy, lookback_days * 5),
        )
    else:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=0 AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
            (lookback_days * 5),
        )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return 0.0
    wins = sum(1 for r in rows if r[0] > 0)
    return wins / len(rows)


def get_monthly_api_cost() -> float:
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE))
    month_start = today.strftime("%Y-%m-01")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE ts >= ?", (month_start,)
    )
    val = cur.fetchone()[0]
    conn.close()
    return float(val)


def get_all_time_stats() -> dict:
    # Filter on pnl_usd != 0 (not action='SELL') so SHORT exits logged as
    # action='BUY' with non-zero pnl are counted correctly.
    # Respects TRADE_SESSION_START so pre-overhaul trades don't skew metrics.
    from config import TRADE_SESSION_START

    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*) as total,
        SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) as losses,
        SUM(pnl_usd) as total_pnl,
        MAX(pnl_usd) as best_trade,
        MIN(pnl_usd) as worst_trade
        FROM trades WHERE paper=0 AND pnl_usd != 0
        AND ts >= ?""",
        (TRADE_SESSION_START,),
    )
    row = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(SUM(fee_usd), 0) FROM trades WHERE paper=0 AND ts >= ?",
        (TRADE_SESSION_START,),
    )
    total_fees = float(cur.fetchone()[0])
    conn.close()
    if not row or not row[0]:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0,
            "total_fees": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "win_rate": 0,
        }
    total = row[0] or 0
    wins = row[1] or 0
    return {
        "total": total,
        "wins": wins,
        "losses": row[2] or 0,
        "total_pnl": row[3] or 0,
        "total_fees": total_fees,
        "best_trade": row[4] or 0,
        "worst_trade": row[5] or 0,
        "win_rate": wins / total if total > 0 else 0,
    }


def get_kelly_stats(strategy: str = None, window: int = 50) -> dict:
    """
    Compute rolling Kelly fraction from the last `window` closed trades.

    Returns:
      kelly_full  — f* = p - q/b  (raw Kelly fraction, can be negative)
      kelly_25pct — 25% fractional Kelly (use this for sizing)
      win_rate    — win rate in the window
      avg_win     — avg winning trade $
      avg_loss    — avg losing trade $ (absolute value)
      b_ratio     — avg_win / avg_loss (payoff ratio)
      n_trades    — number of trades in window
    """
    conn = _conn()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=0 AND strategy=? AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT ?",
            (strategy, window),
        )
    else:
        cur.execute(
            "SELECT pnl_usd FROM trades WHERE paper=0 AND pnl_usd != 0 "
            "ORDER BY ts DESC LIMIT ?",
            (window,),
        )
    rows = [r[0] for r in cur.fetchall()]
    conn.close()

    _default = {
        "kelly_full": 0.0,
        "kelly_25pct": 0.0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "b_ratio": 1.0,
        "n_trades": 0,
    }
    if len(rows) < 10:  # need at least 10 trades for meaningful Kelly
        return _default

    wins = [r for r in rows if r > 0]
    losses = [r for r in rows if r < 0]
    if not wins or not losses:
        return _default

    p = len(wins) / len(rows)
    q = 1.0 - p
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    b = avg_win / avg_loss if avg_loss > 0 else 1.0

    kelly_full = p - q / b
    kelly_25pct = max(0.0, kelly_full * 0.25)  # floor at 0 (never negative size)

    return {
        "kelly_full": round(kelly_full, 4),
        "kelly_25pct": round(kelly_25pct, 4),
        "win_rate": round(p, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "b_ratio": round(b, 4),
        "n_trades": len(rows),
    }


def get_recent_trades(limit=20) -> list:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM trades WHERE paper=0 ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_events(limit=20, level=None) -> list:
    conn = _conn()
    cur = conn.cursor()
    if level:
        cur.execute(
            "SELECT * FROM system_events WHERE level=? ORDER BY ts DESC LIMIT ?",
            (level, limit),
        )
    else:
        cur.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_today_stats() -> dict:
    """Today-only stats: closed trades (pnl_usd != 0), wins, losses, fees, net P&L."""
    today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    # Closed trade counts and gross P&L — only rows with actual P&L
    cur.execute(
        """SELECT
        COUNT(*) as total,
        SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) as losses,
        COALESCE(SUM(pnl_usd), 0) as gross_pnl
        FROM trades WHERE ts LIKE ? AND paper=0 AND pnl_usd != 0""",
        (f"{today}%",),
    )
    row = cur.fetchone()
    # Fees across ALL trades today (BUY + SELL both charged fees)
    cur.execute(
        "SELECT COALESCE(SUM(fee_usd), 0) FROM trades WHERE ts LIKE ? AND paper=0""", (f"{today}%",)
    )
    fees = float(cur.fetchone()[0])
    conn.close()
    total = row[0] or 0
    wins = row[1] or 0
    gross = float(row[3] or 0.0)
    return {
        "total": total,
        "wins": wins,
        "losses": row[2] or 0,
        "win_rate": wins / total if total > 0 else 0.0,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": gross - fees,
    }


def get_tax_summary() -> dict:
    """
    Pull all realized P&L data for tax calculations.
    Separates gains from losses, groups by asset class and year.
    Uses paper=False by default — live trades are what matter for taxes.
    """
    conn = _conn()
    cur = conn.cursor()

    # All closed trades with P&L — both gains and losses
    cur.execute(
        """
        SELECT ts, strategy, symbol, pnl_usd, fee_usd, value_usd
        FROM trades
        WHERE paper=0 AND pnl_usd != 0
        ORDER BY ts ASC
    """
    )
    rows = [dict(r) for r in cur.fetchall()]

    # Annual breakdown
    annual: dict = {}
    for r in rows:
        year = r["ts"][:4]
        if year not in annual:
            annual[year] = {
                "gains": 0.0,
                "losses": 0.0,
                "fees": 0.0,
                "trades": 0,
                "crypto": 0.0,
                "equity": 0.0,
            }
        pnl = float(r["pnl_usd"] or 0)
        fee = float(r["fee_usd"] or 0)
        annual[year]["trades"] += 1
        annual[year]["fees"] += fee
        if pnl > 0:
            annual[year]["gains"] += pnl
        else:
            annual[year]["losses"] += pnl
        if "crypto" in r.get("strategy", ""):
            annual[year]["crypto"] += pnl
        else:
            annual[year]["equity"] += pnl

    total_gains = sum(v["gains"] for v in annual.values())
    total_losses = sum(v["losses"] for v in annual.values())
    total_fees = sum(v["fees"] for v in annual.values())
    net_pnl = total_gains + total_losses  # losses are negative

    conn.close()
    return {
        "rows": rows,
        "annual": annual,
        "total_gains": total_gains,
        "total_losses": total_losses,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "total_trades": len(rows),
    }


def get_recent_tv_signal(symbol: str, max_age_seconds: int = 300) -> dict | None:
    """Return the most recent TradingView webhook signal for `symbol` if it arrived
    within `max_age_seconds`.  Returns None if no fresh signal exists.

    The returned dict has keys: symbol, action, price, tf_min, signal, ts
    """
    from datetime import timezone

    try:
        recent = get_recent_tv_signals(max_age_seconds=max_age_seconds, symbol=symbol)
        if recent:
            return recent[0]
    except Exception:
        pass

    try:
        import json
        conn = _conn()
        cur = conn.cursor()
        # Pull last 20 tradingview events and find a match (small result set, avoids LIKE index miss)
        cur.execute(
            "SELECT message, ts FROM system_events WHERE source='tradingview' ORDER BY ts DESC LIMIT 20"
        )
        rows = cur.fetchall()
        conn.close()
        now = datetime.now(timezone.utc)
        for msg, ts_str in rows:
            try:
                data = json.loads(msg)
            except Exception:
                continue
            # Check symbol match
            if data.get("symbol", "").upper() != symbol.upper():
                continue
            # Check age
            ts_dt = datetime.fromisoformat(data.get("ts", ts_str))
            if not ts_dt.tzinfo:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            age = (now - ts_dt).total_seconds()
            if age <= max_age_seconds:
                return data
        return None
    except Exception:
        return None


def log_tv_signal(
    *,
    symbol: str,
    action_raw: str,
    direction: str,
    htf_bias: str,
    price: float = 0.0,
    tf_min: str = "",
    indicator_name: str = "",
    profile_name: str = "",
    strength: str = "",
    signal_desc: str = "",
    secret_validated: bool = False,
    raw_payload_json: str = "",
) -> int:
    import json

    ts = _ts()
    payload = {
        "symbol": str(symbol or "").upper(),
        "action_raw": str(action_raw or "").lower(),
        "direction": str(direction or "").upper(),
        "htf_bias": str(htf_bias or "").upper(),
        "price": float(price or 0.0),
        "tf_min": str(tf_min or ""),
        "indicator_name": str(indicator_name or ""),
        "profile_name": str(profile_name or ""),
        "strength": str(strength or ""),
        "signal": str(signal_desc or ""),
        "secret_validated": bool(secret_validated),
        "ts": ts,
    }
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO tv_signals
           (ts,symbol,action_raw,direction,htf_bias,price,tf_min,indicator_name,
            profile_name,strength,signal_desc,secret_validated,raw_payload_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ts,
            payload["symbol"],
            payload["action_raw"],
            payload["direction"],
            payload["htf_bias"],
            payload["price"],
            payload["tf_min"],
            payload["indicator_name"],
            payload["profile_name"],
            payload["strength"],
            payload["signal"],
            1 if secret_validated else 0,
            raw_payload_json,
        ),
    )
    cur.execute(
        "INSERT INTO system_events (ts, level, source, message) VALUES (?, ?, ?, ?)",
        (ts, "INFO", "tradingview", json.dumps(payload)),
    )
    row_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return row_id


def get_recent_tv_signals(
    max_age_seconds: int = 300,
    *,
    symbol: str = "",
) -> list[dict]:
    from datetime import datetime, timezone

    try:
        conn = _conn()
        cur = conn.cursor()
        if symbol:
            cur.execute(
                """
                SELECT ts, symbol, action_raw, direction, htf_bias, price, tf_min,
                       indicator_name, profile_name, strength, signal_desc, secret_validated
                FROM tv_signals
                WHERE symbol=?
                ORDER BY ts DESC
                LIMIT 20
                """,
                (str(symbol or "").upper(),),
            )
        else:
            cur.execute(
                """
                SELECT ts, symbol, action_raw, direction, htf_bias, price, tf_min,
                       indicator_name, profile_name, strength, signal_desc, secret_validated
                FROM tv_signals
                ORDER BY ts DESC
                LIMIT 50
                """
            )
        rows = cur.fetchall()
        conn.close()
        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for row in rows:
            ts_str = str(row[0] or "")
            try:
                ts_dt = datetime.fromisoformat(ts_str)
                if not ts_dt.tzinfo:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                age = (now - ts_dt).total_seconds()
            except Exception:
                age = max_age_seconds + 1
            if age > max_age_seconds:
                continue
            out.append(
                {
                    "ts": ts_str,
                    "symbol": str(row[1] or "").upper(),
                    "action_raw": str(row[2] or "").lower(),
                    "direction": str(row[3] or "").upper(),
                    "htf_bias": str(row[4] or "").upper(),
                    "price": float(row[5] or 0.0),
                    "tf_min": str(row[6] or ""),
                    "indicator_name": str(row[7] or ""),
                    "profile_name": str(row[8] or ""),
                    "strength": str(row[9] or ""),
                    "signal": str(row[10] or ""),
                    "secret_validated": bool(row[11]),
                    "age_seconds": age,
                }
            )
        return out
    except Exception:
        return []


def get_recent_notifications(limit=30) -> list:
    """Return notifications written by the alert system (source='notify')."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM system_events WHERE source='notify' ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _parse_scan_msg(msg: str, ts: str) -> dict:
    """Parse a scan_feed log message into structured fields for dashboard display."""
    import re

    out = {
        "ts": ts,
        "symbol": None,
        "action": "HOLD",
        "confidence": 0.0,
        "strategy": "crypto",
        "message": (msg or "")[:120],
    }
    if not msg:
        return out
    # Extract lane and symbol: "[crypto] BTC-USDC ..." or "[perp] BTCUSDT ..."
    m = re.match(r"\[(crypto|perp|equity|futures|deriv)\]\s+(\S+)", msg)
    if m:
        out["strategy"] = m.group(1)
        out["symbol"] = m.group(2).upper()
    # Extract confidence: "conf=75%" pattern
    c = re.search(r"conf=(\d+)%", msg)
    if c:
        out["confidence"] = float(c.group(1)) / 100
    # Determine action from message content
    msg_l = msg.lower()
    if "calling debate" in msg_l or "✅ buy" in msg or "near_miss" in msg_l:
        out["action"] = "BUY"
    # HOLD is the default — skip debate / abort / veto / block all stay HOLD
    return out


def get_scan_feed(limit=40) -> list:
    """Return recent scan activity log entries (source='scan_feed'), newest first.

    Returns structured dicts with symbol/action/confidence/strategy fields
    (parsed from the human-readable log message) so dashboard components can
    use them directly without regex parsing on the caller side.
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT ts, message FROM system_events WHERE source='scan_feed' ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    rows = [_parse_scan_msg(r[1], r[0]) for r in cur.fetchall()]
    conn.close()
    return rows


def get_performance_attribution(lookback_days=30) -> dict:
    """
    Break down P&L, win rate, and trade count by strategy.
    Returns: {strategy_name: {total, wins, losses, win_rate, total_pnl, avg_pnl}}
    """
    from datetime import timedelta

    cutoff = (
        datetime.now(pytz.timezone(MARKET_TIMEZONE)) - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT strategy,
               COUNT(*)                                    AS total,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(pnl_usd), 0)                  AS total_pnl,
               COALESCE(AVG(pnl_usd), 0)                  AS avg_pnl
        FROM trades
        WHERE paper=0 AND pnl_usd != 0 AND ts >= ?
        GROUP BY strategy
        ORDER BY total_pnl DESC
    """,
        (cutoff,),
    )
    rows = cur.fetchall()
    conn.close()
    result = {}
    for r in rows:
        total = r[1] or 0
        wins = r[2] or 0
        result[r[0]] = {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total if total > 0 else 0.0,
            "total_pnl": float(r[3]),
            "avg_pnl": float(r[4]),
        }
    return result


def get_strategy_consecutive_losses(strategy: str) -> int:
    """Return the current consecutive loss streak for a strategy (most recent trades first)."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT pnl_usd FROM trades
        WHERE strategy=? AND paper=0 AND pnl_usd != 0
        ORDER BY ts DESC LIMIT 20""",
        (strategy,),
    )
    rows = cur.fetchall()
    conn.close()
    streak = 0
    for r in rows:
        if r[0] < 0:
            streak += 1
        else:
            break
    return streak


def get_trade_quality_stats(lookback: int = 20) -> dict:
    """
    Compute Trade Quality scorecard from the last N closed trades in trade_attribution.

    Returns
    -------
    dict with keys:
      entry_timing    : 0-10  (10 = zero adverse excursion before price moved in our favour)
      exit_efficiency : 0-10  (10 = exited at peak MFE)
      thesis_hit_rate : 0-1   (fraction where MFE >= 1.5%, i.e. cleared the crypto stop)
      exit_type_dist  : dict  {exit_type: count}
      avg_super_score : float (avg of non-zero super_score values)
      n               : int   (actual row count used)
    """
    _defaults = {
        "entry_timing": 5.0,
        "exit_efficiency": 5.0,
        "thesis_hit_rate": 0.0,
        "exit_type_dist": {},
        "avg_super_score": 0.0,
        "n": 0,
    }
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT mae_pct, mfe_pct, pnl_pct, exit_type, won, super_score, ml_p_win
            FROM trade_attribution
            ORDER BY entry_ts DESC
            LIMIT ?
        """,
            (lookback,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception:
        return _defaults

    if not rows:
        return _defaults

    n = len(rows)

    # entry_timing: 10 * (1 - avg(min(|mae_pct| / 0.015, 1.0)))
    mae_vals = [abs(float(r.get("mae_pct") or 0)) for r in rows]
    avg_mae_ratio = sum(min(v / 0.015, 1.0) for v in mae_vals) / n
    entry_timing = round(10.0 * (1.0 - avg_mae_ratio), 2)

    # exit_efficiency: 10 * avg(pnl_pct / mfe_pct) where mfe_pct > 0.001
    eff_pairs = [
        (float(r.get("pnl_pct") or 0), float(r.get("mfe_pct") or 0))
        for r in rows
        if float(r.get("mfe_pct") or 0) > 0.001
    ]
    if eff_pairs:
        ratios = [min(pnl / mfe, 1.0) for pnl, mfe in eff_pairs]  # cap at 1
        exit_efficiency = round(10.0 * (sum(ratios) / len(ratios)), 2)
    else:
        exit_efficiency = 5.0

    # thesis_hit_rate: fraction where mfe_pct >= 0.015
    thesis_hits = sum(1 for r in rows if float(r.get("mfe_pct") or 0) >= 0.015)
    thesis_hit_rate = round(thesis_hits / n, 4)

    # exit_type_dist
    from collections import Counter

    exit_type_dist = dict(Counter(r.get("exit_type") or "unknown" for r in rows))

    # avg_super_score — exclude rows where super_score == 0 (old rows before this column existed)
    scored_rows = [
        float(r.get("super_score") or 0)
        for r in rows
        if float(r.get("super_score") or 0) > 0
    ]
    avg_super_score = (
        round(sum(scored_rows) / len(scored_rows), 2) if scored_rows else 0.0
    )

    return {
        "entry_timing": max(0.0, min(10.0, entry_timing)),
        "exit_efficiency": max(0.0, min(10.0, exit_efficiency)),
        "thesis_hit_rate": thesis_hit_rate,
        "exit_type_dist": exit_type_dist,
        "avg_super_score": avg_super_score,
        "n": n,
    }


def get_open_position_health() -> list:
    """
    Return current open positions with full health metadata.

    Returns
    -------
    list of dicts: symbol, strategy, entry, stop, target,
                   high_since_entry, low_since_entry, ts_entry, qty, direction
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, strategy, qty, entry, stop, target, "
            "high_since_entry, low_since_entry, ts_entry, direction "
            "FROM open_positions WHERE paper=0"
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append(
                {
                    "symbol": r[0],
                    "strategy": r[1],
                    "qty": r[2],
                    "entry": r[3],
                    "stop": r[4],
                    "target": r[5],
                    "high_since_entry": r[6],
                    "low_since_entry": r[7],
                    "ts_entry": r[8],
                    "direction": r[9] or "LONG",
                }
            )
        return result
    except Exception:
        return []


def get_intelligence_log(limit: int = 30) -> dict:
    """
    Pull together all self-improvement events for the SELF-LEARNING LOG panel.

    Returns:
        meta_analyses:   list of meta-analysis runs (insight, WR, trades, timestamp)
        recommendations: list of active/recent signal weight recommendations
        agent_accuracy:  list of per-agent accuracy stats
        ml_events:       list of ML retrain events from system_events
        signal_shifts:   top signals ranked by Bayesian pts (current state)
    """
    try:
        conn = _conn()
        cur = conn.cursor()

        # Meta-analysis runs (what Gemini learned from recent trades)
        try:
            cur.execute(
                """
                SELECT created_at, trades_analyzed, win_rate, key_insight, patterns_found, recs_count
                FROM meta_analysis_log
                ORDER BY created_at DESC LIMIT ?
            """,
                (limit,),
            )
            meta_analyses = [
                dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()
            ]
        except Exception:
            meta_analyses = []

        # Active + recent signal weight recommendations
        try:
            cur.execute(
                """
                SELECT signal_name, regime, weight_delta, reasoning, pattern, confidence, created_at, applied
                FROM meta_recommendations
                ORDER BY created_at DESC LIMIT ?
            """,
                (limit,),
            )
            recommendations = [
                dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()
            ]
        except Exception:
            recommendations = []

        # Agent accuracy
        try:
            cur.execute("""
                SELECT agent_name, regime, accuracy, total_assessed, votes_buy, votes_hold, last_updated
                FROM agent_stats
                WHERE regime = 'any'
                ORDER BY total_assessed DESC
            """)
            agent_accuracy = [
                dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()
            ]
        except Exception:
            agent_accuracy = []

        # ML retrain events
        try:
            cur.execute(
                """
                SELECT ts, message FROM system_events
                WHERE (source='ml_trainer' OR message LIKE '%retrain%' OR message LIKE '%ml_model%'
                       OR message LIKE '%ML model%' OR message LIKE '%Background retrain%')
                ORDER BY ts DESC LIMIT ?
            """,
                (limit,),
            )
            ml_events = [{"ts": r[0], "message": r[1]} for r in cur.fetchall()]
        except Exception:
            ml_events = []

        # Top signals by current Bayesian pts
        try:
            cur.execute("""
                SELECT signal_name, regime, fires, wins, losses, win_rate, bayesian_pts, last_updated
                FROM signal_stats
                WHERE fires >= 5
                ORDER BY bayesian_pts DESC LIMIT 15
            """)
            signal_shifts = [
                dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()
            ]
        except Exception:
            signal_shifts = []

        conn.close()
        return {
            "meta_analyses": meta_analyses,
            "recommendations": recommendations,
            "agent_accuracy": agent_accuracy,
            "ml_events": ml_events,
            "signal_shifts": signal_shifts,
        }
    except Exception:
        return {
            "meta_analyses": [],
            "recommendations": [],
            "agent_accuracy": [],
            "ml_events": [],
            "signal_shifts": [],
        }


def _csv_append(
    ts,
    strategy,
    broker,
    symbol,
    action,
    order_type,
    qty,
    price,
    value_usd,
    fee_usd,
    pnl_usd,
    order_id,
    notes,
):
    os.makedirs(CSV_LOG_DIR, exist_ok=True)
    date_str = ts[:10]
    path = os.path.join(CSV_LOG_DIR, f"trades_{date_str}.csv")
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(
                [
                    "ts",
                    "strategy",
                    "broker",
                    "symbol",
                    "action",
                    "order_type",
                    "qty",
                    "price",
                    "value_usd",
                    "fee_usd",
                    "pnl_usd",
                    "paper",
                    "order_id",
                    "notes",
                ]
            )
        w.writerow(
            [
                ts,
                strategy,
                broker,
                symbol,
                action,
                order_type,
                qty,
                price,
                value_usd,
                fee_usd,
                pnl_usd,
                0,  # paper=0
                order_id,
                notes,
            ]
        )


init_db()
