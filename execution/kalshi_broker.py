"""
execution/kalshi_broker.py — Kalshi prediction market execution (Pure REST).

This implementation bypasses the official SDK to avoid Pydantic validation
and dependency issues. It uses manual RSA-PSS signing for all V2 API requests.
"""

import logging
import os
import sys
import uuid
import base64
import time
import requests
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization

# Add root to path for logging_db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logging_db.trade_logger import log_event, log_trade

logger = logging.getLogger(__name__)

# ── Credentials ───────────────────────────────────────────────────────────────
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")
KALSHI_API_BASE = "https://external-api.kalshi.com"

# Economic event categories to scan during discovery.
ECONOMIC_CATEGORIES: list[str] = [
    "Economics",
    "Federal Reserve",
    "Financials",
    "Recession",
    "Climate and Weather",
    "Politics",
    "Elections",
    "Social",
]

# Markets to EXCLUDE
EXCLUDED_KEYWORDS: list[str] = [
    "sports",
    "politics",
    "entertainment",
    "celebrity",
    "award",
    "election",
    "novelty",
]

def _is_economic_market(ticker: str, title: str, category: str = "") -> bool:
    """Helper to filter discovered Kalshi events to economic/weather scope only."""
    if not title or not ticker:
        return False
    
    title_lower = title.lower()
    ticker_lower = ticker.lower()
    category_lower = category.lower() if category else ""

    for excl in EXCLUDED_KEYWORDS:
        if excl in category_lower or excl in title_lower:
            return False

    if any(c.lower() in category_lower for c in ECONOMIC_CATEGORIES):
        return True

    allowed_keywords = [
        "cpi", "inflation", "fed", "fomc", "rate", "rates", "payroll",
        "nonfarm", "unemployment", "gdp", "pce", "retail", "housing",
        "consumer", "ppi", "production", "jobs", "employment", "macro",
        "economic", "economy", "debt", "budget", "target", "hike", "cut",
        "growth", "index", "price", "prices", "survey", "manufacturing",
        "temp", "temperature", "rain", "precip", "weather", "degree",
        "hurricane", "storm", "snow", "oil", "gas", "energy", "yield"
    ]
    for kw in allowed_keywords:
        if kw in title_lower or kw in ticker_lower or kw in category_lower:
            return True

    return False

