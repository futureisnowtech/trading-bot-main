import os
import sys
import logging
from execution.coinbase_spot_broker import CoinbaseSpotBroker

logging.basicConfig(level=logging.INFO)

def test_connection():
    print("Initializing CoinbaseSpotBroker (LIVE)...")
    broker = CoinbaseSpotBroker(paper=False)
    
    print("Attempting to connect...")
    # The broker doesn't have an explicit connect() that returns success, 
    # but get_spot_balance() calls the API.
    try:
        balance = broker.get_spot_balance()
        if balance and "usd_available" in balance:
            print(f"SUCCESS! USD Available: ${balance['usd_available']}")
        else:
            print(f"FAILED: Received unexpected balance response: {balance}")
    except Exception as e:
        print(f"FAILED with exception: {e}")

if __name__ == "__main__":
    test_connection()
