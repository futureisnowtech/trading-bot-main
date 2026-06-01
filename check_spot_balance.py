
import os
import sys

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution.coinbase_spot_broker import get_spot_broker

def check_balance():
    try:
        broker = get_spot_broker()
        if not broker.is_connected():
            print("Connecting to broker...")
            connected = broker.connect()
            if not connected:
                print("Failed to connect.")
                return
        
        bal = broker.get_spot_balance()
        print(f"Spot Balance: {bal}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_balance()
