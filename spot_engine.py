"""
spot_engine.py — Coinbase spot execution engine for the supported spot universe.

Manages one long-only spot position per symbol with restart-safe persistence.
Live mode only. Paper mode excised v18.17.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional

from config import (
    SPOT_ALLOWED_REGIMES,
    SPOT_EOD_FLATTEN_ENABLED,
    SPOT_EOD_CLOSE_TIME,
    SPOT_LANE_ACTIVE,
    SPOT_MAKER_POLL_SECONDS,
    SPOT_MIN_ORDER_USD,
    SPOT_MIN_PATH_EFFICIENCY,
    SPOT_SCALP_SYMBOL_CONFIG,
    SPOT_SYMBOLS,
    SPOT_THESIS_MIN_HOLD_MINS,
    SPOT_THESIS_MIN_SCORE,
    SPOT_TARGET_R_BY_REGIME,
    SPOT_TOTAL_ALLOC_CAP_PCT,
    SPOT_TRAIL_ARM_R_BY_REGIME,
)
from runtime.spot_execution_policy import (
    limit_buy_price,
    limit_sell_price,
    maker_poll_count,
)
from runtime.spot_momentum import build_spot_state
from runtime.spot_regime import score_floor_for_regime
from runtime.spot_strategy import (
    edge_policy_for_symbol,
    exit_profile_for_symbol,
    get_spot_strategy,
    score_floor_for_symbol,
    setup_policy_for_symbol,
    spot_quality_block_reason,
    target_r_for_symbol,
    trail_arm_r_for_symbol,
)
from runtime.spot_position_truth import get_spot_position_truth, get_spot_symbol_truth

from functools import wraps

logger = logging.getLogger(__name__)

import system_state

try:
    from execution.coinbase_spot_broker import CoinbaseSpotBroker, get_spot_broker
    _BROKER_OK = True
except Exception:
    _BROKER_OK = False
    CoinbaseSpotBroker = None  # type: ignore[assignment]


def _load_config() -> None:
    """Backward-compatible config refresh for proof tests and runtime callers."""
    import config as _cfg

    globals()["SPOT_EOD_FLATTEN_ENABLED"] = getattr(
        _cfg, "SPOT_EOD_FLATTEN_ENABLED", SPOT_EOD_FLATTEN_ENABLED
    )
    globals()["SPOT_EOD_CLOSE_TIME"] = getattr(
        _cfg, "SPOT_EOD_CLOSE_TIME", SPOT_EOD_CLOSE_TIME
    )
    globals()["SPOT_LANE_ACTIVE"] = getattr(_cfg, "SPOT_LANE_ACTIVE", SPOT_LANE_ACTIVE)
    globals()["SPOT_MAKER_POLL_SECONDS"] = getattr(
        _cfg, "SPOT_MAKER_POLL_SECONDS", SPOT_MAKER_POLL_SECONDS
    )
    globals()["SPOT_MIN_ORDER_USD"] = getattr(
        _cfg, "SPOT_MIN_ORDER_USD", SPOT_MIN_ORDER_USD
    )
    globals()["SPOT_SCALP_SYMBOL_CONFIG"] = getattr(
        _cfg, "SPOT_SCALP_SYMBOL_CONFIG", SPOT_SCALP_SYMBOL_CONFIG
    )
    globals()["SPOT_SYMBOLS"] = getattr(_cfg, "SPOT_SYMBOLS", SPOT_SYMBOLS)
    globals()["SPOT_ALLOWED_REGIMES"] = getattr(
        _cfg, "SPOT_ALLOWED_REGIMES", SPOT_ALLOWED_REGIMES
    )
    globals()["SPOT_MIN_PATH_EFFICIENCY"] = getattr(
        _cfg, "SPOT_MIN_PATH_EFFICIENCY", SPOT_MIN_PATH_EFFICIENCY
    )
    globals()["SPOT_THESIS_MIN_HOLD_MINS"] = getattr(
        _cfg, "SPOT_THESIS_MIN_HOLD_MINS", SPOT_THESIS_MIN_HOLD_MINS
    )
    globals()["SPOT_THESIS_MIN_SCORE"] = getattr(
        _cfg, "SPOT_THESIS_MIN_SCORE", SPOT_THESIS_MIN_SCORE
    )
    globals()["SPOT_TARGET_R_BY_REGIME"] = getattr(
        _cfg, "SPOT_TARGET_R_BY_REGIME", SPOT_TARGET_R_BY_REGIME
    )
    globals()["SPOT_TOTAL_ALLOC_CAP_PCT"] = getattr(
        _cfg, "SPOT_TOTAL_ALLOC_CAP_PCT", SPOT_TOTAL_ALLOC_CAP_PCT
    )
    globals()["SPOT_TRAIL_ARM_R_BY_REGIME"] = getattr(
        _cfg, "SPOT_TRAIL_ARM_R_BY_REGIME", SPOT_TRAIL_ARM_R_BY_REGIME
    )


def _get_db_path() -> str:
    try:
        from config import DB_PATH

        return DB_PATH
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "logs",
            "trades.db",
        )


def _get_broker() -> Optional["CoinbaseSpotBroker"]:
    if not _BROKER_OK:
        system_state.state.update_exchange(connected=False)
        return None
    try:
        broker = get_spot_broker()
        if not broker.is_connected():
            broker.connect()

        # Update system state
        system_state.state.update_exchange(connected=True)
        try:
            bal = broker.get_spot_balance()
            if bal:
                system_state.state.update_exchange(
                    buying_power=float(bal.get("usd_available") or 0.0)
                )
        except Exception:
            pass

        system_state.state.update_prometheus()

        return broker
    except Exception as e:
        logger.error(f"[spot_engine] broker init error: {e}")
        system_state.state.update_exchange(connected=False)
        return None


def _load_spot_positions_from_db() -> List[Dict]:
    try:
        from logging_db.trade_logger import load_open_positions

        # Hardcoded paper=False (0)
        rows = load_open_positions()
        return [r for r in rows if str(r.get("strategy", "")).startswith("spot_")]
    except Exception as e:
        logger.debug(f"[spot_engine] load_spot_positions error: {e}")
        return []


def _current_spot_deployed_usd() -> float:
    # Live: broker cash balance is the binding constraint in v10_runner (usd_available*0.95).
    # Returning 0 here avoids stale DB positions inflating the deployed total.
    return 0.0


def _symbol_cfg(symbol: str) -> dict:
    clean = _clean_symbol(symbol)
    return dict(SPOT_SCALP_SYMBOL_CONFIG.get(clean, {}))


def _clean_symbol(symbol: str) -> str:
    clean = str(symbol or "").upper().replace("/", "-")
    for suffix in ("-USDC", "-USDT", "-USD", "USDC", "USDT", "USD"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean.replace("-", "")


def _position_strategy(symbol: str) -> str:
    return f"spot_{_clean_symbol(symbol).lower()}"


def get_spot_positions() -> List[Dict]:
    return _load_spot_positions_from_db()


def _sync_position_high(
    symbol: str, strategy: str, high_price: float
) -> None:
    try:
        con = sqlite3.connect(_get_db_path(), timeout=5)
        con.execute(
            "UPDATE open_positions SET high_since_entry=? WHERE symbol=? AND strategy=? AND paper=0""",
            (high_price, _clean_symbol(symbol), strategy),
        )
        con.commit()
        con.close()
    except Exception as e:
        logger.debug(f"[spot_engine] high sync error {symbol}: {e}")


