#!/usr/bin/env python3
"""
verification/forensic_auditor.py — Unbiased Integrity Verification Script.

This script runs in complete isolation from the main bot runtime. It does NOT
import system_state, loggers, or complex orchestration layers. It acts as an 
independent observer to verify that the bot's internal SQLite state matches
the ground truth on Coinbase.
"""

import os
import sys
import time
import json
import sqlite3
import secrets
import logging
from typing import Dict, List, Any

# Configure minimal, clean logging for forensic output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("forensic_auditor")

# Add project root to sys.path to access config
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import requests
    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
except ImportError as e:
    logger.error(f"Missing required forensic dependencies: {e}")
    logger.error("Please run: pip install requests pyjwt cryptography")
    sys.exit(1)

def get_env_var(name: str, required: bool = True) -> str:
    val = os.getenv(name, "").strip()
    if not val and required:
        logger.error(f"Missing required environment variable: {name}")
        sys.exit(1)
    return val

def make_jwt(key_name: str, private_key_pem: str, method: str, path: str) -> str:
    """Generate a short-lived CDP JWT for a single request (ES256)."""
    now = int(time.time())
    path_only = path.split("?")[0]
    payload = {
        "sub": key_name,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method} api.coinbase.com{path_only}",
    }
    headers = {
        "kid": key_name,
        "nonce": secrets.token_hex(16),
        "typ": "JWT",
    }
    return jwt.encode(
        payload,
        private_key_pem,
        algorithm="ES256",
        headers=headers,
    )

def fetch_coinbase_accounts(key_name: str, private_key_pem: str) -> List[Dict]:
    path = "/api/v3/brokerage/accounts"
    token = make_jwt(key_name, private_key_pem, "GET", path)
    url = f"https://api.coinbase.com{path}"
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("accounts", [])
    except Exception as e:
        logger.error(f"Failed to fetch ground truth from Coinbase: {e}")
        return []

def get_bot_positions(db_path: str) -> Dict[str, float]:
    """Retrieve positions the bot thinks it holds from SQLite."""
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return {}
    
    positions = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Querying live (paper=0) spot positions
        cur = conn.execute(
            "SELECT symbol, qty FROM open_positions WHERE paper=0 AND strategy LIKE 'spot_%'"
        )
        for row in cur.fetchall():
            sym = row["symbol"].replace("-USD", "")
            positions[sym] = positions.get(sym, 0.0) + float(row["qty"] or 0.0)
        conn.close()
    except Exception as e:
        logger.error(f"Failed to read bot state from DB: {e}")
    return positions

def run_audit():
    logger.info("Starting Forensic Integrity Audit...")
    
    # 1. Setup Configuration
    db_path = os.path.join(_ROOT, "logs", "trades.db")
    key_name = get_env_var("COINBASE_CDP_KEY_NAME")
    private_key_raw = get_env_var("COINBASE_CDP_PRIVATE_KEY")
    
    # Handle newline escapes in private key
    private_key_pem = private_key_raw.replace("\\n", "\n")
    
    # 2. Collect State
    logger.info("Collecting ground truth from Coinbase API...")
    cb_accounts = fetch_coinbase_accounts(key_name, private_key_pem)
    
    actual_holdings = {}
    for acc in cb_accounts:
        currency = acc.get("currency", "")
        # Filter for crypto assets with non-zero balance
        if currency != "USD":
            val = float(acc.get("available_balance", {}).get("value", 0.0))
            if val > 0:
                actual_holdings[currency] = val
                
    logger.info("Collecting internal state from trades.db...")
    bot_holdings = get_bot_positions(db_path)
    
    # 3. Diff Analysis
    logger.info("Performing mathematical diff analysis...")
    
    all_symbols = set(actual_holdings.keys()) | set(bot_holdings.keys())
    discrepancies = []
    
    print("\n" + "="*60)
    print(f"{'SYMBOL':<10} | {'BOT QTY':<15} | {'CB QTY':<15} | {'DIFF':<10}")
    print("-" * 60)
    
    errors = 0
    for sym in sorted(all_symbols):
        bot_qty = bot_holdings.get(sym, 0.0)
        cb_qty = actual_holdings.get(sym, 0.0)
        diff = bot_qty - cb_qty
        
        # We allow a tiny dust tolerance for rounding
        status = "MATCH"
        if abs(diff) > 0.00000001:
            status = "FAIL"
            errors += 1
            discrepancies.append(sym)
            
        print(f"{sym:<10} | {bot_qty:<15.8f} | {cb_qty:<15.8f} | {status}")
    
    print("="*60 + "\n")
    
    if errors == 0:
        logger.info("🏁 AUDIT RESULT: PASS. Bot state matches reality.")
        sys.exit(0)
    else:
        logger.error(f"🏁 AUDIT RESULT: FAIL. {errors} discrepancies detected in: {', '.join(discrepancies)}")
        sys.exit(1)

if __name__ == "__main__":
    run_audit()
