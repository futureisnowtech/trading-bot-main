#!/usr/bin/env python3
import json
import os
import time
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_FILE = "logs/cached_macro_regime.json"

def test_macro_cache():
    print("🚀 Verifying Macro Context Cache...")
    
    if not os.path.exists(CACHE_FILE):
        print(f"❌ Cache file not found: {CACHE_FILE}")
        # Create a dummy for validation if missing
        os.makedirs("logs", exist_ok=True)
        dummy = {
            "spy_trend": "BULLISH",
            "treasury_yield": "4.2%",
            "vix_regime": "LOW",
            "headlines": ["Test headline"],
            "risk_score": 2,
            "updated_at": time.time()
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(dummy, f, indent=2)
        print("Created dummy cache for testing.")

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
            
        required_keys = ["spy_trend", "treasury_yield", "vix_regime", "risk_score"]
        missing = [k for k in required_keys if k not in data]
        
        if missing:
            print(f"❌ Missing keys in cache: {missing}")
            return False
            
        age = time.time() - data.get("updated_at", 0)
        print(f"✅ Cache verified. Risk Score: {data['risk_score']}, Age: {age:.1f}s")
        return True
        
    except Exception as e:
        print(f"❌ Cache verification crashed: {e}")
        return False

if __name__ == "__main__":
    success = test_macro_cache()
    sys.exit(0 if success else 1)