def _sync_position_exit_reason(
    symbol: str, strategy: str, exit_reason: str
) -> None:
    try:
        con = sqlite3.connect(_get_db_path(), timeout=5)
        con.execute(
            "UPDATE open_positions SET exit_reason=? WHERE symbol=? AND strategy=? AND paper=0",
            (exit_reason, _clean_symbol(symbol), strategy),
        )
        con.commit()
        con.close()
    except Exception as e:
        logger.debug(f"[spot_engine] exit_reason sync error {symbol}: {e}")


def _persist_position_from_row(row: dict, *, qty: float | None = None) -> None:
    from logging_db.trade_logger import persist_position

    persist_position(
        symbol=str(row.get("symbol") or ""),
        strategy=str(
            row.get("strategy") or _position_strategy(row.get("symbol") or "")
        ),
        qty=float(qty if qty is not None else row.get("qty") or 0.0),
        entry=float(row.get("entry") or 0.0),
        stop=float(row.get("stop") or 0.0),
        target=float(row.get("target") or 0.0),
        high_since_entry=float(row.get("high_since_entry") or row.get("entry") or 0.0),
        ts_entry=str(row.get("ts_entry") or datetime.datetime.utcnow().isoformat()),
        direction=str(row.get("direction") or "LONG"),
        entry_reason=str(row.get("entry_reason") or ""),
        low_since_entry=float(row.get("low_since_entry") or row.get("entry") or 0.0),
        atr_at_entry=float(row.get("atr_at_entry") or 0.0),
        composite_score=float(row.get("composite_score") or 0.0),
        trailing_active=bool(row.get("trailing_active")),
        trailing_stop_price=float(row.get("trailing_stop_price") or 0.0),
        scale_33_done=bool(row.get("scale_33_done")),
        scale_66_done=bool(row.get("scale_66_done")),
        leverage=int(row.get("leverage") or 1),
        spot_regime=str(row.get("spot_regime") or ""),
        setup_family=str(row.get("setup_family") or ""),
        setup_score=float(row.get("setup_score") or 0.0),
        setup_preference=str(row.get("setup_preference") or ""),
        tf_5m_state=str(row.get("tf_5m_state") or ""),
        tf_30m_state=str(row.get("tf_30m_state") or ""),
        tf_4h_state=str(row.get("tf_4h_state") or ""),
        tf_1d_state=str(row.get("tf_1d_state") or ""),
        structural_confirms=str(row.get("structural_confirms") or ""),
        execution_route=str(row.get("execution_route") or ""),
        cooldown_until=str(row.get("cooldown_until") or ""),
        microstructure_veto=str(row.get("microstructure_veto") or ""),
        stop_model_version=str(row.get("stop_model_version") or ""),
        target_model_version=str(row.get("target_model_version") or ""),
        target_r=float(row.get("target_r") or 0.0),
        trail_arm_r=float(row.get("trail_arm_r") or 0.0),
        risk_dollars=float(row.get("risk_dollars") or 0.0),
        entry_fee_usd=float(row.get("entry_fee_usd") or 0.0),
        exit_reason=str(row.get("exit_reason") or ""),
        entry_trade_id=int(row.get("entry_trade_id") or 0),
        entry_order_id=str(row.get("entry_order_id") or ""),
        entry_feature_snapshot_id=int(row.get("entry_feature_snapshot_id") or 0),
        tv_profile_name=str(row.get("tv_profile_name") or ""),
        tv_signal_bias=str(row.get("tv_signal_bias") or ""),
        tv_signal_ts=str(row.get("tv_signal_ts") or ""),
        tv_signal_age_sec=float(row.get("tv_signal_age_sec") or 0.0),
        tv_indicator_name=str(row.get("tv_indicator_name") or ""),
        tv_signal_strength=str(row.get("tv_signal_strength") or ""),
        candidate_id=int(row.get("candidate_id") or 0),
        candidate_scan_id=str(row.get("candidate_scan_id") or ""),
        raw_scanner_symbol=str(row.get("raw_scanner_symbol") or ""),
        base_asset=str(row.get("base_asset") or ""),
        tv_veto_state=str(row.get("tv_veto_state") or ""),
    )


def _state_payload(spot_state: dict | None) -> dict:
    if not spot_state:
        return {
            "spot_regime": "",
            "setup_family": "",
            "setup_score": 0.0,
            "setup_preference": "",
            "tf_5m_state": "",
            "tf_30m_state": "",
            "tf_4h_state": "",
            "tf_1d_state": "",
            "structural_confirms": "",
        }
    return {
        "spot_regime": spot_state.get("regime", ""),
        "setup_family": spot_state.get("setup_family", ""),
        "setup_score": float(spot_state.get("setup_score") or 0.0),
        "setup_preference": str(
            setup_policy_for_symbol(
                spot_state.get("symbol", ""),
                spot_state.get("setup_family", ""),
                float(spot_state.get("setup_score") or 0.0),
            ).get("preference")
            or ""
        ),
        "tf_5m_state": spot_state.get("tf_5m_state", ""),
        "tf_30m_state": spot_state.get("tf_30m_state", ""),
        "tf_4h_state": spot_state.get("tf_4h_state", ""),
        "tf_1d_state": spot_state.get("tf_1d_state", ""),
        "structural_confirms": spot_state.get("structural_confirms", ""),
    }


def _tv_payload(tv_context: dict | None) -> dict:
    if not isinstance(tv_context, dict):
        return {
            "tv_profile_name": "",
            "tv_signal_bias": "",
            "tv_signal_ts": "",
            "tv_signal_age_sec": 0.0,
            "tv_indicator_name": "",
            "tv_signal_strength": "",
            "tv_signal_active": False,
        }
    bias = str(tv_context.get("htf_bias") or tv_context.get("direction") or "").upper()
    profile = str(tv_context.get("profile_name") or "").strip()
    return {
        "tv_profile_name": profile,
        "tv_signal_bias": bias,
        "tv_signal_ts": str(tv_context.get("ts") or "").strip(),
        "tv_signal_age_sec": float(tv_context.get("age_seconds") or 0.0),
        "tv_indicator_name": str(
            tv_context.get("indicator_name") or tv_context.get("indicator") or ""
        ).strip(),
        "tv_signal_strength": str(tv_context.get("strength") or "").strip(),
        "tv_signal_active": bool(profile and bias == "LONG"),
    }


