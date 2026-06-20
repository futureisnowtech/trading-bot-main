"""Broker-first realized weather performance truth, cached for operator surfaces."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from config import RUNTIME_ROOT, TRADE_SESSION_START
from execution.kalshi_broker import get_kalshi_broker
from forecast.weather_contracts import weather_trade_bucket

_CACHE_FILE = Path(RUNTIME_ROOT) / "weather_settlement_truth.json"
_CACHE_TTL_SECONDS = 300


def _parse_session_start(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.combine(date.fromisoformat(text), datetime.min.time())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_cache_fresh(path: Path, *, max_age_seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except OSError:
        return False
    return age_seconds <= max(1, int(max_age_seconds))


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_cache(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _empty_truth(*, source: str, since_iso: str) -> dict[str, Any]:
    return {
        "source": source,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "since": since_iso,
        "settlement_rows": 0,
        "total": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl_usd": 0.0,
        "total_won_usd": 0.0,
        "total_lost_usd": 0.0,
        "by_bucket": {},
        "curve_points": [],
    }


def _append_bucket_stats(target: dict[str, Any], *, pnl_usd: float) -> None:
    target["total"] += 1
    target["total_pnl_usd"] = round(target["total_pnl_usd"] + pnl_usd, 4)
    if pnl_usd > 0:
        target["wins"] += 1
        target["total_won_usd"] = round(target["total_won_usd"] + pnl_usd, 4)
    elif pnl_usd < 0:
        target["losses"] += 1
        target["total_lost_usd"] = round(target["total_lost_usd"] + pnl_usd, 4)
    total = max(0, int(target["total"]))
    target["win_rate"] = round((target["wins"] / total), 4) if total else 0.0


def build_weather_settlement_truth(
    settlements: list[dict[str, Any]],
    *,
    since_iso: str = TRADE_SESSION_START,
) -> dict[str, Any]:
    truth = _empty_truth(source="broker_settlements", since_iso=since_iso)
    buckets: dict[str, dict[str, Any]] = {}
    curve_points: list[dict[str, Any]] = []

    for row in settlements:
        ticker = str(row.get("ticker") or "")
        bucket = weather_trade_bucket(
            ticker,
            contract_name=str(row.get("event_ticker") or ticker),
        )
        if bucket == "Other Weather":
            continue

        yes_count = _safe_float(row.get("yes_count_fp") or row.get("yes_count"))
        no_count = _safe_float(row.get("no_count_fp") or row.get("no_count"))
        yes_cost = _safe_float(row.get("yes_total_cost_dollars"))
        no_cost = _safe_float(row.get("no_total_cost_dollars"))
        fee_cost = _safe_float(row.get("fee_cost"))
        market_result = str(row.get("market_result") or "").lower()

        payout = yes_count if market_result == "yes" else no_count if market_result == "no" else 0.0
        pnl_usd = round(payout - yes_cost - no_cost - fee_cost, 4)
        if abs(pnl_usd) < 1e-9:
            continue

        _append_bucket_stats(truth, pnl_usd=pnl_usd)
        bucket_stats = buckets.setdefault(
            bucket,
            {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl_usd": 0.0,
                "total_won_usd": 0.0,
                "total_lost_usd": 0.0,
            },
        )
        _append_bucket_stats(bucket_stats, pnl_usd=pnl_usd)
        truth["settlement_rows"] += 1
        curve_points.append(
            {
                "ts": str(row.get("settled_time") or row.get("created_time") or ""),
                "ticker": ticker,
                "bucket": bucket,
                "pnl_usd": pnl_usd,
            }
        )

    truth["by_bucket"] = buckets
    truth["curve_points"] = sorted(curve_points, key=lambda item: str(item.get("ts") or ""))
    return truth


def refresh_weather_settlement_truth(
    *,
    broker=None,
    since_iso: str = TRADE_SESSION_START,
) -> dict[str, Any]:
    broker = broker or get_kalshi_broker()
    if not broker.is_connected():
        with contextlib.redirect_stdout(io.StringIO()):
            if not broker.connect(sync_positions=False, quiet=True):
                raise RuntimeError("broker_connect_failed")

    min_ts = int(_parse_session_start(since_iso).timestamp())
    settlements = broker.get_settlements(min_ts=min_ts)
    payload = build_weather_settlement_truth(settlements, since_iso=since_iso)
    payload["as_of"] = datetime.now(timezone.utc).isoformat()
    payload["source"] = "broker_settlements"
    return _write_cache(_CACHE_FILE, payload)


def load_weather_settlement_truth(
    *,
    max_age_seconds: int = _CACHE_TTL_SECONDS,
    refresh: bool = True,
    since_iso: str = TRADE_SESSION_START,
) -> dict[str, Any]:
    if _is_cache_fresh(_CACHE_FILE, max_age_seconds=max_age_seconds):
        cached = _load_cache(_CACHE_FILE)
        if cached:
            return cached

    if refresh:
        try:
            return refresh_weather_settlement_truth(since_iso=since_iso)
        except Exception:
            cached = _load_cache(_CACHE_FILE)
            if cached:
                cached["source"] = str(cached.get("source") or "broker_settlements_stale")
                cached["stale"] = True
                return cached

    cached = _load_cache(_CACHE_FILE)
    if cached:
        cached["stale"] = True
        return cached
    return _empty_truth(source="unavailable", since_iso=since_iso)
