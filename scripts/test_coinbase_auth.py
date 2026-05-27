import os
import sys
import time
import logging

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.coinbase_spot_broker import get_spot_broker

logging.basicConfig(level=logging.INFO)

def test_auth():
    print("🚀 Testing Coinbase CDP Authentication...")
    broker = get_spot_broker(paper=False)
    
    # Try a simple accounts fetch
    try:
        success = broker.connect()
        print(f"   Connection status: {success}")
        if success:
            balances = broker.get_spot_balance()
            print(f"✅ Auth Successful! USD Available: ${balances.get('usd_available')}")
        else:
            print("❌ Auth Failed. Check your credentials in .env")
    except Exception as e:
        print(f"💥 Auth crashed: {e}")

if __name__ == '__main__':
    test_auth()
