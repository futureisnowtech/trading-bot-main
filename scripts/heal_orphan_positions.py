"""
scripts/heal_orphan_positions.py — Force cost-basis and stop-losses into orphan holdings.

This script identifies positions in the database with 0.0 entry prices or stops
and patches them with current market data and a defensive buffer.
"""

import os
import sys
import logging
import sqlite3
from datetime import datetime, timezone

# Add project root to sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH
from execution.coinbase_spot_broker import get_spot_broker
from logging_db.trade_logger import log_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heal_orphans")

def heal():
    logger.info(f"Connecting to DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 1. Find the orphans
    # We target spot positions with entry=0 or stop=0
    query = """
        SELECT symbol, strategy, qty, entry, stop 
        FROM open_positions 
        WHERE strategy LIKE 'spot_%' 
        AND (entry <= 0.0 OR stop <= 0.0 OR entry IS NULL OR stop IS NULL)
    """
    orphans = conn.execute(query).fetchall()
    
    if not orphans:
        logger.info("✅ No orphan positions found with missing cost-basis or stops.")
        return

    logger.info(f"Found {len(orphans)} orphan positions to heal.")
    
    broker = get_spot_broker()
    if not broker.connect():
        logger.error("Failed to connect to Coinbase broker. Cannot fetch mark prices.")
        return

    for row in orphans:
        symbol = row['symbol']
        logger.info(f"Healing {symbol}...")
        
        # 2. Get current price
        try:
            # We assume symbols in DB are clean (e.g. 'BTC')
            # get_quote expects the full ticker (e.g. 'BTC-USDC')
            quote = broker.get_quote(f"{symbol}-USDC")
            current_price = float(quote.get('mid') or quote.get('bid') or 0.0)
            
            if current_price <= 0:
                logger.warning(f"Could not get valid price for {symbol}, skipping.")
                continue
                
            # 3. Define new entry and stop
            # If entry is 0, we set it to current price (resets P&L to 0)
            new_entry = row['entry'] if (row['entry'] and row['entry'] > 0) else current_price
            
            # If stop is 0, we set it to 5% below current price
            new_stop = current_price * 0.95
            
            logger.info(f"   Assigning Entry: {new_entry:.2f}, Stop: {new_stop:.2f}")
            
            conn.execute(
                """
                UPDATE open_positions 
                SET entry = ?, stop = ? 
                WHERE symbol = ? AND strategy = ?
                """,
                (new_entry, new_stop, symbol, row['strategy'])
            )
            conn.commit()
            
            log_event("INFO", "HealOrphans", f"Patched {symbol}: entry={new_entry}, stop={new_stop}")
            
        except Exception as e:
            logger.error(f"Error healing {symbol}: {e}")

    logger.info("Database healing complete.")
    conn.close()

if __name__ == "__main__":
    heal()
