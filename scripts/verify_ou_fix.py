
import os
import sys
import asyncio
import numpy as np

# Add current dir to path
sys.path.append(os.getcwd())

from data.edge_monitor import update_shadow_state, get_shadow_state

async def verify():
    print("--- 🔬 OU Fix Verification Script ---")
    
    symbol = "BTC"
    
    # 1. Provide dynamic mock data (not flat) to ensure models can fit
    # 100 points of a slightly upward trending sine wave to simulate price action
    t = np.linspace(0, 10, 100)
    prices = (100.0 + np.sin(t) + 0.1 * t).tolist()
    volumes = [1000.0 + np.random.normal(0, 50) for _ in range(100)]
    
    print(f"Updating shadow state for {symbol}...")
    await update_shadow_state(symbol, prices, volumes)
    
    # 2. Inspect the shadow state
    shadow = get_shadow_state(symbol)
    
    print("\n--- Captured Shadow State ---")
    import json
    print(json.dumps(shadow, indent=2))
    
    # 3. Validation
    required_keys = ["ou_transition_prob", "ou_halflife_bars", "adf_stat"]
    missing = [k for k in required_keys if k not in shadow]
    
    if missing:
        print(f"\n❌ FAILURE: Missing keys in shadow state: {missing}")
        sys.exit(1)
        
    ou_prob = shadow["ou_transition_prob"]
    print(f"\nCaptured OU Prob: {ou_prob}")
    
    if ou_prob == 0.5:
        print("❌ FAILURE: OU Probability is still exactly 0.5 (fallback/failed).")
        # Note: it *could* be 0.5 by chance, but with dynamic data it's unlikely to be exactly 0.5000
        sys.exit(1)
    else:
        print("✅ SUCCESS: OU Probability is dynamic!")

    print("\n✅ Verification Complete: Math utilities restored and active.")

if __name__ == "__main__":
    asyncio.run(verify())
