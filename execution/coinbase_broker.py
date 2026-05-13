"""
execution/coinbase_broker.py — Coinbase US perpetual-style futures broker adapter.

Supports the 4 CFTC-regulated nano perp-style futures available to US customers:

  Product ID            Instrument            Contract Size
  BIP-20DEC30-CDE       nano Bitcoin perp     0.01 BTC
  ETP-20DEC30-CDE       nano Ether perp       0.1  ETH
  SLP-20DEC30-CDE       nano Solana perp      5    SOL
  XPP-20DEC30-CDE       nano XRP perp         500  XRP

All four expire December 2030.  Hourly cash adjustments keep price near spot
(funding analog).  ISOLATED margin only.  Max leverage 10×.

Authentication — Coinbase Developer Platform (CDP) JWT / ES256:
  COINBASE_CDP_KEY_NAME    organizations/{org_id}/apiKeys/{key_id}
  COINBASE_CDP_PRIVATE_KEY EC private key in PEM format (\\n-escaped in .env)

Fee model (Advanced Trade API direct):
  Taker: 0.03%  |  Maker: 0.00%
  Verify current promotional status at help.coinbase.com/en/derivatives.

Paper mode: full simulation, zero API calls.
Live mode:  Coinbase Advanced Trade API v3 REST (api.coinbase.com/api/v3/brokerage).

Fail-closed: any symbol not in the supported set → CoinbaseSymbolError (no trade).

API reference: https://docs.cdp.coinbase.com/advanced-trade/reference
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import uuid
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Dependency checks ─────────────────────────────────────────────────────────
try:
    import jwt as _pyjwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    _JWT_OK = True
except ImportError:
    _JWT_OK = False
    logger.warning("[cb] PyJWT / cryptography not installed — live mode disabled")

try:
    import requests as _requests

    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    logger.warning("[cb] requests not installed — live mode disabled")

try:
    import yfinance as _yf

    _YF_OK = True
except ImportError:
    _YF_OK = False

# ── Product registry ──────────────────────────────────────────────────────────

PRODUCT_SPECS: dict[str, dict] = {
    "BTC": {
        "product_id": "BIP-20DEC30-CDE",
        "contract_size": 0.01,
        "base": "BTC",
        "code": "BIP",
    },
    "ETH": {
        "product_id": "ETP-20DEC30-CDE",
        "contract_size": 0.1,
        "base": "ETH",
        "code": "ETP",
    },
    "SOL": {
        "product_id": "SLP-20DEC30-CDE",
        "contract_size": 5.0,
        "base": "SOL",
        "code": "SLP",
    },
    "XRP": {
        "product_id": "XPP-20DEC30-CDE",
        "contract_size": 500.0,
        "base": "XRP",
        "code": "XPP",
    },
}

SUPPORTED_SYMBOLS = set(PRODUCT_SPECS.keys())
PRODUCT_ID_TO_SYMBOL = {
    spec["product_id"]: symbol for symbol, spec in PRODUCT_SPECS.items()
}

COINBASE_TAKER_FEE = 0.0003
COINBASE_MAKER_FEE = 0.0000

_API_BASE = "https://api.coinbase.com"
_MAX_LEVERAGE = 10


class CoinbaseSymbolError(ValueError):
    """Raised when an unsupported symbol is requested."""


class CoinbaseBroker:
    def __init__(self, paper: Optional[bool] = None) -> None:
        # v18.18: If credentials missing, default to paper for safety in tests
        self._key_name = os.getenv("COINBASE_CDP_KEY_NAME", "")
        raw = os.getenv("COINBASE_CDP_PRIVATE_KEY", "")
        self._private_key_pem = raw.replace("\\n", "\n").encode() if raw else b""
        
        if paper is not None:
            self._paper = paper
        else:
            self._paper = not (self._key_name and self._private_key_pem)

        self._connected = False
        self._open_positions: Dict[str, Dict] = {}
        self._symbol_count: Dict[str, int] = {}
        self._session = None

    def _make_jwt(self, method: str, path: str) -> str:
        if self._paper:
            return "paper_jwt"
        if not _JWT_OK:
            raise RuntimeError("PyJWT / cryptography required for live mode")
        now = int(time.time())
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method} api.coinbase.com{path}",
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
        if self._paper:
            return {}
        if not _REQUESTS_OK:
            raise RuntimeError("requests library required for live mode")
        token = self._make_jwt(method.upper(), path)
        url = f"{_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = _requests.request(method, url, headers=headers, json=body, timeout=10)
        if not resp.ok:
            raise RuntimeError(f"Coinbase API {method} {path} → {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    def _spec(self, symbol: str) -> dict:
        s = str(symbol).upper().replace("USDT", "").replace("USD", "").replace("-PERP", "")
        if s not in PRODUCT_SPECS:
            raise CoinbaseSymbolError(f"[cb] '{symbol}' is not supported.")
        return PRODUCT_SPECS[s]

    def _resolve_symbol(self, symbol: str) -> dict:
        return self._spec(symbol)

    def _qty_to_contracts(self, spec: dict, size_usd: float, price: float) -> int:
        if price <= 0: return 0
        base_qty = size_usd / price
        return int(base_qty / spec["contract_size"])

    def connect(self) -> bool:
        if self._paper:
            self._connected = True
            return True
        if not self._key_name or not self._private_key_pem:
            return False
        try:
            # Simple check to verify connectivity/auth
            self._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            self._connected = True
            return True
        except Exception:
            return False

    def is_connected(self) -> bool:
        return self._connected

    def set_leverage(self, symbol: str, leverage: int) -> None:
        pass

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        if margin_type.upper() != "ISOLATED":
            raise ValueError("[cb] Only ISOLATED supported")

    def get_mark_price(self, symbol: str) -> float:
        if not self._connected and not self._paper: 
            return 0.0
            
        if self._paper and (not self._key_name or not self._private_key_pem):
            s = str(symbol).upper().replace("USDT", "").replace("USD", "").replace("-PERP", "")
            return {"BTC": 90000.0, "ETH": 2500.0, "SOL": 150.0, "XRP": 0.5}.get(s, 100.0)
            
        try:
            spec = self._spec(symbol)
            data = self._request("GET", f"/api/v3/brokerage/products/{spec['product_id']}/ticker?limit=1")
            trades = data.get("trades", [])
            if trades: return float(trades[0].get("price", 0))
        except Exception:
            pass
        return 100.0 if self._paper else 0.0

    def get_wallet_balance(self) -> float:
        if self._paper: return 10000.0
        try:
            data = self._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            return float(data.get("balance_summary", {}).get("futures_buying_power", {}).get("value", "0"))
        except Exception:
            return 0.0

    def get_account_balance(self) -> float:
        return self.get_wallet_balance()

    def get_funding_rate(self, symbol: str) -> float:
        return 0.0

    def get_position(self, symbol: str) -> Optional[dict]:
        return self.get_all_positions().get(symbol)

    def sync_live_positions(self) -> Optional[dict]:
        if self._paper: return dict(self._open_positions)
        if not self._connected: return dict(self._open_positions)
        try:
            data = self._request("GET", "/api/v3/brokerage/cfm/positions")
            live = {}
            for p in data.get("positions", []):
                prod_id = p.get("product_id")
                sym = PRODUCT_ID_TO_SYMBOL.get(prod_id)
                if not sym: continue
                qty_contracts = float(p.get("number_of_contracts") or 0.0)
                if qty_contracts <= 0: continue
                spec = PRODUCT_SPECS[sym]
                live[sym] = {
                    "symbol": sym,
                    "direction": "LONG" if p.get("side") == "LONG" else "SHORT",
                    "qty": qty_contracts * spec["contract_size"],
                    "contracts": qty_contracts,
                    "entry_price": float(p.get("avg_entry_price") or 0.0),
                    "current_price": float(p.get("current_price") or 0.0),
                    "unrealized_pnl": float(p.get("unrealized_pnl") or 0.0),
                    "paper": False,
                    "venue": "coinbase",
                }
            self._open_positions = live
            return dict(live)
        except Exception:
            return None

    def get_all_positions(self) -> dict:
        if self._paper or not self._connected: return dict(self._open_positions)
        synced = self.sync_live_positions()
        return synced if synced is not None else dict(self._open_positions)

    def open_long(self, symbol: str, size_usd: float, leverage: int = 3, **kwargs) -> Optional[dict]:
        try:
            spec = self._spec(symbol)
        except CoinbaseSymbolError:
            return None
            
        # v18.18: duplicate open cap (3 per symbol)
        current_count = self._symbol_count.get(f"{symbol}_LONG", 0)
        if current_count >= 3:
            return None

        price = self.get_mark_price(symbol)
        if price <= 0: return None
        contracts = self._qty_to_contracts(spec, size_usd, price)
        if contracts < 1: return None

        self._symbol_count[f"{symbol}_LONG"] = current_count + 1

        if self._paper:
            res = {
                "orderId": f"paper_{uuid.uuid4().hex[:8]}", "symbol": symbol, "side": "BUY",
                "contracts": contracts, "avgPrice": str(price), "status": "FILLED", "paper": True, "venue": "coinbase"
            }
            self._open_positions[symbol] = {
                "symbol": symbol, "direction": "LONG", "entry_price": price, 
                "qty": contracts * spec["contract_size"], "contracts": contracts, "paper": True
            }
            return res

        body = {
            "client_order_id": str(uuid.uuid4()), "product_id": spec["product_id"], "side": "BUY",
            "order_configuration": {"market_market_ioc": {"base_size": str(contracts)}},
            "leverage": str(min(leverage, _MAX_LEVERAGE)), "margin_type": "ISOLATED",
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            if not order: return None
            res = {
                "orderId": order.get("order_id", body["client_order_id"]), "symbol": symbol, "side": "BUY",
                "contracts": contracts, "avgPrice": str(price), "status": "FILLED", "paper": False, "venue": "coinbase"
            }
            self._open_positions[symbol] = {
                "symbol": symbol, "direction": "LONG", "entry_price": price, 
                "qty": contracts * spec["contract_size"], "contracts": contracts, "paper": False
            }
            return res
        except Exception:
            return None

    def open_short(self, symbol: str, size_usd: float, leverage: int = 3, **kwargs) -> Optional[dict]:
        try:
            spec = self._spec(symbol)
        except CoinbaseSymbolError:
            return None
            
        # v18.18: duplicate open cap (3 per symbol)
        current_count = self._symbol_count.get(f"{symbol}_SHORT", 0)
        if current_count >= 3:
            return None

        price = self.get_mark_price(symbol)
        if price <= 0: return None
        contracts = self._qty_to_contracts(spec, size_usd, price)
        if contracts < 1: return None

        self._symbol_count[f"{symbol}_SHORT"] = current_count + 1

        if self._paper:
            res = {
                "orderId": f"paper_{uuid.uuid4().hex[:8]}", "symbol": symbol, "side": "SELL",
                "contracts": contracts, "avgPrice": str(price), "status": "FILLED", "paper": True, "venue": "coinbase"
            }
            self._open_positions[symbol] = {
                "symbol": symbol, "direction": "SHORT", "entry_price": price, 
                "qty": contracts * spec["contract_size"], "contracts": contracts, "paper": True
            }
            return res

        body = {
            "client_order_id": str(uuid.uuid4()), "product_id": spec["product_id"], "side": "SELL",
            "order_configuration": {"market_market_ioc": {"base_size": str(contracts)}},
            "leverage": str(min(leverage, _MAX_LEVERAGE)), "margin_type": "ISOLATED",
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            if not order: return None
            res = {
                "orderId": order.get("order_id", body["client_order_id"]), "symbol": symbol, "side": "SELL",
                "contracts": contracts, "avgPrice": str(price), "status": "FILLED", "paper": False, "venue": "coinbase"
            }
            self._open_positions[symbol] = {
                "symbol": symbol, "direction": "SHORT", "entry_price": price, 
                "qty": contracts * spec["contract_size"], "contracts": contracts, "paper": False
            }
            return res
        except Exception:
            return None

    def close_position(self, symbol: str, pos_fallback: Optional[dict] = None, reason: str = "manual") -> Optional[dict]:
        self._symbol_count.pop(f"{symbol}_LONG", None)
        self._symbol_count.pop(f"{symbol}_SHORT", None)
        pos = self._open_positions.get(symbol) or pos_fallback
        if not pos: return None
        direction = pos.get("direction", "LONG")
        entry_price = float(pos.get("entry_price", 0))
        qty = float(pos.get("qty", 0))
        exit_price = self.get_mark_price(symbol)
        if exit_price <= 0: exit_price = entry_price
        pnl = (exit_price - entry_price) * qty if direction == "LONG" else (entry_price - exit_price) * qty

        if self._paper:
            self._open_positions.pop(symbol, None)
            return {"symbol": symbol, "exit_price": exit_price, "pnl_usd": round(pnl, 4), "reason": reason, "paper": True, "venue": "coinbase"}

        try:
            spec = self._spec(symbol)
            contracts = int(qty / spec["contract_size"])
            body = {
                "client_order_id": str(uuid.uuid4()), "product_id": spec["product_id"],
                "side": "SELL" if direction == "LONG" else "BUY",
                "order_configuration": {"market_market_ioc": {"base_size": str(max(1, contracts))}},
                "margin_type": "ISOLATED",
            }
            self._request("POST", "/api/v3/brokerage/orders", body)
        except Exception:
            pass

        self._open_positions.pop(symbol, None)
        return {"symbol": symbol, "exit_price": exit_price, "pnl_usd": round(pnl, 4), "reason": reason, "paper": False, "venue": "coinbase"}


_coinbase_broker: Optional[CoinbaseBroker] = None

def get_coinbase_broker() -> CoinbaseBroker:
    global _coinbase_broker
    if _coinbase_broker is None:
        _coinbase_broker = CoinbaseBroker()
        _coinbase_broker.connect()
    return _coinbase_broker
