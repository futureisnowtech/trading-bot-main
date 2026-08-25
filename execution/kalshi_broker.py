"""
execution/kalshi_broker.py — Kalshi prediction market execution (Pure REST).

This implementation bypasses the official SDK to avoid Pydantic validation
and dependency issues. It uses manual RSA-PSS signing for all V2 API requests.
"""

import logging
import os
import sqlite3
import sys
import uuid
import base64
import time
import requests
import json
import random
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization

# Add root to path for logging_db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from config import (
    DB_PATH,
    KALSHI_API_KEY_ID,
    REPO_ROOT,
    estimate_kalshi_order_fee_usd,
    resolve_runtime_path,
)
from forecast.weather_contracts import weather_mode_for_ticker
from logging_db.trade_logger import log_event, log_trade

logger = logging.getLogger(__name__)

_KALSHI_MIN_PRICE_CENTS = 1
_KALSHI_MAX_PRICE_CENTS = 99
_KALSHI_MARKETABLE_ENTRY_CENTS = 99
_KALSHI_MARKETABLE_EXIT_CENTS = 1
_WEATHER_SERIES_CACHE_TTL_SECONDS = 3600.0
_WEATHER_SERIES_CACHE: dict[str, object] = {
    "expires_at": 0.0,
    "series_meta": {},
}
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_MIN_REQUEST_INTERVAL_SECONDS = 0.08

# ── Credentials ───────────────────────────────────────────────────────────────
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
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


def _parse_market_strike(ticker: str) -> float:
    import re

    match = re.search(r"-[TBL](-?\d+\.?\d*)$", str(ticker or ""))
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0

