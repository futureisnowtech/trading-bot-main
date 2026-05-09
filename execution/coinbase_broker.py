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
# Maps internal symbol (as used throughout the bot) → Coinbase product spec.
# Only these four symbols may be routed to live execution tonight.

PRODUCT_SPECS: dict[str, dict] = {
    "BTC": {
        "product_id": "BIP-20DEC30-CDE",
        "contract_size": 0.01,  # BTC per contract
        "base": "BTC",
        "code": "BIP",
    },
    "ETH": {
        "product_id": "ETP-20DEC30-CDE",
        "contract_size": 0.1,  # ETH per contract
        "base": "ETH",
        "code": "ETP",
    },
    "SOL": {
        "product_id": "SLP-20DEC30-CDE",
        "contract_size": 5.0,  # SOL per contract
        "base": "SOL",
        "code": "SLP",
    },
    "XRP": {
        "product_id": "XPP-20DEC30-CDE",
        "contract_size": 500.0,  # XRP per contract
        "base": "XRP",
        "code": "XPP",
    },
}

SUPPORTED_SYMBOLS = set(PRODUCT_SPECS.keys())
PRODUCT_ID_TO_SYMBOL = {
    spec["product_id"]: symbol for symbol, spec in PRODUCT_SPECS.items()
}

# Taker fee for Coinbase Advanced Trade API direct (nano perp-style futures).
# 0.00% maker, 0.03% taker — verify at help.coinbase.com/en/derivatives.
COINBASE_TAKER_FEE = 0.0003  # 0.03%
COINBASE_MAKER_FEE = 0.0000  # 0.00%

_API_BASE = "https://api.coinbase.com"
_MAX_LEVERAGE = 10  # Coinbase max for nano perp-style futures


# ── Exceptions ────────────────────────────────────────────────────────────────


class CoinbaseSymbolError(ValueError):
    """Raised when an unsupported symbol is requested.  Fail-closed behaviour."""


# ── Broker class ──────────────────────────────────────────────────────────────


