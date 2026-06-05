#!/usr/bin/env python3
"""
scripts/verify_kalshi_connection.py — Diagnostic script for Kalshi V2 integration.
Follows Step 8 of the Ceiling Protocol: Diagnostic-First Verification.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# Ensure we can import from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.kalshi_broker import get_kalshi_broker

def main():
    print("─── Kalshi Connection Diagnostic ───")
    broker = get_kalshi_broker()
    
    print("[1/4] Attempting connection...")
    if not broker.connect():
        print("❌ FAILED: Could not connect to Kalshi. Check .env and private key path.")
        sys.exit(1)
    print("✅ SUCCESS: Connected to Kalshi.")

    print("\n[2/4] Fetching account balance...")
    balance = broker.get_account_balance()
    print(f"✅ SUCCESS: Account Balance = ${balance:.2f}")

    print("\n[3/4] Testing market discovery (weather scope)...")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            markets = executor.submit(broker.discover_markets).result(timeout=25)
    except TimeoutError:
        print("⚠️ WARN: Market discovery timed out after 25s. Connectivity is live, but discovery is currently slow.")
        markets = []
    except Exception as exc:
        print(f"⚠️ WARN: Market discovery failed: {exc}")
        markets = []

    if not markets:
        print("⚠️ WARN: No markets discovered. This might be normal if markets are closed, or discovery logic is too restrictive.")
    else:
        print(f"✅ SUCCESS: Found {len(markets)} active contracts.")
        # Pick one for quote test
        test_contract = markets[0]
        ticker = test_contract['local_symbol']
        print(f"Testing quote for: {ticker} ({test_contract['long_name']})")

        print("\n[4/4] Testing quote harvest...")
        quote = broker.get_quote(ticker)
        if quote.get('bid') is not None or quote.get('ask') is not None:
            print(f"✅ SUCCESS: Quote received.")
            print(f"   Bid: {quote['bid']}")
            print(f"   Ask: {quote['ask']}")
            print(f"   Mid: {quote['mid']}")
            print(f"   Spread: {quote['spread']}")
        else:
            print("❌ FAILED: Received empty quote. Check market status or orderbook depth.")

    print("\n─── Diagnostic Complete ───")

if __name__ == "__main__":
    main()