def _spot_entry_features(
    symbol: str,
    *,
    composite_score: float,
    final_spot_score: float,
    spot_state: dict | None,
    execution_route: str,
    edge_profile: str,
    target_profile: str,
    tv_context: dict | None,
    candidate_id: int = 0,
    candidate_scan_id: str = "",
    raw_scanner_symbol: str = "",
    base_asset: str = "",
    tv_veto_state: str = "",
) -> dict:
    state = spot_state or {}
    frames = state.get("frames") or {}
    s5 = frames.get("5m") or {}
    s30 = frames.get("30m") or {}
    confirms = {
        token.strip().lower()
        for token in str(state.get("structural_confirms") or "").split(",")
        if token.strip()
    }
    tv_payload = _tv_payload(tv_context)
    return {
        "symbol": _clean_symbol(symbol),
        "regime": str(state.get("regime") or "UNKNOWN"),
        "spot_regime": str(state.get("regime") or "UNKNOWN"),
        "setup_family": str(state.get("setup_family") or ""),
        "setup_score": float(state.get("setup_score") or 0.0),
        "composite_score": float(composite_score or 0.0),
        "conviction_score": float(final_spot_score or 0.0),
        "entry_thesis_score": float(final_spot_score or 0.0),
        "final_spot_score": float(final_spot_score or 0.0),
        "derivative_score": float(state.get("derivative_score") or 0.0),
        "structural_confirm_count": int(state.get("structural_confirm_count") or 0),
        "execution_route": str(execution_route or ""),
        "edge_profile": str(edge_profile or ""),
        "target_profile": str(target_profile or ""),
        "path_efficiency": float(s5.get("path_efficiency") or 0.0),
        "momentum_impulse": float(s5.get("momentum_impulse") or 0.0),
        "structure_component": float(s5.get("structure_component") or 0.0),
        "participation_component": float(s5.get("participation_component") or 0.0),
        "a5": float(s5.get("a") or 0.0),
        "v30": float(s30.get("v") or 0.0),
        "frame_score_5m": float(s5.get("frame_score") or 0.0),
        "frame_score_30m": float(s30.get("frame_score") or 0.0),
        "volatility_quality": float(s30.get("volatility_quality") or 0.0),
        "price_above_vwap": bool(s5.get("price_above_vwap")),
        "compression_release": str(state.get("setup_family") or "").startswith(
            "compression"
        ),
        "supertrend_bullish": "supertrend" in confirms,
        "cloud_bullish": ("cloud" in confirms) or ("ichimoku" in confirms),
        "wae_bullish": "wae" in confirms,
        "wt_oversold_cross": ("wt" in confirms) or ("wavetrend" in confirms),
        "kst_bullish": "kst" in confirms,
        "candidate_id": int(candidate_id or 0),
        "scan_id": str(candidate_scan_id or ""),
        "raw_scanner_symbol": str(raw_scanner_symbol or ""),
        "base_asset": str(base_asset or _clean_symbol(symbol)),
        "executed_symbol": _clean_symbol(symbol),
        "route_type": str(execution_route or ""),
        "tv_veto_state": str(tv_veto_state or ""),
        "tv_signal_active": bool(tv_payload["tv_signal_active"]),
        "tv_profile_name": tv_payload["tv_profile_name"],
        "tv_htf_bias": tv_payload["tv_signal_bias"],
        "tv_signal_age_sec": float(tv_payload["tv_signal_age_sec"] or 0.0),
    }


def _load_entry_feature_snapshot(position: dict) -> dict:
    snapshot_id = int(position.get("entry_feature_snapshot_id") or 0)
    trade_id = int(position.get("entry_trade_id") or 0)
    if snapshot_id <= 0 and trade_id <= 0:
        return {}
    try:
        con = sqlite3.connect(_get_db_path(), timeout=5)
        cur = con.cursor()
        if snapshot_id > 0:
            cur.execute(
                "SELECT features_json FROM trade_features WHERE id=? LIMIT 1",
                (snapshot_id,),
            )
        else:
            cur.execute(
                "SELECT features_json FROM trade_features WHERE trade_id=? ORDER BY id DESC LIMIT 1",
                (trade_id,),
            )
        row = cur.fetchone()
        con.close()
        if not row or not row[0]:
            return {}
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"[spot_engine] feature snapshot load error: {e}")
        return {}


def _resolve_spot_state(symbol: str, *, allow_stale: bool) -> dict | None:
    try:
        return build_spot_state(symbol, allow_stale=allow_stale)
    except TypeError:
        return build_spot_state(symbol)


def _entry_floor(regime: str) -> float:
    return score_floor_for_regime(regime)


def _target_r(regime: str) -> float:
    return float(
        SPOT_TARGET_R_BY_REGIME.get(
            regime, SPOT_TARGET_R_BY_REGIME.get("NEUTRAL", 0.65)
        )
    )


def _trail_arm_r(regime: str) -> float:
    return float(
        SPOT_TRAIL_ARM_R_BY_REGIME.get(
            regime,
            SPOT_TRAIL_ARM_R_BY_REGIME.get("NEUTRAL", 0.40),
        )
    )


def _compute_stop_pct(
    symbol: str, spot_state: dict | None, atr_at_entry: float = 0.0
) -> float:
    cfg = _symbol_cfg(symbol)
    floor = float(cfg.get("stop_floor_pct", 0.01))
    cap = float(cfg.get("stop_cap_pct", 0.02))
    symbol_k = float(cfg.get("symbol_k", 1.1))
    frames = (spot_state or {}).get("frames", {})
    s5 = frames.get("5m", {}) if isinstance(frames, dict) else {}
    atr_pct = float(s5.get("atr_pct") or 0.0)
    if atr_pct <= 0 and atr_at_entry > 0:
        price = float(s5.get("price") or 0.0)
        atr_pct = (atr_at_entry / price) if price > 0 else 0.0
    penalty = 0.0
    if (spot_state or {}).get("regime") == "CHOP":
        penalty += 0.10
    rv_ratio = float((spot_state or {}).get("rv_ratio") or 1.0)
    if rv_ratio > 1.30:
        penalty += min(0.20, (rv_ratio - 1.30) * 0.25)
    if abs(float(s5.get("a") or 0.0)) < 0.05 and abs(float(s5.get("v") or 0.0)) < 0.10:
        penalty += 0.05
    base_vol_stop = max(atr_pct * symbol_k, floor)
    raw_stop = max(floor, min(base_vol_stop * (1.0 + penalty), cap))
    # Apply evidence-derived stop tighten multipliers (config SPOT_STOP_TIGHTEN_*)
    import config as _sc

    _regime = str((spot_state or {}).get("regime") or "NEUTRAL").upper()
    _sf = str((spot_state or {}).get("setup_family") or "")
    _tighten = 1.0
    if _regime == "NEUTRAL":
        _tighten = min(_tighten, float(getattr(_sc, "SPOT_STOP_TIGHTEN_NEUTRAL", 0.92)))
    elif _regime == "CHOP":
        _tighten = min(_tighten, float(getattr(_sc, "SPOT_STOP_TIGHTEN_CHOP", 0.88)))
    if _sf == "pullback_reclaim":
        _tighten = min(
            _tighten, float(getattr(_sc, "SPOT_STOP_TIGHTEN_PULLBACK", 0.90))
        )
    return max(floor, raw_stop * _tighten)


