"""Point-in-time weather decision evidence capture."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH
from intelligence.schema import connect, init_intelligence_db


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _event_key(ticker: str) -> str:
    parts = str(ticker or "").split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else str(ticker or "")


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def record_prediction(
    *,
    scan_id: str,
    candidate: dict,
    decision: str,
    reason: str = "",
    db_path: str = DB_PATH,
) -> int:
    """Upsert one market-level prediction for a scan without blocking execution."""
    init_intelligence_db(db_path)
    result = candidate.get("result")
    contract = candidate.get("contract") or {}
    snapshot = candidate.get("snapshot")
    ticker = str(contract.get("local_symbol") or getattr(snapshot, "ticker", "") or "")
    if not ticker or result is None:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    yes_quote = getattr(snapshot, "yes_quote", {}) or {}
    no_quote = getattr(snapshot, "no_quote", {}) or {}
    contract_name = str(contract.get("contract_name") or getattr(snapshot, "contract_name", "") or ticker)
    strike = _float(contract.get("strike"))
    weather_mode = str(getattr(result, "weather_mode", "") or "")
    q_gfs = _float(getattr(result, "model_prob_gfs", None))
    q_ecmwf = _float(getattr(result, "model_prob_ecmwf", None))
    q_hrrr = None
    q_champion = _float(getattr(result, "q_hat", None))
    provider_at = ""
    provider_payload_hash = ""
    provider_summary: dict[str, Any] = {}

    semantics = None
    city_key = ""
    artifact_id = ""
    try:
        from data.kalshi_weather_monitor import get_contract_weather_data, resolve_weather_city_key
        from forecast.pricing_engine import calculate_pricing
        from forecast.weather_contracts import resolve_weather_contract
        from intelligence.rbi2 import get_champion_artifact

        semantics = resolve_weather_contract(ticker, contract_name=contract_name, strike=strike)
        city_key = str(resolve_weather_city_key(ticker, contract_name=contract_name) or "")
        weather = get_contract_weather_data(
            ticker,
            contract_name=contract_name,
            strike=strike,
            resolution_at=str(contract.get("resolution_at") or ""),
            last_trade_at=str(contract.get("last_trade_at") or ""),
        )
        if weather and semantics is not None and not semantics.ambiguous:
            pricing = calculate_pricing(
                ticker,
                weather,
                float(getattr(result, "hours_to_resolution", 0.0) or 0.0),
                contract_name=contract_name,
                strike=strike,
                db_path=db_path,
            )
            q_gfs = _float(pricing.get("q_gfs"))
            q_ecmwf = _float(pricing.get("q_ecmwf"))
            q_hrrr = _float(pricing.get("q_hrrr"))
            q_champion = _float(pricing.get("q_hat"))
            provider_at = str(weather.get("timestamp") or "")
            provider_summary = {
                "provider_mode": weather.get("provider_mode"),
                "series": weather.get("series"),
                "target_date": weather.get("target_local_date"),
                "target_hour": weather.get("target_local_hour"),
                "gfs_members": len(weather.get("members_high") or weather.get("members_low") or weather.get("members_temp") or []),
                "ecmwf_members": len((weather.get("ecmwf") or {}).get("members_high") or (weather.get("ecmwf") or {}).get("members_low") or (weather.get("ecmwf") or {}).get("members_temp") or []),
            }
            provider_payload_hash = _stable_hash(provider_summary)
        artifact_id = str((get_champion_artifact(db_path=db_path) or {}).get("artifact_id") or "")
    except Exception:
        pass

    q_baseline = None
    if q_gfs is not None and q_ecmwf is not None:
        q_baseline = 0.60 * q_gfs + 0.40 * q_ecmwf

    semantics_payload = {
        "comparator": getattr(semantics, "comparator", None),
        "lower_bound": getattr(semantics, "lower_bound", None),
        "upper_bound": getattr(semantics, "upper_bound", None),
        "threshold": getattr(semantics, "threshold", None),
        "source": getattr(semantics, "source", None),
        "contract_name": contract_name,
    }
    try:
        from runtime.build_info import get_build_info
        code_sha = str(get_build_info().get("sha") or "")
    except Exception:
        code_sha = ""
    try:
        from config import KALSHI_KELLY_FRACTION, KALSHI_MAX_USD_PER_POSITION, PHYSICS_DELTA_ENABLED
        from forecast.strategy_engine import EV_THRESHOLD
        config_hash = _stable_hash(
            {
                "ev_threshold": EV_THRESHOLD,
                "kelly_fraction": KALSHI_KELLY_FRACTION,
                "position_cap": KALSHI_MAX_USD_PER_POSITION,
                "physics_delta": PHYSICS_DELTA_ENABLED,
            }
        )
    except Exception:
        config_hash = ""

    evaluation_key = f"{scan_id}:{ticker}"
    features = {
        "top_factors": list(getattr(result, "top_factors", []) or []),
        "ev": _float(getattr(result, "ev", None)),
        "confidence": _float(getattr(result, "confidence", None)),
        "hours_to_resolution": _float(getattr(result, "hours_to_resolution", None)),
        "position_contracts": int(getattr(result, "position_contracts", 0) or 0),
        "provider": provider_summary,
    }
    values = (
        evaluation_key, scan_id, now, ticker, _event_key(ticker), contract_name,
        weather_mode, city_key, strike, semantics_payload["comparator"],
        semantics_payload["lower_bound"], semantics_payload["upper_bound"],
        semantics_payload["threshold"], str(contract.get("last_trade_at") or ""),
        str(getattr(result, "side", "") or ""), decision, reason,
        q_gfs, q_ecmwf, q_hrrr, q_baseline, q_champion,
        _float(yes_quote.get("bid")), _float(yes_quote.get("ask")),
        _float(no_quote.get("bid")), _float(no_quote.get("ask")),
        str(yes_quote.get("ts") or no_quote.get("ts") or ""), provider_at,
        provider_payload_hash, _stable_hash(semantics_payload), code_sha,
        config_hash, artifact_id, json.dumps(features, sort_keys=True), now,
    )
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO intelligence_predictions
               (evaluation_key, scan_id, evaluated_at, ticker, event_key,
                contract_name, weather_mode, city_key, strike, comparator,
                lower_bound, upper_bound, threshold, market_close_at,
                chosen_side, decision, decision_reason, q_gfs, q_ecmwf, q_hrrr,
                q_baseline, q_champion, yes_bid, yes_ask, no_bid, no_ask,
                quote_at, provider_at, provider_payload_hash, rule_hash, code_sha,
                config_hash, artifact_id, features_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(evaluation_key) DO UPDATE SET
                 decision=excluded.decision,
                 decision_reason=excluded.decision_reason,
                 chosen_side=excluded.chosen_side,
                 q_gfs=COALESCE(excluded.q_gfs, intelligence_predictions.q_gfs),
                 q_ecmwf=COALESCE(excluded.q_ecmwf, intelligence_predictions.q_ecmwf),
                 q_hrrr=COALESCE(excluded.q_hrrr, intelligence_predictions.q_hrrr),
                 q_baseline=COALESCE(excluded.q_baseline, intelligence_predictions.q_baseline),
                 q_champion=COALESCE(excluded.q_champion, intelligence_predictions.q_champion),
                 features_json=excluded.features_json""",
            values,
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM intelligence_predictions WHERE evaluation_key=?",
            (evaluation_key,),
        ).fetchone()
        return int(row["id"] if row else 0)
