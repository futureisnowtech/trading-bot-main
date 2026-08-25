"""Point-in-time weather decision evidence capture."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH, RBI_LEARNING_EPOCH
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


def _reason_code(reason: str) -> str:
    token = str(reason or "").strip()
    if not token:
        return ""
    for splitter in (" (", ":"):
        if splitter in token:
            token = token.split(splitter, 1)[0]
            break
    return token.strip()


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
    pricing_trace = getattr(result, "pricing_trace", {}) or {}
    if not isinstance(pricing_trace, dict):
        pricing_trace = {}
    q_gfs = _float(pricing_trace.get("q_gfs", getattr(result, "model_prob_gfs", None)))
    q_ecmwf = _float(pricing_trace.get("q_ecmwf", getattr(result, "model_prob_ecmwf", None)))
    q_aigfs = _float(pricing_trace.get("q_aigfs", pricing_trace.get("q_graphcast")))
    q_hrrr = _float(pricing_trace.get("q_hrrr"))
    q_champion = _float(getattr(result, "q_hat", None))
    provider_at = str(
        pricing_trace.get("provider_at")
        or pricing_trace.get("provider_timestamp")
        or ""
    )
    provider_payload_hash = ""
    traced_summary = pricing_trace.get("provider_summary") or {}
    provider_summary: dict[str, Any] = (
        dict(traced_summary) if isinstance(traced_summary, dict) else {}
    )
    if pricing_trace:
        provider_summary.update({
            "provider_mode": pricing_trace.get(
                "provider_mode", provider_summary.get("provider_mode")
            ),
            "target_date": pricing_trace.get(
                "target_date", provider_summary.get("target_date")
            ),
            "target_hour": pricing_trace.get(
                "target_hour", provider_summary.get("target_hour")
            ),
            "model_path": pricing_trace.get(
                "model_path", provider_summary.get("model_path")
            ),
            "physics_method": pricing_trace.get(
                "physics_method", provider_summary.get("physics_method")
            ),
            "physics_validation_status": pricing_trace.get(
                "physics_validation_status",
                provider_summary.get("physics_validation_status"),
            ),
            "model_probabilities": {
                "gfs": q_gfs,
                "ecmwf": q_ecmwf,
                "aigfs": q_aigfs,
                "hrrr": q_hrrr,
            },
            "blend_weights": {
                "gfs": _float(pricing_trace.get("gfs_weight")),
                "ecmwf": _float(pricing_trace.get("ecmwf_weight")),
                "hrrr": _float(pricing_trace.get("hrrr_weight")),
            },
            "aigfs_lambda": _float(pricing_trace.get("lambda_scaler")),
        })
    provider_summary = {
        key: value for key, value in provider_summary.items() if value is not None
    }
    if provider_summary:
        provider_payload_hash = str(pricing_trace.get("provider_payload_hash") or "")
        if not provider_payload_hash:
            provider_payload_hash = _stable_hash(provider_summary)

    semantics = None
    city_key = ""
    artifact_id = ""
    try:
        from data.kalshi_weather_monitor import resolve_weather_city_key
        from forecast.weather_contracts import resolve_weather_contract
        from intelligence.rbi2 import get_active_model_weights

        semantics = resolve_weather_contract(ticker, contract_name=contract_name, strike=strike)
        city_key = str(resolve_weather_city_key(ticker, contract_name=contract_name) or "")
        artifact_id = str(
            get_active_model_weights(weather_mode, db_path=db_path).get("artifact_id") or ""
        )
    except Exception:
        pass

    q_baseline = None
    if q_gfs is not None and q_ecmwf is not None:
        try:
            from forecast.pricing_engine import log_odds_blend

            q_baseline = log_odds_blend(
                q_gfs,
                q_ecmwf,
                q_hrrr,
                {"gfs": 0.60, "ecmwf": 0.40},
                float(getattr(result, "hours_to_resolution", 0.0) or 0.0),
            )
        except Exception:
            q_baseline = None

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
        "ev_yes": _float(getattr(result, "ev_yes", None)),
        "ev_no": _float(getattr(result, "ev_no", None)),
        "confidence": _float(getattr(result, "confidence", None)),
        "hours_to_resolution": _float(getattr(result, "hours_to_resolution", None)),
        "position_contracts": int(getattr(result, "position_contracts", 0) or 0),
        "provider": provider_summary,
    }
    try:
        from config import (
            KALSHI_DAILY_ASK_YES_BRACKET_MAX,
            KALSHI_DAILY_ASK_YES_BRACKET_MIN,
            KALSHI_EXPENSIVE_YES_MIN_NET_EDGE,
            KALSHI_EXPENSIVE_YES_THRESHOLD,
            KALSHI_MAX_SPREAD_RATIO,
            estimate_kalshi_fee_per_contract,
        )
        from forecast.strategy_engine import MAX_SPREAD_DOLLARS
        from forecast.weather_contracts import is_hourly_weather_contract

        chosen_side = str(getattr(result, "side", "") or "").upper()
        q_hat_yes = _float(q_champion if q_champion is not None else getattr(result, "q_hat", None))
        ask_yes = _float(getattr(result, "ask_yes", None))
        ask_no = _float(getattr(result, "ask_no", None))
        yes_bid = _float(yes_quote.get("bid"))
        no_bid = _float(no_quote.get("bid"))
        chosen_side_prob = None
        chosen_side_price = None
        if q_hat_yes is not None and chosen_side == "YES":
            chosen_side_prob = q_hat_yes
            chosen_side_price = ask_yes
        elif q_hat_yes is not None and chosen_side == "NO":
            chosen_side_prob = max(0.0, min(1.0, 1.0 - q_hat_yes))
            chosen_side_price = ask_no

        avg_price = None
        available_prices = [price for price in (ask_yes, ask_no) if price and price > 0.0]
        if available_prices:
            avg_price = sum(available_prices) / len(available_prices)
        spread_abs = None
        if yes_bid is not None and ask_yes is not None and yes_bid > 0.0 and ask_yes > 0.0:
            spread_abs = max(0.0, ask_yes - yes_bid)
        spread_ratio = None
        if spread_abs is not None and avg_price is not None and avg_price >= 0.05:
            spread_ratio = spread_abs / avg_price

        yes_edge = None
        no_edge = None
        yes_net_edge = None
        no_net_edge = None
        if q_hat_yes is not None:
            if ask_yes is not None and ask_yes > 0.0:
                yes_edge = q_hat_yes - ask_yes
                yes_net_edge = yes_edge - estimate_kalshi_fee_per_contract(ask_yes, rounded=False)
            if ask_no is not None and ask_no > 0.0:
                no_prob = max(0.0, min(1.0, 1.0 - q_hat_yes))
                no_edge = no_prob - ask_no
                no_net_edge = no_edge - estimate_kalshi_fee_per_contract(ask_no, rounded=False)

        hourly_contract = is_hourly_weather_contract(
            ticker,
            contract_name=contract_name,
        )
        spread_cap_dollars = 0.22 if (weather_mode == "TEMP" or hourly_contract) else MAX_SPREAD_DOLLARS
        spread_ratio_cap = (
            0.36
            if (weather_mode == "TEMP" or hourly_contract)
            else float(KALSHI_MAX_SPREAD_RATIO)
        )
        expensive_yes_threshold = float(KALSHI_EXPENSIVE_YES_THRESHOLD)
        expensive_yes_floor = float(KALSHI_EXPENSIVE_YES_MIN_NET_EDGE)
        bracket_min = float(KALSHI_DAILY_ASK_YES_BRACKET_MIN)
        bracket_max = float(KALSHI_DAILY_ASK_YES_BRACKET_MAX)

        features["audit"] = {
            "reason_code": _reason_code(reason),
            "decision": str(decision or ""),
            "chosen_side": chosen_side,
            "q_hat_yes": q_hat_yes,
            "chosen_side_probability": chosen_side_prob,
            "chosen_side_price": chosen_side_price,
            "ask_yes": ask_yes,
            "ask_no": ask_no,
            "yes_bid": yes_bid,
            "no_bid": no_bid,
            "spread_abs": spread_abs,
            "spread_ratio": spread_ratio,
            "yes_edge": yes_edge,
            "no_edge": no_edge,
            "yes_net_edge": yes_net_edge,
            "no_net_edge": no_net_edge,
            "hourly_contract": bool(hourly_contract),
            "price_bracket_min": bracket_min,
            "price_bracket_max": bracket_max,
            "expensive_yes_threshold": expensive_yes_threshold,
            "expensive_yes_min_net_edge": expensive_yes_floor,
            "spread_cap_dollars": spread_cap_dollars,
            "spread_ratio_cap": spread_ratio_cap,
            "gate_flags": {
                "price_bracket_low": bool(
                    weather_mode in {"HIGH", "LOW"}
                    and ask_yes is not None
                    and ask_yes > 0.0
                    and ask_yes < bracket_min
                ),
                "price_bracket_high": bool(
                    weather_mode in {"HIGH", "LOW"}
                    and ask_yes is not None
                    and ask_yes > bracket_max
                ),
                "expensive_yes_headroom_veto": bool(
                    chosen_side == "YES"
                    and ask_yes is not None
                    and ask_yes >= expensive_yes_threshold
                    and yes_edge is not None
                    and yes_edge > 0.0
                    and yes_net_edge is not None
                    and yes_net_edge < expensive_yes_floor
                ),
                "spread_too_wide_dollars": bool(
                    spread_abs is not None and spread_abs > spread_cap_dollars
                ),
                "spread_too_wide_ratio": bool(
                    spread_ratio is not None and spread_ratio > spread_ratio_cap
                ),
            },
        }
    except Exception:
        pass
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
        config_hash, artifact_id, RBI_LEARNING_EPOCH,
        json.dumps(features, sort_keys=True), now,
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
                config_hash, artifact_id, learning_epoch, features_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(evaluation_key) DO UPDATE SET
                 decision=excluded.decision,
                 decision_reason=excluded.decision_reason,
                 chosen_side=excluded.chosen_side,
                 q_gfs=COALESCE(excluded.q_gfs, intelligence_predictions.q_gfs),
                 q_ecmwf=COALESCE(excluded.q_ecmwf, intelligence_predictions.q_ecmwf),
                 q_hrrr=COALESCE(excluded.q_hrrr, intelligence_predictions.q_hrrr),
                 q_baseline=COALESCE(excluded.q_baseline, intelligence_predictions.q_baseline),
                 q_champion=COALESCE(excluded.q_champion, intelligence_predictions.q_champion),
                 learning_epoch=excluded.learning_epoch,
                 features_json=excluded.features_json""",
            values,
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM intelligence_predictions WHERE evaluation_key=?",
            (evaluation_key,),
        ).fetchone()
        return int(row["id"] if row else 0)