def _execution_micro_ok(symbol: str, top: dict) -> tuple[bool, str]:
    cfg = _symbol_cfg(symbol)
    clean = _clean_symbol(symbol)
    spread_cap = float(cfg.get("spread_cap_pct", 0.0025))
    depth_min = float(cfg.get("depth_min_usd", 5000))
    spread_pct = float(top.get("spread_pct") or 0.0)
    depth = float(top.get("top_depth_usd") or 0.0)
    if spread_pct > spread_cap:
        reason = "spread_cap_exceeded"
        try:
            import system_state
            system_state.state.update_stochastic(clean, {"status": "VETO", "reason": reason})
        except Exception:
            pass
        return False, reason
    if depth > 0 and depth < depth_min:
        reason = "depth_below_minimum"
        try:
            import system_state
            system_state.state.update_stochastic(clean, {"status": "VETO", "reason": reason})
        except Exception:
            pass
        return False, reason
    return True, "none"


def _maker_first_buy(
    broker: "CoinbaseSpotBroker",
    symbol: str,
    size_usd: float,
    *,
    final_spot_score: float | None = None,
    spot_state: dict | None = None,
    target_route: str = "maker_first",
) -> tuple[Optional[dict], str, str]:
    if target_route == "taker":
        order = broker.buy_spot(symbol, size_usd)
        if order:
            order["execution_route"] = "taker"
            return order, "taker", "none"
        return None, "taker_failed", "market_order_rejected"

    top = broker.get_spot_top_of_book(symbol)
    ok, veto = _execution_micro_ok(symbol, top)
    if not ok:
        return None, "skipped_microstructure", veto

    bid = float(top.get("best_bid") or 0.0)
    ask = float(top.get("best_ask") or 0.0)
    limit_px = limit_buy_price(bid, ask)
    order = broker.place_limit_buy_spot(symbol, size_usd, limit_px, post_only=True)
    if not order:
        return None, "maker_first_failed", "limit_order_rejected"

    polls = maker_poll_count()
    for _ in range(polls):
        time.sleep(max(1, SPOT_MAKER_POLL_SECONDS))
        status = broker.get_spot_order_status(
            order["order_id"], fallback_symbol=_clean_symbol(symbol)
        )
        completion = float(status.get("completion_pct") or 0.0)
        if completion >= 80.0 or str(status.get("status", "")).upper() == "FILLED":
            status["execution_route"] = "maker_first"
            return status, "maker_first", "none"

    broker.cancel_spot_order(order["order_id"])
    logger.info(
        f"[spot_engine] Maker order {order['order_id']} failed to fully fill (completion={completion}%)"
    )
    # Taker fallback gate: disabled when SPOT_TAKER_FALLBACK_ENABLED=false (default).
    # Evidence: 113 taker trades in failure window, 0% WR, avg -$1.16, higher fee burn.
    import config as _tfc

    if not getattr(_tfc, "SPOT_TAKER_FALLBACK_ENABLED", False):
        return None, "taker_fallback_disabled", "maker_unfilled_no_taker"
    if final_spot_score is not None:
        regime = str((spot_state or {}).get("regime") or "NEUTRAL").upper()
        taker_floor = score_floor_for_symbol(
            symbol,
            regime,
            structural_confirm_count=int(
                (spot_state or {}).get("structural_confirm_count") or 0
            ),
            setup_family=str((spot_state or {}).get("setup_family") or ""),
            setup_score=float((spot_state or {}).get("setup_score") or 0.0),
            execution_route="taker_fallback",
        )
        if float(final_spot_score) < float(taker_floor):
            return None, "skipped_taker_score", "taker_score_below_threshold"
    taker = broker.buy_spot(symbol, size_usd)
    if taker:
        taker["execution_route"] = "taker_fallback"
        return taker, "taker_fallback", "none"
    return None, "taker_fallback_failed", "unfilled_after_maker"


def _maker_first_sell(
    broker: "CoinbaseSpotBroker", symbol: str, size_units: float
) -> tuple[Optional[dict], str, str]:
    top = broker.get_spot_top_of_book(symbol)
    ok, veto = _execution_micro_ok(symbol, top)
    if not ok:
        return None, "skipped_microstructure", veto

    bid = float(top.get("best_bid") or 0.0)
    ask = float(top.get("best_ask") or 0.0)
    limit_px = limit_sell_price(bid, ask)
    order = broker.place_limit_sell_spot(symbol, size_units, limit_px, post_only=True)
    if not order:
        return None, "maker_first_failed", "limit_order_rejected"

    polls = maker_poll_count()
    for _ in range(polls):
        time.sleep(max(1, SPOT_MAKER_POLL_SECONDS))
        status = broker.get_spot_order_status(
            order["order_id"], fallback_symbol=_clean_symbol(symbol)
        )
        completion = float(status.get("completion_pct") or 0.0)
        if completion >= 80.0 or str(status.get("status", "")).upper() == "FILLED":
            status["execution_route"] = "maker_first"
            return status, "maker_first", "none"

    broker.cancel_spot_order(order["order_id"])
    logger.info(
        f"[spot_engine] Maker sell order {order['order_id']} failed to fully fill (completion={completion}%)"
    )
    import config as _tfc_sell

    if not getattr(_tfc_sell, "SPOT_TAKER_FALLBACK_ENABLED", False):
        return None, "taker_fallback_disabled", "maker_unfilled_no_taker"
    taker = broker.sell_spot(symbol, size_units)
    if taker:
        taker["execution_route"] = "taker_fallback"
        return taker, "taker_fallback", "none"
    return None, "taker_fallback_failed", "unfilled_after_maker"


def _reconcile_qty(symbol: str, issue: dict) -> None:
    """v18.17: Force DB quantity to match canonical broker truth."""
    clean = _clean_symbol(symbol)
    live_qty = float(issue.get("qty", 0.0))
    try:
        con = sqlite3.connect(_get_db_path(), timeout=5)
        con.execute("UPDATE open_positions SET qty=? WHERE symbol=? AND paper=0""", (live_qty, clean))
        con.commit()
        con.close()
        logger.info(f"[spot_engine] Reconciled {clean} DB qty to broker truth: {live_qty}")
    except Exception as e:
        logger.debug(f"[spot_engine] _reconcile_qty failed for {clean}: {e}")


