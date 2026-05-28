"""
execution/coinbase_spot_broker.py — Coinbase Advanced Trade spot broker adapter.

Supports BTC-USD, ETH-USD, SOL-USD, XRP-USD, LTC-USD, DOGE-USD, ADA-USD,
and LINK-USD spot. No leverage, no shorting, no margin.

Authentication — same CDP JWT / ES256 credentials as coinbase_broker.py:
  COINBASE_CDP_KEY_NAME    organizations/{org_id}/apiKeys/{key_id}
  COINBASE_CDP_PRIVATE_KEY EC private key in PEM format (\\n-escaped in .env)

Spot API base: https://api.coinbase.com/api/v3/brokerage/
(NOT /cfm/ — that is for Coinbase Financial Markets futures)

Live mode only. Paper mode excised v18.17.

Fail-closed blocked reasons (typed strings returned in result dicts):
  spot_symbol_not_allowed
  spot_lane_disabled
  spot_balance_unavailable
  spot_broker_ack_missing
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import time
import uuid
from typing import Dict, List, Optional

from config import (
    COINBASE_CDP_KEY_NAME,
    COINBASE_CDP_PRIVATE_KEY,
    SHADOW_EXECUTION,
)

logger = logging.getLogger(__name__)

# ── Dependency checks ─────────────────────────────────────────────────────────
try:
    import jwt as _pyjwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    _JWT_OK = True
except ImportError:
    _JWT_OK = False
    logger.warning("[spot] PyJWT / cryptography not installed — live mode disabled")

try:
    import requests as _requests

    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    logger.warning("[spot] requests not installed — live mode disabled")

# ── Allowed spot symbols (lowercase base asset → Coinbase product_id) ─────────
# v18.17: Authoritative increments (from Coinbase product API)
SPOT_PRODUCT_SPECS: dict[str, dict] = {
    "BTC": {"product_id": "BTC-USD", "base_increment": "0.00000001", "quote_increment": "0.01", "base_precision": 8, "quote_precision": 2},
    "ETH": {"product_id": "ETH-USD", "base_increment": "0.00000001", "quote_increment": "0.01", "base_precision": 8, "quote_precision": 2},
    "SOL": {"product_id": "SOL-USD", "base_increment": "0.01", "quote_increment": "0.01", "base_precision": 2, "quote_precision": 2},
    "XRP": {"product_id": "XRP-USD", "base_increment": "0.000001", "quote_increment": "0.0001", "base_precision": 6, "quote_precision": 4},
    "LTC": {"product_id": "LTC-USD", "base_increment": "0.00000001", "quote_increment": "0.01", "base_precision": 8, "quote_precision": 2},
    "DOGE": {"product_id": "DOGE-USD", "base_increment": "0.1", "quote_increment": "0.00001", "base_precision": 1, "quote_precision": 5},
    "ADA": {"product_id": "ADA-USD", "base_increment": "0.1", "quote_increment": "0.0001", "base_precision": 1, "quote_precision": 4},
    "LINK": {"product_id": "LINK-USD", "base_increment": "0.001", "quote_increment": "0.001", "base_precision": 3, "quote_precision": 3},
}

SPOT_SUPPORTED_SYMBOLS = set(SPOT_PRODUCT_SPECS.keys())

_API_BASE = "https://api.coinbase.com"

# v18.19.4: Deep-trace request/response logging is opt-in. Default off because
# each call serializes the full /accounts JSON (hundreds of lines) and the scan
# loop fires it many times per second — CPU + log volume blow up otherwise.
# Re-enable for debugging via env: COINBASE_DEEP_TRACE=true
_DEEP_TRACE: bool = (
    os.getenv("COINBASE_DEEP_TRACE", "false").strip().lower() == "true"
)

# v18.19.4: TTL cache for GET /accounts. crypto_tradeability calls
# broker.get_spot_balance() once per scanned symbol — 8 calls per scan
# pre-fix. Each scan finishes in <1s, so a 3s TTL collapses redundant
# fetches within a scan into one round-trip without staling balance reads.
_ACCOUNTS_CACHE_TTL_S: float = 3.0


def _holdings_to_positions(holdings: Dict[str, Dict], price_getter) -> List[dict]:
    result = []
    for sym, h in holdings.items():
        qty = h.get("qty", 0.0)
        if qty < 1e-8:
            continue
        avg = h.get("avg_entry", 0.0)
        price = price_getter(sym)
        result.append(
            {
                "symbol": sym,
                "qty": qty,
                "avg_entry": avg,
                "current_value": round(qty * price, 2) if price > 0 else 0.0,
            }
        )
    return result


class CoinbaseSpotSymbolError(ValueError):
    """Raised when a symbol outside the supported spot set is requested. Fail-closed."""


class CoinbaseSpotBroker:
    """
    Minimal spot broker for the configured 8-symbol Coinbase spot universe.

    Interface:
      connect()
      is_connected() → bool
      get_spot_balance() → {"usd_available": float, "symbol_balances": {...}, ...}
      buy_spot(symbol, size_usd) → dict | None
      sell_spot(symbol, size_units) → dict | None
      get_spot_positions() → list[{symbol, qty, avg_entry, current_value}]
    """

    def __init__(self) -> None:
        self._connected = False
        self._key_name: str = ""
        self._private_key_pem: bytes = b""
        # In-process spot holdings: symbol → {"qty": float, "avg_entry": float}
        self._holdings: Dict[str, Dict] = {}
        self._fallback_price = lambda s: 0.0
        # v18.19.4: cached /accounts snapshot — see get_spot_balance().
        self._balance_cache: Optional[dict] = None
        self._balance_cache_ts: float = 0.0

        # Load credentials (same source as futures broker)
        self._key_name = str(COINBASE_CDP_KEY_NAME or "")
        raw = str(COINBASE_CDP_PRIVATE_KEY or "").strip("\"'")
        self._private_key_pem = raw.replace("\\n", "\n").encode() if raw else b""

        if not self._key_name or not self._private_key_pem:
            self._key_name = os.getenv("COINBASE_CDP_KEY_NAME", "")
            raw = os.getenv("COINBASE_CDP_PRIVATE_KEY", "").strip("\"'")
            self._private_key_pem = raw.replace("\\n", "\n").encode() if raw else b""

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _make_jwt(self, method: str, path: str) -> str:
        """Generate a short-lived CDP JWT for a single request (ES256 / ECDSA P-256)."""
        if not _JWT_OK:
            raise RuntimeError("PyJWT / cryptography required for live spot mode")
        
        now = int(time.time())
        # v18.17 Definitive Fix: Official SDK strips query parameters from URI claim.
        # v18.19.1: Restored nbf claim — Coinbase CDP rejects JWTs without it (regression introduced by e6fe462).
        # v19.1: Set nbf to now - 1 to be safe against micro-drifts.
        # v19.1.1: Removed 'typ' header and ensured 'kid' is the full key name as per latest CDP spec.
        path_only = path.split("?")[0]
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now - 1,
            "exp": now + 120,
            "uri": f"{method} api.coinbase.com{path_only}",
        }
        headers = {
            "kid": self._key_name,
            "nonce": secrets.token_hex(16),
        }
        return _pyjwt.encode(
            payload,
            self._private_key_pem,
            algorithm="ES256",
            headers=headers,
        )

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Sign and send a Coinbase Advanced Trade API request."""
        if not _REQUESTS_OK:
            raise RuntimeError("requests library required for live spot mode")
        
        method = method.upper()

        # v18.34: Shadow Execution Guard
        if SHADOW_EXECUTION and method == "POST" and "orders" in path:
            logger.info(f"[spot] SHADOW MODE: Blocked {method} {path} body={body}")
            # Return a fake success structure
            return {
                "success": True, 
                "success_response": {"order_id": f"shadow_{uuid.uuid4().hex[:8]}"},
                "order": {"order_id": f"shadow_{uuid.uuid4().hex[:8]}", "status": "FILLED"}
            }

        token = self._make_jwt(method, path)
        url = f"{_API_BASE}{path}"
        
        headers = {
            "Authorization": f"Bearer {token}",
        }
        if method != "GET":
            headers["Content-Type"] = "application/json"
            
        if _DEEP_TRACE:
            logger.info(f"[spot] Deep Trace Request: {method} {url} body={body}")
        resp = _requests.request(method, url, headers=headers, json=body, timeout=10)
        if _DEEP_TRACE:
            logger.info(f"[spot] Deep Trace Response: {resp.status_code} {resp.text[:500]}")
        if not resp.ok:
            # Failure path always logs — we want the error in production logs.
            logger.warning(
                f"[spot] {method} {path} → {resp.status_code}: {resp.text[:200]}"
            )
            raise RuntimeError(
                f"Coinbase Spot API {method} {path} → {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    # ── Symbol helpers ────────────────────────────────────────────────────────

    def _spec(self, symbol: str) -> dict:
        """Return product spec or raise CoinbaseSpotSymbolError (fail-closed)."""
        # Improved cleaning: strip everything after hyphen and common suffixes
        s = symbol.upper().split("-")[0].replace("USDT", "").replace("USD", "")
        if s not in SPOT_PRODUCT_SPECS:
            raise CoinbaseSpotSymbolError(
                f"[spot] '{symbol}' (cleaned: '{s}') is not in the allowed spot set "
                f"(supported: {sorted(SPOT_SUPPORTED_SYMBOLS)})."
            )
        return SPOT_PRODUCT_SPECS[s]

    def _clean_symbol(self, symbol: str) -> str:
        """Standardize symbol to base asset only."""
        return symbol.upper().split("-")[0].replace("USDT", "").replace("USD", "")

    def _round_base(self, symbol: str, qty: float) -> str:
        """Round quantity down to base_increment and return as string."""
        spec = self._spec(symbol)
        # Derive precision from increment string (e.g. "0.000001" -> 6)
        inc_str = str(spec.get("base_increment", "0.00000001"))
        if "." in inc_str:
            prec = len(inc_str.split(".")[1].rstrip("0"))
        else:
            prec = 0
            
        import math
        increment = float(inc_str)
        # Add a tiny epsilon to prevent float floating-point errors (e.g. 0.999999999 -> 1.0)
        rounded = math.floor(qty / increment + 1e-11) * increment
        
        if prec <= 0:
            return str(int(rounded))
        return f"{rounded:.{prec}f}"

    def _round_quote(self, symbol: str, price: float, side: str = "BUY") -> str:
        """Round price using directional logic (floor for BUY, ceil for SELL) to quote_increment."""
        spec = self._spec(symbol)
        increment = float(spec.get("quote_increment", 0.01))
        import math
        if side.upper() == "SELL":
            rounded = math.ceil(price / increment - 1e-11) * increment
        else:
            rounded = math.floor(price / increment + 1e-11) * increment
        prec = spec.get("quote_precision", 2)
        return f"{rounded:.{prec}f}"

    # ── Connection ────────────────────────────────────────────────────────────


    def connect(self) -> bool:
        if not _JWT_OK or not _REQUESTS_OK:
            logger.error("[spot] Cannot connect live: missing PyJWT/requests")
            return False
        
        if not self._key_name or not self._private_key_pem or b"BEGIN" not in self._private_key_pem:
            logger.warning("[spot] CDP credentials missing or invalid.")
            return False

        try:
            # Verify auth with a lightweight accounts call
            self._request("GET", "/api/v3/brokerage/accounts")
            logger.info("[spot] Connected (LIVE) — Coinbase spot 8-symbol universe")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[spot] Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    # ── Balance ───────────────────────────────────────────────────────────────

    def get_spot_balance(self) -> dict:
        """
        Return available spot balances.
        Returns symbol balances plus USD available.
        """
        # v18.19.4: short TTL cache. crypto_tradeability hits this per-asset
        # within a scan cycle (<1s); without caching we fire /accounts 8 times.
        now = time.time()
        if (
            self._balance_cache is not None
            and (now - self._balance_cache_ts) < _ACCOUNTS_CACHE_TTL_S
        ):
            return self._balance_cache

        try:
            data = self._request("GET", "/api/v3/brokerage/accounts")
            accounts = data.get("accounts", [])
            symbol_balances = {sym: 0.0 for sym in SPOT_SUPPORTED_SYMBOLS}
            usd = 0.0
            for acct in accounts:
                currency = acct.get("currency", "")
                avail = float(acct.get("available_balance", {}).get("value", 0) or 0)
                if currency in symbol_balances:
                    symbol_balances[currency] = avail
                elif currency in ("USD", "USDC"):
                    usd += avail
            result = {"usd_available": usd, "symbol_balances": symbol_balances}
            for sym, qty in symbol_balances.items():
                result[f"{sym.lower()}_available"] = qty
            self._balance_cache = result
            self._balance_cache_ts = now
            return result
        except Exception as e:
            logger.warning(f"[spot] get_spot_balance error: {e}")
            raise

    def sync_live_holdings(self) -> Optional[List[dict]]:
        """
        Refresh in-process holdings from the live Coinbase spot account.

        Returns a list of canonical live holdings or None on failure.
        """
        if not self._connected:
            return _holdings_to_positions(self._holdings, self.get_mark_price)
        try:
            balances = self.get_spot_balance()
            symbol_balances = balances.get("symbol_balances") or {}
            live_holdings: Dict[str, Dict] = {}
            for sym, qty in symbol_balances.items():
                qty_f = float(qty or 0.0)
                if qty_f <= 1e-8:
                    continue
                existing = self._holdings.get(sym, {"avg_entry": 0.0})
                live_holdings[sym] = {
                    "qty": qty_f,
                    "avg_entry": float(existing.get("avg_entry") or 0.0),
                }
            self._holdings = live_holdings
            return _holdings_to_positions(self._holdings, self.get_mark_price)
        except Exception as e:
            logger.warning(f"[spot] sync_live_holdings error: {e}")
            return None

    # ── Mark price ────────────────────────────────────────────────────────────

    def get_mark_price(self, symbol: str) -> float:
        """Return current spot price for symbol (USD)."""
        try:
            spec = self._spec(symbol)
            product_id = spec["product_id"]
            data = self._request(
                "GET", f"/api/v3/brokerage/products/{product_id}/ticker?limit=1"
            )
            trades = data.get("trades", [])
            if trades:
                return float(trades[0].get("price", 0))
        except Exception as e:
            logger.debug(f"[spot] get_mark_price error {symbol}: {e}")

        return 0.0

    def get_historical_candles(
        self, symbol: str, interval: str = "5m", limit: int = 200
    ) -> List[dict]:
        """
        Fetch OHLCV candles from Coinbase Advanced Trade API.
        Intervals supported: 1m, 5m, 15m, 30m, 1h, 6h, 1d.
        """
        if not self._connected:
            return []

        # Coinbase uses seconds for granularity
        _gran_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400, # Not natively supported by ticker but available in candles API
            "6h": 21600,
            "1d": 86400,
        }
        granularity = _gran_map.get(interval, 300)

        try:
            spec = self._spec(symbol)
            product_id = spec["product_id"]
            
            # Calculate start/end
            now = int(time.time())
            start = now - (limit * granularity)
            
            # Note: Coinbase API expects granularity as a string like 'FIVE_MINUTE' 
            # for some versions, or seconds for others. We'll use the seconds-based 
            # map if the above fails, but standard v3 uses string constants.
            
            _cb_gran = {
                "1m": "ONE_MINUTE",
                "5m": "FIVE_MINUTE",
                "15m": "FIFTEEN_MINUTE",
                "30m": "THIRTY_MINUTE",
                "1h": "ONE_HOUR",
                "6h": "SIX_HOUR",
                "1d": "ONE_DAY",
            }
            # Fallback for 4h (Coinbase doesn't have it natively, so we fetch 1h and could resample, 
            # but for now we'll just return what they have or use 6h)
            cb_interval = _cb_gran.get(interval, "FIVE_MINUTE")
            
            data = self._request(
                "GET", f"/api/v3/brokerage/products/{product_id}/candles?start={start}&end={now}&granularity={cb_interval}"
            )
            
            candles = data.get("candles", [])
            if not candles:
                return []
                
            # Coinbase returns: [start, low, high, open, close, volume]
            result = []
            for c in candles:
                result.append({
                    "T": int(float(c.get("start", 0)) * 1000), # to ms
                    "o": float(c.get("open", 0)),
                    "h": float(c.get("high", 0)),
                    "l": float(c.get("low", 0)),
                    "c": float(c.get("close", 0)),
                    "v": float(c.get("volume", 0))
                })
            # Coinbase returns candles in reverse chronological order
            return sorted(result, key=lambda x: x["T"])
            
        except Exception as e:
            logger.debug(f"[spot] get_historical_candles error {symbol}: {e}")
            return []

    def get_spot_top_of_book(self, symbol: str) -> dict:
        """Return best bid/ask and simple depth truth for symbol."""
        try:
            spec = self._spec(symbol)
            data = self._request(
                "GET",
                f"/api/v3/brokerage/product_book?product_id={spec['product_id']}&limit=5",
            )
            book = data.get("pricebook") or {}
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            best_bid = float((bids[0] or {}).get("price", 0) if bids else 0)
            best_ask = float((asks[0] or {}).get("price", 0) if asks else 0)
            bid_depth = sum(
                float(b.get("price", 0) or 0) * float(b.get("size", 0) or 0)
                for b in bids[:3]
            )
            ask_depth = sum(
                float(a.get("price", 0) or 0) * float(a.get("size", 0) or 0)
                for a in asks[:3]
            )
            mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
            spread_pct = ((best_ask - best_bid) / mid) if mid > 0 else 0.0
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_pct": spread_pct,
                "top_depth_usd": min(bid_depth, ask_depth),
            }
        except Exception as e:
            logger.warning(f"[spot] get_spot_top_of_book error {symbol}: {e}")
            px = self.get_mark_price(symbol)
            return {
                "best_bid": px * 0.9995 if px > 0 else 0.0,
                "best_ask": px * 1.0005 if px > 0 else 0.0,
                "spread_pct": 0.001 if px > 0 else 0.0,
                "top_depth_usd": 0.0,
            }

    def _normalise_order_status(self, order_id: str, fallback_symbol: str = "") -> dict:
        # v18.34: Shadow Execution Intercept
        if order_id.startswith("shadow_"):
            return {
                "order_id": order_id,
                "status": "FILLED",
                "filled_size": 0.0, # Forces spot_engine to recalculate based on live price
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "completion_pct": 100.0,
                "fee_usd": 0.0,
                "symbol": fallback_symbol
            }

        try:
            data = self._request("GET", f"/api/v3/brokerage/orders/historical/{order_id}")
            order = data.get("order") or {}
        except Exception as e:
            if "404" in str(e):
                logger.info(f"[spot] Order {order_id} not found on Coinbase (404). Triggering self-healing purge.")
                # Return a special status that spot_engine.py can use to purge the local record
                return {"order_id": order_id, "status": "PURGE_GHOST_ORDER", "symbol": fallback_symbol}
            raise e

        fee_usd = 0.0
        fills = self.list_spot_fills(order_id)
        if fills:
            fee_usd = sum(float(f.get("fee_usd") or 0.0) for f in fills)
        avg_price = float(
            order.get("average_filled_price")
            or order.get("avg_price")
            or order.get("filled_average_price")
            or 0.0
        )
        filled_size = float(order.get("filled_size") or order.get("base_size") or 0.0)
        filled_value = float(order.get("filled_value") or 0.0)
        completion = float(
            order.get("completion_percentage")
            or order.get("completion_percent")
            or (100.0 if str(order.get("status", "")).upper() == "FILLED" else 0.0)
        )
        return {
            "order_id": order_id,
            "status": str(order.get("status") or "UNKNOWN").upper(),
            "filled_size": filled_size,
            "filled_value": filled_value,
            "average_filled_price": avg_price,
            "completion_pct": completion,
            "fee_usd": fee_usd,
            "symbol": fallback_symbol
            or self._clean_symbol(order.get("product_id", "")),
        }

    def get_spot_order_status(self, order_id: str, fallback_symbol: str = "") -> dict:
        try:
            return self._normalise_order_status(
                order_id, fallback_symbol=fallback_symbol
            )
        except Exception as e:
            logger.warning(f"[spot] get_spot_order_status error {order_id}: {e}")
            return {
                "order_id": order_id,
                "status": "UNKNOWN",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "completion_pct": 0.0,
                "fee_usd": 0.0,
                "symbol": fallback_symbol,
            }

    def cancel_spot_order(self, order_id: str) -> bool:
        try:
            self._request(
                "POST",
                "/api/v3/brokerage/orders/batch_cancel",
                {"order_ids": [order_id]},
            )
            return True
        except Exception as e:
            logger.warning(f"[spot] cancel_spot_order error {order_id}: {e}")
            return False

    def list_spot_fills(self, order_id: str) -> List[dict]:
        try:
            data = self._request(
                "GET", f"/api/v3/brokerage/orders/historical/fills?order_id={order_id}"
            )
            fills = data.get("fills") or []
            result = []
            for fill in fills:
                comm = fill.get("commission_detail_total") or {}
                fee_usd = float(
                    comm.get("total_commission")
                    or fill.get("commission")
                    or fill.get("fee")
                    or 0.0
                )
                result.append(
                    {
                        "order_id": str(fill.get("order_id") or order_id),
                        "price": float(fill.get("price") or 0.0),
                        "size": float(
                            fill.get("size") or fill.get("size_in_quote") or 0.0
                        ),
                        "fee_usd": fee_usd,
                    }
                )
            return result
        except Exception as e:
            logger.warning(f"[spot] list_spot_fills error {order_id}: {e}")
            return []

    def place_limit_buy_spot(
        self, symbol: str, size_usd: float, limit_price: float, post_only: bool = True
    ) -> Optional[dict]:
        try:
            spec = self._spec(symbol)
        except CoinbaseSpotSymbolError as e:
            logger.warning(str(e))
            return None
        clean = self._clean_symbol(symbol)
        
        # v18.17: Precision rounding for both size and price
        raw_qty = size_usd / limit_price if limit_price > 0 else 0.0
        qty_str = self._round_base(symbol, raw_qty)
        qty = float(qty_str)
        limit_px_str = self._round_quote(symbol, limit_price, side="BUY")
        limit_px = float(limit_px_str)

        base_min = spec.get("base_min_size", 0.0)
        if qty > 0 and qty < base_min:
            logger.info(f"[spot] place_limit_buy_spot {clean}: raising qty {qty} to base_min {base_min}")
            qty = base_min
            qty_str = self._round_base(symbol, qty)

        if qty <= 0 or limit_px <= 0:
            return None

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "BUY",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": qty_str,
                    "limit_price": limit_px_str,
                    "post_only": bool(post_only),
                    "rfq_disabled": True,
                }
            },
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            order_id = order.get("order_id", body["client_order_id"])
            return {
                "order_id": order_id,
                "symbol": clean,
                "side": "BUY",
                "status": "OPEN",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "paper": False,
            }
        except Exception as e:
            logger.error(f"[spot] place_limit_buy_spot error {symbol}: {e}")
            return None

    def place_limit_sell_spot(
        self, symbol: str, size_units: float, limit_price: float, post_only: bool = True
    ) -> Optional[dict]:
        try:
            spec = self._spec(symbol)
        except CoinbaseSpotSymbolError as e:
            logger.warning(str(e))
            return None
        clean = self._clean_symbol(symbol)
        
        # v18.17: Precision rounding for both size and price
        qty_str = self._round_base(symbol, size_units)
        qty = float(qty_str)
        limit_px_str = self._round_quote(symbol, limit_price, side="SELL")
        limit_px = float(limit_px_str)

        base_min = spec.get("base_min_size", 0.0)
        if qty > 0 and qty < base_min:
            logger.info(f"[spot] place_limit_sell_spot {clean}: raising size {qty} to base_min {base_min}")
            qty = base_min
            qty_str = self._round_base(symbol, qty)

        if qty <= 0 or limit_px <= 0:
            return None

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "SELL",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": qty_str,
                    "limit_price": limit_px_str,
                    "post_only": bool(post_only),
                    "rfq_disabled": True,
                }
            },
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            order_id = order.get("order_id", body["client_order_id"])
            return {
                "order_id": order_id,
                "symbol": clean,
                "side": "SELL",
                "status": "OPEN",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "paper": False,
            }
        except Exception as e:
            logger.error(f"[spot] place_limit_sell_spot error {symbol}: {e}")
            return None

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy_spot(self, symbol: str, size_usd: float) -> Optional[dict]:
        """
        Buy spot using market order for size_usd USD worth of the asset.
        Returns order dict or None.
        """
        try:
            spec = self._spec(symbol)
        except CoinbaseSpotSymbolError as e:
            logger.warning(str(e))
            return None

        clean = self._clean_symbol(symbol)
        price = self.get_mark_price(symbol)
        if price <= 0:
            logger.warning(f"[spot] buy_spot {symbol}: cannot get price")
            return None

        # v18.17: Ensure we clear the exchange minimum lot size
        base_min = spec.get("base_min_size", 0.0)
        min_usd_for_base = base_min * price
        
        # Use the larger of: 
        # 1. requested size_usd
        # 2. USD needed for 1.1x min_lot_size (safety margin)
        # 3. broker-spec min_order_usd
        # 4. global config SPOT_MIN_ORDER_USD
        try:
            from config import SPOT_MIN_ORDER_USD as global_min
        except ImportError:
            global_min = 10.0

        floor_usd = max(min_usd_for_base * 1.1, spec.get("min_order_usd", 1.0), global_min)
        
        if size_usd < floor_usd:
            logger.info(f"[spot] buy_spot {clean}: raising size ${size_usd:.2f} to floor ${floor_usd:.2f} (price={price:.4f})")
            size_usd = floor_usd

        qty = size_usd / price

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "BUY",
            "order_configuration": {
                "market_market_ioc": {"quote_size": str(round(size_usd, 2))}
            },
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            
            # v18.17: More detailed error handling for better diagnosis
            if not order:
                error_response = resp.get("error_response") or resp
                logger.error(f"[spot] buy_spot REJECTED {clean}: {error_response}")
                return None

            real_id = order.get("order_id", body["client_order_id"])
            logger.info(f"[spot] LIVE BUY {clean}: ${size_usd:.2f} order_id={real_id}")
            status = self.get_spot_order_status(real_id, fallback_symbol=clean)
            avg_price = float(status.get("average_filled_price") or price or 0.0)
            filled_size = float(status.get("filled_size") or qty or 0.0)
            filled_value = float(status.get("filled_value") or size_usd or 0.0)
            result = {
                "order_id": real_id,
                "symbol": clean,
                "product_id": spec["product_id"],
                "side": "BUY",
                "filled_size": str(round(filled_size, 8)),
                "filled_value": str(round(filled_value, 2)),
                "average_filled_price": str(round(avg_price or price, 8)),
                "fee_usd": float(status.get("fee_usd") or 0.0),
                "execution_route": "taker_market",
                "status": "FILLED",
                "paper": False,
            }
            existing = self._holdings.get(clean, {"qty": 0.0, "avg_entry": 0.0})
            old_qty = existing["qty"]
            old_avg = existing["avg_entry"]
            new_qty = old_qty + filled_size
            new_avg = (
                (old_qty * old_avg + filled_size * (avg_price or price)) / new_qty
                if new_qty > 0
                else (avg_price or price)
            )
            self._holdings[clean] = {"qty": new_qty, "avg_entry": new_avg}
            return result
        except Exception as e:
            logger.error(f"[spot] buy_spot LIVE error {symbol}: {e}")
            return None

    def sell_spot(self, symbol: str, size_units: float) -> Optional[dict]:
        """
        Sell size_units of symbol at market.
        Returns order dict or None.
        """
        try:
            spec = self._spec(symbol)
        except CoinbaseSpotSymbolError as e:
            logger.warning(str(e))
            return None

        clean = self._clean_symbol(symbol)
        base_min = spec.get("base_min_size", 0.0)
        if size_units > 0 and size_units < base_min:
            logger.info(f"[spot] sell_spot {clean}: raising size {size_units:.8f} to base_min {base_min}")
            size_units = base_min

        price = self.get_mark_price(symbol)
        if price <= 0:
            logger.warning(f"[spot] sell_spot {symbol}: cannot get price")
            return None

        value_usd = size_units * price

        try:
            # v18.17: Use precision rounding for market sell
            qty_str = self._round_base(symbol, size_units)

            # Live path — base_size (units)
            body = {
                "client_order_id": str(uuid.uuid4()),
                "product_id": spec["product_id"],
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": qty_str,
                    }
                },
            }
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            if not order:
                raise RuntimeError(f"spot_broker_ack_missing: {resp}")
            real_id = order.get("order_id", body["client_order_id"])
            logger.info(
                f"[spot] LIVE SELL {clean}: {size_units:.6f} units @ ~{price:.4f} order_id={real_id}"
            )
            status = self.get_spot_order_status(real_id, fallback_symbol=clean)
            filled_size = float(status.get("filled_size") or size_units or 0.0)
            filled_value = float(status.get("filled_value") or value_usd or 0.0)
            avg_price = float(status.get("average_filled_price") or price or 0.0)
            result = {
                "order_id": real_id,
                "symbol": clean,
                "product_id": spec["product_id"],
                "side": "SELL",
                "filled_size": str(round(filled_size, 8)),
                "filled_value": str(round(filled_value, 2)),
                "average_filled_price": str(round(avg_price or price, 8)),
                "fee_usd": float(status.get("fee_usd") or 0.0),
                "execution_route": "taker_market",
                "status": "FILLED",
                "paper": False,
            }
            holding = self._holdings.get(clean)
            if holding:
                new_qty = max(0.0, holding["qty"] - filled_size)
                if new_qty < 1e-8:
                    self._holdings.pop(clean, None)
                else:
                    self._holdings[clean]["qty"] = new_qty
            return result
        except Exception as e:
            logger.error(f"[spot] sell_spot LIVE error {symbol}: {e}")
            return None

    # ── Position snapshot ─────────────────────────────────────────────────────

    def get_spot_positions(self) -> List[dict]:
        """
        Return current spot holdings as a list.
        Each entry: {symbol, qty, avg_entry, current_value}
        """
        if self._connected:
            synced = self.sync_live_holdings()
            if synced is not None:
                return synced
        return _holdings_to_positions(self._holdings, self.get_mark_price)

    def get_order_history(self, limit: int = 50) -> list[dict]:
        """
        Return filled orders from the Coinbase Advanced Trade API.
        Only sees orders placed via this API key — not app/website purchases.

        Each entry: {order_id, product_id, symbol, side, filled_size, avg_fill_price,
                     total_value_usd, fee_usd, created_time, status}
        """
        try:
            data = self._request(
                "GET",
                f"/api/v3/brokerage/orders/historical/batch?order_status=FILLED&limit={limit}",
            )
            orders = data.get("orders") or []
            result = []
            for o in orders:
                product_id = o.get("product_id", "")
                symbol = product_id.replace("-USD", "").replace("-USDT", "")
                filled = float(o.get("filled_size") or 0)
                avg_price = float(o.get("average_filled_price") or 0)
                fee = float((o.get("total_fees") or "0"))
                result.append(
                    {
                        "order_id": o.get("order_id", ""),
                        "product_id": product_id,
                        "symbol": symbol,
                        "side": o.get("side", ""),
                        "filled_size": filled,
                        "avg_fill_price": avg_price,
                        "total_value_usd": round(filled * avg_price, 2),
                        "fee_usd": round(fee, 4),
                        "created_time": o.get("created_time", ""),
                        "status": o.get("status", ""),
                    }
                )
            return result
        except Exception as e:
            logger.warning(f"[spot] get_order_history error: {e}")
            return []


# ── Singleton ─────────────────────────────────────────────────────────────────

_spot_broker: Optional[CoinbaseSpotBroker] = None


def get_spot_broker() -> CoinbaseSpotBroker:
    global _spot_broker
    if _spot_broker is None:
        _spot_broker = CoinbaseSpotBroker()
        if not _spot_broker.is_connected():
            _spot_broker.connect()

    return _spot_broker