class KalshiBroker:
    def __init__(self) -> None:
        self._connected = False
        self._open_positions: dict[str, dict] = {}  # key = f"{ticker}_{right}"
        self._private_key = None
        
    def connect(self) -> bool:
        """Verify credentials and load private key for signing."""
        if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY_PATH:
            log_event("ERROR", "KalshiBroker", "Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH in .env")
            return False

        try:
            with open(KALSHI_PRIVATE_KEY_PATH, 'r') as f:
                key_pem = f.read()
            
            self._private_key = serialization.load_pem_private_key(
                key_pem.encode(),
                password=None
            )
            
            # Verify connection by getting balance
            resp = self._request("GET", "/trade-api/v2/portfolio/balance")
            if "error" in resp:
                raise RuntimeError(f"Auth verification failed: {resp['error']}")
                
            self._connected = True
            print(f"[KalshiBroker] Connected (LIVE) ✅ | Balance: ${float(resp.get('balance_dollars', 0)):.2f}")
            log_event("INFO", "KalshiBroker", "Connected (LIVE)")
            
            self._sync_positions()
            return True
        except Exception as e:
            print(f"[KalshiBroker] Connection error: {e}")
            log_event("ERROR", "KalshiBroker", f"Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected and self._private_key is not None

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Execute signed Kalshi V2 request."""
        try:
            ts = str(int(time.time() * 1000))
            body_str = json.dumps(body, separators=(',', ':')) if body else ""
            
            # Message to sign: timestamp + method + path + body
            msg = f"{ts}{method}{path}{body_str}"
            
            signature = self._private_key.sign(
                msg.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            sig_b64 = base64.b64encode(signature).decode()
            
            headers = {
                "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-SIGNATURE": sig_b64,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "Content-Type": "application/json"
            }
            
            url = f"{KALSHI_API_BASE}{path}"
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=10)
            elif method == "POST":
                resp = requests.post(url, headers=headers, data=body_str, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=10)
            else:
                return {"error": "unsupported_method"}
                
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def _sync_positions(self) -> None:
        """Sync open positions from Kalshi into local state."""
        if not self.is_connected():
            return
        try:
            data = self._request("GET", "/trade-api/v2/portfolio/positions")
            self._open_positions.clear()

            positions = data.get("positions", [])
            for p in positions:
                qty = p.get("position", 0)
                if qty == 0: continue
                
                ticker = p.get("market_ticker")
                side = "YES" if qty > 0 else "NO"
                right = "C" if side == "YES" else "P"
                abs_qty = abs(qty)

                key = f"{ticker}_{right}"
                self._open_positions[key] = {
                    "local_symbol": ticker,
                    "right": right,
                    "qty": abs_qty,
                    "entry_price": 0.0,
                    "side": side,
                    "order_id": "EXISTING",
                    "entered_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")

    def discover_markets(self) -> list[dict]:
        """Discover active Kalshi event contracts."""
        if not self.is_connected():
            return []

        results = []
        try:
            data = self._request("GET", "/trade-api/v2/events", params={"limit": 200, "status": "open"})
            events = data.get("events", [])
            
            for event in events:
                e_ticker = event.get("event_ticker")
                if not _is_economic_market(e_ticker, event.get("title"), event.get("category")):
                    continue
                
                m_data = self._request("GET", "/trade-api/v2/markets", params={"event_ticker": e_ticker})
                markets = m_data.get("markets", [])
                
                for m in markets:
                    if m.get("status") != "active": continue
                        
                    for side in ["YES", "NO"]:
                        right = "C" if side == "YES" else "P"
                        results.append({
                            "underlier": e_ticker,
                            "local_symbol": m.get("ticker"),
                            "conid": None,
                            "right": right,
                            "strike": 0.0,
                            "last_trade_at": m.get("close_time", ""),
                            "exchange": "KALSHI",
                            "currency": "USD",
                            "long_name": m.get("title"),
                            "category": event.get("category"),
                            "side": side,
                        })
        except Exception as e:
            log_event("ERROR", "KalshiBroker", f"Market discovery error: {e}")

        return results

    def get_quote(self, ticker: str) -> dict:
        """Fetch bid/ask/mid using raw orderbook access."""
        if not self.is_connected():
            return {"local_symbol": ticker, "bid": None, "ask": None, "ts": datetime.now(timezone.utc).isoformat()}
        
        try:
            data = self._request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
            book = data.get("orderbook", {})
            
            yes_levels = book.get("yes", [])
            no_levels = book.get("no", [])
            
            # Yes Bid: The highest price someone is willing to pay for YES
            # No Bid: The highest price someone is willing to pay for NO
            # Ask for YES = 1.0 - (No Bid)
            yes_bid = float(yes_levels[0][0]) / 100.0 if yes_levels else None
            no_bid = float(no_levels[0][0]) / 100.0 if no_levels else None
            yes_ask = (1.0 - no_bid) if no_bid is not None else None
            
            mid = round((yes_bid + yes_ask) / 2.0, 4) if yes_bid and yes_ask else yes_bid or yes_ask
            spread = round(yes_ask - yes_bid, 4) if yes_bid and yes_ask else None

            return {
                "local_symbol": ticker,
                "bid": yes_bid,
                "ask": yes_ask,
                "mid": mid,
                "spread": spread,
                "implied_prob": mid,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"local_symbol": ticker, "bid": None, "ask": None, "ts": datetime.now(timezone.utc).isoformat()}

    def get_quotes_batch(self, contracts: list[dict]) -> list[dict]:
        return [self.get_quote(c["local_symbol"]) for c in contracts]

    def place_buy_order(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        """Place a limit buy order."""
        if not self.is_connected():
            return {"order_id": f"KS_PAPER_{uuid.uuid4().hex[:8]}", "price": limit_price, "qty": qty}

        ticker = contract_dict["local_symbol"]
        side = "yes" if contract_dict["right"] == "C" else "no"
        limit_cents = int(round(limit_price * 100))
        
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": int(qty),
            "type": "limit",
            "yes_price": limit_cents if side == "yes" else None,
            "no_price": limit_cents if side == "no" else None,
            "client_order_id": str(uuid.uuid4()),
        }
        
        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        order_id = resp.get("order_id", "ERR")
        
        if order_id != "ERR":
            print(f"[KalshiBroker] BUY {qty} {ticker} ({side.upper()}) @ {limit_price:.4f} | ID={order_id}")
            key = f"{ticker}_{contract_dict['right']}"
            self._open_positions[key] = {"qty": qty, "side": side.upper(), "local_symbol": ticker}

        return {"order_id": order_id, "price": limit_price, "qty": qty}

    def flatten_position(self, local_symbol: str, right: str, qty: int, **kwargs) -> dict:
        """Exit a position by selling."""
        if not self.is_connected(): return {"order_id": "PAPER_EXIT"}
        
        side = "yes" if right == "C" else "no"
        key = f"{local_symbol}_{right}"
        
        quote = self.get_quote(local_symbol)
        # To sell YES, we hit the YES bid.
        # To sell NO, we hit the NO bid (which is 1 - yes_ask).
        if side == "yes":
            price = max(0.01, (quote.get("bid") or 0.01) - 0.01)
        else:
            no_bid = (1.0 - quote.get("ask")) if quote.get("ask") else 0.01
            price = max(0.01, no_bid - 0.01)
            
        limit_cents = int(round(price * 100))
        
        body = {
            "ticker": local_symbol,
            "action": "sell",
            "side": side,
            "count": int(qty),
            "type": "limit",
            "yes_price": limit_cents if side == "yes" else None,
            "no_price": limit_cents if side == "no" else None,
            "client_order_id": str(uuid.uuid4()),
        }
        
        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        self._open_positions.pop(key, None)
        return {"order_id": resp.get("order_id", "ERR"), "flattened_qty": qty}

    def get_positions(self) -> list[dict]:
        return list(self._open_positions.values())

    def get_account_balance(self) -> float:
        resp = self._request("GET", "/trade-api/v2/portfolio/balance")
        return float(resp.get("balance_dollars", 0))

    def disconnect(self) -> None:
        self._connected = False

_kalshi_broker: Optional[KalshiBroker] = None

def get_kalshi_broker() -> KalshiBroker:
    global _kalshi_broker
    if _kalshi_broker is None:
        _kalshi_broker = KalshiBroker()
    return _kalshi_broker
