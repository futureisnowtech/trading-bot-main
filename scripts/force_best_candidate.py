#!/usr/bin/env python3
"""
scripts/force_best_candidate.py — Force entry on the #1 ranked scanner candidate.
Automatically logs to trades.db so the running bot manages the exit.
"""

import os
import sys
import logging
import json
from datetime import datetime, timezone

# Add project root to path
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJ)

import config
from execution.coinbase_spot_broker import get_spot_broker
from logging_db.trade_logger import log_trade, log_trade_features, init_db, log_event
from runtime.spot_momentum import warm_spot_universe, build_spot_state
from runtime.spot_strategy import ACTIVE_UNIVERSE, calculate_execution_profile

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("force_entry")

def main():
    print("\n🚀 FORCING BEST CANDIDATE ENTRY...")
    print("============================================================\n")

    # 1. Initialize
    init_db()
    broker = get_spot_broker()
    
    # 2. Warm up universe
    print("🔍 Scanning live market for best candidates...")
    warm_spot_universe(list(ACTIVE_UNIVERSE))
    
    candidates = []
    for symbol in ACTIVE_UNIVERSE:
        state = build_spot_state(symbol)
        if state:
            # Bypass technical vetoes, just look at the composite score
            score = state.get("composite", 0)
            candidates.append({
                "symbol": symbol,
                "score": score,
                "state": state
            })
    
    if not candidates:
        print("❌ No candidates found. Check internet/API.")
        return 1
        
    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    symbol = best["symbol"]
    score = best["score"]
    state = best["state"]
    
    print(f"🏆 Best candidate: {symbol} (Composite Score: {score:.2f})")
    
    # 3. Decision & Sizing
    size_usd = config.SPOT_LIVE_MAX_POSITION_USD
    current_price = broker.get_mark_price(symbol)
    
    print(f"💰 Execution Plan: BUY ${size_usd:.2f} of {symbol} @ ~${current_price:.4f}")
    
    confirm = input("\n⚠️  CONFIRM LIVE EXECUTION? (y/n): ")
    if confirm.lower() != 'y':
        print("Aborted.")
        return 0
        
    # 4. Execute Live
    print(f"\n📡 Transmitting ORDER to Coinbase...")
    res = broker.buy_spot(symbol, size_usd)
    
    if not res or not res.get("order_id"):
        print(f"❌ Execution failed: {res}")
        return 1
        
    order_id = res["order_id"]
    fill_price = float(res.get("fill_price") or current_price)
    qty = float(res.get("qty_base") or 0.0)
    
    print(f"✅ Success! Order: {order_id} | Filled: {qty} {symbol} @ ${fill_price:.4f}")
    
    # 5. Record to DB so bot manages the exit
    try:
        # Calculate stop/target for logger
        stop_price = fill_price * (1 - config.SPOT_STOP_PCT)
        target_price = fill_price * (1 + (config.SPOT_STOP_PCT * config.SPOT_TARGET_R))
        
        trade_id = log_trade(
            strategy="spot_scalp", # Use standard strategy name so bot picks it up
            broker="coinbase_spot",
            symbol=symbol,
            action="BUY",
            order_type="MARKET",
            qty=qty,
            price=fill_price,
            order_id=order_id,
            paper=0,
            notes=f"Forced entry via script (Score: {score:.1f})"
        )
        
        # Manually insert into open_positions because log_trade only logs to trades table
        # We need it in open_positions for the bot to manage it.
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute("""
            INSERT INTO open_positions 
            (symbol, strategy, qty, entry, stop, target, paper, direction, ts_entry, lane, high_since_entry, low_since_entry) 
            VALUES (?, ?, ?, ?, ?, ?, 0, 'LONG', ?, 'lane2', ?, ?)
        """, (symbol, "spot_scalp", qty, fill_price, stop_price, target_price, datetime.now(timezone.utc).isoformat(), fill_price, fill_price))
        conn.commit()
        conn.close()
        
        # Snapshot features
        features = state.get("features", {})
        if trade_id > 0:
            log_trade_features(trade_id, symbol, "LONG", features)
        
        log_event("INFO", "force_entry", f"Forced entry on {symbol} (trade_id={trade_id}) successful.")
        print(f"\n📝 Logged to trades.db and open_positions. Trade ID: {trade_id}")
        print(f"🏁 NYC Bot will now manage this exit automatically.")
        
    except Exception as e:
        print(f"⚠️  Trade executed but logging failed: {e}")
        print("Manually ensure the trade is in open_positions if the bot misses it.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
