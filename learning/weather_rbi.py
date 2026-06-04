"""
learning/weather_rbi.py — Research, Backtest, Incubation Loop for Weather.
v19.1.KALSHI: Probabilistic Calibration and Brier Score tracking.
"""

import os
import sqlite3
import logging
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "logs", "trades.db")

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

def init_rbi_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(_DDL_CALIBRATION)
            conn.commit()
    except Exception as e:
        logger.error(f"[weather_rbi] DB Init error: {e}")

def run_weather_rbi():
    """Execute the daily calibration loop."""
    logger.info("[weather_rbi] Starting calibration cycle...")
    init_rbi_db()
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            
            # Fetch resolved trades (last 7 days)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            trades = conn.execute(
                "SELECT symbol, price, pnl_usd, qty, action FROM trades WHERE broker='kalshi' AND ts > ? AND action='SELL'", 
                (cutoff,)
            ).fetchall()
            
            if not trades:
                logger.info("[weather_rbi] No resolved trades in period. Skipping.")
                return

            brier_sum = 0.0
            wins = 0
            accuracy_sum = 0.0
            
            for t in trades:
                # We need the entry price to know the 'forecast'
                # Find the corresponding BUY trade
                buy_trade = conn.execute(
                    "SELECT price, side FROM trades WHERE symbol=? AND action='BUY' AND ts < ? ORDER BY ts DESC LIMIT 1",
                    (t["symbol"], t["ts"] if "ts" in t.keys() else datetime.now().isoformat())
                ).fetchone()
                
                if not buy_trade: continue
                
                f_t = float(buy_trade["price"])
                side = str(buy_trade["side"]).upper()
                implied_prob = f_t if side == "YES" else (1.0 - f_t)
                o_t = 1.0 if float(t["pnl_usd"]) > 0 else 0.0
                
                brier_sum += (implied_prob - o_t) ** 2
                wins += int(o_t)
                
                # Accuracy: how close was our probability to the outcome
                accuracy_sum += (1.0 - abs(implied_prob - o_t))

            count = len(trades)
            if count == 0: return

            avg_brier = brier_sum / count
            win_rate = wins / count
            avg_accuracy = accuracy_sum / count
            
            # Edge Decay (Simplified: PNL per unit of risk)
            edge_decay = sum(float(t["pnl_usd"]) for t in trades) / count
            
            now_ts = datetime.now(timezone.utc).isoformat()
            
            conn.execute(
                "INSERT OR REPLACE INTO weather_calibration (ts, brier_score, win_rate, ensemble_accuracy, sample_size, edge_decay) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_ts, avg_brier, win_rate, avg_accuracy, count, edge_decay)
            )
            conn.commit()
            
            logger.info(f"[weather_rbi] Calibration Complete. Brier: {avg_brier:.4f} | WR: {win_rate:.2%} | Accuracy: {avg_accuracy:.2%}")

    except Exception as e:
        logger.error(f"[weather_rbi] Cycle failed: {e}")

if __name__ == "__main__":
    run_weather_rbi()