class CoinbaseBroker:
    """
    Drop-in replacement for BinanceBroker on the live crypto lane.

    The interface exposed to perps_engine.py is identical:
      connect() / is_connected()
      set_leverage(symbol, leverage)       — no-op; Coinbase does not take per-call setting
      set_margin_type(symbol, mode)        — enforces ISOLATED; raises on CROSS attempt
      open_long(symbol, size_usd, leverage) → dict | None
      open_short(symbol, size_usd, leverage) → dict | None
      close_position(symbol, pos_fallback) → dict | None
      get_position(symbol) → dict | None
      get_all_positions() → dict
      get_mark_price(symbol) → float
      get_wallet_balance() → float
      get_funding_rate(symbol) → float  (returns 0.0 — not applicable to dated futures)
    """

    def __init__(self, paper: Optional[bool] = None) -> None:
        self._paper = False
        self._connected = False
        self._key_name: str = ""
        self._private_key_pem: bytes = b""
        self._open_positions: Dict[str, Dict] = {}
        self._symbol_count: Dict[
            str, int
        ] = {}  # tracks entries per symbol for per-symbol cap
        self._session = None

        # Load credentials
        try:
            from config import COINBASE_CDP_KEY_NAME, COINBASE_CDP_PRIVATE_KEY

            self._key_name = str(COINBASE_CDP_KEY_NAME or "")
            raw = str(COINBASE_CDP_PRIVATE_KEY or "")
            # Support \\n-escaped PEM in .env
            self._private_key_pem = raw.replace("\\n", "\n").encode()
        except ImportError:
            pass

        if not self._key_name or not self._private_key_pem:
            # Fallback: try env vars directly
            self._key_name = os.getenv("COINBASE_CDP_KEY_NAME", "")
            raw = os.getenv("COINBASE_CDP_PRIVATE_KEY", "")
            self._private_key_pem = raw.replace("\\n", "\n").encode() if raw else b""

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _make_jwt(self, method: str, path: str) -> str:
        """Generate a short-lived CDP JWT for a single request (ES256 / ECDSA P-256)."""
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
        """Sign and send a Coinbase Advanced Trade API request."""
        if not _REQUESTS_OK:
            raise RuntimeError("requests library required for live mode")
        token = self._make_jwt(method.upper(), path)
        url = f"{_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = _requests.request(
            method,
            url,
            headers=headers,
            json=body,
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Coinbase API {method} {path} → {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    # ── Symbol helpers ────────────────────────────────────────────────────────

    def _spec(self, symbol: str) -> dict:
        """Return product spec or raise CoinbaseSymbolError (fail-closed)."""
        # Strip common suffixes callers might pass (BTCUSDT → BTC)
        s = symbol.upper().replace("USDT", "").replace("USD", "").replace("-PERP", "")
        if s not in PRODUCT_SPECS:
            raise CoinbaseSymbolError(
                f"[cb] '{symbol}' is not in the Coinbase supported launch set "
                f"(supported: {sorted(SUPPORTED_SYMBOLS)}).  No trade placed."
            )
        return PRODUCT_SPECS[s]

    def _resolve_symbol(self, symbol: str) -> dict:
        """Alias for _spec — resolves symbol or raises CoinbaseSymbolError."""
        return self._spec(symbol)

    def _qty_to_contracts(self, spec: dict, size_usd: float, price: float) -> int:
        """Convert USD notional to whole contracts (floor).  Returns 0 if too small."""
        if price <= 0:
            return 0
        base_qty = size_usd / price
        contracts = int(base_qty / spec["contract_size"])
        return contracts

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not _JWT_OK or not _REQUESTS_OK:
            logger.error("[cb] Cannot connect live: missing PyJWT/requests")
            return False
        if not self._key_name or not self._private_key_pem:
            logger.error(
                "[cb] Cannot connect live: COINBASE_CDP_KEY_NAME / COINBASE_CDP_PRIVATE_KEY not set"
            )
            return False

        try:
            # Verify auth with a balance check (CFM = Coinbase Financial Markets, CFTC nano futures)
            data = self._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            bal = float(
                data.get("balance_summary", {})
                .get("futures_buying_power", {})
                .get("value", 0)
            )
            logger.info(
                f"[cb] Connected (LIVE) account=futures buying_power=${bal:,.2f}"
            )
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[cb] Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    # ── Margin / leverage (Coinbase does not use per-call settings) ───────────

    def set_leverage(self, symbol: str, leverage: int) -> None:
        clamped = min(int(leverage), _MAX_LEVERAGE)
        if clamped != leverage:
            logger.warning(
                f"[cb] set_leverage: clamped {leverage}→{clamped} (Coinbase max {_MAX_LEVERAGE}x)"
            )
        # Leverage on Coinbase nano futures is controlled by margin deposited, not a per-symbol API call.
        logger.debug(f"[cb] set_leverage({symbol}, {clamped}) — informational only")

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        if margin_type.upper() != "ISOLATED":
            raise ValueError(f"[cb] Only ISOLATED margin supported. Got: {margin_type}")
        logger.debug(f"[cb] set_margin_type({symbol}, ISOLATED) — Coinbase default")

    # ── Mark price ────────────────────────────────────────────────────────────

    def get_mark_price(self, symbol: str) -> float:
        if not self._connected:
            return 0.0
        try:
            spec = self._spec(symbol)
            data = self._request(
                "GET", f"/api/v3/brokerage/products/{spec['product_id']}/ticker?limit=1"
            )
            trades = data.get("trades", [])
            if trades:
                return float(trades[0].get("price", 0))
        except Exception as e:
            logger.debug(f"[cb] get_mark_price error for {symbol}: {e}")
        return 0.0

    # ── Balance ───────────────────────────────────────────────────────────────

    def get_wallet_balance(self) -> float:
        """Return current account equity."""
        try:
            data = self._request("GET", "/api/v3/brokerage/cfm/balance_summary")
            val = (
                data.get("balance_summary", {})
                .get("futures_buying_power", {})
                .get("value", "0")
            )
            return float(val)
        except Exception as e:
            logger.debug(f"[cb] get_wallet_balance error: {e}")
            return 0.0

    def get_account_balance(self) -> float:
        """Alias for get_wallet_balance — compatible with v10_runner._get_account_balance()."""
        return self.get_wallet_balance()

    # ── Funding rate (not applicable to dated contracts) ──────────────────────

    def get_funding_rate(self, symbol: str) -> float:
        # Coinbase nano perp-style futures use hourly cash adjustments, not a periodic
        # funding rate that can be fetched via this interface.  Return 0.0 so the
        # economics gate treats carry as neutral.
        return 0.0

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[dict]:
        if self._connected:
            try:
                return self.get_all_positions().get(self._spec(symbol)["base"])
            except Exception:
                pass
        return self._open_positions.get(symbol)

    def sync_live_positions(self) -> Optional[dict]:
        if not self._connected:
            return dict(self._open_positions)
        try:
            data = self._request("GET", "/api/v3/brokerage/cfm/positions")
            live_positions: Dict[str, Dict] = {}
            for raw in data.get("positions", []):
                product_id = str(raw.get("product_id") or "")
                symbol = PRODUCT_ID_TO_SYMBOL.get(product_id)
                if not symbol:
                    continue

                side = str(raw.get("side") or "UNKNOWN").upper()
                contracts = float(raw.get("number_of_contracts") or 0.0)
                if contracts <= 0 or side == "UNKNOWN":
                    continue

                spec = PRODUCT_SPECS[symbol]
                qty = contracts * float(spec["contract_size"])
                live_positions[symbol] = {
                    "symbol": symbol,
                    "product_id": product_id,
                    "direction": "LONG" if side == "LONG" else "SHORT",
                    "entry_price": float(raw.get("avg_entry_price") or 0.0),
                    "qty": qty,
                    "contracts": contracts,
                    "current_price": float(raw.get("current_price") or 0.0),
                    "unrealized_pnl": float(raw.get("unrealized_pnl") or 0.0),
                    "paper": False,
                    "venue": "coinbase",
                }

            self._open_positions = live_positions
            return dict(live_positions)
        except Exception as e:
            logger.warning(f"[cb] get_all_positions live sync error: {e}")
            return None

    def get_all_positions(self) -> dict:
        if not self._connected:
            return dict(self._open_positions)
        synced = self.sync_live_positions()
        if synced is not None:
            return synced
        return dict(self._open_positions)

    # ── Order placement ───────────────────────────────────────────────────────

    def open_long(
        self,
        symbol: str,
        size_usd: float,
        leverage: int = 3,
        stop_pct: float = 0.0,
        take_profit_pct: float = 0.0,
        strategy: str = "v10_perp",
    ) -> Optional[dict]:
        """Place a long order.  Returns order dict or None."""
        try:
            spec = self._spec(symbol)
        except CoinbaseSymbolError as e:
            logger.warning(str(e))
            return None

        price = self.get_mark_price(symbol)
        if price <= 0:
            logger.warning(f"[cb] open_long {symbol}: cannot get price")
            return None

        # Block opposite-side hedge stacking (LONG while SHORT open)
        _existing = self._open_positions.get(symbol, {})
        if _existing.get("direction") == "SHORT":
            logger.warning(f"[cb] open_long blocked — SHORT already open on {symbol}.")
            return None
        # Per-symbol position cap (default 3 — allows scaling in same direction)
        _MAX_PER_SYMBOL = 3
        if self._symbol_count.get(symbol, 0) >= _MAX_PER_SYMBOL:
            logger.warning(
                f"[cb] open_long blocked — {symbol} already has "
                f"{self._symbol_count[symbol]}/{_MAX_PER_SYMBOL} positions open."
            )
            return None

        # Live path
        contracts = self._qty_to_contracts(spec, size_usd, price)
        if contracts < 1:
            logger.warning(
                f"[cb] open_long {symbol}: size ${size_usd:.0f} too small for 1 {spec['code']} contract "
                f"(need ~${spec['contract_size'] * price:.0f})"
            )
            return None

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "BUY",
            "order_configuration": {"market_market_ioc": {"base_size": str(contracts)}},
            "leverage": str(min(leverage, _MAX_LEVERAGE)),
            "margin_type": "ISOLATED",
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            if not order:
                raise RuntimeError(f"empty order response: {resp}")
            logger.info(
                f"[cb] LIVE LONG {symbol} ({spec['product_id']}): {contracts} contracts @ ~{price:.4f}"
            )
            result = {
                "orderId": order.get("order_id", body["client_order_id"]),
                "symbol": symbol,
                "product_id": spec["product_id"],
                "side": "BUY",
                "contracts": contracts,
                "avgPrice": str(price),
                "origQty": str(contracts * spec["contract_size"]),
                "status": "FILLED",
                "paper": False,
                "venue": "coinbase",
            }
            # Track in-process position so the duplicate guard fires for subsequent
            # calls within the same Python process (e.g. manual scan executing multiple
            # trades in one button click). Without this, the guard at line 444 is always
            # empty in live mode and lets SOL LONG → SOL SHORT both go through.
            self._open_positions[symbol] = {
                "direction": "LONG",
                "entry_price": price,
                "qty": contracts * spec["contract_size"],
                "symbol": symbol,
            }
            self._symbol_count[symbol] = self._symbol_count.get(symbol, 0) + 1
            return result
        except Exception as e:
            logger.error(f"[cb] open_long LIVE error {symbol}: {e}")
            return None

    def open_short(
        self,
        symbol: str,
        size_usd: float,
        leverage: int = 3,
        stop_pct: float = 0.0,
        take_profit_pct: float = 0.0,
        strategy: str = "v10_perp",
    ) -> Optional[dict]:
        """Place a short order.  Returns order dict or None."""
        try:
            spec = self._spec(symbol)
        except CoinbaseSymbolError as e:
            logger.warning(str(e))
            return None

        price = self.get_mark_price(symbol)
        if price <= 0:
            logger.warning(f"[cb] open_short {symbol}: cannot get price")
            return None

        # Block opposite-side hedge stacking (SHORT while LONG open)
        _existing = self._open_positions.get(symbol, {})
        if _existing.get("direction") == "LONG":
            logger.warning(f"[cb] open_short blocked — LONG already open on {symbol}.")
            return None
        # Per-symbol position cap (default 3 — allows scaling in same direction)
        _MAX_PER_SYMBOL = 3
        if self._symbol_count.get(symbol, 0) >= _MAX_PER_SYMBOL:
            logger.warning(
                f"[cb] open_short blocked — {symbol} already has "
                f"{self._symbol_count[symbol]}/{_MAX_PER_SYMBOL} positions open."
            )
            return None

        # Live path
        contracts = self._qty_to_contracts(spec, size_usd, price)
        if contracts < 1:
            logger.warning(
                f"[cb] open_short {symbol}: size ${size_usd:.0f} too small for 1 {spec['code']} contract"
            )
            return None

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": spec["product_id"],
            "side": "SELL",
            "order_configuration": {"market_market_ioc": {"base_size": str(contracts)}},
            "leverage": str(min(leverage, _MAX_LEVERAGE)),
            "margin_type": "ISOLATED",
        }
        try:
            resp = self._request("POST", "/api/v3/brokerage/orders", body)
            order = resp.get("success_response") or resp.get("order") or {}
            if not order:
                raise RuntimeError(f"empty order response: {resp}")
            logger.info(
                f"[cb] LIVE SHORT {symbol} ({spec['product_id']}): {contracts} contracts @ ~{price:.4f}"
            )
            result = {
                "orderId": order.get("order_id", body["client_order_id"]),
                "symbol": symbol,
                "product_id": spec["product_id"],
                "side": "SELL",
                "contracts": contracts,
                "avgPrice": str(price),
                "origQty": str(contracts * spec["contract_size"]),
                "status": "FILLED",
                "paper": False,
                "venue": "coinbase",
            }
            # Track in-process position so the duplicate guard fires for subsequent
            # calls within the same Python process (same fix as open_long live path).
            self._open_positions[symbol] = {
                "direction": "SHORT",
                "entry_price": price,
                "qty": contracts * spec["contract_size"],
                "symbol": symbol,
            }
            self._symbol_count[symbol] = self._symbol_count.get(symbol, 0) + 1
            return result
        except Exception as e:
            logger.error(f"[cb] open_short LIVE error {symbol}: {e}")
            return None

    def close_position(
        self,
        symbol: str,
        pos_fallback: Optional[dict] = None,
        reason: str = "manual",
    ) -> Optional[dict]:
        """Close the open position for symbol.  Returns close result or None."""
        pos = self._open_positions.get(symbol) or pos_fallback
        if not pos:
            logger.warning(f"[cb] close_position {symbol}: no position found")
            return None

        direction = pos.get("direction", "LONG")
        entry_price = float(pos.get("entry_price", 0))
        qty = float(pos.get("qty", 0))
        exit_price = self.get_mark_price(symbol)
        if exit_price <= 0:
            exit_price = entry_price

        if direction == "LONG":
            pnl_usd = (exit_price - entry_price) * qty
        else:
            pnl_usd = (entry_price - exit_price) * qty

        # Live: place opposing market order to close
        try:
            spec = self._spec(symbol)
            contracts = pos.get(
                "contracts",
                self._qty_to_contracts(
                    spec, pos.get("position_usd", 0), exit_price
                ),
            )
            if contracts < 1:
                contracts = 1
            close_side = "SELL" if direction == "LONG" else "BUY"
            body = {
                "client_order_id": str(uuid.uuid4()),
                "product_id": spec["product_id"],
                "side": close_side,
                "order_configuration": {
                    "market_market_ioc": {"base_size": str(contracts)}
                },
                "margin_type": "ISOLATED",
            }
            self._request("POST", "/api/v3/brokerage/orders", body)
            logger.info(
                f"[cb] LIVE CLOSE {symbol}: {contracts} contracts @ ~{exit_price:.4f} pnl=${pnl_usd:.2f}"
            )
        except Exception as e:
            logger.error(f"[cb] close_position LIVE error {symbol}: {e}")
            # Return a close result anyway so the bot can clean up state

        self._open_positions.pop(symbol, None)
        self._symbol_count[symbol] = max(0, self._symbol_count.get(symbol, 0) - 1)
        if self._symbol_count[symbol] == 0:
            del self._symbol_count[symbol]
        return {
            "symbol": symbol,
            "exit_price": exit_price,
            "pnl_usd": round(pnl_usd, 4),
            "reason": reason,
            "venue": "coinbase",
            "paper": False,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_coinbase_broker: Optional[CoinbaseBroker] = None


def get_coinbase_broker() -> CoinbaseBroker:
    global _coinbase_broker
    if _coinbase_broker is None:
        _coinbase_broker = CoinbaseBroker()
        _coinbase_broker.connect()
    return _coinbase_broker
