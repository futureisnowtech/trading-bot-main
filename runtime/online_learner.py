"""
runtime/online_learner.py — The Sovereign Brain (v18.30)
Recursive Evolution: Real-time Fee-Alpha Reconciliation & Self-Vaccination.
"""

import time
import logging
import sqlite3
from typing import Dict, List, Any
from config import DB_PATH

logger = logging.getLogger("OnlineLearner")

class OnlineLearner:
    """
    The 'Brain' for autonomous symbol vaccination.
    Audits trades and API costs to detect structural alpha leaks.
    """
    def __init__(self, lane_id: str = "spot"):
        self.db_path = DB_PATH
        self.lane_id = lane_id
        self.symbol_scores: Dict[str, float] = {}
        self.vaccination_threshold = 0.5 # Alpha/Fee ratio below which symbol is 'Vaccinated' (Blocked)
        logger.info(f"[online_learner] Initialized lane '{lane_id}'")

    def audit_performance(self):
        """
        Calculates 'Realized Alpha vs. Fee Drag' per symbol from the last 7 days.
        """
        logger.info(f"[online_learner] [{self.lane_id}] Commencing Sovereign Performance Audit...")
        
        # v18.33: Filter by lane if possible (requires trades table to have lane column)
        query = """
        SELECT symbol, 
               SUM(pnl_usd) as net_pnl, 
               SUM(fee_usd) as total_fees,
               COUNT(*) as trade_count
        FROM trades 
        WHERE ts > ? AND paper = 0
        GROUP BY symbol
        """
        
        lookback = time.time() - (86400 * 7) # 7-day window
        
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(query, (lookback,)).fetchall()
                
                for row in rows:
                    symbol = row['symbol']
                    net_pnl = float(row['net_pnl'] or 0.0)
                    fees = float(row['total_fees'] or 0.0)
                    trades = int(row['trade_count'] or 0)
                    
                    if trades == 0: continue
                    
                    gross_pnl = net_pnl + fees
                    efficiency = net_pnl / fees if fees > 0 else 1.0
                    
                    logger.info(f"[online_learner] [{self.lane_id}] {symbol}: Net=${net_pnl:.2f}, Fees=${fees:.2f}, Efficiency={efficiency:.2f}")
                    
                    if efficiency < self.vaccination_threshold:
                        self.vaccinate_symbol(symbol, efficiency)
                    else:
                        self.restore_symbol(symbol)
                        
        except Exception as e:
            logger.error(f"[online_learner] [{self.lane_id}] Audit failed: {e}")

    def vaccinate_symbol(self, symbol: str, efficiency: float):
        """
        Autonomously tightens the symbol's admission requirements.
        """
        logger.warning(f"[online_learner] [{self.lane_id}] 💉 VACCINATING {symbol}: Efficiency {efficiency:.2f} below threshold!")
        
        message = f"VACCINATED [{self.lane_id.upper()}]: {symbol} admission tightened. Realized alpha efficiency {efficiency:.2f} is insufficient."
        
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                conn.execute(
                    "INSERT INTO system_events (ts, level, source, message) VALUES (?, ?, ?, ?)",
                    (time.time(), "WARN", f"online_learner_{self.lane_id}", message)
                )
                # v18.33: Multi-lane learner state support
                conn.execute("CREATE TABLE IF NOT EXISTS learner_state (symbol TEXT, lane TEXT, vaccinated INTEGER, score REAL, PRIMARY KEY (symbol, lane))")
                conn.execute(
                    "INSERT OR REPLACE INTO learner_state (symbol, lane, vaccinated, score) VALUES (?, ?, 1, ?)",
                    (symbol, self.lane_id, efficiency)
                )
        except Exception as e:
            logger.error(f"[online_learner] [{self.lane_id}] Vaccination write failed: {e}")

    def restore_symbol(self, symbol: str):
        """Removes vaccination if performance improves."""
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                conn.execute("DELETE FROM learner_state WHERE symbol = ? AND lane = ?", (symbol, self.lane_id))
        except: pass

def run_learner_cycle(lane_id: str = "spot"):
    learner = OnlineLearner(lane_id=lane_id)
    learner.audit_performance()

if __name__ == "__main__":
    run_learner_cycle()
