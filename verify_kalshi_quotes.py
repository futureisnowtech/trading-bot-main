import os
import sys
import json
import logging

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution.kalshi_broker import get_kalshi_broker

logging.basicConfig(level=logging.INFO)

def main():
    broker = get_kalshi_broker()
    if not broker.connect():
        print("Failed to connect to Kalshi")
        return

    print("\nFetching open events (limit=200)...")
    data = broker._request("GET", "/trade-api/v2/events", params={"limit": 200, "status": "open"})
    events = data.get("events", [])
    
    kx_count = 0
    native_count = 0
    for e in events:
        if e.get("event_ticker", "").startswith("KX"):
            kx_count += 1
        else:
            native_count += 1

    print(f"Total: {len(events)}")
    print(f"KX: {kx_count}")
    print(f"Native: {native_count}")

if __name__ == "__main__":
    main()
