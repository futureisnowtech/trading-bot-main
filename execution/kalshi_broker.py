"""
execution/kalshi_broker.py — Kalshi prediction market execution.

Kalshi is a CFTC-regulated prediction market. This broker implements the 
V2 API using the kalshi-python-sync SDK.

Architecture:
- Singleton pattern via get_kalshi_broker().
- Synchronous implementation (forecast loop runs on its own background thread).
- Maps Kalshi's 'yes'/'no' to the system's 'C'/'P' (Call/Put) right-side bias 
  to maintain compatibility with the forecast_contracts database schema.
"""

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict

# Add root to path for logging_db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logging_db.trade_logger import log_event, log_trade

logger = logging.getLogger(__name__)

# ── Credentials ───────────────────────────────────────────────────────────────
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")

# Kalshi zero-fee maker model, but we'll assume a small slippage/fee buffer for safety
KALSHI_FEE_PER_CONTRACT = 0.0

# Economic event categories to scan during discovery.
ECONOMIC_CATEGORIES: list[str] = [
    "Economics",
    "Federal Reserve",
    "Financials",
    "Recession",
]

# Markets to EXCLUDE from v1
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
    """Return True if this market is in-scope for economic and weather trading."""
    category_lower = category.lower()
    title_lower = title.lower()
    ticker_lower = ticker.lower()

    # Hard exclusions
    for excl in EXCLUDED_KEYWORDS:
        if excl in category_lower or excl in title_lower:
            return False

    # Allowed keywords for Economics and Weather
    allowed_keywords = [
        "cpi", "inflation", "fed", "fomc", "rate", "rates", "payroll",
        "nonfarm", "unemployment", "gdp", "pce", "retail", "housing",
        "consumer", "ppi", "production", "jobs", "employment", "macro",
        "economic", "economy", "debt", "budget",
        "temp", "temperature", "rain", "precip", "weather", "degree"
    ]
    for kw in allowed_keywords:
        if kw in title_lower or kw in ticker_lower or kw in category_lower:
            return True

    return False

