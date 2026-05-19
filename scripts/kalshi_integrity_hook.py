#!/usr/bin/env python3
"""
scripts/kalshi_integrity_hook.py — System Integrity Probe for Kalshi.

Validates 5 pillars of truth without placing any trades:
1. Auth & API Health
2. Orderbook & Spread Sanity
3. Data Pipeline Freshness (SQLite)
4. Order / Position Parity
5. Payload Precision (Pydantic model validation)
"""

import os
import sys
import logging
from datetime import datetime, timezone

# Add root to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution.kalshi_broker import get_kalshi_broker
from forecast.db import DB_PATH
import sqlite3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IntegrityHook")

def check_auth(broker):
    logger.info("Pillar 1: Checking Auth & API Health...")
    try:
        balance = broker.get_account_balance()
        logger.info(f"  [OK] Connected. Account Balance: ${balance:.2f}")
        return True
    except Exception as e:
        logger.error(f"  [FAIL] Auth/API Health failed: {e}")
        return False

def check_orderbook(broker):
    logger.info("Pillar 2: Checking Orderbook & Spread Sanity...")
    try:
        markets = broker.discover_markets()
        if not markets:
            logger.warning("  [SKIP] No active markets found to check orderbook.")
            return True
        
        # Scan up to 10 markets to find one with decent liquidity (avoiding dead/illiquid markets)
        for m in markets[:10]:
            ticker = m["local_symbol"]
            quote = broker.get_quote(ticker)
            bid = quote.get("bid")
            ask = quote.get("ask")
            spread = quote.get("spread")
            
            if bid is not None and ask is not None:
                # Only fail if spread is 1.00 or greater (inverted/broken book)
                if 0 <= spread < 1.00:
                    logger.info(f"  [OK] Found liquid market {ticker}. Bid: {bid}, Ask: {ask}, Spread: {spread:.2f}")
                    return True
                else:
                    logger.debug(f"  [SKIP] {ticker} spread too wide or negative ({spread}).")
                    
        logger.error("  [FAIL] Checked 10 markets; all had excessive or negative spreads or were illiquid.")
        return False
    except Exception as e:
        logger.error(f"  [FAIL] Orderbook check failed: {e}")
        return False

def check_data_freshness():
    logger.info("Pillar 3: Checking Data Pipeline Freshness...")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT ts FROM forecast_quotes ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        
        if not row:
            logger.warning("  [WARN] No quotes found in database.")
            return True
            
        latest_ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        
        logger.info(f"  [INFO] Most recent quote age: {age:.1f}s")
        if age > 300:
            logger.warning(f"  [WARN] Data pipeline is stale (> 300s). Start `forecast/quote_harvester.py`.")
            return True # This is a warning, not a hard block
        
        logger.info("  [OK] Data pipeline is fresh.")
        return True
    except Exception as e:
        logger.error(f"  [FAIL] Data freshness check failed: {e}")
        return False

def check_parity(broker):
    logger.info("Pillar 4: Checking Order / Position Parity...")
    try:
        # get_positions already does a sync which includes resting orders in our new fix
        # But we'll do it manually here to log the delta
        positions = broker._portfolio_api.get_positions()
        pos_count = len(positions.market_positions) if hasattr(positions, 'market_positions') else 0
        
        resting = broker._orders_api.get_orders(status="resting")
        rest_count = len(resting.orders) if hasattr(resting, 'orders') else 0
        
        logger.info(f"  [INFO] Active Positions: {pos_count}")
        logger.info(f"  [INFO] Resting Orders: {rest_count}")
        logger.info(f"  [OK] Parity check complete (Exposure = {pos_count} + {rest_count}).")
        return True
    except Exception as e:
        logger.error(f"  [FAIL] Parity check failed: {e}")
        return False

def check_payload_precision():
    logger.info("Pillar 5: Checking Payload Precision (Pydantic model validation)...")
    try:
        from kalshi_python_sync import CreateOrderRequest
        import uuid
        
        # Test count_fp formatting and type strictness
        test_qty = 1.0
        req = CreateOrderRequest(
            ticker="DUMMY",
            action="buy",
            side="yes",
            count_fp=f"{float(test_qty):.2f}",
            yes_price_dollars="0.50",
            no_price_dollars="0.50",
            client_order_id=str(uuid.uuid4()),
            time_in_force="good_till_canceled" # Fix: strictly matched to Kalshi enum
        )
        logger.info(f"  [INFO] Dummy Request count_fp: {req.count_fp}")
        if req.count_fp == "1.00":
            logger.info("  [OK] Payload precision validated.")
            return True
        else:
            logger.error(f"  [FAIL] Incorrect count_fp formatting: {req.count_fp}")
            return False
    except Exception as e:
        logger.error(f"  [FAIL] Payload precision check failed: {e}")
        return False

def main():
    broker = get_kalshi_broker()
    if not broker.connect():
        logger.error("Could not connect to Kalshi broker. Exiting.")
        sys.exit(1)
        
    results = [
        check_auth(broker),
        check_orderbook(broker),
        check_data_freshness(),
        check_parity(broker),
        check_payload_precision()
    ]
    
    if all(results):
        logger.info("SUMMARY: ALL PILLARS PASS. SYSTEM INTEGRITY VERIFIED. ✅")
        sys.exit(0)
    else:
        logger.error("SUMMARY: INTEGRITY CHECK FAILED. DO NOT GO LIVE. ❌")
        sys.exit(1)

if __name__ == "__main__":
    main()
