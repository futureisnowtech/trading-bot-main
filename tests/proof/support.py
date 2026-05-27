from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def build_candidate(**overrides) -> dict:
    candidate = {
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "exchange": "binance",
        "direction": "LONG",
        "price": 100.0,
        "volume_24h_usd": 25_000_000.0,
        "vol_usd": 25_000_000.0,
        "funding_rate": 0.12,  # annualized decimal, same convention as scanner output
        "spread_pct": 0.05,
        "bid_depth_usd": 25_000.0,
        "ask_depth_usd": 25_000.0,
        "atr_15m": 1.0,
        "stop_pct": 3.0,
        "target_pct": 6.0,
        "scan_setups": ["momentum"],
        "primary_setup": "",
        "vol_regime": 2,
        "fg_current": 50.0,
        "correlation_penalty": 1.0,
        "cascade_risk_score": 0.0,
        "win_rate_estimate": 0.54,
        "stop_multiplier": 3.0,
    }
    candidate.update(overrides)
    return candidate


def build_features(**overrides) -> dict:
    features = {
        "cvd_divergence": 1,
        "mom_macd_long_aligned": 1,
        "mom_macd_hist_fast": 0.8,
        "mom_macd_hist_slow": 0.6,
        "mom_rsi_divergence": 1,
        "deriv_funding_rate": -0.2,
        "vwap_reclaim": 1,
        "ob_imbalance_l5": 0.64,
        "mom_williams_r": 0.15,
        "liq_cascade_risk": 0.1,
        "liq_long_dist_pct": 0.12,
        "onchain_whale_signal": 1,
        "deriv_skew_direction": 1,
        "vol_spike_5c": 2.0,
        "mom_rsi_14": 0.55,
        "supertrend_bullish": 1,
        "supertrend_bearish": 0,
        "cloud_bullish": 1,
        "cloud_bearish": 0,
        "wae_bullish": 1,
        "wae_bearish": 0,
        "wae_exploding": 1,
        "fisher_cross_up": 1,
        "fisher_cross_down": 0,
        "chop_trending": 1,
        "chop_ranging": 0,
        "wt_oversold_cross": 1,
        "wt_overbought": 0,
        "lrsi_value": 0.1,
        "kst_bullish": 1,
        "kst_value": 0.9,
        "tv_signal": 0,
        "vwap_band_position": 0,
        "price_return_5c": 0.01,
        "squeeze_fired": 1,
        "squeeze_direction": 1,
        "regime": "TRENDING_UP",
    }
    features.update(overrides)
    return features


def insert_trade(db_path: Path, **overrides) -> None:
    row = {
        "ts": "2026-04-10 09:30:00",
        "strategy": "crypto_perp",
        "broker": "binance",
        "symbol": "BTCUSDT",
        "action": "SELL",
        "order_type": "MARKET",
        "qty": 1.0,
        "price": 100.0,
        "value_usd": 100.0,
        "fee_usd": 1.0,
        "pnl_usd": 5.0,
        "paper": 1,
        "order_id": "proof_trade",
        "notes": "",
        "won": 1,
        "source": "clean_paper_v10",
        "pnl_pct": 0.05,
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trades
                (ts, strategy, broker, symbol, action, order_type, qty, price, value_usd,
                 fee_usd, pnl_usd, paper, order_id, notes, won, source, pnl_pct)
            VALUES
                (:ts, :strategy, :broker, :symbol, :action, :order_type, :qty, :price, :value_usd,
                 :fee_usd, :pnl_usd, :paper, :order_id, :notes, :won, :source, :pnl_pct)
            """,
            row,
        )


def insert_trade_attribution(db_path: Path, **overrides) -> None:
    row = {
        "trade_ref": "proof_attr",
        "symbol": "BTCUSDT",
        "strategy": "crypto_perp",
        "regime": "trending",
        "source": "replay_harness",
        "entry_ts": "2026-04-10T09:30:00+00:00",
        "exit_ts": "2026-04-10T10:30:00+00:00",
        "entry_price": 100.0,
        "exit_price": 104.0,
        "pnl_usd": 4.0,
        "pnl_pct": 0.04,
        "fee_usd": 0.5,
        "won": 1,
        "signals_json": '{"squeeze_breakout": true}',
        "conviction": 72.0,
        "exit_reason": "target_hit",
        "hold_minutes": 60.0,
        "paper": 1,
        "lesson": "Proof replay",
        "created_at": "2026-04-10T10:30:00+00:00",
        "mae_pct": 0.01,
        "mfe_pct": 0.05,
        "exit_type": "target_hit",
        "is_fee_trap": 0,
        "ml_p_win": 0.61,
        "super_score": 68.0,
        "composite_score": 71.0,
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trade_attribution
                (trade_ref, symbol, strategy, regime, source, entry_ts, exit_ts, entry_price,
                 exit_price, pnl_usd, pnl_pct, fee_usd, won, signals_json, conviction,
                 exit_reason, hold_minutes, paper, lesson, created_at, mae_pct, mfe_pct,
                 exit_type, is_fee_trap, ml_p_win, super_score, composite_score)
            VALUES
                (:trade_ref, :symbol, :strategy, :regime, :source, :entry_ts, :exit_ts, :entry_price,
                 :exit_price, :pnl_usd, :pnl_pct, :fee_usd, :won, :signals_json, :conviction,
                 :exit_reason, :hold_minutes, :paper, :lesson, :created_at, :mae_pct, :mfe_pct,
                 :exit_type, :is_fee_trap, :ml_p_win, :super_score, :composite_score)
            """,
            row,
        )