class KalshiBroker:
    """
    Kalshi broker implementation for prediction market event contracts.

    Manages discovery, quote fetching, order placement, and position tracking.
    """

    def __init__(self) -> None:
        self._client = None
        self._connected = False
        self._open_positions: dict[str, dict] = {}  # key = f"{ticker}_{right}"
        
        # API classes
        self._account_api = None
        self._market_api = None
        self._events_api = None
        self._orders_api = None
        self._portfolio_api = None

    def connect(self) -> bool:
        """Connect to Kalshi using Key ID and Private Key Path."""
        if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY_PATH:
            log_event("ERROR", "KalshiBroker", "Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH in .env")
            return False

        try:
            from kalshi_python_sync import (
                Configuration, KalshiClient, AccountApi, MarketApi, 
                EventsApi, OrdersApi, PortfolioApi
            )
            
            # Read private key from path
            with open(KALSHI_PRIVATE_KEY_PATH, 'r') as f:
                private_key = f.read()

            config = Configuration(host="https://external-api.kalshi.com/trade-api/v2")
            config.api_key_id = KALSHI_API_KEY_ID
            config.private_key_pem = private_key

            self._client = KalshiClient(config)
            
            # Initialize API interfaces
            self._account_api = AccountApi(self._client)
            self._market_api = MarketApi(self._client)
            self._events_api = EventsApi(self._client)
            self._orders_api = OrdersApi(self._client)
            self._portfolio_api = PortfolioApi(self._client)
            
            # Verify connection by getting balance
            self._portfolio_api.get_balance()
            self._connected = True
            
            print("[KalshiBroker] Connected (LIVE) ✅")
            log_event("INFO", "KalshiBroker", "Connected (LIVE)")
            
            self._sync_positions()
            return True
        except Exception as e:
            print(f"[KalshiBroker] Connection error: {e}")
            log_event("ERROR", "KalshiBroker", f"Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    def _sync_positions(self) -> None:
        """Sync open positions from Kalshi into local state, including resting orders."""
        if not self.is_connected():
            return
        try:
            # 1. Sync actual filled positions
            portfolio = self._portfolio_api.get_positions()
            if hasattr(portfolio, 'market_positions'):
                for pos in portfolio.market_positions:
                    qty_fp = float(pos.position_fp) if pos.position_fp else 0.0
                    if qty_fp != 0:
                        ticker = pos.ticker
                        side = "YES" if qty_fp > 0 else "NO"
                        right = "C" if side == "YES" else "P"
                        key = f"{ticker}_{right}"
                        
                        self._open_positions[key] = {
                            "local_symbol": ticker,
                            "right": right,
                            "strike": 0.0,
                            "last_trade_at": "",
                            "conid": None,
                            "qty": abs(int(qty_fp)),
                            "entry_price": 0.0,
                            "side": side,
                            "order_id": "SYNCED",
                            "entered_at": datetime.now(timezone.utc).isoformat(),
                        }
            
            # 2. Sync resting orders (Exposure = Filled + Resting)
            # This prevents the system from firing multiple orders for the same target
            # if the previous limit order hasn't filled yet.
            resting = self._orders_api.get_orders(status="resting")
            if hasattr(resting, 'orders'):
                for order in resting.orders:
                    ticker = order.ticker
                    side = "YES" if order.side == "yes" else "NO"
                    right = "C" if side == "YES" else "P"
                    key = f"{ticker}_{right}"
                    qty = int(float(order.count_fp))
                    
                    if key in self._open_positions:
                        self._open_positions[key]["qty"] += qty
                    else:
                        self._open_positions[key] = {
                            "local_symbol": ticker,
                            "right": right,
                            "strike": 0.0,
                            "last_trade_at": "",
                            "conid": None,
                            "qty": qty,
                            "entry_price": float(order.yes_price_dollars) if side == "YES" else float(order.no_price_dollars),
                            "side": side,
                            "order_id": order.order_id,
                            "entered_at": datetime.now(timezone.utc).isoformat(),
                        }
                    print(f"[KalshiBroker] Included RESTING {side} {qty} {ticker}")
                    
            for key, p in self._open_positions.items():
                print(f"[KalshiBroker] Total Exposure {p['side']} {p['qty']} {p['local_symbol']}")
                
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")

    def discover_markets(
        self,
        category_filter: Optional[str] = None,
        underliers: Optional[list[str]] = None,
    ) -> list[dict]:
        """Discover active Kalshi event contracts."""
        if not self.is_connected():
            return []

        results = []
        try:
            events_resp = self._events_api.get_events(limit=100, status="open")
            for event in events_resp.events:
                if not _is_economic_market(event.event_ticker, event.title, event.category):
                    continue
                
                markets_resp = self._market_api.get_markets(event_ticker=event.event_ticker)
                for m in markets_resp.markets:
                    if m.status != "active":
                        continue
                        
                    for side in ["YES", "NO"]:
                        right = "C" if side == "YES" else "P"
                        results.append({
                            "underlier": event.event_ticker,
                            "local_symbol": m.ticker,
                            "conid": None,
                            "right": right,
                            "strike": 0.0,
                            "last_trade_at": m.close_time.strftime("%Y%m%d %H:%M:%S") if hasattr(m, 'close_time') and m.close_time else "",
                            "exchange": "KALSHI",
                            "currency": "USD",
                            "long_name": m.title,
                            "category": event.category,
                            "side": side,
                        })
        except Exception as e:
            log_event("ERROR", "KalshiBroker", f"Market discovery error: {e}")

        return results

    def get_quote(self, ticker: str, local_symbol: str = "") -> dict:
        """Fetch bid/ask/mid for a Kalshi ticker."""
        if not self.is_connected():
            return {
                "local_symbol": ticker,
                "bid": None, "ask": None, "mid": None, "spread": None,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        
        try:
            resp = self._market_api.get_market_orderbook(ticker)
            orderbook = resp.orderbook_fp
            
            best_yes_bid = float(orderbook.yes_dollars[0][0]) if orderbook.yes_dollars else None
            best_no_bid = float(orderbook.no_dollars[0][0]) if orderbook.no_dollars else None
            
            bid = best_yes_bid
            ask = (1.0 - best_no_bid) if best_no_bid is not None else None
            
            mid = round((bid + ask) / 2.0, 4) if bid and ask else bid or ask
            spread = round(ask - bid, 4) if bid and ask else None

            return {
                "local_symbol": ticker,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread,
                "implied_prob": mid,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"get_quote error for {ticker}: {e}")
            return {"local_symbol": ticker, "bid": None, "ask": None, "ts": datetime.now(timezone.utc).isoformat()}

    def get_quotes_batch(self, contracts: list[dict]) -> list[dict]:
        quotes = []
        for c in contracts:
            q = self.get_quote(c["local_symbol"])
            q["right"] = c.get("right", "")
            q["strike"] = c.get("strike", 0.0)
            quotes.append(q)
        return quotes

    def place_buy_order(
        self,
        contract_dict: dict,
        qty: int,
        limit_price: float,
        reason: str = "signal",
        strategy: str = "forecast_event",
    ) -> dict:
        """Place a limit buy order on Kalshi."""
        from kalshi_python_sync import CreateOrderRequest
        
        ticker = contract_dict["local_symbol"]
        right = contract_dict["right"]
        side = "YES" if right == "C" else "NO"
        kalshi_side = "yes" if side == "YES" else "no"

        if not self.is_connected():
            order_id = f"KS_PAPER_{uuid.uuid4().hex[:8]}"
            print(f"[KalshiBroker] ⚠️ Paper-logging BUY {qty} {ticker} ({side}) @ {limit_price:.4f}")
        else:
            try:
                # Use CreateOrderRequest (V1/V2 hybrid supported by SDK)
                req = CreateOrderRequest(
                    ticker=ticker,
                    action="buy",
                    side=kalshi_side,
                    count_fp=f"{float(qty):.2f}",
                    yes_price_dollars=f"{limit_price:.2f}" if kalshi_side == "yes" else None,
                    no_price_dollars=f"{limit_price:.2f}" if kalshi_side == "no" else None,
                    client_order_id=str(uuid.uuid4()),
                    time_in_force="good_till_canceled"
                )
                order_resp = self._orders_api.create_order(req)
                order_id = order_resp.order_id
                print(f"[KalshiBroker] BUY {qty} {ticker} ({side}) @ {limit_price:.4f} | ID={order_id}")
            except Exception as e:
                log_event("ERROR", "KalshiBroker", f"place_buy_order error: {e}")
                order_id = f"KS_ERR_{uuid.uuid4().hex[:8]}"

        # Update local position
        key = f"{ticker}_{right}"
        existing = self._open_positions.get(key, {})
        self._open_positions[key] = {
            "local_symbol": ticker,
            "right": right,
            "qty": existing.get("qty", 0) + qty,
            "entry_price": limit_price,
            "side": side,
            "order_id": order_id,
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }

        log_trade(
            strategy=strategy, broker="kalshi", symbol=ticker, action="BUY",
            order_type="Limit", qty=qty, price=limit_price,
            fee_usd=0, order_id=order_id, notes=f"side={side} reason={reason}"
        )
        return {"order_id": order_id, "price": limit_price, "side": side, "qty": qty}

    def flatten_position(
        self,
        local_symbol: str,
        right: str,
        qty: int,
        strategy: str = "forecast_event",
        reason: str = "exit",
    ) -> dict:
        """Flatten by selling the position with adversarial safeguards."""
        from kalshi_python_sync import CreateOrderRequest
        
        side = "YES" if right == "C" else "NO"
        kalshi_side = "yes" if side == "YES" else "no"
        key = f"{local_symbol}_{right}"
        
        pos = self._open_positions.get(key)
        if not pos:
            return {"error": "no_open_position"}

        flatten_qty = min(qty, pos.get("qty", 0))
        
        if self.is_connected():
            try:
                # ADVERSARY FIX #4: Cancel any resting BUY orders before selling
                # This prevents being trapped in a "buy back" loop during exit.
                resting = self._orders_api.get_orders(status="resting", ticker=local_symbol)
                if hasattr(resting, "orders"):
                    for o in resting.orders:
                        if o.action == "buy":
                            logger.info(f"[KalshiBroker] Canceling resting BUY order {o.order_id} before exit")
                            self._orders_api.cancel_order(order_id=o.order_id)

                # ADVERSARY FIX #2: Dynamic Slippage Control
                # Instead of hardcoded 0.01, we query the book and cross the spread by max $0.02.
                quote = self.get_quote(local_symbol)
                bid = quote.get("bid") # This is best YES bid
                ask = quote.get("ask") # This is (1 - best NO bid)
                
                if kalshi_side == "yes":
                    # Selling YES: We hit the best YES bid
                    # Slippage allowance: $0.02
                    limit_price = max(0.01, (bid - 0.02)) if bid is not None else 0.01
                    yes_price = f"{limit_price:.2f}"
                    no_price = "0.01"
                else:
                    # Selling NO: We hit the best NO bid (which is 1 - ask)
                    no_bid = (1.0 - ask) if ask is not None else None
                    limit_price = max(0.01, (no_bid - 0.02)) if no_bid is not None else 0.01
                    no_price = f"{limit_price:.2f}"
                    yes_price = "0.01"

                req = CreateOrderRequest(
                    ticker=local_symbol,
                    action="sell",
                    side=kalshi_side,
                    count_fp=f"{float(flatten_qty):.2f}",
                    yes_price_dollars=yes_price,
                    no_price_dollars=no_price,
                    client_order_id=str(uuid.uuid4()),
                    time_in_force="immediate_or_cancel"
                )
                order_resp = self._orders_api.create_order(req)
                order_id = order_resp.order_id
            except Exception as e:
                # ADVERSARY FIX #6: Post-Resolution API Spam Loop
                # If market is closed (HTTP 400), forcefully clear local position state.
                err_str = str(e).lower()
                if "market is not active" in err_str or "market closed" in err_str or "400" in err_str:
                    logger.warning(f"[KalshiBroker] Market {local_symbol} resolved/closed. Clearing local state.")
                    self._open_positions.pop(key, None)
                    return {"order_id": "RESOLVED", "price": 0, "side": side, "qty": flatten_qty}
                
                log_event("ERROR", "KalshiBroker", f"flatten_position error: {e}")
                order_id = "ERR"
        else:
            order_id = "PAPER_EXIT"

        remaining = pos.get("qty", 0) - flatten_qty
        if remaining <= 0:
            self._open_positions.pop(key, None)
        else:
            self._open_positions[key]["qty"] = remaining

        return {"order_id": order_id, "flattened_qty": flatten_qty}

    def get_positions(self) -> list[dict]:
        return list(self._open_positions.values())

    def get_account_balance(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            resp = self._portfolio_api.get_balance()
            return float(resp.balance) / 100.0
        except Exception:
            return 0.0

    def disconnect(self) -> None:
        self._connected = False
        self._client = None

# ── Singleton ──────────────────────────────────────────────────────────────────
_kalshi_broker: Optional[KalshiBroker] = None

def get_kalshi_broker() -> KalshiBroker:
    global _kalshi_broker
    if _kalshi_broker is None:
        _kalshi_broker = KalshiBroker()
    return _kalshi_broker