def open_spot(
    symbol: str,
    size_usd: float,
    composite_score: float = 0.0,
    atr_at_entry: float = 0.0,
    spot_state: dict | None = None,
    final_spot_score: float | None = None,
    risk_dollars: float = 0.0,
    cooldown_until: str = "",
    tv_context: dict | None = None,
    candidate_id: int = 0,
    candidate_scan_id: str = "",
    raw_scanner_symbol: str = "",
    base_asset: str = "",
) -> Optional[Dict]:
    clean = _clean_symbol(symbol)
    if not SPOT_LANE_ACTIVE:
        logger.info(f"[spot_engine] {clean} blocked — spot_lane_disabled")
        return None
    if clean not in SPOT_SYMBOLS:
        logger.warning(f"[spot_engine] {clean} blocked — spot_symbol_not_allowed")
        return None
    if not get_spot_strategy(clean)["enabled"]:
        logger.warning(f"[spot_engine] {clean} blocked — spot_strategy_symbol_disabled")
        return None

    # v18.17: Force fresh truth reconciliation before entry
    # Hardcoded paper=False (0)
    truth = get_spot_position_truth()
    truth_row = next(
        (
            row
            for row in truth.get("all_live_holdings", [])
            if _clean_symbol(row.get("symbol")) == clean
        ),
        None,
    )

    if truth_row:
        truth_status = str(truth_row.get("position_truth_status") or "")
        if truth_status == "qty_mismatch":
            _reconcile_qty(clean, truth_row)
            # Re-fetch after reconciliation
            truth_row = get_spot_symbol_truth(clean)
            truth_status = str((truth_row or {}).get("position_truth_status") or "")

        # v18.17: Auto-Adoption of Unclassified Assets
        if truth_status == "unclassified":
            logger.info(f"[spot_engine] {clean} was unclassified. Auto-adopting into DB.")
            try:
                import sqlite3
                from config import DB_PATH
                # truth_row has 'current_price' and 'qty'
                entry_price = float(truth_row.get("current_price", 0.0))
                if entry_price <= 0:
                    entry_price = 1.0
                qty = float(truth_row.get("qty", 0.0))
                with sqlite3.connect(DB_PATH, timeout=5) as conn:
                    conn.execute(
                        "INSERT INTO open_positions (symbol, strategy, qty, entry, paper, direction, ts_entry) VALUES (?, ?, ?, ?, 0, 'LONG', ?)",
                        (clean, f"spot_{clean.lower()}", qty, entry_price, datetime.datetime.utcnow().isoformat())
                    )
                truth_status = "matched_bot_position"
            except Exception as e:
                logger.debug(f"Failed to adopt {clean}: {e}")

        if truth_status in {
            "matched_bot_position",
        }:
            # v18.17: Downgrade from warning to info to stop Telegram spam
            logger.info(f"[spot_engine] {clean} blocked — spot_truth_{truth_status}")
            return {"blocked": f"spot_truth_{truth_status}"}
    if any(
        str(p.get("symbol", "")).upper() == clean
        for p in _load_spot_positions_from_db()
    ):
        logger.info(f"[spot_engine] {clean} blocked — spot_position_already_open")
        return {"blocked": "spot_position_already_open"}
    if size_usd < SPOT_MIN_ORDER_USD:
        logger.info(f"[spot_engine] {clean} blocked — spot_size_below_minimum")
        return {"blocked": "spot_size_below_minimum"}

    broker = _get_broker()
    if broker is None:
        logger.error(f"[spot_engine] {clean} — broker unavailable")
        return None

    try:
        bal = broker.get_spot_balance() or {}
        usd_available = float(bal.get("usd_available") or 0.0)
        deployed = float(_current_spot_deployed_usd())
        total_spot_equity = max(usd_available + deployed, deployed)
        projected = deployed + float(size_usd)
        if total_spot_equity > 0 and (projected / total_spot_equity) > float(
            SPOT_TOTAL_ALLOC_CAP_PCT
        ):
            logger.info(f"[spot_engine] {clean} blocked — spot_deployment_cap_exceeded")
            return None
    except Exception:
        pass

    if spot_state is None:
        try:
            spot_state = _resolve_spot_state(clean, allow_stale=False)
        except Exception as e:
            logger.warning(f"[spot_engine] {clean} spot_state unavailable: {e}")
            spot_state = None

    regime = str((spot_state or {}).get("regime") or "NEUTRAL")
    stop_pct = _compute_stop_pct(clean, spot_state, atr_at_entry=atr_at_entry)
    target_r = target_r_for_symbol(clean, regime)
    trail_arm_r = trail_arm_r_for_symbol(clean, regime)
    edge_policy = edge_policy_for_symbol(clean)
    edge_profile = str(edge_policy.get("profile") or "balanced")
    target_profile = str(
        exit_profile_for_symbol(clean, regime) or edge_profile or "balanced"
    )
    score_used = float(
        final_spot_score if final_spot_score is not None else composite_score
    )
    # v18.16: Dynamic Taker Routing for TREND breakouts with high structural conviction
    target_route = "maker_first"
    if regime == "TREND" and int(spot_state.get("structural_confirm_count") or 0) >= 3:
        target_route = "taker"

    block_reason, score_floor = spot_quality_block_reason(
        clean,
        spot_state,
        final_spot_score=score_used,
        execution_route=target_route,
        tv_context=tv_context,
    )
    if block_reason:
        logger.info(f"[spot_engine] {clean} blocked — {block_reason}")
        return None

    # Apply execution multiplier (soft vetoes: Kyle's Lambda, OBI/TFI divergence)
    from runtime.spot_strategy import calculate_execution_profile as _calc_exec_profile

    _exec_mult, _exec_tag = _calc_exec_profile(clean, spot_state)
    if _exec_mult < 1.0:
        logger.info(
            f"[spot_engine] {clean} exec_mult={_exec_mult:.2f} ({_exec_tag}) "
            f"— size_usd reduced from ${size_usd:.2f} to ${size_usd * _exec_mult:.2f}"
        )
    size_usd = round(size_usd * _exec_mult, 2)

    order = None
    execution_route = "none"
    micro_veto = "none"
    t0 = time.time()
    
    order, execution_route, micro_veto = _maker_first_buy(
        broker,
        clean,
        size_usd,
        final_spot_score=score_used,
        spot_state=spot_state,
        target_route=target_route,
    )
    if execution_route == "skipped_microstructure":
        logger.info(f"[spot_engine] {clean} blocked — microstructure {micro_veto}")
        return None
    if execution_route == "skipped_taker_score":
        logger.info(f"[spot_engine] {clean} blocked — {micro_veto}")
        return None

    if not order:
        if execution_route in ("skipped_microstructure", "skipped_taker_score", "taker_fallback_disabled"):
            logger.info(f"[spot_engine] {clean} buy skipped: route={execution_route} veto={micro_veto}")
        else:
            logger.error(f"[spot_engine] {clean} buy failed: route={execution_route} veto={micro_veto}")
        return None

    # Push to Prometheus
    try:
        from monitoring import metrics

        metrics.TRADES_COUNTER.inc()
        metrics.EXECUTION_LATENCY_HISTOGRAM.observe(latency)
    except Exception:
        pass

    price = float(
        order.get("average_filled_price") or broker.get_mark_price(clean) or 0.0
    )
    qty = float(order.get("filled_size") or 0.0)
    if qty <= 0 and price > 0:
        qty = size_usd / price
    fee_usd = float(order.get("fee_usd") or 0.0)
    stop_price = round(price * (1.0 - stop_pct), 8) if price > 0 else 0.0
    target_price = round(price * (1.0 + stop_pct * target_r), 8) if price > 0 else 0.0

    from logging_db.trade_logger import log_trade, log_trade_features, persist_position

    state_payload = _state_payload(spot_state)
    tv_payload = _tv_payload(tv_context)
    entry_trade_id = log_trade(
        strategy=_position_strategy(clean),
        broker="coinbase_spot",
        symbol=clean,
        action="BUY",
        order_type="LIMIT" if execution_route == "maker_first" else "MARKET",
        qty=qty,
        price=price,
        fee_usd=fee_usd,
        pnl_usd=0.0,
        notes=(
            f"spot_buy route={execution_route} stop={stop_price:.8g} "
            f"target={target_price:.8g} stop_pct={stop_pct:.4%} "
            f"final_spot_score={score_used:.1f} edge_profile={edge_profile} "
            f"target_profile={target_profile}"
        ),
    )
    entry_features = _spot_entry_features(
        clean,
        composite_score=composite_score,
        final_spot_score=score_used,
        spot_state=spot_state,
        execution_route=execution_route,
        edge_profile=edge_profile,
        target_profile=target_profile,
        tv_context=tv_context,
        candidate_id=int(candidate_id or 0),
        candidate_scan_id=str(candidate_scan_id or ""),
        raw_scanner_symbol=str(raw_scanner_symbol or ""),
        base_asset=str(base_asset or clean),
        tv_veto_state=str(block_reason or ""),
    )
    entry_feature_snapshot_id = log_trade_features(
        entry_trade_id,
        clean,
        "LONG",
        entry_features,
    )
    persist_position(
        symbol=clean,
        strategy=_position_strategy(clean),
        qty=qty,
        entry=price,
        stop=stop_price,
        target=target_price,
        high_since_entry=price,
        ts_entry=datetime.datetime.now().isoformat(),
        direction="LONG",
        entry_reason="spot_scalp_entry",
        atr_at_entry=atr_at_entry,
        composite_score=composite_score,
        leverage=1,
        spot_regime=regime,
        setup_family=(spot_state or {}).get("setup_family", ""),
        setup_score=state_payload["setup_score"],
        setup_preference=state_payload["setup_preference"],
        tf_5m_state=state_payload["tf_5m_state"],
        tf_30m_state=state_payload["tf_30m_state"],
        tf_4h_state=state_payload["tf_4h_state"],
        tf_1d_state=state_payload["tf_1d_state"],
        structural_confirms=state_payload["structural_confirms"],
        execution_route=execution_route,
        cooldown_until=cooldown_until,
        microstructure_veto=micro_veto,
        stop_model_version="spot_scalp_v1",
        target_model_version=f"spot_scalp_{target_profile}_v1",
        target_r=target_r,
        trail_arm_r=trail_arm_r,
        risk_dollars=risk_dollars,
        entry_fee_usd=fee_usd,
        exit_reason="",
        entry_trade_id=entry_trade_id,
        entry_order_id=str(order.get("order_id") or ""),
        entry_feature_snapshot_id=entry_feature_snapshot_id,
        tv_profile_name=tv_payload["tv_profile_name"],
        tv_signal_bias=tv_payload["tv_signal_bias"],
        tv_signal_ts=tv_payload["tv_signal_ts"],
        tv_signal_age_sec=tv_payload["tv_signal_age_sec"],
        tv_indicator_name=tv_payload["tv_indicator_name"],
        tv_signal_strength=tv_payload["tv_signal_strength"],
        candidate_id=int(candidate_id or 0),
        candidate_scan_id=str(candidate_scan_id or ""),
        raw_scanner_symbol=str(raw_scanner_symbol or ""),
        base_asset=str(base_asset or clean),
        tv_veto_state=str(block_reason or ""),
    )
    return {
        "symbol": clean,
        "strategy": _position_strategy(clean),
        "qty": qty,
        "entry": price,
        "stop_price": stop_price,
        "target_price": target_price,
        "size_usd": size_usd,
        "risk_dollars": risk_dollars,
        "stop_pct": stop_pct,
        "target_r": target_r,
        "trail_arm_r": trail_arm_r,
        "setup_family": state_payload["setup_family"],
        "setup_score": state_payload["setup_score"],
        "setup_preference": state_payload["setup_preference"],
        "target_profile": target_profile,
        "order_id": order.get("order_id", ""),
        "fee_usd": fee_usd,
        "execution_route": execution_route,
        "candidate_id": int(candidate_id or 0),
        "scan_id": str(candidate_scan_id or ""),
        "paper": False,
    }


