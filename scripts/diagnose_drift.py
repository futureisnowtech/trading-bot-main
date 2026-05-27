import os
import sys
import time
import json
import pandas as pd
from datetime import datetime, timezone

# Setup paths
sys.path.insert(0, '.')
from execution.coinbase_spot_broker import get_spot_broker
from data.historical_data import get_candles

def diagnose(symbol="ADA"):
    print(f"--- DIAGNOSING {symbol} ---")
    
    # 1. Check System Time
    now = datetime.now(timezone.utc)
    print(f"System UTC Time: {now.isoformat()}")
    print(f"System Timestamp: {int(time.time())}")
    
    # 2. Check Live Price
    broker = get_spot_broker()
    live_price = broker.get_mark_price(symbol)
    print(f"Live Price (Ticker): ${live_price}")
    
    # 3. Check Candles (5m)
    print("\nFetching 5m candles...")
    df = get_candles(symbol, "5m", limit=5)
    if df is not None and not df.empty:
        print("Last 5 Candles:")
        print(df.tail(5))
        
        last_ts = df.index[-1]
        last_close = df["close"].iloc[-1]
        diff_pct = abs(last_close - live_price) / live_price if live_price > 0 else 0
        
        print(f"\nLast Candle TS: {last_ts}")
        print(f"Last Candle Close: ${last_close}")
        print(f"Drift: {diff_pct:.2%}")
        
        # Check for temporal gap
        gap = now - last_ts
        print(f"Gap between System Now and Last Candle: {gap}")
    else:
        print("Failed to fetch candles.")

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "ADA"
    diagnose(sym)