class KalshiBroker:
    def __init__(self) -> None:
        self._connected = False
        self._open_positions: dict[str, dict] = {}  # key = f"{ticker}_{right}"
        self._private_key = None
        self._positions_synced_at_monotonic: float | None = None
        self._positions_authoritative = False
        self._position_sync_error = "not_synced"

    @property
    def _paper_balance_path(self) -> str:
        """Dry-run balance file. Single lane now that Lane A/B are retired."""
        return os.path.join(REPO_ROOT, "logs", "shadow_balance.json")

    def _init_shadow_balance(self, sync_positions: bool = True, quiet: bool = False) -> bool:
        self._connected = True
        self._private_key = None
        if not quiet:
            print("[KalshiBroker] Connected (SHADOW-ONLY FALLBACK) ✅")
        log_event("INFO", "KalshiBroker", "Connected (SHADOW-ONLY FALLBACK)")

        balance_dir = os.path.join(REPO_ROOT, "logs")
        os.makedirs(balance_dir, exist_ok=True)
        balance_path = self._paper_balance_path
        from config import ACCOUNT_SIZE
        if not os.path.exists(balance_path):
            try:
                with open(balance_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "balance": float(ACCOUNT_SIZE),
                        "last_updated": datetime.now(timezone.utc).isoformat()
                    }, f, indent=2)
            except Exception as exc:
                logger.warning(f"[KalshiBroker] Shadow balance init failed: {exc}")
        self._paper_balance = float(ACCOUNT_SIZE)
        if os.path.exists(balance_path):
            try:
                with open(balance_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._paper_balance = float(data.get("balance", ACCOUNT_SIZE))
            except Exception:
                pass

        # Load paper positions from SQLite table forecast_positions_paper
        self._restore_shadow_positions()
        self._positions_synced_at_monotonic = time.monotonic()
        self._positions_authoritative = True
        self._position_sync_error = ""
        return True

    def connect(self, *, sync_positions: bool = True, quiet: bool = False) -> bool:
        """Verify credentials and load private key for signing."""
        if config.SHADOW_EXECUTION:
            # A dry run must never authenticate against or read from the live
            # account before falling back to its isolated shadow book.
            return self._init_shadow_balance(
                sync_positions=sync_positions,
                quiet=quiet,
            )

        private_key_path = resolve_runtime_path(
            KALSHI_PRIVATE_KEY_PATH,
            "/run/secrets/kalshi_private_key.pem",
            os.path.join(REPO_ROOT, "kalshi_private_key.pem"),
        )

        if not KALSHI_API_KEY_ID or not private_key_path:
            log_event("ERROR", "KalshiBroker", "Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH in .env")
            if config.SHADOW_EXECUTION:
                return self._init_shadow_balance(sync_positions=sync_positions, quiet=quiet)
            return False

        if not os.path.exists(private_key_path):
            log_event(
                "ERROR",
                "KalshiBroker",
                f"Kalshi private key not found at resolved path: {private_key_path}",
            )
            if config.SHADOW_EXECUTION:
                return self._init_shadow_balance(sync_positions=sync_positions, quiet=quiet)
            return False

        try:
            with open(private_key_path, 'r') as f:
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
            if not quiet:
                print(f"[KalshiBroker] Connected (LIVE) ✅ | Balance: ${float(resp.get('balance_dollars', 0)):.2f}")
            log_event("INFO", "KalshiBroker", "Connected (LIVE)")

            if sync_positions:
                if not self._sync_positions():
                    raise RuntimeError(
                        "Initial position snapshot was not authoritative: "
                        f"{self._position_sync_error or 'unknown_error'}"
                    )
        except Exception as e:
            if config.SHADOW_EXECUTION:
                return self._init_shadow_balance(sync_positions=sync_positions, quiet=quiet)
            else:
                if not quiet:
                    print(f"[KalshiBroker] Connection error: {e}")
                log_event("ERROR", "KalshiBroker", f"Connection failed: {e}")
                self._connected = False
                return False

        return True


    def is_connected(self) -> bool:
        return self._connected and (self._private_key is not None or config.SHADOW_EXECUTION)


    def sync_positions(self) -> bool:
        """Refresh local position cache; True means the snapshot is authoritative."""
        return self._sync_positions()

    def position_snapshot_status(self, *, max_age_sec: float | None = None) -> dict:
        """Expose whether risk controls may safely rely on the cached positions."""
        age = None
        if self._positions_synced_at_monotonic is not None:
            age = max(0.0, time.monotonic() - self._positions_synced_at_monotonic)
        limit = float(
            max_age_sec
            if max_age_sec is not None
            else getattr(config, "KALSHI_POSITION_SNAPSHOT_MAX_AGE_SEC", 60.0)
        )
        fresh = bool(
            self._positions_authoritative
            and age is not None
            and age <= max(1.0, limit)
        )
        return {
            "authoritative": bool(self._positions_authoritative),
            "fresh": fresh,
            "age_sec": age,
            "max_age_sec": limit,
            "error": self._position_sync_error,
            "position_count": len(self._open_positions),
        }

    def has_fresh_position_snapshot(self, *, max_age_sec: float | None = None) -> bool:
        return bool(self.position_snapshot_status(max_age_sec=max_age_sec)["fresh"])

    def _load_latest_entry_context(self, ticker: str, side: str) -> dict:
        """Recover weather entry metadata so exits remain learnable after restarts."""
        payload = {
            "entry_price": None,
            "forecast_yes_prob": None,
            "model_prob_gfs": None,
            "model_prob_ecmwf": None,
            "weather_mode": None,
            "forecast_hours_to_resolution": None,
            "entered_at": None,
        }
        try:
            with sqlite3.connect(config.DB_PATH, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT price,
                           forecast_yes_prob,
                           model_prob_gfs,
                           model_prob_ecmwf,
                           weather_mode,
                           forecast_hours_to_resolution,
                           ts
                    FROM trades
                    WHERE broker='kalshi'
                      AND action='BUY'
                      AND symbol=?
                      AND contract_side=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (ticker, side.upper()),
                ).fetchone()

                if not row:
                    pos_row = conn.execute(
                        """
                        SELECT entry_price, category
                        FROM forecast_positions
                        WHERE ticker=?
                          AND active=1
                        """,
                        (ticker,),
                    ).fetchone()
                    if pos_row:
                        payload["entry_price"] = pos_row["entry_price"]
                        payload["weather_mode"] = pos_row["category"]
                        return payload
        except Exception:
            return payload

        if not row:
            return payload

        payload.update(
            {
                "entry_price": row["price"],
                "forecast_yes_prob": row["forecast_yes_prob"],
                "model_prob_gfs": row["model_prob_gfs"],
                "model_prob_ecmwf": row["model_prob_ecmwf"],
                "weather_mode": row["weather_mode"],
                "forecast_hours_to_resolution": row["forecast_hours_to_resolution"],
            }
        )
        ts_value = row["ts"]
        if ts_value not in (None, ""):
            try:
                payload["entered_at"] = datetime.fromtimestamp(
                    float(ts_value), tz=timezone.utc
                ).isoformat()
            except Exception:
                payload["entered_at"] = str(ts_value)
        return payload

    def _normalize_price_cents(self, price: float) -> int:
        cents = int(round(float(price) * 100))
        return max(_KALSHI_MIN_PRICE_CENTS, min(_KALSHI_MAX_PRICE_CENTS, cents))

    def _extract_error_code(self, resp: dict) -> str:
        error = resp.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "error")
        if error:
            return str(error)
        return ""

    @staticmethod
    def _coerce_price(raw) -> float | None:
        """Kalshi prices arrive either as decimal dollar strings or integer cents."""
        if raw in (None, ""):
            return None
        try:
            if isinstance(raw, str) and "." in raw:
                return float(raw)
            return float(raw) / 100.0
        except (TypeError, ValueError):
            return None

    def _extract_average_fill_price(self, order_info: dict) -> float:
        """Average fill price, always denominated on the YES leg.

        _realized_pnl treats YES and NO as complements, which is only valid if
        both legs speak one denomination. They did not: a NO entry is submitted
        as side="no" and echoes no_price, while closing that NO position is
        submitted as side="yes"/action="buy" and echoes yes_price. Booking a
        no_price entry against a yes_price exit overstated the round trip by
        2*entry - 1 per contract and flipped sign whenever 1-entry < exit < entry
        -- which is most of the band for NO entries at 0.6-0.85.

        yes_price_dollars is authoritative when present; anything derived from
        our own echo or from fill cost is flipped to the YES leg using
        outcome_side.
        """
        direct = self._coerce_price(order_info.get("yes_price_dollars"))
        if direct is not None and direct > 0:
            return direct

        outcome = str(order_info.get("outcome_side") or "").strip().lower()
        needs_flip = outcome == "no"

        def _as_yes(price: float) -> float:
            return (1.0 - price) if needs_flip else price

        for key in ("average_price", "average_fill_price", "price"):
            price = self._coerce_price(order_info.get(key))
            if price is not None:
                return _as_yes(price)

        fill_count = self._extract_fill_count(order_info)
        if fill_count > 0:
            total_cost = 0.0
            for key in ("taker_fill_cost_dollars", "maker_fill_cost_dollars"):
                raw = order_info.get(key)
                if raw in (None, ""):
                    continue
                try:
                    total_cost += float(raw)
                except (TypeError, ValueError):
                    continue
            if total_cost > 0:
                return _as_yes(total_cost / fill_count)
        return 0.0

    def _extract_fill_count(self, order_info: dict) -> float:
        for key in ("fill_count_fp", "fill_count"):
            raw = order_info.get(key)
            if raw in (None, ""):
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _extract_remaining_count(self, order_info: dict, requested_qty: int) -> float:
        for key in ("remaining_count", "remaining_count_fp", "remaining_orders_count", "count_left"):
            raw = order_info.get(key)
            if raw in (None, ""):
                continue
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
        fill_qty = self._extract_fill_count(order_info)
        return max(0.0, float(requested_qty) - float(fill_qty))

    def _extract_total_fees(self, order_info: dict, qty: int) -> float:
        total = 0.0
        found = False
        for key in ("taker_fees_dollars", "maker_fees_dollars"):
            raw = order_info.get(key)
            if raw in (None, ""):
                continue
            try:
                total += float(raw)
                found = True
            except (TypeError, ValueError):
                continue
        if found:
            return total

        avg_fee = order_info.get("average_fee_paid")
        fill_count = self._extract_fill_count(order_info) or float(qty)
        if avg_fee not in (None, "") and fill_count > 0:
            try:
                return float(avg_fee) * fill_count
            except (TypeError, ValueError):
                pass
        # Fallback estimate for historical/external fills. Production entries
        # are taker-only, but the exchange can still report either fee bucket
        # for older orders, so infer the bucket from its authoritative costs.
        def _cost(key: str) -> float:
            try:
                return float(order_info.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        is_maker = _cost("maker_fill_cost_dollars") > _cost("taker_fill_cost_dollars")
        fill_price = self._extract_average_fill_price(order_info)
        price = fill_price if (fill_price > 0 and qty > 0) else 0.50
        return estimate_kalshi_order_fee_usd(
            qty, price, maker=is_maker, round_up_cents=True
        )

    @staticmethod
    def _event_order_side(*, right: str, action: str) -> str:
        normalized_right = str(right or "C").upper()
        normalized_action = str(action or "buy").lower()
        if normalized_action == "buy":
            return "bid" if normalized_right == "C" else "ask"
        return "ask" if normalized_right == "C" else "bid"

    @staticmethod
    def _realized_pnl(
        *,
        held_side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        fee_usd: float,
    ) -> float:
        """Realized P&L for a single exit, in raw Kalshi contract-price units.

        YES and NO are complements: a NO holder gains as the contract price
        falls. Fill prices stay contract-native (the API-facing conversion in
        _yes_leg_price applies only to the outbound order body) -- the sole
        thing that varies by side is the sign of the basis.

        Sole owner of this formula. 174d23d inlined it, dropped the NO branch,
        and every NO exit was then booked with long-YES sign.
        """
        side = str(held_side or "").strip().upper()
        if side not in ("YES", "NO"):
            raise ValueError(
                f"Cannot compute realized P&L for unknown held side {held_side!r}; "
                "expected 'YES' or 'NO'."
            )
        basis = (entry_price - exit_price) if side == "NO" else (exit_price - entry_price)
        return basis * float(qty) - float(fee_usd)

    @staticmethod
    def _yes_leg_price(right: str, contract_price: float) -> float:
        normalized_right = str(right or "C").upper()
        price = float(contract_price or 0.0)
        if normalized_right == "P":
            price = 1.0 - price
        return max(0.01, min(0.99, round(price, 4)))

    def _build_event_order_body(
        self,
        *,
        ticker: str,
        right: str,
        qty: int,
        limit_price: float,
        action: str,
        reduce_only: bool = False,
    ) -> dict:
        yes_leg_price = self._yes_leg_price(right, limit_price)
        return {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": self._event_order_side(right=right, action=action),
            "count": f"{max(0, int(qty)):.2f}",
            "price": f"{yes_leg_price:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": False,
            "reduce_only": bool(reduce_only),
            "subaccount": 0,
            "exchange_index": 0,
        }

    def _normalize_order_response(
        self,
        resp: dict,
        *,
        requested_qty: int,
    ) -> tuple[dict, str]:
        order_info = resp.get("order") if isinstance(resp.get("order"), dict) else dict(resp)
        fill_qty = self._extract_fill_count(order_info)
        remaining_qty = self._extract_remaining_count(order_info, requested_qty)
        status = str(order_info.get("status") or "").strip().lower()
        # Every production order is IOC. Kalshi's V2 create response is a flat
        # object with fill_count/remaining_count and may have no status; an IOC
        # can also be reported as canceled after a partial fill. Any positive
        # fill is therefore an executed fill, never a resting order.
        if fill_qty > 0:
            return order_info, "executed"
        if status in {"executed", "filled"}:
            # The compact create response can omit fill details; hydrate before
            # deciding whether the apparent execution actually filled.
            return order_info, "executed"
        if status in {"resting", "pending", "open"}:
            return order_info, "unexpected_resting"
        if status in {"canceled", "cancelled", "expired", "rejected"}:
            return order_info, "no_fill"
        if remaining_qty > 0:
            return order_info, "no_fill"
        return order_info, "no_fill"

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a resting order. Returns True if the exchange accepted the cancel.

        The path must match the endpoint family the order was CREATED on.
        Orders are placed on /portfolio/events/orders (v2); the bare
        /portfolio/orders/{id} cancel is the v1 endpoint and now answers
        HTTP 410 deprecated_v1_order_endpoint. Verified live 2026-08-18: the
        old path left a historical resting order on the book after a
        "successful" cancel returned False.
        """
        oid = str(order_id or "").strip()
        if not oid:
            return False
        try:
            resp = self._request("DELETE", f"/trade-api/v2/portfolio/events/orders/{oid}")
            code = self._extract_error_code(resp)
            if code:
                logger.error("Cancel rejected for order %s: %s", oid, code)
                return False
            return True
        except Exception as exc:
            logger.warning("Cancel failed for order %s: %s", oid, exc)
            return False

    def list_resting_orders(self) -> list[dict]:
        """Every order still live on the exchange book; uncertainty is an error."""
        resp = self._request(
            "GET", "/trade-api/v2/portfolio/orders", params={"status": "resting"}
        )
        code = self._extract_error_code(resp)
        if code:
            raise RuntimeError(f"resting_order_snapshot_failed:{code}")
        orders = resp.get("orders")
        if not isinstance(orders, list):
            raise RuntimeError("resting_order_snapshot_missing_orders")
        return list(orders)

    def cancel_and_confirm(self, order_id: str) -> bool:
        """Cancel an order and prove it is no longer resting before continuing."""
        oid = str(order_id or "").strip()
        if not oid or not self.cancel_order(oid):
            return False
        for _attempt in range(3):
            try:
                resting_ids = {
                    str(row.get("order_id") or "").strip()
                    for row in self.list_resting_orders()
                }
                if oid not in resting_ids:
                    return True
            except Exception as exc:
                logger.warning("Cancellation confirmation failed for %s: %s", oid, exc)
            time.sleep(0.25)
        logger.error("Order %s could not be proven cancelled.", oid)
        return False

    def cancel_all_resting_orders(self, *, reason: str = "startup") -> dict:
        """Cancel every resting order and return an authoritative sweep result.

        Production never intentionally rests an entry. This sweep protects
        against historical, external, or exchange-anomalous orders before any
        strategy work begins.
        """
        try:
            resting = self.list_resting_orders()
        except Exception as exc:
            logger.error("[OrphanSweep] Could not inspect resting orders: %s", exc)
            return {"ok": False, "found": 0, "cleared": 0, "failed": [], "error": str(exc)}
        if not resting:
            logger.info("[OrphanSweep] No resting orders at %s.", reason)
            return {"ok": True, "found": 0, "cleared": 0, "failed": []}
        cleared = 0
        failed: list[str] = []
        for order in resting:
            oid = str(order.get("order_id") or "").strip()
            if not oid:
                continue
            ticker = order.get("ticker") or "?"
            if self.cancel_and_confirm(oid):
                cleared += 1
                logger.warning(
                    "[OrphanSweep] Cancelled stray resting order %s on %s (%s).",
                    oid, ticker, reason,
                )
            else:
                failed.append(oid)
                logger.error(
                    "[OrphanSweep] FAILED to cancel resting order %s on %s -- "
                    "manual intervention required.", oid, ticker,
                )
        return {
            "ok": not failed,
            "found": len(resting),
            "cleared": cleared,
            "failed": failed,
        }

    def _order_filled_qty(self, order_id: str) -> float:
        """Contracts filled so far on a resting order."""
        details = self._request("GET", f"/trade-api/v2/portfolio/orders/{order_id}")
        code = self._extract_error_code(details)
        if code:
            raise RuntimeError(f"order_status_failed:{code}")
        order = details.get("order")
        if not isinstance(order, dict):
            raise RuntimeError("order_status_missing_order")
        return self._extract_fill_count(order)

    def _hydrate_order_details(self, order_info: dict) -> dict:
        order_id = str(order_info.get("order_id") or "").strip()
        if not order_id:
            return order_info
        try:
            details = self._request("GET", f"/trade-api/v2/portfolio/orders/{order_id}")
            hydrated = details.get("order", {})
            if isinstance(hydrated, dict) and hydrated:
                return hydrated
        except Exception:
            pass
        return order_info

    def _apply_exit_fill(
        self,
        *,
        ticker: str,
        fallback_right: str,
        requested_qty: int,
        order_info: dict,
        order_type: str,
        default_side: str,
        reason: str,
        strategy: str,
    ) -> dict:
        key_yes = f"{ticker}_C"
        key_no = f"{ticker}_P"
        key = key_yes if key_yes in self._open_positions else key_no
        if key not in self._open_positions:
            key = f"{ticker}_{fallback_right}"

        pos_info = self._open_positions.get(key, {})
        held_qty = float(pos_info.get("qty") or 0.0)
        fill_qty = self._extract_fill_count(order_info)
        fill_qty = max(0.0, min(fill_qty, held_qty or float(requested_qty)))
        if fill_qty <= 0:
            return {
                "order_id": order_info.get("order_id", "ERR"),
                "status": str(order_info.get("status") or "pending"),
                "entry_price": float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50),
                "exit_price": 0.0,
                "pnl_usd": 0.0,
                "filled_qty": 0.0,
                "remaining_position_qty": held_qty or float(requested_qty),
            }
        exit_price = self._extract_average_fill_price(order_info)
        fee_usd = self._extract_total_fees(order_info, int(round(fill_qty or requested_qty)))
        order_id = order_info.get("order_id", "ERR")
        entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)
        held_side = str(pos_info.get("side", default_side) or default_side).upper()
        if exit_price > 0:
            pnl_usd = self._realized_pnl(
                held_side=held_side,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=fill_qty,
                fee_usd=fee_usd,
            )
        else:
            pnl_usd = 0.0
        remaining_qty = max(0.0, held_qty - fill_qty)

        if remaining_qty > 0 and key in self._open_positions:
            self._open_positions[key]["qty"] = remaining_qty
        else:
            self._open_positions.pop(key, None)

        try:
            log_trade(
                strategy=strategy,
                broker="kalshi",
                symbol=ticker,
                action="SELL",
                order_type=order_type,
                qty=fill_qty,
                price=exit_price,
                fee_usd=fee_usd,
                pnl_usd=pnl_usd,
                order_id=order_id,
                notes=reason,
                won=(pnl_usd > 0),
                contract_side=pos_info.get("side", default_side).upper(),
                forecast_yes_prob=pos_info.get("forecast_yes_prob"),
                model_prob_gfs=pos_info.get("model_prob_gfs"),
                model_prob_ecmwf=pos_info.get("model_prob_ecmwf"),
                weather_mode=pos_info.get("weather_mode"),
                forecast_hours_to_resolution=pos_info.get("forecast_hours_to_resolution"),
            )
        except Exception as e:
            logger.error(f"[KalshiBroker] log_trade exit error: {e}")

        return {
            "order_id": order_id,
            "status": "executed",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_usd": pnl_usd,
            "filled_qty": fill_qty,
            "remaining_position_qty": remaining_qty,
        }

    def _apply_entry_fill(
        self,
        *,
        ticker: str,
        right: str,
        requested_qty: int,
        order_info: dict,
        order_type: str,
        status: str,
        context: dict,
    ) -> dict:
        fill_qty = self._extract_fill_count(order_info)
        if fill_qty <= 0:
            return {
                "order_id": order_info.get("order_id", "ERR"),
                "status": status,
                "price": 0.0,
                "qty": 0,
                "filled_qty": 0,
                "remaining_order_qty": self._extract_remaining_count(order_info, requested_qty),
            }

        fill_qty = max(0.0, min(float(requested_qty), float(fill_qty)))
        fill_price = self._extract_average_fill_price(order_info)
        fee_usd = self._extract_total_fees(order_info, int(round(fill_qty)))
        order_id = order_info.get("order_id", "ERR")
        key = f"{ticker}_{right}"
        side = "YES" if right == "C" else "NO"
        held_fill_price = fill_price if side == "YES" else (1.0 - fill_price)
        existing = self._open_positions.get(key, {})
        prior_qty = float(existing.get("qty") or 0.0)
        prior_entry = float(existing.get("entry_price") or existing.get("entry") or 0.0)
        prior_held_entry = float(
            existing.get("held_side_entry_price")
            or (prior_entry if side == "YES" else 1.0 - prior_entry)
        )
        blended_entry = fill_price
        if prior_qty > 0 and fill_price > 0:
            blended_entry = ((prior_qty * prior_entry) + (fill_qty * fill_price)) / (prior_qty + fill_qty)
        total_qty = prior_qty + fill_qty
        blended_held_entry = held_fill_price
        if prior_qty > 0 and held_fill_price > 0:
            blended_held_entry = (
                (prior_qty * prior_held_entry) + (fill_qty * held_fill_price)
            ) / total_qty
        # Production entries are IOC, so the venue cancels every unfilled
        # remainder; it must never be represented as a locally resting order.
        remaining_order_qty = 0.0

        self._open_positions[key] = {
            "qty": total_qty,
            "side": side,
            "local_symbol": ticker,
            "right": right,
            "entry": blended_entry,
            "entry_price": blended_entry,
            "yes_leg_entry_price": blended_entry,
            "held_side_entry_price": blended_held_entry,
            "market_exposure_usd": total_qty * blended_held_entry,
            "forecast_yes_prob": context.get("forecast_yes_prob"),
            "model_prob_gfs": context.get("model_prob_gfs"),
            "model_prob_ecmwf": context.get("model_prob_ecmwf"),
            "weather_mode": context.get("weather_mode"),
            "forecast_hours_to_resolution": context.get("forecast_hours_to_resolution"),
            "last_trade_at": context.get("last_trade_at", ""),
            "entered_at": existing.get("entered_at") or datetime.now(timezone.utc).isoformat(),
            "resting_order_id": order_id if remaining_order_qty > 0 else None,
            "resting_remaining_qty": remaining_order_qty,
        }

        try:
            log_trade(
                strategy=context.get("strategy", "forecast_weather"),
                broker="kalshi",
                symbol=ticker,
                action="BUY",
                order_type=order_type,
                qty=fill_qty,
                price=fill_price,
                fee_usd=fee_usd,
                order_id=order_id,
                notes=context.get("reason", ""),
                contract_side=side,
                forecast_yes_prob=context.get("forecast_yes_prob"),
                model_prob_gfs=context.get("model_prob_gfs"),
                model_prob_ecmwf=context.get("model_prob_ecmwf"),
                weather_mode=context.get("weather_mode"),
                forecast_hours_to_resolution=context.get("forecast_hours_to_resolution"),
            )
        except Exception as e:
            logger.error(f"[KalshiBroker] log_trade entry error: {e}")

        return {
            "order_id": order_id,
            "status": status,
            "price": fill_price,
            "yes_leg_price": fill_price,
            "held_side_price": held_fill_price,
            "qty": int(round(fill_qty)),
            "filled_qty": fill_qty,
            "remaining_order_qty": remaining_order_qty,
            "position_qty_after_fill": total_qty,
        }

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Pace all calls and retry idempotent reads after Kalshi throttling."""
        attempts = 4 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            global _LAST_REQUEST_AT
            with _REQUEST_LOCK:
                delay = _MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _LAST_REQUEST_AT)
                if delay > 0:
                    time.sleep(delay)
                result = self._request_once(method, path, params=params, body=body)
                _LAST_REQUEST_AT = time.monotonic()
            error = result.get("error") if isinstance(result, dict) else None
            code = str(error.get("code") if isinstance(error, dict) else error or "")
            if "too_many_requests" not in code or attempt == attempts - 1:
                return result
            time.sleep(min(4.0, 0.35 * (2 ** attempt)) + random.uniform(0.0, 0.2))
        return {"error": {"code": "too_many_requests", "message": "retry budget exhausted"}}

    def _request_once(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Execute signed Kalshi V2 request."""

        if config.SHADOW_EXECUTION and method.upper() == "POST" and "orders" in path:
            raise RuntimeError("Mutating request blocked by shadow mode firewall")

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

            payload = None
            try:
                payload = resp.json()
            except Exception as json_err:
                if resp.status_code >= 400:
                    level = "WARNING" if resp.status_code == 429 else "ERROR"
                    log_event(
                        level,
                        "KalshiBroker",
                        f"HTTP {resp.status_code} {path}: {resp.text[:160]}",
                    )
                    logger.error(
                        "[KalshiBroker] Non-JSON error for %s. Status=%s Text=%s",
                        url,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return {
                        "error": {
                            "code": "too_many_requests" if resp.status_code == 429 else f"http_{resp.status_code}",
                            "message": resp.text[:200] or f"json_decode_failed: {json_err}",
                            "http_status": resp.status_code,
                        }
                    }
                logger.error(f"[KalshiBroker] JSON decode failed for {url}. Status={resp.status_code} Text={resp.text[:200]}")
                return {"error": f"json_decode_failed: {str(json_err)}"}

            if resp.status_code >= 400:
                level = "WARNING" if resp.status_code == 429 else "ERROR"
                log_event(
                    level,
                    "KalshiBroker",
                    f"HTTP {resp.status_code} {path}: {resp.text[:160]}",
                )
                if isinstance(payload, dict) and payload.get("error"):
                    error = payload["error"]
                    if isinstance(error, dict):
                        error.setdefault("http_status", resp.status_code)
                    return payload
                return {
                    "error": {
                        "code": "too_many_requests" if resp.status_code == 429 else f"http_{resp.status_code}",
                        "message": resp.text[:200],
                        "http_status": resp.status_code,
                    }
                }

            return payload if isinstance(payload, dict) else {"error": "unexpected_response_shape"}
        except Exception as e:
            return {"error": str(e)}

    def _restore_shadow_positions(self) -> None:
        """Start shadow mode with a flat book.

        Shadow state used to be rehydrated from forecast_positions_paper. The paper
        lanes are retired and that table is gone, so a dry run now begins with no
        positions rather than inheriting a simulated book.
        """
        self._open_positions.clear()

    def _sync_positions(self) -> bool:
        """Sync positions atomically; never turn an API failure into a flat book."""
        if config.SHADOW_EXECUTION:
            self._restore_shadow_positions()
            self._positions_synced_at_monotonic = time.monotonic()
            self._positions_authoritative = True
            self._position_sync_error = ""
            return True

        if not self.is_connected():
            self._positions_authoritative = False
            self._position_sync_error = "broker_not_connected"
            return False
        try:
            data = self._request("GET", "/trade-api/v2/portfolio/positions")
            # Parse before clearing. Clearing first meant one 429 or read timeout
            # emptied the position map that the concurrency cap, deployed-capital
            # gate and duplicate guard all read, so a transient API failure looked
            # exactly like a flat book.
            if self._extract_error_code(data):
                self._positions_authoritative = False
                self._position_sync_error = self._extract_error_code(data)
                logger.error(
                    "[KalshiBroker] Position sync failed, keeping previous snapshot: %s",
                    (data or {}).get("error"),
                )
                return False
            positions = (data or {}).get("market_positions")
            if positions is None:
                self._positions_authoritative = False
                self._position_sync_error = "missing_market_positions"
                logger.error(
                    "[KalshiBroker] Position sync returned no market_positions; "
                    "keeping previous snapshot."
                )
                return False

            next_positions: dict[str, dict] = {}
            for p in positions:
                qty_str = p.get("position_fp", "0")
                qty = float(qty_str)
                if qty == 0:
                    continue

                ticker = p.get("ticker")
                side = "YES" if qty > 0 else "NO"
                right = "C" if side == "YES" else "P"
                abs_qty = abs(qty)
                market_exposure_usd = abs(float(p.get("market_exposure_dollars") or 0.0))
                entry_context = self._load_latest_entry_context(ticker, side)
                held_side_entry_price = (
                    market_exposure_usd / abs_qty
                    if abs_qty > 0 and market_exposure_usd > 0
                    else 0.0
                )
                if held_side_entry_price > 0.0:
                    # Broker exposure is the authoritative held-side basis for
                    # the current remaining position. Local fills are retained
                    # only for forecast context, never allowed to override it.
                    entry_price = (
                        held_side_entry_price
                        if side == "YES"
                        else 1.0 - held_side_entry_price
                    )
                else:
                    entry_price = float(entry_context.get("entry_price") or 0.0)
                    held_side_entry_price = (
                        entry_price if side == "YES" else 1.0 - entry_price
                    )

                key = f"{ticker}_{right}"
                next_positions[key] = {
                    "local_symbol": ticker,
                    "right": right,
                    "qty": abs_qty,
                    "entry": entry_price,
                    "entry_price": entry_price,
                    "yes_leg_entry_price": entry_price,
                    "held_side_entry_price": held_side_entry_price,
                    "market_exposure_usd": market_exposure_usd,
                    "side": side,
                    "forecast_yes_prob": entry_context.get("forecast_yes_prob"),
                    "model_prob_gfs": entry_context.get("model_prob_gfs"),
                    "model_prob_ecmwf": entry_context.get("model_prob_ecmwf"),
                    "weather_mode": entry_context.get("weather_mode"),
                    "forecast_hours_to_resolution": entry_context.get("forecast_hours_to_resolution"),
                    "order_id": "EXISTING",
                    "entered_at": entry_context.get("entered_at")
                    or datetime.now(timezone.utc).isoformat(),
                }
            self._open_positions = next_positions
            self._positions_synced_at_monotonic = time.monotonic()
            self._positions_authoritative = True
            self._position_sync_error = ""
            return True
        except Exception as e:
            self._positions_authoritative = False
            self._position_sync_error = str(e)
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")
            return False

    def discover_markets(self) -> list[dict]:
        """Discover active Kalshi weather contracts."""
        if not self.is_connected():
            return []

        results = []
        try:
            from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key

            def _is_error_payload(payload: dict) -> bool:
                return bool(isinstance(payload, dict) and payload.get("error"))

            def _series_is_tradeable_weather_lane(series_info: dict) -> bool:
                ticker = str(series_info.get("ticker") or "")
                title = str(series_info.get("title") or "")
                if not ticker:
                    return False

                title_lower = title.lower()
                if not ticker.startswith(
                    ("KXTEMP", "KXHIGH", "KXLOW", "KXRAIN", "KXHIGHT", "KXLOWT", "HIGH", "LOW", "RAIN")
                ) and not (
                    "hourly directional" in title_lower and "temperature" in title_lower
                ):
                    return False

                blob = f"{ticker} {title}".lower()
                city_key = resolve_weather_city_key(ticker, contract_name=title)
                mode = weather_mode_for_ticker(ticker)
                if mode is None and "hourly directional" in title_lower and "temperature" in title_lower:
                    mode = "TEMP"

                if city_key is None or mode not in {"HIGH", "LOW", "RAIN", "TEMP"}:
                    return False

                return any(keyword in blob for keyword in ("temperature", "temp", "rain"))

            weather_series_meta: dict[str, dict] = {}
            cache_expires_at = float(_WEATHER_SERIES_CACHE.get("expires_at") or 0.0)
            if cache_expires_at > time.time():
                cached_meta = _WEATHER_SERIES_CACHE.get("series_meta") or {}
                if isinstance(cached_meta, dict):
                    weather_series_meta = dict(cached_meta)

            if not weather_series_meta:
                series_catalog = self._request(
                    "GET",
                    "/trade-api/v2/series",
                    params={"limit": 200},
                )
                if not _is_error_payload(series_catalog):
                    for series_info in series_catalog.get("series", []):
                        if _series_is_tradeable_weather_lane(series_info):
                            series_id = str(series_info.get("ticker") or "")
                            if series_id:
                                weather_series_meta[series_id] = {
                                    "title": str(series_info.get("title") or ""),
                                    "category": str(series_info.get("category") or ""),
                                }
                _WEATHER_SERIES_CACHE["expires_at"] = time.time() + _WEATHER_SERIES_CACHE_TTL_SECONDS
                _WEATHER_SERIES_CACHE["series_meta"] = dict(weather_series_meta)

            discovery_series: list[str] = []
            seen_series: set[str] = set()
            if weather_series_meta:
                for series_id, meta in weather_series_meta.items():
                    title_lower = str(meta.get("title") or "").lower()
                    city_key = resolve_weather_city_key(series_id, contract_name=str(meta.get("title") or ""))
                    lane = weather_mode_for_ticker(series_id)
                    if lane is None and "hourly directional" in title_lower and "temperature" in title_lower:
                        lane = "TEMP"
                    if city_key is None or lane not in {"HIGH", "LOW", "RAIN", "TEMP"}:
                        continue
                    discovery_series.append(series_id)

                discovery_series = sorted(list(set(discovery_series)))
                seen_series.update(discovery_series)

            if not discovery_series:
                for loc in STATIONS.values():
                    for series_id in loc.get("series", []):
                        if series_id not in seen_series:
                            seen_series.add(series_id)
                            discovery_series.append(series_id)

            seen_contracts: set[tuple[str, str]] = set()
            seen_stubs: set[str] = set()

            for series_id in discovery_series:
                meta = weather_series_meta.get(series_id, {})
                title_lower = str(meta.get("title") or "").lower()
                scan_statuses = ("open", "unopened") if "hourly directional" in title_lower else ("open",)
                for event_status in scan_statuses:
                    data = self._request(
                        "GET",
                        "/trade-api/v2/events",
                        params={
                            "series_ticker": series_id,
                            "status": event_status,
                            "with_nested_markets": "true",
                        },
                    )
                    if _is_error_payload(data):
                        err = data.get("error")
                        log_event(
                            "WARNING",
                            "KalshiBroker",
                            f"Weather discovery skipped {series_id} {event_status}: {err}",
                        )
                        continue

                    for event in data.get("events", []):
                        ticker = str(event.get("event_ticker") or "")
                        event_title = str(event.get("title") or meta.get("title") or ticker)
                        cat = str(event.get("category") or meta.get("category") or "")
                        if not ticker:
                            continue
                        if not _is_weather_market(ticker, event_title, cat):
                            city_key = resolve_weather_city_key(ticker, contract_name=event_title)
                            if city_key is None or weather_mode_for_ticker(ticker) is None:
                                continue

                        markets = event.get("markets") or []
                        initialized_seen = False
                        initialized_close_time = ""

                        for market in markets:
                            market_status = str(market.get("status") or "").lower()
                            market_ticker = str(market.get("ticker") or "")
                            if not market_ticker:
                                continue

                            if market_status == "initialized":
                                initialized_seen = True
                                initialized_close_time = (
                                    str(market.get("close_time") or "")
                                    or str(market.get("expiration_time") or "")
                                )
                                continue

                            if market_status != "active":
                                continue
                            if weather_mode_for_ticker(market_ticker) is None:
                                continue

                            strike = _parse_market_strike(market_ticker)
                            contract_name = str(market.get("title") or "")
                            last_trade_at = str(
                                market.get("close_time")
                                or market.get("expiration_time")
                                or ""
                            )
                            for side in ("YES", "NO"):
                                key = (market_ticker, side)
                                if key in seen_contracts:
                                    continue
                                seen_contracts.add(key)
                                right = "C" if side == "YES" else "P"
                                results.append(
                                    {
                                        "underlier": ticker,
                                        "event_title": event_title or ticker,
                                        "local_symbol": market_ticker,
                                        "conid": None,
                                        "right": right,
                                        "strike": strike,
                                        "last_trade_at": last_trade_at,
                                        "exchange": "KALSHI",
                                        "currency": "USD",
                                        "contract_name": contract_name,
                                        "long_name": contract_name,
                                        "category": cat,
                                        "side": side,
                                    }
                                )

                        if initialized_seen and ticker not in seen_stubs:
                            seen_stubs.add(ticker)
                            results.append(
                                {
                                    "underlier": ticker,
                                    "event_title": event_title or ticker,
                                    "market_name": event_title or ticker,
                                    "exchange": "KALSHI",
                                    "category": cat,
                                    "last_trade_at": initialized_close_time,
                                    "stub_only": True,
                                }
                            )
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
            log_event("ERROR", "KalshiBroker", f"get_quote error for {ticker}: {e}")
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
        if config.SHADOW_EXECUTION:
            return self._execute_shadow_buy(contract_dict, qty, limit_price, **kwargs)

        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        ticker = contract_dict["local_symbol"]
        right = str(contract_dict.get("right") or "C").upper()
        side = "yes" if right == "C" else "no"
        order_type = kwargs.get("type", "limit").lower()

        body = self._build_event_order_body(
            ticker=ticker,
            right=right,
            qty=qty,
            limit_price=limit_price,
            action="buy",
            reduce_only=False,
        )

        resp = self._request("POST", "/trade-api/v2/portfolio/events/orders", body=body)
        error_code = self._extract_error_code(resp)
        if error_code:
            logger.error(f"Order failed or rejected: {resp}")
            return {"order_id": "ERR", "status": error_code, "error": resp.get("error")}

        order_info, status = self._normalize_order_response(resp, requested_qty=qty)
        context = {
            "forecast_yes_prob": kwargs.get("forecast_yes_prob"),
            "model_prob_gfs": kwargs.get("model_prob_gfs"),
            "model_prob_ecmwf": kwargs.get("model_prob_ecmwf"),
            "weather_mode": kwargs.get("weather_mode"),
            "forecast_hours_to_resolution": kwargs.get("forecast_hours_to_resolution"),
            "last_trade_at": contract_dict.get("last_trade_at", ""),
            "reason": kwargs.get("reason", ""),
            "strategy": kwargs.get("strategy", "forecast_weather"),
        }

        if status == "unexpected_resting":
            order_id = str(order_info.get("order_id") or "").strip()
            cancelled = bool(order_id) and self.cancel_and_confirm(order_id)
            return {
                "order_id": order_id or "ERR",
                "status": "unexpected_resting_cancelled" if cancelled else "unexpected_resting_uncertain",
                "qty": 0,
                "filled_qty": 0,
            }

        if status == "executed":
            order_info = self._hydrate_order_details(order_info)
            result = self._apply_entry_fill(
                ticker=ticker,
                right=contract_dict["right"],
                requested_qty=qty,
                order_info=order_info,
                order_type=order_type.capitalize(),
                status=status,
                context=context,
            )
            if float(result.get("filled_qty") or 0.0) > 0:
                print(
                    f"[KalshiBroker] BUY {result['filled_qty']:g} {ticker} ({side.upper()}) "
                    f"@ {float(result.get('price') or 0.0):.4f} | ID={result['order_id']}"
                )
            return result

        logger.error(f"Order failed or rejected: {resp}")
        return {"order_id": "ERR", "status": status}

    def live_position_qty(self, ticker: str) -> float:
        """Absolute contracts held on this ticker, straight from the broker.

        Standing replacement for reduce_only on resting sells, so it reads the
        exchange rather than any local cache. Returns -1.0 if unknown.
        """
        try:
            data = self._request("GET", "/trade-api/v2/portfolio/positions")
            for row in (data.get("market_positions") or []):
                if str(row.get("ticker") or "") == str(ticker):
                    return abs(float(row.get("position_fp") or 0.0))
        except Exception as exc:
            logger.warning("[PositionExit] Position lookup failed for %s: %s", ticker, exc)
            return -1.0
        return 0.0

    def place_sell_order(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        """SRE FIX: Dedicated Sell Order Handler for Limit Exits."""
        if config.SHADOW_EXECUTION:
            side = kwargs.get("side", "yes").lower()
            return self._execute_shadow_sell(
                contract_dict,
                qty=qty,
                limit_price=limit_price,
                side=side,
                strategy=kwargs.get("strategy", "forecast_exit"),
                reason=kwargs.get("reason", ""),
            )

        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        ticker = contract_dict["local_symbol"]
        # In Kalshi, selling a YES is action=sell side=yes (if you held YES)
        # OR buying a NO. The runner seems to use flatten_position for exits.
        # But if the runner calls place_sell_order, we need to know the 'side' held.
        # Assume we held YES for now as it's the primary weather bet.
        side = kwargs.get("side", "yes").lower()
        order_type = kwargs.get("type", "limit").lower()
        right = "C" if side == "yes" else "P"

        body = self._build_event_order_body(
            ticker=ticker,
            right=right,
            qty=qty,
            limit_price=limit_price,
            action="sell",
            reduce_only=True,
        )

        resp = self._request("POST", "/trade-api/v2/portfolio/events/orders", body=body)
        error_code = self._extract_error_code(resp)
        if error_code:
            logger.error(f"Order failed or rejected: {resp}")
            return {"order_id": "ERR", "status": error_code, "error": resp.get("error")}

        order_info, status = self._normalize_order_response(resp, requested_qty=qty)

        if status == "unexpected_resting":
            order_id = str(order_info.get("order_id") or "").strip()
            cancelled = bool(order_id) and self.cancel_and_confirm(order_id)
            return {
                "order_id": order_id or "ERR",
                "status": "unexpected_resting_cancelled" if cancelled else "unexpected_resting_uncertain",
                "filled_qty": 0,
                "remaining_position_qty": float(qty),
            }

        if status == "executed":
            order_info = self._hydrate_order_details(order_info)
            result = self._apply_exit_fill(
                ticker=ticker,
                fallback_right="C" if side == "yes" else "P",
                requested_qty=qty,
                order_info=order_info,
                order_type=order_type.capitalize(),
                default_side=side,
                reason=kwargs.get("reason", ""),
                strategy=kwargs.get("strategy", "forecast_exit"),
            )
            result["status"] = status
            if float(result.get("filled_qty") or 0.0) > 0:
                print(
                    f"[KalshiBroker] SELL {result['filled_qty']:g} {ticker} "
                    f"@ {result['exit_price']:.4f} | ID={result['order_id']}"
                )
            return result

        return {"order_id": order_info.get("order_id", "ERR"), "status": status}

    def flatten_position(self, local_symbol: str, right: str, qty: int, **kwargs) -> dict:
        key = f"{local_symbol}_{right}"
        pos_info = self._open_positions.get(key, {})
        entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)

        if config.SHADOW_EXECUTION:
            quote = self.get_quote(local_symbol)
            bid_key = "yes_bid" if right == "C" else "no_bid"
            bid_price = float(quote.get(bid_key) or 0.0)

            res = self._execute_shadow_sell(
                {"local_symbol": local_symbol, "right": right},
                qty=qty,
                limit_price=bid_price,
                strategy=kwargs.get("strategy", "forecast_exit"),
                reason=kwargs.get("reason", "salvage_exit"),
            )
            # Calculate PnL for the response
            pnl_usd = (res.get("price", 0.0) - entry_price) * res.get("qty", 0)
            current_qty = int(self._open_positions.get(key, {}).get("qty", 0))
            return {
                "order_id": res.get("order_id"),
                "status": res.get("status"),
                "exit_price": res.get("price", 0.0),
                "entry_price": entry_price,
                "pnl_usd": pnl_usd,
                "filled_qty": res.get("qty", 0),
                "remaining_position_qty": max(0, current_qty),
            }

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
            **self._build_event_order_body(
                ticker=local_symbol,
                right=right,
                qty=qty,
                limit_price=bid_price,
                action="sell",
                reduce_only=True,
            )
        }

        pos_info = self._open_positions.get(key, {})
        entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)

        try:
            resp = self._request("POST", "/trade-api/v2/portfolio/events/orders", body=body)
            error_code = self._extract_error_code(resp)
            if error_code:
                logger.error(f"Order failed or rejected: {resp}")
                return {
                    "order_id": "ERR",
                    "status": error_code,
                    "flattened_qty": qty,
                    "exit_price": 0.0,
                    "entry_price": entry_price,
                    "pnl_usd": 0.0,
                }

            order_info, status = self._normalize_order_response(resp, requested_qty=qty)
            order_id = order_info.get("order_id") or resp.get("order_id", "ERR")

            if status == "unexpected_resting":
                cancelled = bool(order_id) and self.cancel_and_confirm(order_id)
                return {
                    "order_id": order_id,
                    "status": "unexpected_resting_cancelled" if cancelled else "unexpected_resting_uncertain",
                    "flattened_qty": 0,
                    "filled_qty": 0,
                    "exit_price": 0.0,
                    "entry_price": entry_price,
                    "pnl_usd": 0.0,
                }

            if status == "executed":
                order_info = self._hydrate_order_details(order_info)
                result = self._apply_exit_fill(
                    ticker=local_symbol,
                    fallback_right=right,
                    requested_qty=qty,
                    order_info=order_info,
                    order_type="Market",
                    default_side=side.upper(),
                    reason=kwargs.get("reason", "salvage_exit"),
                    strategy=kwargs.get("strategy", "forecast_exit"),
                )
                result["status"] = status
                exit_price = result["exit_price"]
                pnl_usd = result["pnl_usd"]
                filled_qty = result["filled_qty"]
                remaining_qty = result["remaining_position_qty"]
            else:
                exit_price = 0.0
                pnl_usd = 0.0
                filled_qty = 0
                remaining_qty = float(qty)

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
            "filled_qty": filled_qty if "filled_qty" in locals() else 0,
            "remaining_position_qty": remaining_qty if "remaining_qty" in locals() else float(qty),
        }

    def get_position(self, local_symbol: str, right: str) -> Optional[dict]:
        key = f"{local_symbol}_{right}"
        return self._open_positions.get(key)

    def get_positions(self) -> list[dict]:
        return list(self._open_positions.values())

    def get_account_balance(self) -> float:
        if config.SHADOW_EXECUTION:
            balance_path = self._paper_balance_path
            try:
                with open(balance_path, "r", encoding="utf-8") as f:
                    balance_data = json.load(f)
                    self._paper_balance = float(balance_data.get("balance", 0.0))
            except Exception:
                pass
            return self._paper_balance

        resp = self._request("GET", "/trade-api/v2/portfolio/balance")
        return float(resp.get("balance_dollars", 0))

    def get_settlements(
        self,
        *,
        min_ts: int | None = None,
        limit: int = 500,
        max_pages: int = 20,
    ) -> list[dict]:
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        results: list[dict] = []
        cursor = ""
        pages = 0
        while pages < max(1, int(max_pages)):
            params: dict[str, object] = {"limit": max(1, min(int(limit), 1000))}
            if cursor:
                params["cursor"] = cursor
            if min_ts is not None:
                params["min_ts"] = int(min_ts)
            payload = self._request("GET", "/trade-api/v2/portfolio/settlements", params=params)
            error_code = self._extract_error_code(payload)
            if error_code:
                raise RuntimeError(f"get_settlements_failed: {error_code}")

            settlements = payload.get("settlements") or []
            if not isinstance(settlements, list):
                settlements = []
            results.extend([row for row in settlements if isinstance(row, dict)])
            cursor = str(payload.get("cursor") or "").strip()
            pages += 1
            if not cursor:
                break
        return results

    def _execute_shadow_buy(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        import math
        ticker = contract_dict["local_symbol"]
        right = str(contract_dict.get("right") or "C").upper()
        side = "YES" if right == "C" else "NO"
        order_type = kwargs.get("type", "limit").lower()

        # 1. Fetch live quote
        try:
            quote = self.get_quote(ticker)
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to fetch quote for {ticker}: {exc}")
            return {"order_id": "ERR", "status": "no_quote"}

        if not quote:
            return {"order_id": "ERR", "status": "no_quote"}

        # 2. Pessimistic fill price & depth.
        # Shadow execution models the production taker route by crossing the ask.
        if side == "YES":
            ask = float(quote.get("yes_ask") or 0.0)
            ask_size = int(float(quote.get("yes_ask_vol") or 0.0))
        else:
            ask = float(quote.get("no_ask") or 0.0)
            ask_size = int(float(quote.get("no_ask_vol") or 0.0))
        fill_price = ask
        available_size = ask_size

        if fill_price <= 0.0:
            logger.warning(f"[ShadowBroker] No sell depth on quote for {ticker}")
            return {"order_id": "ERR", "status": "no_depth"}

        # 3. Clamp quantity to resting size
        fill_qty = min(int(qty), available_size)
        if fill_qty <= 0:
            logger.warning(f"[ShadowBroker] Zero liquidity at price for {ticker}")
            return {"order_id": "ERR", "status": "no_depth"}

        # 4. Fee calculation
        fee_usd = estimate_kalshi_order_fee_usd(fill_qty, fill_price, maker=False)
        cost_usd = (fill_qty * fill_price) + fee_usd

        # Lock check
        balance_path = self._paper_balance_path
        try:
            with open(balance_path, "r", encoding="utf-8") as f:
                balance_data = json.load(f)
                self._paper_balance = float(balance_data.get("balance", 0.0))
        except Exception:
            pass

        if cost_usd > self._paper_balance:
            logger.warning(f"[ShadowBroker] Insufficient virtual funds: cost=${cost_usd:.2f} balance=${self._paper_balance:.2f}")
            return {"order_id": "ERR", "status": "insufficient_funds"}

        # 5. Deduct balance
        self._paper_balance -= cost_usd
        try:
            with open(balance_path, "w", encoding="utf-8") as f:
                json.dump({
                    "balance": self._paper_balance,
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to save paper balance: {exc}")

        # 6. Shadow positions live in memory only. They used to be mirrored into
        # forecast_positions_paper; that table is retired with the paper lanes, and a
        # dry run has no business writing to the same store the live lane reads.
        order_id = f"shadow_{uuid.uuid4().hex[:8]}"

        # 7. Update memory cache
        key = f"{ticker}_{right}"
        existing = self._open_positions.get(key, {})
        prior_qty = float(existing.get("qty") or 0.0)
        prior_entry = float(existing.get("held_side_entry_price") or fill_price)
        blended_entry = fill_price
        if prior_qty > 0:
            blended_entry = ((prior_qty * prior_entry) + (fill_qty * fill_price)) / (prior_qty + fill_qty)
        yes_leg_entry = blended_entry if side == "YES" else (1.0 - blended_entry)

        self._open_positions[key] = {
            "local_symbol": ticker,
            "right": right,
            "qty": prior_qty + fill_qty,
            "entry": yes_leg_entry,
            "entry_price": yes_leg_entry,
            "yes_leg_entry_price": yes_leg_entry,
            "held_side_entry_price": blended_entry,
            "market_exposure_usd": (prior_qty + fill_qty) * blended_entry,
            "side": side,
            "order_id": order_id,
            "entered_at": existing.get("entered_at") or datetime.now(timezone.utc).isoformat(),
        }

        # 8. Log trade in trades table
        try:
            log_trade(
                strategy=kwargs.get("strategy", "forecast_weather"),
                broker="kalshi",
                symbol=ticker,
                action="BUY",
                order_type=order_type.capitalize(),
                qty=fill_qty,
                price=fill_price,
                fee_usd=fee_usd,
                order_id=order_id,
                notes=kwargs.get("reason", "Shadow order entry"),
                contract_side=side,
                forecast_yes_prob=kwargs.get("forecast_yes_prob"),
                model_prob_gfs=kwargs.get("model_prob_gfs"),
                model_prob_ecmwf=kwargs.get("model_prob_ecmwf"),
                weather_mode=kwargs.get("weather_mode"),
                forecast_hours_to_resolution=kwargs.get("forecast_hours_to_resolution"),
            )
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to log shadow trade: {exc}")

        print(f"[ShadowBroker] BUY {fill_qty:g} {ticker} ({side}) @ {fill_price:.4f} | ID={order_id}")

        return {
            "order_id": order_id,
            "status": "executed",
            "price": fill_price,
            "yes_leg_price": fill_price if side == "YES" else (1.0 - fill_price),
            "held_side_price": fill_price,
            "qty": fill_qty,
            "filled_qty": fill_qty,
            "remaining_order_qty": 0,
        }

    def _execute_shadow_sell(self, contract_dict: dict, qty: int, limit_price: float, **kwargs) -> dict:
        ticker = contract_dict["local_symbol"]
        right = str(contract_dict.get("right") or "C").upper()
        side = "YES" if right == "C" else "NO"
        order_type = kwargs.get("type", "limit").lower()
        key = f"{ticker}_{right}"

        existing = self._open_positions.get(key)
        if not existing:
            logger.warning(f"[ShadowBroker] No open shadow position for {ticker}")
            return {"order_id": "ERR", "status": "no_position"}

        current_qty = int(existing["qty"])
        sell_qty = min(int(qty), current_qty)
        if sell_qty <= 0:
            return {"order_id": "ERR", "status": "no_position"}

        # 1. Fetch live quote
        try:
            quote = self.get_quote(ticker)
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to fetch quote for {ticker}: {exc}")
            return {"order_id": "ERR", "status": "no_quote"}

        if not quote:
            return {"order_id": "ERR", "status": "no_quote"}

        # 2. Pessimistic fill price & depth
        if side == "YES":
            bid = float(quote.get("yes_bid") or 0.0)
            bid_size = int(float(quote.get("yes_bid_vol") or 0.0))
            fill_price = bid
            available_size = bid_size
        else:
            bid = float(quote.get("no_bid") or 0.0)
            bid_size = int(float(quote.get("no_bid_vol") or 0.0))
            fill_price = bid
            available_size = bid_size

        if fill_price <= 0.0:
            logger.warning(f"[ShadowBroker] No buy depth on quote for {ticker}")
            return {"order_id": "ERR", "status": "no_depth"}

        # 3. Clamp quantity to resting size
        fill_qty = min(sell_qty, available_size)
        if fill_qty <= 0:
            logger.warning(f"[ShadowBroker] Zero liquidity at bid price for {ticker}")
            return {"order_id": "ERR", "status": "no_depth"}

        # 4. Fee calculation
        fee_usd = estimate_kalshi_order_fee_usd(fill_qty, fill_price)
        proceeds_usd = (fill_qty * fill_price) - fee_usd

        # 5. Add to virtual balance
        balance_path = self._paper_balance_path
        try:
            with open(balance_path, "r", encoding="utf-8") as f:
                balance_data = json.load(f)
                self._paper_balance = float(balance_data.get("balance", 0.0))
        except Exception:
            pass

        self._paper_balance += proceeds_usd
        try:
            with open(balance_path, "w", encoding="utf-8") as f:
                json.dump({
                    "balance": self._paper_balance,
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to save paper balance: {exc}")

        # 6. Shadow positions live in memory only (see the buy path).
        order_id = f"shadow_{uuid.uuid4().hex[:8]}"
        remaining_qty = current_qty - fill_qty

        # 7. Update memory cache
        if remaining_qty > 0:
            self._open_positions[key]["qty"] = remaining_qty
        else:
            self._open_positions.pop(key, None)

        # 8. Log trade in trades table
        try:
            log_trade(
                strategy=kwargs.get("strategy", "forecast_weather"),
                broker="kalshi",
                symbol=ticker,
                action="SELL",
                order_type=order_type.capitalize(),
                qty=fill_qty,
                price=fill_price,
                fee_usd=fee_usd,
                order_id=order_id,
                notes=kwargs.get("reason", "Shadow order exit"),
                contract_side=side,
                forecast_yes_prob=kwargs.get("forecast_yes_prob"),
                model_prob_gfs=kwargs.get("model_prob_gfs"),
                model_prob_ecmwf=kwargs.get("model_prob_ecmwf"),
                weather_mode=kwargs.get("weather_mode"),
                forecast_hours_to_resolution=kwargs.get("forecast_hours_to_resolution"),
            )
        except Exception as exc:
            logger.error(f"[ShadowBroker] Failed to log shadow trade: {exc}")

        print(f"[ShadowBroker] SELL {fill_qty:g} {ticker} ({side}) @ {fill_price:.4f} | ID={order_id}")

        return {
            "order_id": order_id,
            "status": "executed",
            "price": fill_price,
            "qty": fill_qty,
            "filled_qty": fill_qty,
            "remaining_order_qty": 0,
        }

    def disconnect(self) -> None:
        self._connected = False

_kalshi_broker: Optional[KalshiBroker] = None

def get_kalshi_broker() -> KalshiBroker:
    global _kalshi_broker
    if _kalshi_broker is None:
        _kalshi_broker = KalshiBroker()
    return _kalshi_broker