def insert_signal_stat(db_path: Path, **overrides) -> None:
    row = {
        "signal_name": "squeeze_breakout",
        "regime": "any",
        "source": "replay_harness",
        "fires": 5,
        "wins": 4,
        "losses": 1,
        "total_pnl": 12.5,
        "avg_pnl": 2.5,
        "win_rate": 0.8,
        "bayesian_pts": 18.0,
        "prior_pts": 15.0,
        "last_updated": "2026-04-10T10:30:00+00:00",
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO signal_stats
                (signal_name, regime, source, fires, wins, losses, total_pnl, avg_pnl,
                 win_rate, bayesian_pts, prior_pts, last_updated)
            VALUES
                (:signal_name, :regime, :source, :fires, :wins, :losses, :total_pnl, :avg_pnl,
                 :win_rate, :bayesian_pts, :prior_pts, :last_updated)
            """,
            row,
        )


def insert_system_event(db_path: Path, **overrides) -> None:
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": "INFO",
        "source": "main",
        "message": "Bot started — paper v18.16",
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (:ts, :level, :source, :message)",
            row,
        )


def write_log(log_path: Path, *lines: str) -> None:
    rendered = "\n".join(lines).rstrip() + "\n"
    log_path.write_text(rendered, encoding="utf-8")


def insert_open_position(db_path: Path, **overrides) -> None:
    """Insert a row into open_positions. Defaults to paper=1 (paper mode)."""
    row = {
        "symbol": "BTCUSDT",
        "strategy": "crypto_perp",
        "qty": 0.01,
        "entry": 50000.0,
        "stop": 49000.0,
        "target": 52000.0,
        "high_since_entry": 50100.0,
        "ts_entry": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "paper": 1,
        "direction": "LONG",
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO open_positions
               (symbol, strategy, qty, entry, stop, target, high_since_entry,
                ts_entry, paper, direction)
               VALUES (:symbol, :strategy, :qty, :entry, :stop, :target,
                       :high_since_entry, :ts_entry, :paper, :direction)""",
            row,
        )


def upsert_runtime_state(db_path: Path, process_mode: str = "live") -> None:
    """
    Write (or replace) the single system_runtime_state row so that
    _runtime_paper_flag() reads the given process_mode from the test DB.
    Creates the table if absent.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS system_runtime_state (
               id INTEGER PRIMARY KEY DEFAULT 1,
               process_mode TEXT,
               startup_ts TEXT,
               process_alive INTEGER DEFAULT 0,
               active_lanes TEXT DEFAULT '[]',
               global_status TEXT DEFAULT 'OK',
               last_global_heartbeat_at TEXT,
               launch_readiness_state TEXT DEFAULT 'NOT_READY',
               updated_at TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO system_runtime_state (id, process_mode, startup_ts, updated_at)
               VALUES (1, ?, datetime('now'), datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   process_mode=excluded.process_mode,
                   updated_at=excluded.updated_at""",
            (process_mode,),
        )
