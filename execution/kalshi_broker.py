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
from datetime import datetime, timezone
from typing import Optional, List, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization

# Add root to path for logging_db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DB_PATH,
    KALSHI_API_KEY_ID,
    REPO_ROOT,
    SHADOW_EXECUTION,
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
        
    def connect(self) -> bool:
        """Verify credentials and load private key for signing."""
        private_key_path = resolve_runtime_path(
            KALSHI_PRIVATE_KEY_PATH,
            "/run/secrets/kalshi_private_key.pem",
            os.path.join(REPO_ROOT, "kalshi_private_key.pem"),
        )

        if not KALSHI_API_KEY_ID or not private_key_path:
            log_event("ERROR", "KalshiBroker", "Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH in .env")
            return False

        if not os.path.exists(private_key_path):
            log_event(
                "ERROR",
                "KalshiBroker",
                f"Kalshi private key not found at resolved path: {private_key_path}",
            )
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

    def sync_positions(self) -> None:
        """Refresh local position cache from broker reality."""
        self._sync_positions()

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
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
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

    def _extract_average_fill_price(self, order_info: dict) -> float:
        for key in ("average_price", "average_fill_price", "price"):
            raw = order_info.get(key)
            if raw in (None, ""):
                continue
            try:
                if isinstance(raw, str) and "." in raw:
                    return float(raw)
                return float(raw) / 100.0
            except (TypeError, ValueError):
                continue
        fill_count = self._extract_fill_count(order_info)
        if fill_count > 0:
            for key in ("taker_fill_cost_dollars", "maker_fill_cost_dollars"):
                raw = order_info.get(key)
                if raw in (None, ""):
                    continue
                try:
                    total_cost = float(raw)
                    if total_cost > 0:
                        return total_cost / fill_count
                except (TypeError, ValueError):
                    continue
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
        fill_price = self._extract_average_fill_price(order_info)
        if fill_price > 0 and qty > 0:
            return estimate_kalshi_order_fee_usd(qty, fill_price)
        return estimate_kalshi_order_fee_usd(qty, 0.50)

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
        pnl_usd = (exit_price - entry_price) * fill_qty if exit_price > 0 else 0.0
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
        existing = self._open_positions.get(key, {})
        prior_qty = float(existing.get("qty") or 0.0)
        prior_entry = float(existing.get("entry_price") or existing.get("entry") or 0.0)
        blended_entry = fill_price
        if prior_qty > 0 and fill_price > 0:
            blended_entry = ((prior_qty * prior_entry) + (fill_qty * fill_price)) / (prior_qty + fill_qty)
        total_qty = prior_qty + fill_qty
        remaining_order_qty = self._extract_remaining_count(order_info, requested_qty)

        self._open_positions[key] = {
            "qty": total_qty,
            "side": side,
            "local_symbol": ticker,
            "right": right,
            "entry": blended_entry,
            "entry_price": blended_entry,
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
            "qty": int(round(fill_qty)),
            "filled_qty": fill_qty,
            "remaining_order_qty": remaining_order_qty,
            "position_qty_after_fill": total_qty,
        }

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
                total_traded = float(p.get("total_traded_dollars") or 0.0)
                entry_context = self._load_latest_entry_context(ticker, side)
                entry_price = float(entry_context.get("entry_price") or 0.0)
                if entry_price <= 0.0:
                    entry_price = (total_traded / abs_qty) if abs_qty > 0 and total_traded > 0 else 0.0

                key = f"{ticker}_{right}"
                self._open_positions[key] = {
                    "local_symbol": ticker,
                    "right": right,
                    "qty": abs_qty,
                    "entry": entry_price,
                    "entry_price": entry_price,
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
        except Exception as e:
            log_event("WARN", "KalshiBroker", f"Position sync error: {e}")

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
                ranked_families: dict[tuple[str, str], tuple[int, str]] = {}
                for series_id, meta in weather_series_meta.items():
                    title_lower = str(meta.get("title") or "").lower()
                    city_key = resolve_weather_city_key(series_id, contract_name=str(meta.get("title") or ""))
                    lane = weather_mode_for_ticker(series_id)
                    if lane is None and "hourly directional" in title_lower and "temperature" in title_lower:
                        lane = "TEMP"
                    if city_key is None or lane not in {"HIGH", "LOW", "RAIN", "TEMP"}:
                        continue

                    score = 0
                    if series_id.startswith("KX"):
                        score += 100
                    if lane == "TEMP" and series_id.startswith("KXTEMP"):
                        score += 25
                    if lane == "HIGH" and series_id.startswith("KXHIGH"):
                        score += 20
                    if lane == "LOW" and series_id.startswith("KXLOWT"):
                        score += 20
                    if lane == "RAIN" and series_id.startswith("KXRAIN"):
                        score += 20
                    if "hourly directional" in title_lower:
                        score += 10

                    family_key = (city_key, lane)
                    current = ranked_families.get(family_key)
                    if current is None or score > current[0]:
                        ranked_families[family_key] = (score, series_id)

                discovery_series = sorted(
                    {series_id for _score, series_id in ranked_families.values()}
                )
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
        if not self.is_connected():
            raise RuntimeError("[KalshiBroker] Not connected to Kalshi")

        ticker = contract_dict["local_symbol"]
        side = "yes" if contract_dict["right"] == "C" else "no"
        order_type = kwargs.get("type", "limit").lower()
        limit_cents = self._normalize_price_cents(limit_price)
        
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": int(qty),
            "client_order_id": str(uuid.uuid4()),
        }

        if order_type == "market":
            # Kalshi now only supports limit-style writes. Emulate market intent
            # with a marketable limit plus a hard max-cost cap.
            aggressive_cents = _KALSHI_MARKETABLE_ENTRY_CENTS
            buy_cap_cents = min(_KALSHI_MAX_PRICE_CENTS, limit_cents + 1)
            body["buy_max_cost"] = int(qty) * buy_cap_cents
            body["time_in_force"] = "fill_or_kill"
            if side == "yes":
                body["yes_price"] = aggressive_cents
            else:
                body["no_price"] = aggressive_cents
        else:
            if side == "yes":
                body["yes_price"] = limit_cents
            else:
                body["no_price"] = limit_cents
        
        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        error_code = self._extract_error_code(resp)
        if error_code:
            logger.error(f"Order failed or rejected: {resp}")
            return {"order_id": "ERR", "status": error_code, "error": resp.get("error")}

        order_info = resp.get("order", {})
        status = order_info.get("status")
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

        if status in ["executed", "resting", "pending"]:
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
            elif status in ["resting", "pending"]:
                logger.info(
                    "Order %s with no immediate fill yet. ID=%s",
                    status,
                    order_info.get("order_id"),
                )
            return result

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
            "client_order_id": str(uuid.uuid4()),
        }

        limit_cents = self._normalize_price_cents(limit_price)
        if order_type == "market":
            if side == "yes":
                body["yes_price"] = _KALSHI_MARKETABLE_EXIT_CENTS
            else:
                body["no_price"] = _KALSHI_MARKETABLE_EXIT_CENTS
            body["time_in_force"] = "ioc"
        else:
            if side == "yes":
                body["yes_price"] = limit_cents
            else:
                body["no_price"] = limit_cents

        resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
        error_code = self._extract_error_code(resp)
        if error_code:
            logger.error(f"Order failed or rejected: {resp}")
            return {"order_id": "ERR", "status": error_code, "error": resp.get("error")}

        order_info = resp.get("order", {})
        status = order_info.get("status")

        if status in ["executed", "resting", "pending"]:
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
            "client_order_id": str(uuid.uuid4()),
            "time_in_force": "ioc",
        }
        if side == "yes":
            body["yes_price"] = _KALSHI_MARKETABLE_EXIT_CENTS
        else:
            body["no_price"] = _KALSHI_MARKETABLE_EXIT_CENTS
        
        pos_info = self._open_positions.get(key, {})
        entry_price = float(pos_info.get("entry_price") or pos_info.get("entry") or 0.50)

        try:
            resp = self._request("POST", "/trade-api/v2/portfolio/orders", body=body)
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

            order_info = resp.get("order", {})
            order_id = order_info.get("order_id") or resp.get("order_id", "ERR")
            status = order_info.get("status")

            if status in ["executed", "resting", "pending"]:
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
