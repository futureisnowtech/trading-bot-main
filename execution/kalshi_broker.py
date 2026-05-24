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
from config import SHADOW_EXECUTION
from logging_db.trade_logger import log_event, log_trade

logger = logging.getLogger(__name__)

# ── Credentials ───────────────────────────────────────────────────────────────
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")
KALSHI_API_BASE = "https://external-api.kalshi.com"

# ─── Kalshi Category & Keyword Dual-Gate ──────────────────────────────────────

APPROVED_CATEGORIES: set[str] = {
    "economics",
    "federal reserve",
    "financials",
    "recession",
    "climate and weather",
    "politics",
    "elections",
    "social",
}

GLOBAL_EXCLUDES: list[str] = [
    "sports", "entertainment", "celebrity", "award", "novelty",
    "oscar", "grammy", "movie", "box office", "actor", "actress",
    "tiktok", "youtube", "follower", "crypto", "bitcoin", "ethereum", 
    "btc", "eth"
]

CATEGORY_REQUIRED_KEYWORDS: dict[str, list[str]] = {
    "economics": [
        "cpi", "inflation", "fed", "fomc", "rate", "payroll", "nonfarm",
        "unemployment", "gdp", "pce", "retail", "housing", "consumer",
        "ppi", "production", "jobs", "employment", "macro", "economy",
        "debt", "budget", "target", "hike", "cut", "growth", "manufacturing"
    ],
    "federal reserve": [
        "fed", "fomc", "rate", "hike", "cut", "target", "powell", "balance sheet"
    ],
    "financials": [
        "yield", "treasury", "bond", "index", "price", "survey"
    ],
    "recession": [
        "recession", "contraction", "gdp", "nber"
    ],
    "climate and weather": [
        "temp", "temperature", "rain", "precip", "precipitation", "weather",
        "degree", "hurricane", "storm", "snow", "landfall", "cat 5", "category 5"
    ],
    "politics": [
        "president", "presidential", "senate", "house of representatives",
        "congress", "supreme court", "mayor", "governor", "prime minister",
        "cabinet", "policy", "bill", "legislation", "race", "seat"
    ],
    "elections": [
        "election", "vote", "popular vote", "electoral college", "nominee",
        "primary", "caucus", "senate", "house", "governor", "president", "race", "seat"
    ],
    "social": [
        "population", "census", "demographic", "migration"
    ]
}

def _is_economic_market(ticker: str, title: str, category: str = "") -> bool:
    """
    Expert Dual-Gate System for Kalshi Discovery.
    1. Category must be whitelisted.
    2. Must not contain global noise keywords (sports, crypto, celebrities).
    3. Must contain at least one high-signal keyword mapped to its category.
    """
    if not title or not ticker:
        return False
    
    t_lower = f"{ticker} {title}".lower()
    c_lower = category.lower() if category else ""

    # Gate 1: Category Whitelist
    if c_lower not in APPROVED_CATEGORIES:
        return False

    # Gate 2: Global Noise Exclusions
    for excl in GLOBAL_EXCLUDES:
        if excl in t_lower or excl in c_lower:
            return False

    # Gate 3: Category-Specific Signal Match
    required_kws = CATEGORY_REQUIRED_KEYWORDS.get(c_lower, [])
    if not required_kws:
        return False

    for kw in required_kws:
        if kw in t_lower:
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
        
        # v18.34: Shadow Mode
        if SHADOW_EXECUTION and method.upper() == "POST" and "orders" in path:
            print(f"[Kalshi] SHADOW MODE: Blocked {method} {path} body={body}")
            return {"order_id": f"shadow_{uuid.uuid4().hex[:8]}"}

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
                    
                    # ── Hard Liquidity Gate (v18.35) ────────────────────────────────
                    # Skip dormant markets with zero activity to avoid dead polling
                    oi = int(m.get("open_interest", 0))
                    vol = int(m.get("volume", 0))
                    liq = int(m.get("liquidity", 0))
                    
                    if oi == 0 and vol == 0:
                        continue # No trades, no interest
                        
                    if liq < 100: # Less than $1.00 of liquidity (cents based?)
                        # Kalshi liq is often in cents, 100 = $1.00
                        continue
                        
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
            yes_ask = round(1.0 - no_bid, 4) if no_bid is not None else None
            
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
            # RC: Un-silence the error for X-Ray observability
            msg = f"[KalshiBroker] get_quote error for {ticker}: {e}"
            logger.error(msg)
            log_event("ERROR", "KalshiBroker", msg)
            return {"local_symbol": ticker, "bid": None, "ask": None, "ts": datetime.now(timezone.utc).isoformat()}

    def get_quotes_batch(self, contracts: list[dict]) -> list[dict]:
        return [self.get_quote(c["local_symbol"]) for c in contracts]

    def place_buy_order(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        """Place a limit buy order."""
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi — blocking trade")

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
            self._open_positions[key] = {
                "qty": qty,
                "side": side.upper(),
                "local_symbol": ticker,
                "entry_price": limit_price,
            }
            # log_trade for database persistence
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_unknown"),
                    broker="kalshi",
                    symbol=ticker,
                    action="BUY",
                    order_type="Limit",
                    qty=qty,
                    price=limit_price,
                    order_id=order_id,
                    notes=kwargs.get("reason", ""),
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade error: {e}")

        return {"order_id": order_id, "price": limit_price, "qty": qty}

    def flatten_position(self, local_symbol: str, right: str, qty: int, **kwargs) -> dict:
        """Exit a position by selling."""
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi — blocking exit")
        
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
        order_id = resp.get("order_id", "ERR")
        
        # Calculate PnL
        pos_info = self._open_positions.pop(key, {})
        entry_price = pos_info.get("entry_price", 0.0)
        pnl_usd = (price - entry_price) * qty if entry_price > 0 else 0.0

        if order_id != "ERR":
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_exit"),
                    broker="kalshi",
                    symbol=local_symbol,
                    action="SELL",
                    order_type="Limit",
                    qty=qty,
                    price=price,
                    pnl_usd=pnl_usd,
                    order_id=order_id,
                    notes=kwargs.get("reason", "exit"),
                    won=(pnl_usd > 0)
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade exit error: {e}")

        return {
            "order_id": order_id,
            "flattened_qty": qty,
            "exit_price": price,
            "entry_price": entry_price,
            "pnl_usd": pnl_usd,
        }

    def get_position(self, local_symbol: str, right: str) -> Optional[dict]:
        key = f"{local_symbol}_{right}"
        return self._open_positions.get(key)

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
