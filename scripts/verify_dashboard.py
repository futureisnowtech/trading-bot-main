#!/usr/bin/env python3
import asyncio
import sys
import os
import logging

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.api.server import get_db_snapshot
from dashboard.data.forecast import get_forecast_pnl_summary
from dashboard.data.balance import _unrealized_pnl

logging.basicConfig(level=logging.INFO)

async def run_audit():
    print("🚀 Initiating Comprehensive Dashboard Backend Audit...")
    failures = 0

    print("\n[1/3] Testing get_db_snapshot()...")
    try:
        snapshot = await get_db_snapshot()
        if "error" in snapshot:
            print(f"❌ get_db_snapshot returned an error map: {snapshot['error']}")
            failures += 1
        else:
            print("✅ get_db_snapshot() successfully returned telemetry data.")
    except Exception as e:
        print(f"❌ get_db_snapshot() crashed: {e}")
        failures += 1

    print("\n[2/3] Testing get_forecast_pnl_summary()...")
    try:
        perf = get_forecast_pnl_summary()
        print(f"✅ get_forecast_pnl_summary() succeeded. Total Trades: {perf.get('total_trades', 0)}")
    except Exception as e:
        print(f"❌ get_forecast_pnl_summary() crashed: {e}")
        failures += 1

    print("\n[3/3] Testing data.balance._unrealized_pnl()...")
    try:
        upnl = _unrealized_pnl()
        print(f"✅ _unrealized_pnl() succeeded. Value: {upnl}")
    except Exception as e:
        print(f"❌ _unrealized_pnl() crashed: {e}")
        failures += 1

    if failures == 0:
        print("\n🟢 All dashboard systems operational. Backend error resolved.")
        sys.exit(0)
    else:
        print(f"\n🔴 Dashboard audit failed with {failures} error(s).")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_audit())