def close_spot(
    symbol: str,
    exit_reason: str = "manual_exit",
) -> Optional[Dict]:
    clean = _clean_symbol(symbol)
    pos = next(
        (
            p
            for p in _load_spot_positions_from_db()
            if str(p.get("symbol", "")).upper() == clean
        ),
        None,
    )
    if not pos:
        logger.warning(f"[spot_engine] close_spot {clean}: no open position found")
        return None

    qty = float(pos.get("qty") or 0.0)
    entry_price = float(pos.get("entry") or 0.0)
    strategy = str(pos.get("strategy") or _position_strategy(clean))
    if qty <= 0:
        return None
    broker = _get_broker()
    if broker is None:
        return None

    # v18.17: Authoritative Inventory Sync
    # Query broker directly before sell to avoid INSUFFICIENT_FUND loops
    try:
        bal = broker.get_spot_balance()
        actual_qty = float(bal.get("symbol_balances", {}).get(clean, 0.0))
        if actual_qty < qty:
            logger.warning(
                f"[spot_engine] close_spot {clean}: DB qty={qty:.5f} > broker qty={actual_qty:.5f} "
                "— selling actual qty to avoid INSUFFICIENT_FUND"
            )
            qty = actual_qty
    except Exception as _e:
        logger.debug(f"[spot_engine] broker balance sync failed for {clean}: {_e}")

    if qty <= 0:
        # v18.17: If actual_qty is 0, auto-purge the ghost DB position and return success
        try:
            from logging_db.trade_logger import delete_position

            delete_position(clean, strategy=strategy)
            logger.info(
                f"[spot_engine] close_spot {clean}: broker actual_qty is 0, "
                "auto-purging DB and returning success"
            )
        except Exception as e:
            logger.debug(f"[spot_engine] auto-purge failed for {clean}: {e}")

        return {
            "symbol": clean,
            "exit_reason": "auto_purged_0_qty",
            "qty": 0.0,
            "pnl_usd": 0.0,
            "fee_usd": 0.0,
            "paper": False,
        }

    order = None
    execution_route = "none"
    micro_veto = "none"
    t0 = time.time()
    
    order, execution_route, micro_veto = _maker_first_sell(broker, clean, qty)
    if execution_route == "skipped_microstructure":
        order = broker.sell_spot(clean, qty)
        execution_route = "taker_fallback"

    latency = time.time() - t0
    if not order:
        logger.warning(f"[spot_engine] close_spot {clean}: maker_first failed ({micro_veto}). Attempting TAKER fallback...")
        # v18.17: Ironclad Loop Breaker
        # If maker fails, we MUST try a market order to clear the position and stop the Telegram spam.
        try:
            bal = broker.get_spot_balance()
            actual_qty = float(bal.get("symbol_balances", {}).get(clean, 0.0))
            if actual_qty > 0:
                order = broker.sell_spot(clean, actual_qty)
                if order:
                    logger.info(f"[spot_engine] close_spot {clean}: Taker fallback success. Loop broken.")
                    # Fall through to the P&L calculation and DB deletion logic below
                else:
                    logger.error(f"[spot_engine] close_spot {clean}: FATAL - Taker fallback failed. Clearing DB to stop loop.")
                    from logging_db.trade_logger import delete_position
                    delete_position(clean, strategy=strategy)
                    return None
            else:
                logger.warning(f"[spot_engine] close_spot {clean}: Broker balance is 0. Purging DB ghost.")
                from logging_db.trade_logger import delete_position
                delete_position(clean, strategy=strategy)
                return None
        except Exception as _re:
            logger.error(f"[spot_engine] close_spot {clean}: critical recovery failure: {_re}")
            return None

    # Push to Prometheus
    try:
        from monitoring import metrics

        metrics.TRADES_COUNTER.inc()
        metrics.EXECUTION_LATENCY_HISTOGRAM.observe(latency)
    except Exception:
        pass

    exit_price = float(
        order.get("average_filled_price") or broker.get_mark_price(clean) or entry_price
    )
    filled_qty = float(order.get("filled_size") or qty)

    # v18.16: Force DB to zero immediately after fill to prevent ghost positions
    try:
        con = sqlite3.connect(_get_db_path(), timeout=5)
        con.execute("UPDATE open_positions SET qty = 0 WHERE symbol = ?", (clean,))
        con.commit()
        con.close()
    except Exception as _e:
        logger.debug(f"[spot_engine] ghost-kill failed for {clean}: {_e}")
    fee_usd = float(order.get("fee_usd") or 0.0)
    pnl_usd = (
        (exit_price - entry_price) * filled_qty
        - fee_usd
        - float(pos.get("entry_fee_usd") or 0.0)
    )

    from logging_db.trade_logger import log_trade

    entry_features = _load_entry_feature_snapshot(pos)
    exit_state = None
    try:
        exit_state = _resolve_spot_state(clean, allow_stale=True)
    except Exception:
        exit_state = None

    _sync_position_exit_reason(clean, strategy, exit_reason)
    close_trade_id = log_trade(
        strategy=strategy,
        broker="coinbase_spot",
        symbol=clean,
        action="SELL",
        order_type="LIMIT" if execution_route == "maker_first" else "MARKET",
        qty=filled_qty,
        price=exit_price,
        fee_usd=fee_usd,
        pnl_usd=pnl_usd,
        won=1 if pnl_usd > 0 else 0,
        notes=(
            f"spot_sell exit_reason={exit_reason} route={execution_route} "
            f"micro_veto={micro_veto} pnl={pnl_usd:.2f}"
        ),
    )
    total_fee_usd = fee_usd + float(pos.get("entry_fee_usd") or 0.0)
    learning_snapshot_written = False
    attribution_written = False
    if close_trade_id > 0:
        trade_ref = f"spot:{int(pos.get('entry_trade_id') or 0)}:{close_trade_id}"
        try:
            import learning_loop as _ll

            entry_features.setdefault("candidate_id", int(pos.get("candidate_id") or 0))
            entry_features.setdefault(
                "scan_id", str(pos.get("candidate_scan_id") or "")
            )
            entry_features.setdefault(
                "raw_scanner_symbol", str(pos.get("raw_scanner_symbol") or clean)
            )
            entry_features.setdefault("base_asset", str(pos.get("base_asset") or clean))
            entry_features.setdefault("executed_symbol", clean)
            entry_features.setdefault(
                "route_type", str(pos.get("execution_route") or execution_route or "")
            )
            entry_features.setdefault(
                "tv_veto_state", str(pos.get("tv_veto_state") or "")
            )
            entry_features.setdefault(
                "tv_profile_name", str(pos.get("tv_profile_name") or "")
            )
            entry_features.setdefault(
                "tv_htf_bias", str(pos.get("tv_signal_bias") or "")
            )
            entry_features.setdefault(
                "tv_signal_age_sec", float(pos.get("tv_signal_age_sec") or 0.0)
            )
            _ll.record_closed_trade(
                trade_id=close_trade_id,
                symbol=clean,
                direction="LONG",
                won=pnl_usd > 0,
                pnl_usd=pnl_usd,
                entry_price=entry_price,
                exit_price=exit_price,
                entry_score=float(entry_features.get("entry_thesis_score") or 0.0),
                exit_score=float((exit_state or {}).get("derivative_score") or 0.0),
                regime=str(
                    entry_features.get("regime")
                    or pos.get("spot_regime")
                    or (exit_state or {}).get("regime")
                    or "UNKNOWN"
                ),
                features=entry_features,
                exit_reason=exit_reason,
                trade_ref=trade_ref,
            )
        except Exception as e:
            logger.debug(f"[spot_engine] learning_loop close error {clean}: {e}")
        try:
            from learning.post_trade_analyzer import analyze_closed_trade as _pta

            _pta(
                symbol=clean,
                strategy=strategy,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=filled_qty,
                fee_usd=total_fee_usd,
                entry_ts=str(
                    pos.get("ts_entry") or datetime.datetime.utcnow().isoformat()
                ),
                exit_ts=datetime.datetime.utcnow().isoformat(),
                exit_reason=exit_reason,
                market_data_at_entry=entry_features,
                source="live_v10",
                trade_ref=trade_ref,
                exit_type=exit_reason,
                composite_score=float(entry_features.get("composite_score") or 0.0),
                close_order_id=str(order.get("order_id") or ""),
                entry_order_id=str(pos.get("entry_order_id") or ""),
                feature_snapshot_id=int(pos.get("entry_feature_snapshot_id") or 0),
            )
        except Exception as e:
            logger.debug(f"[spot_engine] post_trade_analyzer close error {clean}: {e}")
        try:
            con = sqlite3.connect(_get_db_path(), timeout=5)
            learning_snapshot_written = bool(
                con.execute(
                    "SELECT 1 FROM ml_feature_snapshots WHERE trade_id=? LIMIT 1",
                    (close_trade_id,),
                ).fetchone()
            )
            attribution_written = bool(
                con.execute(
                    "SELECT 1 FROM trade_attribution WHERE trade_ref=? LIMIT 1",
                    (trade_ref,),
                ).fetchone()
            )
            con.close()
        except Exception:
            learning_snapshot_written = False
            attribution_written = False

    if close_trade_id > 0 and (not learning_snapshot_written or not attribution_written):
        try:
            from runtime.spot_kill_switch import trigger_spot_halt

            reason = (
                "spot_learning_snapshot_missing"
                if not learning_snapshot_written
                else "spot_trade_attribution_missing"
            )
            trigger_spot_halt(
                reason,
                {
                    "symbol": clean,
                    "trade_ref": trade_ref,
                    "close_trade_id": close_trade_id,
                    "snapshot_written": learning_snapshot_written,
                    "attribution_written": attribution_written,
                },
            )
        except Exception as e:
            logger.warning(
                f"[spot_engine] unable to trigger spot halt for {clean}: {e}"
            )

    try:
        truth = get_spot_position_truth()
        residual = next(
            (
                row
                for row in truth.get("all_live_holdings") or []
                if str(row.get("symbol") or "").upper() == clean
            ),
            None,
        )
    except Exception:
        residual = None
    if residual and float(residual.get("qty") or 0.0) > 1e-8:
        updated_row = dict(pos)
        updated_row["qty"] = float(residual.get("qty") or 0.0)
        updated_row["entry"] = float(
            residual.get("entry") or pos.get("entry") or entry_price
        )
        updated_row["paper"] = 0
        updated_row["exit_reason"] = str(exit_reason or "")
        _persist_position_from_row(updated_row, qty=updated_row["qty"])
    else:
        from logging_db.trade_logger import delete_position

        delete_position(clean, strategy=strategy)
    
    try:
        from data.edge_monitor import record_incubation_trade

        record_incubation_trade("spot_scalp")
    except Exception:
        pass
    return {
        "symbol": clean,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": filled_qty,
        "pnl_usd": round(pnl_usd, 4),
        "fee_usd": fee_usd,
        "execution_route": execution_route,
        "exit_reason": exit_reason,
        "order_id": order.get("order_id", ""),
        "paper": False,
    }


