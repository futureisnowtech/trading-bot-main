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
    def __init__(self):
        self.db_path = DB_PATH
        self.symbol_scores: Dict[str, float] = {}
        self.vaccination_threshold = 0.5 # Alpha/Fee ratio below which symbol is 'Vaccinated' (Blocked)

    def audit_performance(self):
        """
        Calculates 'Realized Alpha vs. Fee Drag' per symbol from the last 7 days.
        """
        logger.info("[online_learner] Commencing Sovereign Performance Audit...")
        
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
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(query, (lookback,)).fetchall()
                
                for row in rows:
                    symbol = row['symbol']
                    net_pnl = float(row['net_pnl'] or 0.0)
                    fees = float(row['total_fees'] or 0.0)
                    trades = int(row['trade_count'] or 0)
                    
                    if trades == 0: continue
                    
                    gross_pnl = net_pnl + fees
                    # Alpha Efficiency: How much profit we kept vs how much we gave to exchange
                    efficiency = net_pnl / fees if fees > 0 else 1.0
                    
                    logger.info(f"[online_learner] {symbol}: Net=${net_pnl:.2f}, Fees=${fees:.2f}, Efficiency={efficiency:.2f}")
                    
                    if efficiency < self.vaccination_threshold:
                        self.vaccinate_symbol(symbol, efficiency)
                    else:
                        self.restore_symbol(symbol)
                        
        except Exception as e:
            logger.error(f"[online_learner] Audit failed: {e}")

    def vaccinate_symbol(self, symbol: str, efficiency: float):
        """
        Autonomously tightens the symbol's admission requirements.
        Writes a 'VACCINATED' event to system_events and updates DAG state.
        """
        logger.warning(f"[online_learner] 💉 VACCINATING {symbol}: Efficiency {efficiency:.2f} below threshold!")
        
        message = f"VACCINATED: {symbol} admission tightened. Realized alpha efficiency {efficiency:.2f} is insufficient."
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO system_events (ts, level, source, message) VALUES (?, ?, ?, ?)",
                    (time.time(), "WARN", "online_learner", message)
                )
                # v18.30: Persist vaccination into a new learner_state table
                conn.execute("CREATE TABLE IF NOT EXISTS learner_state (symbol TEXT PRIMARY KEY, vaccinated INTEGER, score REAL)")
                conn.execute(
                    "INSERT OR REPLACE INTO learner_state (symbol, vaccinated, score) VALUES (?, 1, ?)",
                    (symbol, efficiency)
                )
        except Exception as e:
            logger.error(f"[online_learner] Vaccination write failed: {e}")

    def restore_symbol(self, symbol: str):
        """Removes vaccination if performance improves (manual or drift)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM learner_state WHERE symbol = ?", (symbol,))
        except: pass

def run_learner_cycle():
    learner = OnlineLearner()
    learner.audit_performance()

if __name__ == "__main__":
    run_learner_cycle()
