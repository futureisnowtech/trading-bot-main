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
from config import KALSHI_FEE_PER_CONTRACT, SHADOW_EXECUTION
from logging_db.trade_logger import log_event, log_trade

logger = logging.getLogger(__name__)

# ── Credentials ───────────────────────────────────────────────────────────────
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")
KALSHI_API_BASE = "https://external-api.kalshi.com"

# ─── Kalshi Weather Filter (Purified) ────────────────────────────────────────

def _is_weather_market(ticker: str, title: str, category: str = "") -> bool:
    """
    Hardened Weather Filter.
    Only allows markets that are explicitly weather-related.
    """
    if not title or not ticker:
        return False
    
    t_lower = f"{ticker} {title}".lower()
    c_lower = category.lower() if category else ""

    # v19.1.KALSHI: Pure weather focus.
    weather_keywords = ["temp", "temperature", "rain", "precip", "precipitation", "weather", "degree", "hurricane", "storm", "snow", "landfall", "cat 5", "category 5"]
    
    if "weather" in c_lower or any(kw in t_lower for kw in weather_keywords):
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
        
        if SHADOW_EXECUTION and method.upper() == "POST" and "orders" in path:
            print(f"[Kalshi] SHADOW MODE: Blocked {method} {path} body={body}")
            return {"order_id": f"shadow_{uuid.uuid4().hex[:8]}"}

        try:
            ts = str(int(time.time() * 1000))
            method_upper = method.upper()
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
            data = self._request("GET", "/trade-api/v2/portfolio/positions")
            self._open_positions.clear()

            positions = data.get("market_positions", [])
            for p in positions:
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
                    "entry": 0.0,
                    "entry_price": 0.0,
                    "side": side,
                    "forecast_yes_prob": None,
                    "order_id": "EXISTING",
                    "entered_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")

    def discover_markets(self) -> list[dict]:
        """Discover active Kalshi weather contracts."""
        if not self.is_connected():
            return []

        results = []
        try:
            from data.kalshi_weather_monitor import STATIONS
            
            weather_events = []
            for loc in STATIONS.values():
                for series_id in loc.get("series", []):
                    data = self._request("GET", "/trade-api/v2/events", params={"series_ticker": series_id, "status": "open"})
                    weather_events.extend(data.get("events", []))
            
            generic_events = []
            cursor = ""
            for _ in range(5):  # Fewer pages for purified focus
                data = self._request("GET", "/trade-api/v2/events", params={"limit": 200, "status": "open", "cursor": cursor})
                page_events = data.get("events", [])
                if not page_events: break
                generic_events.extend(page_events)
                cursor = data.get("cursor", "")
                if not cursor: break
            
            seen_tickers = set()
            all_events = []
            for e in (weather_events + generic_events):
                ticker = e.get("event_ticker")
                if ticker not in seen_tickers:
                    all_events.append(e)
                    seen_tickers.add(ticker)

            for event in all_events:
                # SRE FIX: HARD WEATHER GATE (Sovereign Mandate Enforcement)
                cat = event.get("category", "")
                ticker = event.get("event_ticker", "")
                
                if "Weather" not in cat and not ticker.startswith("KX"):
                    continue

                if not _is_weather_market(ticker, event.get("title"), cat):
                    continue
                
                m_data = self._request(
                    "GET", "/trade-api/v2/markets", params={"event_ticker": ticker}
                )
                markets = m_data.get("markets", [])
                
                for m in markets:
                    if m.get("status") != "active": continue
                    
                    strike = 0.0
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
                            "underlier": ticker,
                            "local_symbol": m.get("ticker"),
                            "conid": None,
                            "right": right,
                            "strike": strike,
                            "last_trade_at": m.get("close_time", ""),
                            "exchange": "KALSHI",
                            "currency": "USD",
                            "long_name": m.get("title"),
                            "category": cat,
                            "side": side,
                        })
        except Exception as e:
            log_event("ERROR", "KalshiBroker", f"Market discovery error: {e}")

        return results

    def get_quote(self, ticker: str) -> dict:
        """Fetch bid/ask/mid using raw orderbook access."""
        if not self.is_connected():
            return {
                "local_symbol": ticker,
                "bid": None,
                "ask": None,
                "bid_vol": 0.0,
                "ask_vol": 0.0,
                "bid_size": 0.0,
                "ask_size": 0.0,
                "yes_bid": None,
                "yes_ask": None,
                "yes_bid_vol": 0.0,
                "yes_ask_vol": 0.0,
                "yes_bid_size": 0.0,
                "yes_ask_size": 0.0,
                "no_bid": None,
                "no_ask": None,
                "no_bid_vol": 0.0,
                "no_ask_vol": 0.0,
                "no_bid_size": 0.0,
                "no_ask_size": 0.0,
                "mid": None,
                "spread": None,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        
        try:
            data = self._request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
            book = data.get("orderbook_fp", {})
            
            yes_levels = book.get("yes_dollars", [])
            no_levels = book.get("no_dollars", [])

            def _level_num(levels: list, idx: int, default: float | None = None) -> float | None:
                if not levels:
                    return default
                try:
                    return float(levels[-1][idx])
                except (TypeError, ValueError, IndexError):
                    return default

            yes_bid = _level_num(yes_levels, 0)
            yes_bid_vol = _level_num(yes_levels, 1, 0.0) or 0.0

            no_bid = _level_num(no_levels, 0)
            no_bid_vol = _level_num(no_levels, 1, 0.0) or 0.0
            
            yes_ask = round(1.0 - no_bid, 4) if no_bid is not None else None
            yes_ask_vol = no_bid_vol
            no_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None
            no_ask_vol = yes_bid_vol

            def _mid_and_spread(bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
                if bid is not None and ask is not None:
                    return round((bid + ask) / 2.0, 4), round(ask - bid, 4)
                return (bid if bid is not None else ask), None

            yes_mid, yes_spread = _mid_and_spread(yes_bid, yes_ask)
            no_mid, no_spread = _mid_and_spread(no_bid, no_ask)

            return {
                "local_symbol": ticker,
                "bid": yes_bid,
                "bid_vol": yes_bid_vol,
                "bid_size": yes_bid_vol,
                "ask": yes_ask,
                "ask_vol": yes_ask_vol,
                "ask_size": yes_ask_vol,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "yes_bid_vol": yes_bid_vol,
                "yes_ask_vol": yes_ask_vol,
                "yes_bid_size": yes_bid_vol,
                "yes_ask_size": yes_ask_vol,
                "yes_mid": yes_mid,
                "yes_spread": yes_spread,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_bid_vol": no_bid_vol,
                "no_ask_vol": no_ask_vol,
                "no_bid_size": no_bid_vol,
                "no_ask_size": no_ask_vol,
                "no_mid": no_mid,
                "no_spread": no_spread,
                "mid": yes_mid,
                "spread": yes_spread,
                "implied_prob": yes_mid,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"[KalshiBroker] get_quote error for {ticker}: {e}")
            return {"local_symbol": ticker, "bid": None, "ask": None, "ts": datetime.now(timezone.utc).isoformat()}

    def get_historical_candles(self, ticker: str, interval_min: int = 1, limit: int = 100) -> list[dict]:
        if not self.is_connected():
            return []
        
        if interval_min not in [1, 60, 1440]:
            interval_min = 1

        now_ts = int(time.time())
        lookback_sec = interval_min * 60 * (limit + 10)
        start_ts = now_ts - lookback_sec

        params = {
            "market_tickers": ticker,
            "period_interval": interval_min,
            "start_ts": start_ts,
            "end_ts": now_ts
        }
        
        data = self._request("GET", "/trade-api/v2/markets/candlesticks", params=params)
        
        if "error" in data:
            return []

        markets = data.get("markets", [])
        if not markets:
            return []
        
        candles = markets[0].get("candlesticks", [])
        results = []
        for c in candles:
            try:
                bid_o = float(c.get("yes_bid", {}).get("open_dollars") or 0)
                ask_o = float(c.get("yes_ask", {}).get("open_dollars") or 1.0)
                
                bid_h = float(c.get("yes_bid", {}).get("high_dollars") or 0)
                ask_h = float(c.get("yes_ask", {}).get("high_dollars") or 1.0)
                
                bid_l = float(c.get("yes_bid", {}).get("low_dollars") or 0)
                ask_l = float(c.get("yes_ask", {}).get("low_dollars") or 1.0)
                
                bid_c = float(c.get("yes_bid", {}).get("close_dollars") or 0)
                ask_c = float(c.get("yes_ask", {}).get("close_dollars") or 1.0)

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
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        ticker = contract_dict["local_symbol"]
        side = "yes" if contract_dict["right"] == "C" else "no"
        order_type = kwargs.get("type", "limit").lower()
        
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
        status = order_info.get("status")
        
        # SRE FIX: Await legitimate fills. No more $0.00 ghost trades.
        if status == "executed":
            fill_price = float(order_info.get("average_price", 0.0)) / 100.0
            order_id = order_info.get("order_id", "ERR")
            
            print(f"[KalshiBroker] BUY {qty} {ticker} ({side.upper()}) @ {fill_price:.4f} | ID={order_id}")
            key = f"{ticker}_{contract_dict['right']}"
            self._open_positions[key] = {
                "qty": qty,
                "side": side.upper(),
                "local_symbol": ticker,
                "right": contract_dict["right"],
                "entry": fill_price,
                "entry_price": fill_price,
                "forecast_yes_prob": kwargs.get("forecast_yes_prob"),
                "last_trade_at": contract_dict.get("last_trade_at", ""),
                "entered_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_weather"),
                    broker="kalshi",
                    symbol=ticker,
                    action="BUY",
                    order_type=order_type.capitalize(),
                    qty=qty,
                    price=fill_price,
                    fee_usd=KALSHI_FEE_PER_CONTRACT * qty,
                    order_id=order_id,
                    notes=kwargs.get("reason", ""),
                    contract_side=side.upper(),
                    forecast_yes_prob=kwargs.get("forecast_yes_prob"),
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade error: {e}")
            return {
                "order_id": order_id,
                "status": status,
                "price": fill_price,
                "qty": qty,
            }
            
        elif status in ["resting", "pending"]:
            logger.info(f"Order resting, not updating positions table yet. ID: {order_info.get('order_id')}")
            return {"order_id": order_info.get("order_id"), "status": status}
        else:
            logger.error(f"Order failed or rejected: {resp}")
            return {"order_id": "ERR", "status": status}

    def place_sell_order(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        """SRE FIX: Dedicated Sell Order Handler for Limit Exits."""
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        ticker = contract_dict["local_symbol"]
        # In Kalshi, selling a YES is action=sell side=yes (if you held YES)
        # OR buying a NO. The runner seems to use flatten_position for exits.
        # But if the runner calls place_sell_order, we need to know the 'side' held.
        # Assume we held YES for now as it's the primary weather bet.
        side = kwargs.get("side", "yes").lower() 
        order_type = kwargs.get("type", "limit").lower()

        body = {
            "ticker": ticker,
            "action": "sell",
            "side": side,
            "count": int(qty),
            "type": order_type,
            "client_order_id": str(uuid.uuid4()),
        }

        if order_type == "limit":
            limit_cents = int(round(limit_price * 100))
            if side == "yes": body["yes_price"] = limit_cents
            else: body["no_price"] = limit_cents

        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        order_info = resp.get("order", {})
        status = order_info.get("status")

        if status == "executed":
            exit_price = float(order_info.get("average_price", 0.0)) / 100.0
            order_id = order_info.get("order_id", "ERR")
            print(f"[KalshiBroker] SELL {qty} {ticker} @ {exit_price:.4f} | ID={order_id}")
            
            # PnL Calc
            key_yes = f"{ticker}_C"; key_no = f"{ticker}_P"
            pos_info = self._open_positions.pop(key_yes, {}) or self._open_positions.pop(key_no, {})
            entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)
            pnl_usd = (exit_price - entry_price) * qty
            
            try:
                log_trade(
                    strategy=kwargs.get("strategy", "forecast_exit"),
                    broker="kalshi",
                    symbol=ticker,
                    action="SELL",
                    order_type=order_type.capitalize(),
                    qty=qty,
                    price=exit_price,
                    fee_usd=KALSHI_FEE_PER_CONTRACT * qty,
                    pnl_usd=pnl_usd,
                    order_id=order_id,
                    notes=kwargs.get("reason", ""),
                    won=(pnl_usd > 0),
                    contract_side=pos_info.get("side", side).upper(),
                    forecast_yes_prob=pos_info.get("forecast_yes_prob"),
                )
            except Exception as e:
                logger.error(f"[KalshiBroker] log_trade exit error: {e}")
            return {
                "order_id": order_id,
                "status": status,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_usd": pnl_usd,
            }
        
        return {"order_id": order_info.get("order_id", "ERR"), "status": status}

    def flatten_position(self, local_symbol: str, right: str, qty: int, **kwargs) -> dict:
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")
        
        side = "yes" if right == "C" else "no"
        key = f"{local_symbol}_{right}"
        
        quote = self.get_quote(local_symbol)
        bid_key = "yes_bid" if right == "C" else "no_bid"
        bid_price = float(quote.get(bid_key) or 0.0)
        
        if bid_price < 0.01:
            return {
                "order_id": "ERR",
                "status": "no_bid_liquidity",
                "exit_price": 0.0,
                "entry_price": float(
                    (self._open_positions.get(key) or {}).get("entry_price")
                    or (self._open_positions.get(key) or {}).get("entry")
                    or 0.50
                ),
                "pnl_usd": 0.0,
            }

        body = {
            "ticker": local_symbol,
            "action": "sell",
            "side": side,
            "count": int(qty),
            "type": "market",
            "client_order_id": str(uuid.uuid4()),
        }
        if side == "yes":
            body["yes_price"] = 1
        else:
            body["no_price"] = 1
        
        pos_info = self._open_positions.get(key, {})
        entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)

        try:
            resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
            order_info = resp.get("order", {})
            order_id = order_info.get("order_id") or resp.get("order_id", "ERR")
            status = order_info.get("status")

            if status == "executed":
                exit_price = (
                    float(order_info.get("average_price", 0.0)) / 100.0
                    if order_info.get("average_price") is not None
                    else float(order_info.get("price", 0.0)) / 100.0
                )
                pnl_usd = (exit_price - entry_price) * qty if exit_price > 0 else 0.0
                self._open_positions.pop(key, None)

                try:
                    log_trade(
                        strategy=kwargs.get("strategy", "forecast_exit"),
                        broker="kalshi",
                        symbol=local_symbol,
                        action="SELL",
                        order_type="Market",
                        qty=qty,
                        price=exit_price,
                        fee_usd=KALSHI_FEE_PER_CONTRACT * qty,
                        pnl_usd=pnl_usd,
                        order_id=order_id,
                        notes=kwargs.get("reason", "salvage_exit"),
                        won=(pnl_usd > 0),
                        contract_side=pos_info.get("side", side.upper()),
                        forecast_yes_prob=pos_info.get("forecast_yes_prob"),
                    )
                except Exception as e:
                    logger.error(f"[KalshiBroker] log_trade exit error: {e}")
            else:
                exit_price = 0.0
                pnl_usd = 0.0

        except Exception as e:
            logger.error(f"[KalshiBroker] Fatal exception during flatten: {e}")
            order_id = "FATAL"
            exit_price = 0.0
            pnl_usd = 0.0

        return {
            "order_id": order_id,
            "status": status if "status" in locals() else "error",
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
