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
    "economy",
    "federal reserve",
    "financials",
    "finance",
    "recession",
    "climate and weather",
    "weather",
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
    "economy": [
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
    "finance": [
        "yield", "treasury", "bond", "index", "price", "survey"
    ],
    "recession": [
        "recession", "contraction", "gdp", "nber"
    ],
    "climate and weather": [
        "temp", "temperature", "rain", "precip", "precipitation", "weather",
        "degree", "hurricane", "storm", "snow", "landfall", "cat 5", "category 5"
    ],
    "weather": [
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
            
            # v18.34: Ensure method is uppercase for signature
            method_upper = method.upper()
            
            # v18.34: Kalshi V2 signing typically ONLY uses ts + method + path.
            # Query params and body are usually excluded from the signature msg 
            # but included in the actual request.
            msg = f"{ts}{method_upper}{path}"
            
            signature = self._private_key.sign(
                msg.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
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
            
            body_str = json.dumps(body, separators=(',', ':')) if body else ""
            
            url = f"{KALSHI_API_BASE}{path}"
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=10)
            elif method == "POST":
                resp = requests.post(url, headers=headers, data=body_str, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=10)
            else:
                return {"error": "unsupported_method"}
            
            try:
                return resp.json()
            except Exception as json_err:
                logger.error(f"[KalshiBroker] JSON decode failed for {url}. Status={resp.status_code} Text={resp.text[:200]}")
                return {"error": f"json_decode_failed: {str(json_err)}"}
        except Exception as e:
            return {"error": str(e)}

    def _sync_positions(self) -> None:
        """Sync open positions from Kalshi into local state."""
        if not self.is_connected():
            return
        try:
            # v18.34: Kalshi V2 uses 'market_positions' array and 'position_fp' field.
            data = self._request("GET", "/trade-api/v2/portfolio/positions")
            self._open_positions.clear()

            positions = data.get("market_positions", [])
            for p in positions:
                # position_fp is a signed fixed-point string. 
                # Positive = YES contracts, Negative = NO contracts.
                qty_str = p.get("position_fp", "0")
                qty = float(qty_str)
                if qty == 0: continue
                
                ticker = p.get("ticker")
                side = "YES" if qty > 0 else "NO"
                right = "C" if side == "YES" else "P"
                abs_qty = abs(qty)

                key = f"{ticker}_{right}"
                self._open_positions[key] = {
                    "local_symbol": ticker,
                    "right": right,
                    "qty": abs_qty,
                    "entry_price": 0.0, # Not available in summary, will be enriched by DB
                    "side": side,
                    "order_id": "EXISTING",
                    "entered_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")

    def discover_markets(self) -> list[dict]:
        """
        Discover active Kalshi event contracts.
        v19.1.5: Implements Precision Lane Targeting for Weather.
        Increases pagination and targets specific series to avoid discovery blindness.
        """
        if not self.is_connected():
            return []

        results = []
        try:
            # ── Precision Targeting: Weather Series ────────────────────────────
            # v19.1.6: Explicitly query all series for our 15+ expanded stations
            from data.kalshi_weather_monitor import STATIONS
            
            weather_events = []
            for loc in STATIONS.values():
                for series_id in loc.get("series", []):
                    data = self._request("GET", "/trade-api/v2/events", params={"series_ticker": series_id, "status": "open"})
                    weather_events.extend(data.get("events", []))
            
            # ── Generic Discovery Loop (v18.36 Expanded) ──────────────────────
            # Fetch up to 2000 events to catch macro/politics shifts.
            generic_events = []
            cursor = ""
            for _ in range(10):  # 10 pages of 200 = 2000 events
                data = self._request("GET", "/trade-api/v2/events", params={"limit": 200, "status": "open", "cursor": cursor})
                page_events = data.get("events", [])
                if not page_events: break
                generic_events.extend(page_events)
                cursor = data.get("cursor", "")
                if not cursor: break
            
            # Combine, ensuring unique events by ticker
            seen_tickers = set()
            all_events = []
            for e in (weather_events + generic_events):
                ticker = e.get("event_ticker")
                if ticker not in seen_tickers:
                    all_events.append(e)
                    seen_tickers.add(ticker)

            for event in all_events:
                e_ticker = event.get("event_ticker")
                category = event.get("category", "")
                is_weather = "weather" in category.lower()
                
                if not _is_economic_market(e_ticker, event.get("title"), category):
                    continue
                
                m_data = self._request("GET", "/trade-api/v2/markets", params={"event_ticker": e_ticker})
                markets = m_data.get("markets", [])
                
                for m in markets:
                    if m.get("status") != "active": continue
                    
                    # ── Hard Liquidity Gate (v18.35) ────────────────────────────────
                    # Skip dormant markets with zero activity to avoid dead polling.
                    # v19.1.5: Bypass for weather alpha — we want to be first in.
                    if not is_weather:
                        try:
                            vol_raw = m.get("volume_fp")
                            liq_raw = m.get("liquidity_dollars")
                            if vol_raw is not None and liq_raw is not None:
                                if float(vol_raw or 0) <= 0.0 and float(liq_raw or 0) <= 0.0:
                                    continue 
                        except (ValueError, TypeError):
                            continue

                    # ── v19.1.6: Dynamic Strike Extraction ──────────────────────
                    # Extract strike from ticker (e.g., -T82, -B80.5)
                    strike = 0.0
                    if is_weather:
                        import re
                        match = re.search(r'-[TBL](-?\d+\.?\d*)$', m.get("ticker", ""))
                        if match:
                            try:
                                strike = float(match.group(1))
                            except ValueError:
                                pass

                    for side in ["YES", "NO"]:
                        right = "C" if side == "YES" else "P"
                        results.append({
                            "underlier": e_ticker,
                            "local_symbol": m.get("ticker"),
                            "conid": None,
                            "right": right,
                            "strike": strike,
                            "last_trade_at": m.get("close_time", ""),
                            "exchange": "KALSHI",
                            "currency": "USD",
                            "long_name": m.get("title"),
                            "category": category,
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
            # v18.34: Kalshi V2 uses 'orderbook_fp' and 'yes_dollars' / 'no_dollars'
            data = self._request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
            book = data.get("orderbook_fp", {})
            
            yes_levels = book.get("yes_dollars", [])
            no_levels = book.get("no_dollars", [])
            
            # Yes Bid: The highest price someone is willing to pay for YES (last element in yes_dollars)
            # No Bid: The highest price someone is willing to pay for NO (last element in no_dollars)
            # v18.34: In orderbook_fp, levels are sorted by price ascending. 
            # Highest bid is the LAST element.
            yes_bid = float(yes_levels[-1][0]) if yes_levels else None
            no_bid = float(no_levels[-1][0]) if no_levels else None
            
            # Ask for YES = 1.0 - (No Bid)
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

    def get_historical_candles(self, ticker: str, interval_min: int = 1, limit: int = 100) -> list[dict]:
        """
        Fetch historical candlesticks for a market.
        Valid interval_min: 1 (1m), 60 (1h), 1440 (1d).
        Returns a list of candle dicts with keys: o, h, l, c, ts_open, ts_close.
        """
        if not self.is_connected():
            return []
        
        # Kalshi V2 API strictly allows 1, 60, 1440
        if interval_min not in [1, 60, 1440]:
            logger.warning(f"[KalshiBroker] Unsupported interval {interval_min}m. Defaulting to 1m.")
            interval_min = 1

        # Compute start_ts and end_ts (Unix seconds)
        # v18.34: Fetch enough history for 100 bars of the requested interval
        now_ts = int(time.time())
        lookback_sec = interval_min * 60 * (limit + 10)
        start_ts = now_ts - lookback_sec

        params = {
            "market_tickers": ticker,
            "period_interval": interval_min,
            "start_ts": start_ts,
            "end_ts": now_ts
        }
        
        # v18.34: Verified endpoint /trade-api/v2/markets/candlesticks
        data = self._request("GET", "/trade-api/v2/markets/candlesticks", params=params)
        
        if "error" in data:
            logger.warning(f"[KalshiBroker] Candlestick API error for {ticker}: {data['error']}")
            return []

        markets = data.get("markets", [])
        if not markets:
            logger.debug(f"[KalshiBroker] No market data in response for {ticker}.")
            return []
        
        candles = markets[0].get("candlesticks", [])
        results = []
        for c in candles:
            # We use mid-price (bid+ask)/2 for our bars
            # Handle cases where bid/ask might be missing or only have close
            try:
                bid_o = float(c.get("yes_bid", {}).get("open_dollars") or 0)
                ask_o = float(c.get("yes_ask", {}).get("open_dollars") or 1.0)
                
                bid_h = float(c.get("yes_bid", {}).get("high_dollars") or 0)
                ask_h = float(c.get("yes_ask", {}).get("high_dollars") or 1.0)
                
                bid_l = float(c.get("yes_bid", {}).get("low_dollars") or 0)
                ask_l = float(c.get("yes_ask", {}).get("low_dollars") or 1.0)
                
                bid_c = float(c.get("yes_bid", {}).get("close_dollars") or 0)
                ask_c = float(c.get("yes_ask", {}).get("close_dollars") or 1.0)

                # Fallback to price if bid/ask missing (unlikely in V2 but safe)
                if not bid_c and not ask_c:
                    p = float(c.get("price", {}).get("close_dollars") or 0)
                    bid_c = ask_c = p

                results.append({
                    "o": round((bid_o + ask_o) / 2.0, 4),
                    "h": round((bid_h + ask_h) / 2.0, 4),
                    "l": round((bid_l + ask_l) / 2.0, 4),
                    "c": round((bid_c + ask_c) / 2.0, 4),
                    "ts_open": datetime.fromtimestamp(c.get("end_period_ts", 0) - (interval_min * 60), tz=timezone.utc).isoformat(),
                    "ts_close": datetime.fromtimestamp(c.get("end_period_ts", 0), tz=timezone.utc).isoformat(),
                })
            except (ValueError, TypeError):
                continue
        
        results.sort(key=lambda x: x["ts_open"])
        return results

    def get_quotes_batch(self, contracts: list[dict]) -> list[dict]:
        return [self.get_quote(c["local_symbol"]) for c in contracts]

    def place_buy_order(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        """Place a buy order (limit or market)."""
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi — blocking trade")

        ticker = contract_dict["local_symbol"]
        side = "yes" if contract_dict["right"] == "C" else "no"
        order_type = kwargs.get("type", "limit").lower()
        
        # v18.34: Kalshi V2 expects either yes_price or no_price, not both with nulls.
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": int(qty),
            "type": order_type,
            "client_order_id": str(uuid.uuid4()),
        }

        if order_type == "limit":
            limit_cents = int(round(limit_price * 100))
            if side == "yes":
                body["yes_price"] = limit_cents
            else:
                body["no_price"] = limit_cents
        
        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        order_info = resp.get("order", {})
        order_id = order_info.get("order_id", "ERR")
        
        if order_id == "ERR":
            order_id = resp.get("order_id", "ERR")
            
        if order_id == "ERR":
            logger.error(f"[KalshiBroker] {order_type.upper()} buy failed for {ticker}. Response: {resp}")
        
        if order_id != "ERR":
            print(f"[KalshiBroker] BUY {qty} {ticker} ({side.upper()}) @ {limit_price:.4f} [{order_type.upper()}] | ID={order_id}")
            key = f"{ticker}_{contract_dict['right']}"
            self._open_positions[key] = {
                "qty": qty,
                "side": side.upper(),
                "local_symbol": ticker,
                "entry_price": limit_price,
            }
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_unknown"),
                    broker="kalshi",
                    symbol=ticker,
                    action="BUY",
                    order_type=order_type.capitalize(),
                    qty=qty,
                    price=limit_price,
                    order_id=order_id,
                    notes=kwargs.get("reason", ""),
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade error: {e}")

        return {"order_id": order_id, "price": limit_price, "qty": qty}

    def flatten_position(self, local_symbol: str, right: str, qty: int, **kwargs) -> dict:
        """
        Exit a position immediately. 
        v19.7: Uses MARKET order for guaranteed immediate exit and clears cache 
        regardless of API success to prevent logic deadlocks.
        """
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi — blocking exit")
        
        side = "yes" if right == "C" else "no"
        key = f"{local_symbol}_{right}"
        
        # v19.7: Forced Market Execution for Salvage/TP
        # v19.8: Liquidity Floor. Skip if Bid is literally zero.
        quote = self.get_quote(local_symbol)
        bid_price = float(quote.get("bid") or 0.0)
        
        if bid_price < 0.01:
            logger.warning(f"[KalshiBroker] Zero liquidity detected for {local_symbol} (Bid=${bid_price}). Discarding position without API call.")
            self._open_positions.pop(key, {}) # Still remove from local cache
            return {"order_id": "DISCARDED", "exit_price": 0.0, "pnl_usd": 0.0}

        body = {
            "ticker": local_symbol,
            "action": "sell",
            "side": side,
            "count": int(qty),
            "type": "market",
            "client_order_id": str(uuid.uuid4()),
        }
        
        # Pre-emptively clear from cache to prevent loop deadlock
        pos_info = self._open_positions.pop(key, {})
        entry_price = float(pos_info.get("entry_price") or 0.50)

        try:
            resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
            order_info = resp.get("order", {})
            order_id = order_info.get("order_id") or resp.get("order_id", "ERR")
            exit_price = float(order_info.get("price", 0) / 100.0) if order_info.get("price") else 0.0
            
            if order_id == "ERR":
                logger.error(f"[KalshiBroker] Market sell failed for {local_symbol}: {resp}")
            else:
                logger.info(f"[KalshiBroker] FLATTENED {qty} {local_symbol} via MARKET ID={order_id}")
                
            pnl_usd = (exit_price - entry_price) * qty if exit_price > 0 else 0.0
            
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_exit"),
                    broker="kalshi",
                    symbol=local_symbol,
                    action="SELL",
                    order_type="Market",
                    qty=qty,
                    price=exit_price,
                    pnl_usd=pnl_usd,
                    order_id=order_id,
                    notes=kwargs.get("reason", "salvage_exit"),
                    won=(pnl_usd > 0)
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade exit error: {e}")

        except Exception as e:
            logger.error(f"[KalshiBroker] Fatal exception during flatten: {e}")
            order_id = "FATAL"
            exit_price = 0.0
            pnl_usd = 0.0

        return {
            "order_id": order_id,
            "flattened_qty": qty,
            "exit_price": exit_price,
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
