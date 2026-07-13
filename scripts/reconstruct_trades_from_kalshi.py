import os
import sys
import json
import sqlite3

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.kalshi_broker import get_kalshi_broker

def fetch_history():
    broker = get_kalshi_broker()
    if not broker.connect():
        print("Failed to connect!")
        sys.exit(1)
        
    print("Connected to Kalshi. Attempting to fetch fills...")
    
    # Try fetching fills
    try:
        fills_data = broker._request("GET", "/trade-api/v2/portfolio/fills")
        print(f"Fills response type: {type(fills_data)}")
        print("Fills keys:", fills_data.keys() if isinstance(fills_data, dict) else "Not a dict")
        if isinstance(fills_data, dict) and "fills" in fills_data:
            fills = fills_data["fills"]
            print(f"Found {len(fills)} fills.")
            for f in fills[:10]:
                print(json.dumps(f, indent=2))
        else:
            print("No fills key found in response.")
            print(json.dumps(fills_data, indent=2)[:500])
    except Exception as e:
        print(f"Error fetching fills: {e}")
        
    # Try fetching historical orders
    print("\nAttempting to fetch orders...")
    try:
        orders_data = broker._request("GET", "/trade-api/v2/portfolio/orders")
        print(f"Orders response type: {type(orders_data)}")
        print("Orders keys:", orders_data.keys() if isinstance(orders_data, dict) else "Not a dict")
        if isinstance(orders_data, dict) and "orders" in orders_data:
            orders = orders_data["orders"]
            print(f"Found {len(orders)} orders.")
            for o in orders[:10]:
                print(json.dumps(o, indent=2))
        else:
            print("No orders key found in response.")
            print(json.dumps(orders_data, indent=2)[:500])
    except Exception as e:
        print(f"Error fetching orders: {e}")

if __name__ == "__main__":
    fetch_history()