def check_spot_stops() -> List[Dict]:
    closed: List[Dict] = []
    broker = _get_broker()
    if broker is None:
        return closed
    for pos in _load_spot_positions_from_db():
        sym = str(pos.get("symbol") or "").upper()
        stop_price = float(pos.get("stop") or 0.0)
        if stop_price <= 0:
            continue
        current_price = float(broker.get_mark_price(sym) or 0.0)
        if current_price > 0 and current_price <= stop_price:
            result = close_spot(sym, exit_reason="hard_stop")
            if result:
                result["trigger"] = "hard_stop"
                closed.append(result)
    return closed


def check_spot_trailing() -> List[Dict]:
    closed: List[Dict] = []
    broker = _get_broker()
    if broker is None:
        return closed
    for pos in _load_spot_positions_from_db():
        sym = str(pos.get("symbol") or "").upper()
        strategy = str(pos.get("strategy") or _position_strategy(sym))
        entry = float(pos.get("entry") or 0.0)
        stop = float(pos.get("stop") or 0.0)
        high_since_entry = float(pos.get("high_since_entry") or entry)
        target_r = float(
            pos.get("target_r") or _target_r(str(pos.get("spot_regime") or "NEUTRAL"))
        )
        trail_arm_r = float(
            pos.get("trail_arm_r")
            or _trail_arm_r(str(pos.get("spot_regime") or "NEUTRAL"))
        )
        current_price = float(broker.get_mark_price(sym) or 0.0)
        if current_price <= 0 or entry <= 0 or stop <= 0:
            continue
        if current_price > high_since_entry:
            high_since_entry = current_price
            _sync_position_high(sym, strategy, high_since_entry)
        risk_per_unit = entry - stop
        if risk_per_unit <= 0:
            continue
        arm_price = entry + risk_per_unit * trail_arm_r
        if high_since_entry < arm_price:
            continue
        trail_width = risk_per_unit * max(0.6, min(target_r, 1.0))
        trail_stop = high_since_entry - trail_width
        if current_price <= trail_stop and current_price > entry:
            result = close_spot(sym, exit_reason="trailing_stop")
            if result:
                result["trigger"] = "trailing_stop"
                closed.append(result)
    return closed


