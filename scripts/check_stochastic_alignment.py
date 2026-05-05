
import os
import sys
import json
import time

# Add current dir to path
sys.path.append(os.getcwd())

import system_state
from runtime.spot_strategy import spot_quality_block_reason
from data.edge_monitor import update_shadow_state
import asyncio

async def test_alignment():
    print("--- 🔍 Stochastic Alignment Audit ---")
    
    symbol = "BTC"
    
    # 1. Mock some prices and volumes for shadow state
    prices = [100.0] * 50
    volumes = [1000.0] * 50
    
    print(f"Updating shadow state for {symbol}...")
    await update_shadow_state(symbol, prices, volumes)
    
    # 2. Mock a spot_state
    spot_state = {
        "regime": "TREND",
        "setup_family": "wae_momentum_explosion",
        "setup_score": 65.0,
        "structural_confirm_count": 2,
        "microprice": 100.5,
        "mid_price": 100.0,
        "frames": {
            "5m": {
                "obi": 0.25,
                "frame_score": 55.0,
                "momentum_impulse": 0.5,
                "structure_component": 0.4,
                "path_efficiency": 0.3,
                "participation_component": 0.2,
                "atr_pct": 0.01  # > 0.004 floor
            },
            "30m": {
                "frame_score": 60.0,
                "volatility_quality": 0.5
            }
        }
    }
    
    print(f"Calling spot_quality_block_reason for {symbol}...")
    reason, floor = spot_quality_block_reason(symbol, spot_state, final_spot_score=60.0)
    
    print(f"Block Reason: '{reason}' (Score Floor: {floor})")
    
    # 3. Check System State
    state = system_state.state.get_state()
    stoch = state["strategy"].get("stochastic", {}).get(symbol, {})
    
    print("\n--- Live Vitals in System State ---")
    print(json.dumps(stoch, indent=2))
    
    if stoch:
        print("\n✅ SUCCESS: Stochastic vitals are being pushed to system_state.")
    else:
        print("\n❌ FAILURE: Stochastic vitals missing from system_state.")
        sys.exit(1)
        
    # 4. Check Multiplier
    if stoch.get("multiplier", 0) > 0:
        print(f"✅ SUCCESS: Multiplier is active ({stoch['multiplier']})")
    else:
        print("❌ FAILURE: Multiplier is 0.0 or missing.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_alignment())
