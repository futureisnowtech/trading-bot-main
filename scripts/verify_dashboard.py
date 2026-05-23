#!/usr/bin/env python3
import asyncio
import sys
import os
import logging

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.api.server import get_db_snapshot
from dashboard.data.forecast import get_forecast_performance

logging.basicConfig(level=logging.INFO)

async def run_audit():
    print("🚀 Initiating Dashboard Backend Audit...")
    failures = 0

    print("\n[1/2] Testing get_db_snapshot()...")
    try:
        snapshot = await get_db_snapshot()
        if "error" in snapshot:
            print(f"❌ get_db_snapshot returned an error map: {snapshot['error']}")
            failures += 1
        else:
            print("✅ get_db_snapshot() successfully returned telemetry data.")
            # Print keys for verification
            print(f"   Keys: {list(snapshot.keys())}")
            if "forecast" in snapshot:
                print(f"   Forecast Data: {snapshot['forecast']}")
    except Exception as e:
        print(f"❌ get_db_snapshot() crashed: {e}")
        import traceback
        traceback.print_exc()
        failures += 1

    print("\n[2/2] Testing get_forecast_performance()...")
    try:
        # Note: the plan mentioned get_forecast_performance, but the file has get_forecast_pnl_summary
        from dashboard.data.forecast import get_forecast_pnl_summary
        perf = get_forecast_pnl_summary()
        print(f"✅ get_forecast_pnl_summary() succeeded. Total Trades: {perf.get('total_trades', 0)}")
    except Exception as e:
        print(f"❌ get_forecast_pnl_summary() crashed: {e}")
        failures += 1

    if failures == 0:
        print("\n🟢 Dashboard backend logic is structurally sound.")
        sys.exit(0)
    else:
        print(f"\n🔴 Dashboard audit failed with {failures} error(s).")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_audit())