def check_spot_targets() -> List[Dict]:
    closed: List[Dict] = []
    broker = _get_broker()
    if broker is None:
        return closed
    for pos in _load_spot_positions_from_db():
        sym = str(pos.get("symbol") or "").upper()
        target = float(pos.get("target") or 0.0)
        current_price = float(broker.get_mark_price(sym) or 0.0)
        if target > 0 and current_price >= target:
            result = close_spot(sym, exit_reason="target_hit")
            if result:
                result["trigger"] = "target_hit"
                closed.append(result)
    return closed


def check_spot_stagnation_exits() -> List[Dict]:
    closed: List[Dict] = []
    broker = _get_broker()
    if broker is None:
        return closed
    for pos in _load_spot_positions_from_db():
        sym = str(pos.get("symbol") or "").upper()
        try:
            spot_state = _resolve_spot_state(sym, allow_stale=True)
        except Exception:
            continue
        entry = float(pos.get("entry") or 0.0)
        stop = float(pos.get("stop") or 0.0)
        current_price = float(broker.get_mark_price(sym) or 0.0)
        ts_entry = str(pos.get("ts_entry") or "")
        if not ts_entry or entry <= 0 or stop <= 0 or current_price <= 0:
            continue
        held_min = (
            datetime.datetime.now() - datetime.datetime.fromisoformat(ts_entry)
        ).total_seconds() / 60.0
        expected_half_life = max(
            6.0, min(45.0, float(spot_state.get("ou_halflife_minutes") or 15.0))
        )
        risk_per_unit = entry - stop
        progress_r = (
            ((current_price - entry) / risk_per_unit) if risk_per_unit > 0 else 0.0
        )
        s5 = spot_state["frames"]["5m"]
        if (
            held_min > expected_half_life
            and progress_r < 0.25
            and s5["v"] <= 0
            and s5["a"] <= 0
        ):
            result = close_spot(sym, exit_reason="stagnation_exit")
            if result:
                result["trigger"] = "stagnation_exit"
                closed.append(result)
    return closed


def check_spot_thesis_exits() -> List[Dict]:
    closed: List[Dict] = []
    for pos in _load_spot_positions_from_db():
        sym = str(pos.get("symbol") or "").upper()
        ts_entry = str(pos.get("ts_entry") or "")
        if ts_entry:
            try:
                held_min = (
                    datetime.datetime.now() - datetime.datetime.fromisoformat(ts_entry)
                ).total_seconds() / 60.0
                if held_min < float(SPOT_THESIS_MIN_HOLD_MINS):
                    continue
            except Exception:
                pass
        try:
            spot_state = _resolve_spot_state(sym, allow_stale=True)
        except Exception:
            continue
        if (
            spot_state["derivative_score"] < SPOT_THESIS_MIN_SCORE
            or spot_state["frames"]["5m"]["v"] <= 0
        ):
            result = close_spot(sym, exit_reason="thesis_decay")
            if result:
                result["trigger"] = "thesis_decay"
                closed.append(result)
    return closed


def check_spot_eod_close() -> List[Dict]:
    if not SPOT_EOD_FLATTEN_ENABLED:
        return []
    import pytz

    et = pytz.timezone("America/New_York")
    now_et = datetime.datetime.now(et)
    if now_et.weekday() >= 5:
        return []
    try:
        eod_h, eod_m = [int(x) for x in SPOT_EOD_CLOSE_TIME.split(":")]
    except Exception:
        eod_h, eod_m = 15, 45
    if now_et.hour < eod_h or (now_et.hour == eod_h and now_et.minute < eod_m):
        return []
    closed: List[Dict] = []
    for pos in _load_spot_positions_from_db():
        result = close_spot(
            str(pos.get("symbol") or ""), exit_reason="eod_close"
        )
        if result:
            result["trigger"] = "eod_close"
            closed.append(result)
    return closed
