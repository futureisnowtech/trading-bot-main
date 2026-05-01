"""
execution/coinbase_spot_broker.py — Coinbase Advanced Trade spot broker adapter.

Supports BTC-USD, ETH-USD, SOL-USD, XRP-USD, LTC-USD, DOGE-USD, ADA-USD,
and LINK-USD spot. No leverage, no shorting, no margin.

Authentication — same CDP JWT / ES256 credentials as coinbase_broker.py:
  COINBASE_CDP_KEY_NAME    organizations/{org_id}/apiKeys/{key_id}
  COINBASE_CDP_PRIVATE_KEY EC private key in PEM format (\\n-escaped in .env)

Spot API base: https://api.coinbase.com/api/v3/brokerage/
(NOT /cfm/ — that is for Coinbase Financial Markets futures)

Paper mode: zero API calls, returns mock fills.
Live mode:  Coinbase Advanced Trade API v3 REST.

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
SPOT_PRODUCT_SPECS: dict[str, dict] = {
    "BTC": {"product_id": "BTC-USD", "min_order_usd": 1.0},
    "ETH": {"product_id": "ETH-USD", "min_order_usd": 1.0},
    "SOL": {"product_id": "SOL-USD", "min_order_usd": 1.0},
    "XRP": {"product_id": "XRP-USD", "min_order_usd": 1.0},
    "LTC": {"product_id": "LTC-USD", "min_order_usd": 1.0},
    "DOGE": {"product_id": "DOGE-USD", "min_order_usd": 1.0},
    "ADA": {"product_id": "ADA-USD", "min_order_usd": 1.0},
    "LINK": {"product_id": "LINK-USD", "min_order_usd": 1.0},
}

SPOT_SUPPORTED_SYMBOLS = set(SPOT_PRODUCT_SPECS.keys())

_API_BASE = "https://api.coinbase.com"


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

    def __init__(self, paper: Optional[bool] = None) -> None:
        if paper is not None:
            self._paper = bool(paper)
        else:
            try:
                from config import PAPER_TRADING

                self._paper = bool(PAPER_TRADING)
            except ImportError:
                self._paper = True

        self._connected = False
        self._key_name: str = ""
        self._private_key_pem: bytes = b""
        # In-process spot holdings: symbol → {"qty": float, "avg_entry": float}
        self._holdings: Dict[str, Dict] = {}

        # Load credentials (same source as futures broker)
        try:
            from config import COINBASE_CDP_KEY_NAME, COINBASE_CDP_PRIVATE_KEY

            self._key_name = str(COINBASE_CDP_KEY_NAME or "")
            raw = str(COINBASE_CDP_PRIVATE_KEY or "")
            self._private_key_pem = raw.replace("\\n", "\n").encode()
        except ImportError:
            pass

        if not self._key_name or not self._private_key_pem:
            self._key_name = os.getenv("COINBASE_CDP_KEY_NAME", "")
            raw = os.getenv("COINBASE_CDP_PRIVATE_KEY", "")
            self._private_key_pem = raw.replace("\\n", "\n").encode() if raw else b""

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _make_jwt(self, method: str, path: str) -> str:
        if not _JWT_OK:
            raise RuntimeError("PyJWT / cryptography required for live spot mode")
        now = int(time.time())
        # CDP JWT URI claim must be PATH-only — strip query string if present.
        # product_book is a GET with ?product_id=&limit= which uniquely triggers 401
        # when query params are included in the URI claim.
        path_only = path.split("?")[0]
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now,
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
        if not _REQUESTS_OK:
            raise RuntimeError("requests library required for live spot mode")
        token = self._make_jwt(method.upper(), path)
        url = f"{_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = _requests.request(method, url, headers=headers, json=body, timeout=10)
        if not resp.ok:
            raise RuntimeError(
                f"Coinbase Spot API {method} {path} → {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    # ── Symbol helpers ────────────────────────────────────────────────────────

    def _spec(self, symbol: str) -> dict:
        s = symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")
        if s not in SPOT_PRODUCT_SPECS:
            raise CoinbaseSpotSymbolError(
                f"[spot] '{symbol}' is not in the allowed spot set "
                f"(supported: {sorted(SPOT_SUPPORTED_SYMBOLS)})."
            )
        return SPOT_PRODUCT_SPECS[s]

    def _clean_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if self._paper:
            logger.info("[spot] Connected (PAPER) — Coinbase spot 8-symbol universe")
            self._connected = True
            return True

        if not _JWT_OK or not _REQUESTS_OK:
            logger.error("[spot] Cannot connect live: missing PyJWT/requests")
            return False
        if not self._key_name or not self._private_key_pem:
            logger.error("[spot] Cannot connect live: CDP credentials not set")
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
        if self._paper:
            balances = {
                f"{sym.lower()}_available": 0.0 for sym in SPOT_SUPPORTED_SYMBOLS
            }
            balances["symbol_balances"] = {sym: 0.0 for sym in SPOT_SUPPORTED_SYMBOLS}
            balances["usd_available"] = 0.0
            return balances

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
            return result
        except Exception as e:
            logger.warning(f"[spot] get_spot_balance error: {e}")
            raise

    def sync_live_holdings(self) -> Optional[List[dict]]:
        """
        Refresh in-process holdings from the live Coinbase spot account.

        Returns a list of canonical live holdings or None on failure.
        """
        if self._paper or not self._connected:
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
        clean = self._clean_symbol(symbol)
        if self._paper:
            return self._fallback_price(clean)
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
        return self._fallback_price(clean)

    _PAPER_PRICE_FALLBACKS: dict[str, float] = {
        "BTC": 90_000.0,
        "ETH": 2_500.0,
        "SOL": 180.0,
        "XRP": 2.0,
        "LTC": 85.0,
        "DOGE": 0.22,
        "ADA": 0.95,
        "LINK": 22.0,
    }

    def _fallback_price(self, clean_sym: str) -> float:
        try:
            import yfinance as _yf

            tk = _yf.Ticker(f"{clean_sym}-USD")
            hist = tk.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return self._PAPER_PRICE_FALLBACKS.get(clean_sym, 0.0)

    def get_spot_top_of_book(self, symbol: str) -> dict:
        """Return best bid/ask and simple depth truth for symbol."""
        clean = self._clean_symbol(symbol)
        if self._paper:
            px = self._fallback_price(clean)
            return {
                "best_bid": px * 0.9995 if px > 0 else 0.0,
                "best_ask": px * 1.0005 if px > 0 else 0.0,
                "spread_pct": 0.001 if px > 0 else 0.0,
                "top_depth_usd": 1_000_000.0,
            }
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
        if self._paper:
            return {
                "order_id": order_id,
                "status": "FILLED",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "completion_pct": 100.0,
                "fee_usd": 0.0,
                "symbol": fallback_symbol,
            }
        data = self._request("GET", f"/api/v3/brokerage/orders/historical/{order_id}")
        order = data.get("order") or {}
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
        if self._paper:
            return True
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
        if self._paper:
            return []
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
        qty = size_usd / limit_price if limit_price > 0 else 0.0
        if qty <= 0:
            return None
        if self._paper:
            order_id = f"spot_limit_paper_{clean}_{int(time.time())}"
            return {
                "order_id": order_id,
                "symbol": clean,
                "side": "BUY",
                "status": "OPEN",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "paper": True,
            }
        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "BUY",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": str(round(qty, 8)),
                    "limit_price": str(round(limit_price, 8)),
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
        if size_units <= 0 or limit_price <= 0:
            return None
        if self._paper:
            order_id = f"spot_limit_paper_{clean}_{int(time.time())}"
            return {
                "order_id": order_id,
                "symbol": clean,
                "side": "SELL",
                "status": "OPEN",
                "filled_size": 0.0,
                "filled_value": 0.0,
                "average_filled_price": 0.0,
                "paper": True,
            }
        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "SELL",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": str(round(size_units, 8)),
                    "limit_price": str(round(limit_price, 8)),
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

        qty = size_usd / price
        order_id = f"spot_paper_{clean}_{int(time.time())}" if self._paper else None

        if self._paper:
            logger.info(
                f"[spot] PAPER BUY {clean}: ${size_usd:.2f} = {qty:.6f} units @ {price:.4f}"
            )
            result = {
                "order_id": order_id,
                "symbol": clean,
                "product_id": spec["product_id"],
                "side": "BUY",
                "filled_size": str(round(qty, 6)),
                "filled_value": str(round(size_usd, 2)),
                "average_filled_price": str(round(price, 8)),
                "fee_usd": 0.0,
                "execution_route": "paper_market",
                "status": "FILLED",
                "paper": True,
            }
            # Update in-process holdings
            existing = self._holdings.get(clean, {"qty": 0.0, "avg_entry": 0.0})
            old_qty = existing["qty"]
            old_avg = existing["avg_entry"]
            new_qty = old_qty + qty
            new_avg = (
                (old_qty * old_avg + qty * price) / new_qty if new_qty > 0 else price
            )
            self._holdings[clean] = {"qty": new_qty, "avg_entry": new_avg}
            return result

        # Live path — use quote_size (USD amount) for market order
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
            if not order:
                raise RuntimeError(f"spot_broker_ack_missing: {resp}")
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
        price = self.get_mark_price(symbol)
        if price <= 0:
            logger.warning(f"[spot] sell_spot {symbol}: cannot get price")
            return None

        value_usd = size_units * price
        order_id = f"spot_paper_{clean}_{int(time.time())}" if self._paper else None

        if self._paper:
            logger.info(
                f"[spot] PAPER SELL {clean}: {size_units:.6f} units = ${value_usd:.2f} @ {price:.4f}"
            )
            result = {
                "order_id": order_id,
                "symbol": clean,
                "product_id": spec["product_id"],
                "side": "SELL",
                "filled_size": str(round(size_units, 6)),
                "filled_value": str(round(value_usd, 2)),
                "average_filled_price": str(round(price, 8)),
                "fee_usd": 0.0,
                "execution_route": "paper_market",
                "status": "FILLED",
                "paper": True,
            }
            holding = self._holdings.get(clean)
            if holding:
                new_qty = max(0.0, holding["qty"] - size_units)
                if new_qty < 1e-8:
                    self._holdings.pop(clean, None)
                else:
                    self._holdings[clean]["qty"] = new_qty
            return result

        # Live path — base_size (units)
        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "SELL",
            "order_configuration": {
                "market_market_ioc": {"base_size": str(round(size_units, 6))}
            },
        }
        try:
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
        if not self._paper and self._connected:
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
        if self._paper:
            return []
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
        _spot_broker.connect()
    return _spot_broker
