"""
forecast/runner.py — Kalshi forecast cycle helpers.

Core cadences:
  discovery      every 30 min  — refresh market/contract cache from Kalshi
  quote harvest  every 60 sec  — collect bid/ask/mid for active contracts
  strategy eval  every 5 min   — run the strategy engine and submit entries
  position mon   every 30 sec  — monitor open positions and flatten resolved ones

The lean runtime normally calls one-pass helpers from `sniper_cron.py`, while
the underlying runner functions remain reusable for diagnostics and local loops.
"""

import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from logging_db.trade_logger import log_event
logger = logging.getLogger(__name__)

# v19.1.5: Ensure core risk caps are available at module level for forced cycles
try:
    from config import KALSHI_SAME_EVENT_FAMILY_CAP
except ImportError:
    KALSHI_SAME_EVENT_FAMILY_CAP = 2

# ── Lazy imports (avoid heavy deps at module load time) ────────────────────────
_broker = None
_harvester = None
_discovery_lock = threading.Lock()
_eval_lock = threading.Lock()


def _forecast_runtime_snapshot(*, connected: bool, contracts: int, stubs: int, active_markets: int = 0) -> dict:
    """Canonical runtime-state mapping for the forecast lane."""
    res = {
        "connected": 1 if connected else 0,
        "tradable": 1 if contracts > 0 else 0,
        "active_markets": active_markets,
        "health": "OK" if connected else "WARN",
        "blocked_reason": "" if connected else "broker_disconnected",
        "action_needed": "" if connected else "connect_kalshi",
        "readiness_state": "OPERATIONAL" if connected else "BROKER_DISCONNECTED",
    }
    if not connected:
        return res
    if contracts == 0 and stubs > 0:
        res.update({
            "tradable": 0,
            "health": "WARN",
            "blocked_reason": "no_tradable_contracts_right_now",
            "action_needed": "check_kalshi_permissions",
            "readiness_state": "NO_TRADABLE_CONTRACTS",
        })
    elif contracts == 0 and stubs == 0:
        res.update({
            "tradable": 0,
            "health": "WARN",
            "blocked_reason": "no_underliers",
            "action_needed": "check_discovery",
            "readiness_state": "NO_UNDERLIERS",
        })
    return res


def _get_broker():
    from execution.kalshi_broker import get_kalshi_broker

    return get_kalshi_broker()


def _get_harvester():
    global _harvester
    if _harvester is None:
        from forecast.quote_harvester import QuoteHarvester

        _harvester = QuoteHarvester(broker=_get_broker())
    return _harvester


def _refresh_quotes_once() -> None:
    """Populate fresh quote rows without starting the long-running harvester loop."""
    harvester = _get_harvester()
    broker = _get_broker()
    if not broker.is_connected():
        return
    harvester.run_once()


def _held_bid_fields(right: str) -> tuple[str, str]:
    if right == "C":
        return "yes_bid", "yes_bid_vol"
    return "no_bid", "no_bid_vol"


def _held_quote_fields(side: str) -> tuple[str, str]:
    if str(side).upper() == "NO":
        return "no_bid", "no_ask"
    return "yes_bid", "yes_ask"


def _held_mark_from_quote(position: dict, quote: dict) -> float:
    side = str(position.get("side") or "YES").upper()
    bid_key, ask_key = _held_quote_fields(side)
    bid = float(quote.get(bid_key) or 0.0)
    ask = float(quote.get(ask_key) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid or ask or 0.0


def _live_position_summary(positions: list[dict]) -> tuple[int, float]:
    count = 0
    deployed = 0.0
    for position in positions or []:
        qty = float(position.get("qty") or 0.0)
        if qty <= 0:
            continue
        count += 1
        deployed += qty * float(
            position.get("entry_price")
            or position.get("entry")
            or position.get("avg_entry")
            or 0.0
        )
    return count, round(deployed, 4)


def _publish_forecast_lane_state(
    *,
    broker=None,
    snapshot: dict | None = None,
    connected: bool | None = None,
    tradable: bool | None = None,
    health: str | None = None,
    blocked_reason: str | None = None,
    action_needed: str | None = None,
    readiness_state: str | None = None,
) -> None:
    import json

    from config import FORECAST_AUTONOMOUS_ENABLED, FORECAST_LANE_ACTIVE, KALSHI_ENABLED
    from runtime.runtime_state import upsert_lane_state

    broker = broker or _get_broker()
    if connected is None:
        connected = bool(broker.is_connected())

    positions: list[dict] = []
    buying_power_usd = 0.0
    if connected:
        try:
            positions = broker.get_positions()
        except Exception:
            positions = []
        try:
            buying_power_usd = float(broker.get_account_balance() or 0.0)
        except Exception:
            buying_power_usd = 0.0

    positions_open, capital_deployed_usd = _live_position_summary(positions)
    if snapshot and snapshot.get("equity") not in (None, ""):
        buying_power_usd = float(snapshot.get("equity") or buying_power_usd)

    if health is None:
        health = "OK" if connected else "WARN"
    if blocked_reason is None:
        blocked_reason = "" if connected else "broker_disconnected"
    if action_needed is None:
        action_needed = "" if connected else "connect_kalshi"
    if readiness_state is None:
        readiness_state = "OPERATIONAL" if connected else "BROKER_DISCONNECTED"

    payload = {
        "enabled": 1 if KALSHI_ENABLED else 0,
        "active": 1 if FORECAST_LANE_ACTIVE else 0,
        "configured": 1,
        "dashboard_visible": 1,
        "autonomous_enabled": 1 if FORECAST_AUTONOMOUS_ENABLED else 0,
        "manual_allowed": 1,
        "mode": "autonomous" if FORECAST_AUTONOMOUS_ENABLED else "manual",
        "connected": 1 if connected else 0,
        "health": health,
        "blocked_reason": blocked_reason,
        "action_needed": action_needed,
        "readiness_state": readiness_state,
        "positions_open": positions_open,
        "capital_deployed_usd": capital_deployed_usd,
        "buying_power_usd": round(buying_power_usd, 2),
    }
    if tradable is not None:
        payload["tradable"] = 1 if tradable else 0
    if health == "OK":
        payload["last_success_at"] = datetime.now(timezone.utc).isoformat()
    if snapshot is not None:
        payload["snapshot_json"] = json.dumps(snapshot)

    upsert_lane_state("forecast", **payload)


def _weather_contract_yes_probability(
    ticker: str,
    w_data: dict | None,
    *,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
) -> float | None:
    from data.kalshi_weather_monitor import get_contract_weather_data
    from forecast.strategy_engine import blended_weather_yes_probability

    if contract_name or strike is not None or resolution_at or last_trade_at:
        projected = get_contract_weather_data(
            ticker,
            contract_name=contract_name,
            strike=strike,
            resolution_at=resolution_at,
            last_trade_at=last_trade_at,
        )
        if projected:
            w_data = projected

    return blended_weather_yes_probability(
        ticker=ticker,
        w_data=w_data,
        contract_name=contract_name,
        strike=strike,
    )


def _held_model_probability(right: str, model_yes: float | None) -> float | None:
    if model_yes is None:
        return None
    return model_yes if right == "C" else (1.0 - model_yes)


def _remaining_edge_to_resolution(held_model_p: float | None, bid_price: float) -> float | None:
    if held_model_p is None or held_model_p <= 0:
        return None
    from config import KALSHI_FEE_PER_CONTRACT

    return float(held_model_p) - float(bid_price) - float(KALSHI_FEE_PER_CONTRACT)


def _should_time_decay_exit(hours_to_resolution: float, bid_price: float, remaining_edge: float | None) -> bool:
    if remaining_edge is None:
        return False
    from config import (
        KALSHI_EXIT_REDEPLOY_EDGE,
        KALSHI_EXIT_TIME_DECAY_BID_FLOOR,
        KALSHI_EXIT_TIME_DECAY_HOURS,
    )

    return (
        hours_to_resolution <= float(KALSHI_EXIT_TIME_DECAY_HOURS)
        and bid_price >= float(KALSHI_EXIT_TIME_DECAY_BID_FLOOR)
        and remaining_edge <= float(KALSHI_EXIT_REDEPLOY_EDGE)
    )


def _should_model_invalidation_exit(
    entry_held_p: float | None,
    held_model_p: float | None,
    remaining_edge: float | None,
) -> bool:
    if entry_held_p is None or held_model_p is None or remaining_edge is None:
        return False
    from config import (
        KALSHI_EXIT_MODEL_INVALIDATION_DELTA,
        KALSHI_EXIT_REDEPLOY_EDGE,
    )

    return (
        held_model_p <= (entry_held_p - float(KALSHI_EXIT_MODEL_INVALIDATION_DELTA))
        and remaining_edge <= max(0.0, float(KALSHI_EXIT_REDEPLOY_EDGE) - 0.01)
    )


# ── Discovery loop ─────────────────────────────────────────────────────────────


def run_discovery_cycle() -> dict:
    """
    30-min cycle: refresh market/contract list from Kalshi.
    Idempotent — upserts current weather scope and deactivates missing rows.
    """
    with _discovery_lock:
        try:
            from forecast.discovery import run_discovery
            from forecast.db import init_forecast_db

            init_forecast_db()
            broker = _get_broker()
            result = run_discovery(broker=broker)
            stubs = result.get("stubs_persisted", 0)
            contracts = result.get("persisted", 0)
            active_in_db = result.get("active_in_db", 0)
            logger.info(
                f"[ForecastRunner] Discovery: found={result['found']} "
                f"persisted={contracts} "
                f"stubs={stubs} "
                f"active={active_in_db}"
            )
            try:
                _connected = bool(broker.is_connected())
                _snapshot = _forecast_runtime_snapshot(
                    connected=_connected,
                    contracts=contracts,
                    stubs=stubs,
                    active_markets=active_in_db
                )
                _publish_forecast_lane_state(
                    broker=broker,
                    connected=bool(_snapshot["connected"]),
                    tradable=bool(_snapshot["tradable"]),
                    health=_snapshot["health"],
                    blocked_reason=_snapshot["blocked_reason"],
                    action_needed=_snapshot["action_needed"],
                    readiness_state=_snapshot["readiness_state"],
                )
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"[ForecastRunner] Discovery cycle error: {e}")
            try:
                _publish_forecast_lane_state(
                    health="ERROR",
                    blocked_reason=str(e)[:160],
                    action_needed="inspect_forecast_runner",
                )
            except Exception:
                pass
            return {"found": 0, "persisted": 0, "errors": [str(e)]}


def run_execution_cycle(
    bankroll: float = 100.0,
    *,
    refresh_quotes: bool = True,
    sync_resolutions: bool = True,
    run_rbi: bool = False,
) -> dict:
    """
    Single-pass Lean execution cycle.

    Order of operations:
      1. Ensure DB + broker connectivity
      2. Discovery once
      3. Expire stale/resolved active rows
      4. Quote refresh once
      5. Strategy evaluation once
      6. Position monitor once
      7. Resolution sync / cache refresh / optional RBI
    """
    from forecast.db import init_forecast_db
    from config import DB_PATH, FORECAST_LANE_ACTIVE, KALSHI_ENABLED

    if not KALSHI_ENABLED:
        logger.warning("[ForecastRunner] Kalshi lane disabled. Skipping execution cycle.")
        try:
            _publish_forecast_lane_state(
                connected=False,
                tradable=False,
                health="WARN",
                blocked_reason="kalshi_disabled",
                action_needed="enable_kalshi",
                readiness_state="DISABLED",
            )
        except Exception:
            pass
        return {
            "broker_connected": False,
            "discovery": {},
            "entries": 0,
            "resolution_sync": {},
            "skipped_reason": "kalshi_disabled",
        }

    if not FORECAST_LANE_ACTIVE:
        logger.warning("[ForecastRunner] Forecast lane inactive. Skipping execution cycle.")
        try:
            _publish_forecast_lane_state(
                connected=False,
                tradable=False,
                health="WARN",
                blocked_reason="forecast_lane_inactive",
                action_needed="enable_forecast_lane",
                readiness_state="INACTIVE",
            )
        except Exception:
            pass
        return {
            "broker_connected": False,
            "discovery": {},
            "entries": 0,
            "resolution_sync": {},
            "skipped_reason": "forecast_lane_inactive",
        }

    db_path = DB_PATH
    init_forecast_db(db_path=db_path)

    broker = _get_broker()
    connected = broker.is_connected()
    if not connected:
        try:
            connected = bool(broker.connect())
        except Exception as exc:
            logger.error("[ForecastRunner] Broker bootstrap failed: %s", exc)
            connected = False

    discovery = run_discovery_cycle()
    universe_cleanup = {}

    try:
        from forecast.db import deactivate_expired_contracts

        expired = deactivate_expired_contracts(db_path=db_path)
        universe_cleanup["expired_contracts"] = expired
        if expired:
            logger.info(
                "[ForecastRunner] Deactivated %s expired/resolved contract row(s) before quote refresh.",
                expired,
            )
    except Exception as exc:
        logger.warning("[ForecastRunner] Active-universe cleanup failed: %s", exc)
        universe_cleanup["error"] = str(exc)

    weather_sync = {}
    try:
        from forecast.db import get_active_contracts
        from data.kalshi_weather_monitor import ensure_weather_data

        active_contracts = get_active_contracts(db_path=db_path)
        weather_symbols = [
            str(contract.get("local_symbol") or "")
            for contract in active_contracts
            if "KXHIGH" in str(contract.get("local_symbol") or "")
            or "KXLOW" in str(contract.get("local_symbol") or "")
            or "KXRAIN" in str(contract.get("local_symbol") or "")
        ]
        weather_sync = ensure_weather_data(weather_symbols)
        if weather_sync.get("refreshed_series"):
            logger.info("[ForecastRunner] Weather cold-start hydration: %s", weather_sync)
    except Exception as exc:
        logger.warning("[ForecastRunner] Weather hydration failed: %s", exc)

    if refresh_quotes:
        try:
            _refresh_quotes_once()
        except Exception as exc:
            logger.warning("[ForecastRunner] One-shot quote refresh failed: %s", exc)

    entries = run_strategy_cycle(bankroll=bankroll)
    run_position_monitor()

    resolution_summary = {}
    if sync_resolutions:
        try:
            from forecast.resolution_sync import sync_forecast_resolutions

            resolution_summary = sync_forecast_resolutions(db_path=db_path)
        except Exception as exc:
            logger.warning("[ForecastRunner] Resolution sync failed: %s", exc)
            resolution_summary = {"error": str(exc)}

    snapshot = {}
    try:
        snapshot = _cache_forecast_state() or {}
    except Exception as exc:
        logger.warning("[ForecastRunner] Cache refresh failed: %s", exc)
        try:
            _publish_forecast_lane_state(
                broker=broker,
                connected=connected,
                health="WARN",
                blocked_reason="cache_refresh_failed",
                action_needed="inspect_forecast_state_cache",
                readiness_state="DEGRADED",
            )
        except Exception:
            pass

    if run_rbi:
        try:
            from learning.weather_rbi import run_weather_rbi

            run_weather_rbi()
        except Exception as exc:
            logger.warning("[ForecastRunner] RBI cycle failed: %s", exc)

    summary = {
        "broker_connected": connected,
        "discovery": discovery,
        "universe_cleanup": universe_cleanup,
        "weather_sync": weather_sync,
        "entries": len(entries),
        "resolution_sync": resolution_summary,
        "positions_open": len((snapshot or {}).get("positions", [])),
    }
    logger.info("[ForecastRunner] Single-pass execution summary: %s", summary)
    return summary


# ── Strategy evaluation loop ───────────────────────────────────────────────────


def run_strategy_cycle(bankroll: float = 100.0) -> list[dict]:
    """
    5-min cycle: evaluate all active contracts, submit approved entries.

    Returns list of entry results (empty if nothing qualified).
    """
    with _eval_lock:
        from config import FORECAST_LANE_ACTIVE, KALSHI_ENABLED

        if not KALSHI_ENABLED or not FORECAST_LANE_ACTIVE:
            logger.warning(
                "[ForecastRunner] Strategy cycle skipped: kalshi_enabled=%s forecast_lane_active=%s",
                KALSHI_ENABLED,
                FORECAST_LANE_ACTIVE,
            )
            return []

        logger.info(f"[ForecastRunner] Starting strategy cycle (bankroll=${bankroll:.2f})...")
        entries = []
        try:
            from execution.kalshi_execution_controller import (
                KalshiExecutionController,
                TradeIntent,
            )
            from forecast.db import get_active_contracts, get_bars
            from forecast.market_snapshot import build_market_snapshots
            from forecast.quote_harvester import get_paired_quotes
            from forecast.strategy_engine import (
                MAX_CONCURRENT_POSITIONS,
                MAX_DEPLOYED_PCT,
                evaluate_market_snapshots,
            )

            broker = _get_broker()
            if broker.is_connected():
                try:
                    broker.sync_positions()
                except Exception as exc:
                    logger.warning("[ForecastRunner] Broker position sync failed before eval: %s", exc)
            
            # ADVERSARY FIX #7: Dynamic Bankroll Fetch
            # Anchoring to startup balance throws off Kelly risk math.
            if broker.is_connected():
                try:
                    live_balance = broker.get_account_balance()
                    if live_balance > 0:
                        bankroll = live_balance
                except Exception as e:
                    logger.warning(f"[ForecastRunner] Dynamic bankroll fetch failed: {e}. Using default {bankroll}")

            active = get_active_contracts()
            if not active:
                logger.info("[ForecastRunner] No active contracts in DB — skip eval")
                return []

            # Current position state
            open_positions = broker.get_positions() if broker.is_connected() else []
            open_count = len(open_positions)
            
            # v19.1.12: Opportunistic Swap Evaluation
            # We allow evaluation even at cap to see if better plays exist.
            is_at_cap = open_count >= MAX_CONCURRENT_POSITIONS

            # Deployed capital fraction
            deployed_value = sum(
                (p.get("entry_price") or 0) * (p.get("qty") or 0)
                for p in open_positions
            )
            deployed_pct = min(1.0, deployed_value / max(bankroll, 1.0))

            # v19.5.2: Sovereign Salvage & Take-Profit (Unblocked)
            # We run these BEFORE the capital guard so we can free up capital.
            if open_positions:
                from data.kalshi_weather_monitor import get_weather_data
                from forecast.db import mark_forecast_position_closed
                from forecast.db import get_contract_metadata
                
                for pos in open_positions:
                    ticker = pos.get("local_symbol", "")
                    right = pos.get("right", "C")
                    side = pos.get("side", "YES").upper()
                    w_data = get_weather_data(ticker)
                    quote = broker.get_quote(ticker)
                    bid_key, _bid_vol_key = _held_bid_fields(right)
                    current_price = float(quote.get(bid_key) or pos.get("entry_price") or 0.50)
                    
                    # 1. Sovereign Salvage (Dead-Trade Purge)
                    contract_meta = get_contract_metadata(ticker)
                    contract_name = (
                        str(contract_meta.get("contract_name") or "")
                        if contract_meta
                        else ""
                    )
                    strike = (
                        float(contract_meta.get("strike") or 0.0)
                        if contract_meta and contract_meta.get("strike") is not None
                        else None
                    )
                    resolution_at = (
                        str(contract_meta.get("resolution_at") or "")
                        if contract_meta
                        else ""
                    )
                    model_yes = _weather_contract_yes_probability(
                        ticker,
                        w_data,
                        contract_name=contract_name,
                        strike=strike,
                        resolution_at=resolution_at,
                        last_trade_at=str(pos.get("last_trade_at") or ""),
                    )
                    if model_yes is not None:
                        live_prob = model_yes if side == "YES" else (1.0 - model_yes)
                        if live_prob < 0.15:
                            logger.warning(f"[SovereignSalvage] PURGING toxic position {ticker} (p={live_prob:.1%})")
                            flatten_res = broker.flatten_position(
                                ticker,
                                right,
                                pos.get("qty", 0),
                            )
                            if flatten_res.get("status") == "executed":
                                mark_forecast_position_closed(ticker, exit_type="salvage_exit")
                            log_event("INFO", "ForecastRunner", f"Salvage: Purged {ticker} at {live_prob:.1%}")
                            # Reset flags to allow eval to proceed in this tick
                            is_at_cap = False
                            deployed_pct = 0.0
                            break

                    # 2. Institutional Take-Profit (70% Lock-in)
                    entry_price = float(pos.get("entry_price") or 0.50)
                    max_gain = 1.0 - entry_price
                    target_gain = max_gain * 0.70
                    
                    if (current_price - entry_price) >= target_gain:
                        logger.info(f"[SovereignHUD] TAKE-PROFIT: Locking in 70% gain for {ticker} (Price={current_price:.2f})")
                        flatten_res = broker.flatten_position(
                            ticker,
                            pos.get("right", "C"),
                            pos.get("qty", 0),
                        )
                        if flatten_res.get("status") == "executed":
                            mark_forecast_position_closed(ticker, exit_type="take_profit")
                        log_event("INFO", "ForecastRunner", f"TakeProfit: Locked {ticker} at {current_price:.2f}")
                        is_at_cap = False
                        deployed_pct = 0.0
                        break

            if deployed_pct >= MAX_DEPLOYED_PCT:
                logger.warning(
                    f"[ForecastRunner] Deployed cap hit ({deployed_pct:.1%}/{MAX_DEPLOYED_PCT:.1%}) — skip eval"
                )
                return []

            buying_power_usd = bankroll

            # Open event families (to detect same-event exposure)
            from collections import defaultdict
            open_event_families_counts = defaultdict(int)
            for p in open_positions:
                family = p.get("local_symbol", "").split("-")[0]
                open_event_families_counts[family] += 1

            def _get_bars_fn(contract_id: int, interval: str) -> list[dict]:
                return get_bars(contract_id, interval, limit=200)

            def _get_quotes_fn(
                market_id: int, strike: float, last_trade_at: str
            ) -> dict:
                return get_paired_quotes(market_id, strike, last_trade_at)

            # v18.34: Dual-Path Macro Context Injection
            macro_ctx = {}
            try:
                from forecast.strategy_engine import _get_macro_context
                macro_ctx = _get_macro_context()
            except Exception:
                pass

            snapshots = build_market_snapshots(
                active,
                get_bars_fn=_get_bars_fn,
                get_quotes_fn=_get_quotes_fn,
            )
            candidates = evaluate_market_snapshots(
                snapshots=snapshots,
                bankroll=bankroll,
                deployed_pct=deployed_pct,
                open_positions_count=open_count,
                open_event_families=open_event_families_counts,
                macro_context=macro_ctx,
                open_positions=open_positions,
            )
            execution_controller = KalshiExecutionController(broker)

            # v19.1.10: Sovereign Instrumentation (Discovery Layer)
            try:
                from monitoring import metrics
                for cand in candidates:
                    res = cand["result"]
                    sym = cand["contract"].get("local_symbol", "UNKNOWN")
                    # Ensemble prob is stored in confidence for weather family
                    if res.strategy_family == "weather_ensemble":
                        # We use the raw confidence before convergence multiplier for prob
                        # Actually confidence = ensemble_prob * conv_mult
                        # Let's just push the confidence as it represents the 'calibrated prob'
                        metrics.WEATHER_ENSEMBLE_PROB_GAUGE.labels(ticker=sym).set(res.confidence)
            except Exception as _m_err:
                logger.debug(f"Metrics update failed: {_m_err}")

            if not candidates:
                logger.info("[ForecastRunner] No trade candidates qualified in this cycle.")

            for candidate in candidates:
                result = candidate["result"]
                contract = candidate["contract"]
                local_sym = contract.get("local_symbol", "")

                # Only enter if econ approved AND contracts > 0
                if not result.econ_approved or result.position_contracts <= 0:
                    veto_msg = f"[ForecastRunner] {local_sym} vetoed: {result.veto_reason or 'sizing_zero'}"
                    logger.debug(veto_msg)
                    # SRE FIX: Upgrade to WARNING if it's an economic veto so incident tracker picks it up
                    lvl = "WARNING" if not result.econ_approved else "INFO"
                    log_event(lvl, "ForecastRunner", veto_msg)
                    continue

                # v19.1.12: Concurrency & Swap Logic
                if is_at_cap:
                    # Identify worst open position by EV
                    # For simplicity in v1, we compare Candidate EV vs 0.0 
                    # unless we can fetch the 'live EV' of open positions.
                    # SWAP RULE: Candidate EV must be > 0.15 to justify a swap churn.
                    if result.ev < 0.15:
                        logger.warning(f"[ForecastRunner] {local_sym} (ev={result.ev:.4f}) skipped: at cap and EV < 0.15 swap floor.")
                        continue
                    
                    # Logic: Flatten the absolute WORST open position
                    # We sort open positions by their 'stale' entry EV or just pick one.
                    # Best approach: find the one with the lowest current EV if possible.
                    # For this MVP: Flatten the oldest position to make room for high-alpha new play.
                    worst_pos = open_positions[0] # Simplest: FIFO churn
                    worst_sym = worst_pos.get("local_symbol", "UNKNOWN")
                    
                    logger.info(f"[ForecastRunner] SWAP TRIGGERED: Flattening {worst_sym} to make room for {local_sym} (ev={result.ev:.4f})")
                    flatten_res = broker.flatten_position(
                        worst_sym,
                        worst_pos.get("right", "C"),
                        worst_pos.get("qty", 0),
                    )
                    if flatten_res.get("status") == "executed":
                        from forecast.db import mark_forecast_position_closed
                        mark_forecast_position_closed(worst_sym, exit_type="swap_exit")
                    log_event("INFO", "ForecastRunner", f"Swap: Flattened {worst_sym} for {local_sym}")

                # Hard duplicate guard: no same-contract double-down
                key = f"{contract.get('local_symbol')}_{contract.get('right')}"
                existing = (
                    broker.get_position(
                        contract.get("local_symbol", ""),
                        contract.get("right", "C"),
                    )
                    if broker.is_connected()
                    else None
                )

                if existing:
                    logger.debug(
                        f"[ForecastRunner] Duplicate guard: {key} already open"
                    )
                    continue

                try:
                    forecast_yes_prob = result.q_hat
                    if result.strategy_family == "weather_ensemble":
                        forecast_yes_prob = (
                            result.confidence if result.side == "YES" else (1.0 - result.confidence)
                        )
                    forecast_yes_prob = max(0.0, min(1.0, float(forecast_yes_prob)))

                    trade_intent = TradeIntent(
                        contract=contract,
                        result=result,
                        bankroll=bankroll,
                        buying_power_usd=buying_power_usd,
                        market_snapshot=candidate.get("snapshot"),
                    )
                    execution_plan = execution_controller.plan_entry(trade_intent)
                    if execution_plan.status != "ready":
                        logger.info(
                            "[ForecastRunner] Entry plan blocked for %s (%s)",
                            contract.get("local_symbol"),
                            execution_plan.reason,
                        )
                        log_event(
                            "WARNING",
                            "ForecastRunner",
                            f"[ForecastRunner] {contract.get('local_symbol')} execution_blocked: {execution_plan.reason}",
                        )
                        continue

                    entry_result = execution_controller.execute_plan(
                        execution_plan,
                        forecast_yes_prob=forecast_yes_prob,
                        model_prob_gfs=result.model_prob_gfs,
                        model_prob_ecmwf=result.model_prob_ecmwf,
                        weather_mode=result.weather_mode or None,
                        forecast_hours_to_resolution=result.hours_to_resolution,
                    )
                    
                    if entry_result.get("status") == "executed":
                        actual_price = entry_result.get("price") or execution_plan.limit_price
                        actual_qty = int(entry_result.get("qty") or execution_plan.executable_qty)
                        entry_msg = (
                            f"[ForecastRunner] Entry: {contract.get('local_symbol')} "
                            f"{result.side.upper()} x{actual_qty} @ {actual_price} "
                            f"(ev={result.ev:.4f})"
                        )
                        log_event("INFO", "ForecastRunner", entry_msg)
                        
                        try:
                            from forecast.db import insert_forecast_position
                            insert_forecast_position(
                                ticker=contract.get("local_symbol", ""),
                                qty=actual_qty,
                                entry_price=actual_price,
                                side=result.side.upper()
                            )
                        except Exception as _db_err:
                            logger.error(f"[ForecastRunner] DB insertion error: {_db_err}")

                        try:
                            from notifications.notification_engine import notify_trade_open
                            notify_trade_open(
                                symbol=contract.get("local_symbol", ""),
                                direction=result.side.upper(),
                                size_usd=actual_qty * actual_price,
                                entry_price=actual_price,
                                score=result.ev,
                                top_3=result.top_factors or [],
                                features={},
                                regime="KALSHI",
                            )
                        except Exception as _ne_err:
                            logger.error(f"[ForecastRunner] Notification error: {_ne_err}")
                        entries.append(
                            {
                                "contract": contract,
                                "result": result,
                                "entry": entry_result,
                            }
                        )
                        logger.info(
                            f"[ForecastRunner] ENTERED {contract.get('local_symbol')} "
                            f"{result.side} x{actual_qty} @ {actual_price:.4f} "
                            f"| strategy={result.strategy_family} ev={result.ev:.4f} "
                            f"q_hat={result.q_hat:.4f}"
                        )
                        from config import KALSHI_FEE_PER_CONTRACT
                        buying_power_usd = max(
                            0.0,
                            buying_power_usd
                            - (actual_qty * (actual_price + float(KALSHI_FEE_PER_CONTRACT))),
                        )
                    else:
                        outcome_msg = (
                            f"[ForecastRunner] {contract.get('local_symbol')} execution_result: "
                            f"{entry_result.get('status') or 'unknown'}"
                        )
                        reason = str(entry_result.get("execution_reason") or "").strip()
                        if reason:
                            outcome_msg = f"{outcome_msg} ({reason})"
                        log_event("WARNING", "ForecastRunner", outcome_msg)
                        logger.info(
                            "[ForecastRunner] Entry not booked into local truth for %s "
                            "(status=%s order_id=%s reason=%s)",
                            contract.get("local_symbol"),
                            entry_result.get("status"),
                            entry_result.get("order_id"),
                            entry_result.get("execution_reason"),
                        )
                        if entry_result.get("status") == "too_many_requests":
                            logger.warning(
                                "[ForecastRunner] Kalshi rate limit hit after %s; halting new entries until next cycle.",
                                contract.get("local_symbol"),
                            )
                            break
                        if entry_result.get("status") == "rate_limit_cooldown":
                            logger.warning(
                                "[ForecastRunner] Local rate-limit cooldown active; halting new entries until next cycle."
                            )
                            break
                except Exception as e:
                    logger.error(
                        f"[ForecastRunner] Entry failed for {contract.get('local_symbol')}: {e}"
                    )

        except Exception as e:
            logger.error(f"[ForecastRunner] Strategy cycle error: {e}")

        return entries


# ── Position monitor loop ──────────────────────────────────────────────────────


def run_position_monitor() -> None:
    """
    30-sec cycle: check open positions, flatten resolved contracts.
    v19.1.10: Self-healing Broker Reconciliation.
    """
    try:
        broker = _get_broker()
        if not broker.is_connected():
            return
        try:
            broker.sync_positions()
        except Exception as exc:
            logger.warning("[ForecastRunner] Broker position sync failed before monitor: %s", exc)
            return

        # 1. Pull Current Broker Reality
        broker_positions = broker.get_positions()
        
        # 2. Pull Local DB Expectation and reconcile it to broker reality
        from config import DB_PATH

        db_path = DB_PATH
        from forecast.db import (
            get_open_forecast_positions,
            reconcile_forecast_positions,
        )

        recon = reconcile_forecast_positions(broker_positions, db_path=db_path)
        if recon.get("adopted"):
            logger.info(
                "[Sovereign Recon] Auto-adopted %s broker position(s) into DB.",
                recon["adopted"],
            )
        if recon.get("closed"):
            logger.info(
                "[Sovereign Recon] Closed %s stale DB position(s) missing at broker.",
                recon["closed"],
            )
        db_positions = get_open_forecast_positions(db_path=db_path)

        # ── v19.1.10: Standard Flattening / Exit Protocol ───────────────────
        now = datetime.now(timezone.utc)
        for pos in broker_positions:
            local_symbol = pos.get("local_symbol", "")
            right = pos.get("right", "C")
            qty = pos.get("qty", 0)
            last_trade = pos.get("last_trade_at", "")
            entered_at = pos.get("entered_at", "")

            if not qty:
                continue

            # Check resolution: has the contract expired?
            resolved = False
            hours_to_resolution = 0.0
            if last_trade:
                try:
                    # v19.1.6: Support ISO format and standard format
                    from dateutil.parser import parse as date_parse
                    expiry = date_parse(last_trade)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    hours_to_resolution = max(
                        0.0, (expiry - now).total_seconds() / 3600.0
                    )
                    if now >= expiry:
                        resolved = True
                        logger.info(
                            f"[ForecastRunner] Contract expired: {local_symbol} — flattening"
                        )
                except Exception:
                    # Fallback to standard formats
                    try:
                        fmt = "%Y%m%d %H:%M:%S" if " " in last_trade else "%Y%m%d"
                        expiry = datetime.strptime(last_trade, fmt).replace(
                            tzinfo=timezone.utc
                        )
                        hours_to_resolution = max(
                            0.0, (expiry - now).total_seconds() / 3600.0
                        )
                        if now >= expiry:
                            resolved = True
                    except: pass

            # v19.1.9: Sovereign Exit Protocol (Profit Protection)
            try:
                q = broker.get_quote(local_symbol)
                bid_key, bid_vol_key = _held_bid_fields(right)
                if q and q.get(bid_key):
                    bid_price = float(q[bid_key])

                    # 1. Take Profit: Narrow Bin (Double-sided risk)
                    # We assume narrow if strike looks like a mid-range or 'between'
                    # For now, we'll use a conservative 85c trigger for all weather
                    is_weather = any(
                        token in local_symbol
                        for token in ("KXHIGH", "KXLOW", "KXRAIN", "KXSNOW", "KXWIND")
                    )

                    if is_weather:
                        from forecast.db import get_contract_metadata
                        from forecast.weather_contracts import resolve_weather_contract
                        from data.kalshi_weather_monitor import get_contract_weather_data

                        contract_meta = get_contract_metadata(local_symbol)
                        contract_name = (
                            str(contract_meta.get("contract_name") or "")
                            if contract_meta
                            else ""
                        )
                        strike = (
                            float(contract_meta.get("strike") or 0.0)
                            if contract_meta and contract_meta.get("strike") is not None
                            else None
                        )
                        semantics = resolve_weather_contract(
                            local_symbol,
                            contract_name=contract_name,
                            strike=strike,
                        )

                        # Get current model probability
                        w_data = get_contract_weather_data(
                            local_symbol,
                            contract_name=contract_name,
                            strike=strike,
                            resolution_at=str(contract_meta.get("resolution_at") or "") if contract_meta else "",
                            last_trade_at=str(last_trade or ""),
                        )
                        if not w_data or semantics is None or semantics.ambiguous:
                            model_yes = 0.0
                            held_model_p = 0.0
                            remaining_edge = None
                            entry_held_p = None
                            intraday = {}
                            metar_temp = None
                            hrrr_high = None
                            daily_max = None
                            daily_min = None
                        else:
                            model_yes = (
                                _weather_contract_yes_probability(
                                    local_symbol,
                                    w_data,
                                    contract_name=contract_name,
                                    strike=strike,
                                    resolution_at=str(contract_meta.get("resolution_at") or "") if contract_meta else "",
                                    last_trade_at=str(last_trade or ""),
                                )
                                or 0.0
                            )
                            held_model_p = _held_model_probability(right, model_yes) or 0.0
                            entry_model_yes = pos.get("forecast_yes_prob")
                            entry_held_p = None
                            if entry_model_yes is not None:
                                try:
                                    entry_yes = float(entry_model_yes)
                                    entry_held_p = _held_model_probability(right, entry_yes)
                                except (TypeError, ValueError):
                                    entry_held_p = None
                            remaining_edge = _remaining_edge_to_resolution(held_model_p, bid_price)
                            intraday = w_data.get("intraday", {})
                            metar_temp = intraday.get("metar_temp")
                            hrrr_high = intraday.get("hrrr_high")
                            daily_max = intraday.get("daily_max", metar_temp)
                            daily_min = intraday.get("daily_min", metar_temp)

                        # RULE 1: Narrow Bin Take-Profit (85c)
                        # If market prices us at 85% and it's a weather bin, take the money.
                        if bid_price >= 0.85:
                            logger.info(f"[Sovereign Exit] TP Triggered: {local_symbol} at {bid_price:.2f} (85c Floor)")
                            resolved = True

                        # RULE 2: Model Invalidation (Stop-Loss)
                        # If our own model prob drops below 50%, the thesis is dead.
                        elif held_model_p < 0.50 and held_model_p > 0:
                            logger.warning(
                                f"[Sovereign Exit] SL Triggered: {local_symbol} "
                                f"held_p={held_model_p:.2f} < 0.50"
                            )
                            resolved = True

                        # RULE 3: Model Invalidation Delta
                        # If the live model has moved materially against the entry
                        # and the remaining edge is mostly gone, redeploy capital.
                        elif _should_model_invalidation_exit(
                            entry_held_p,
                            held_model_p,
                            remaining_edge,
                        ):
                            logger.warning(
                                f"[Sovereign Exit] INVALIDATION: {local_symbol} "
                                f"entry_p={entry_held_p:.2f} live_p={held_model_p:.2f} "
                                f"remaining_edge={remaining_edge:.3f}"
                            )
                            resolved = True

                        # RULE 4: Time-Decay Redeploy
                        # When the market has already priced most of the thesis and
                        # little edge remains, redeploy instead of waiting for the
                        # final settlement jump.
                        elif _should_time_decay_exit(
                            hours_to_resolution,
                            bid_price,
                            remaining_edge,
                        ):
                            logger.info(
                                f"[Sovereign Exit] TIME-DECAY REDEPLOY: {local_symbol} "
                                f"hours_to_res={hours_to_resolution:.1f} bid={bid_price:.2f} "
                                f"remaining_edge={remaining_edge:.3f}"
                            )
                            resolved = True
                        
                        # ── v19.1.10: Intraday Precinct (METAR/HRRR) ─────────
                        # v19.1.10: Sovereign Instrumentation (Intraday Layer)
                        try:
                            from monitoring import metrics
                            diff_anchor = None
                            if semantics is not None:
                                if (
                                    semantics.comparator == "between"
                                    and semantics.lower_bound is not None
                                    and semantics.upper_bound is not None
                                ):
                                    diff_anchor = (semantics.lower_bound + semantics.upper_bound) / 2.0
                                elif semantics.threshold is not None:
                                    diff_anchor = semantics.threshold
                            if metar_temp is not None and diff_anchor is not None:
                                metrics.WEATHER_METAR_DIFF_GAUGE.labels(ticker=local_symbol).set(metar_temp - diff_anchor)
                            if hrrr_high is not None and diff_anchor is not None:
                                metrics.WEATHER_HRRR_DIFF_GAUGE.labels(ticker=local_symbol).set(hrrr_high - diff_anchor)
                        except Exception as _m_err:
                            logger.debug(f"Metrics update failed: {_m_err}")
                        
                        if (
                            right == "C"
                            and semantics is not None
                            and not semantics.ambiguous
                            and semantics.mode in {"HIGH", "LOW"}
                            and semantics.comparator == "between"
                            and semantics.lower_bound is not None
                            and semantics.upper_bound is not None
                            and (daily_max is not None or daily_min is not None)
                        ):
                            # 1. BUST EXIT (Salvage Capital)
                            # If the daily high/low has already breached our bracket, the YES thesis is dead.
                            is_high = semantics.mode == "HIGH"
                            limit_lower = float(semantics.lower_bound)
                            limit_upper = float(semantics.upper_bound)
                            
                            # HIGH YES Bust: The record high for today is already above our bracket ceiling.
                            if is_high and daily_max >= limit_upper:
                                logger.warning(f"[Sovereign Precinct] BUST EXIT: {local_symbol} Day-High {daily_max}F > limit {limit_upper}F. Salvaging capital.")
                                resolved = True
                            
                            # LOW YES Bust: The record low for today is already below our bracket floor.
                            elif not is_high and daily_min < limit_lower:
                                logger.warning(f"[Sovereign Precinct] BUST EXIT: {local_symbol} Day-Low {daily_min}F < limit {limit_lower}F. Salvaging capital.")
                                resolved = True

                            # 2. LOCK EXIT (Early Profit Capture)
                            # If we are in the winning zone and heating time is depleted.
                            import pytz
                            from data.kalshi_weather_monitor import STATIONS
                            
                            city_match = None
                            for k, v in STATIONS.items():
                                if any(local_symbol.startswith(s) for s in v.get("series", [])):
                                    city_match = k
                                    break

                            if city_match:
                                loc_data = STATIONS[city_match]
                                tz = pytz.timezone(loc_data.get("tz", "UTC"))
                                local_now = datetime.now(tz)
                                local_hour = local_now.hour
                                
                                # If after 4 PM local and currently within limits
                                in_zone = False
                                if is_high:
                                    in_zone = metar_temp is not None and limit_lower <= metar_temp < limit_upper
                                else:
                                    in_zone = metar_temp is not None and limit_lower <= metar_temp < limit_upper

                                # v19.1.10: Precision Lock (Front-run the whole degree)
                                # v19.1.11: Restricted to HIGH markets; LOW markets resolve at night.
                                if is_high and in_zone and local_hour >= 16:
                                    # High probability of 'locked' result
                                    # If within 0.2F of limit and trend is flat/reversing, take 94c+
                                    if bid_price >= 0.94:
                                        logger.info(f"[Sovereign Precinct] LOCK EXIT: {local_symbol} at {bid_price:.2f} (After 4PM local, in zone).")
                                        resolved = True
                                    elif is_high and metar_temp is not None and metar_temp >= (limit_upper - 0.2) and bid_price >= 0.90:
                                         logger.info(f"[Sovereign Precinct] PRECISION LOCK: {local_symbol} at {bid_price:.2f} (0.2F from limit).")
                                         resolved = True

                                # v19.1.11: Midnight Spike Guard
                                # If late evening (8 PM+) and HRRR predicts a spoiler max/min
                                if not resolved and local_hour >= 20 and bid_price >= 0.90:
                                    if hrrr_high is not None:
                                        # HIGH YES Spike: HRRR predicts spike above bracket or drop below
                                        if is_high and (hrrr_high >= limit_upper or hrrr_high < limit_lower):
                                            logger.warning(f"[Sovereign Precinct] SPIKE GUARD: {local_symbol} dumping {bid_price:.2f} due to HRRR spoiler {hrrr_high}F.")
                                            resolved = True
                            # 3. TREND DIVERGENCE / SALVAGE (Capital Salvage)
                            # If HRRR (3km resolution) is predicting a result that makes winning impossible
                            if not resolved and hrrr_high is not None:
                                # High YES Salvage: HRRR predicts a clear miss below our bracket.
                                if is_high and hrrr_high < (limit_lower - 1.5) and bid_price > 0.05:
                                    logger.warning(f"[Sovereign Precinct] SALVAGE EXIT: {local_symbol} HRRR predicts max {hrrr_high}F. Cutting for {bid_price:.2f}.")
                                    resolved = True
                                # High YES Divergence: HRRR projects a bracket miss.
                                elif is_high and (hrrr_high < limit_lower or hrrr_high >= limit_upper):
                                    logger.warning(f"[Sovereign Precinct] TREND EXIT: {local_symbol} HRRR predicts {hrrr_high}F vs bracket start {limit_lower}F. Cutting loss.")
                                    resolved = True

            except Exception as e:
                logger.debug(f"Exit Protocol check failed for {local_symbol}: {e}")

            # Dead-money backstop: > 96h open
            if not resolved and entered_at:
                try:
                    entered = datetime.fromisoformat(entered_at.replace("Z", "+00:00"))
                    hours_open = (now - entered).total_seconds() / 3600.0
                    if hours_open > 96:
                        resolved = True
                        logger.warning(
                            f"[ForecastRunner] Dead-money exit: {local_symbol} "
                            f"open {hours_open:.1f}h > 96h backstop"
                        )
                except Exception:
                    pass

            if resolved:
                try:
                    # SRE FIX: Dynamic Liquidity-Checked Limit Orders
                    quote = broker.get_quote(local_symbol)
                    bid_key, bid_vol_key = _held_bid_fields(right)
                    current_bid = float(quote.get(bid_key, 0) or 0)
                    current_bid_vol = int(quote.get(bid_vol_key, 0) or 0)

                    if current_bid > 0.01 and current_bid_vol >= qty:
                        flatten_res = broker.place_sell_order(
                            contract_dict={"local_symbol": local_symbol},
                            qty=qty,
                            limit_price=current_bid,
                            type="limit",
                            side="yes" if right == "C" else "no",
                            reason="sovereign_exit_limit"
                        )
                        if flatten_res.get("status") != "executed":
                            logger.warning(
                                f"Limit exit for {local_symbol} did not execute "
                                f"(status={flatten_res.get('status')}). Falling back to market."
                            )
                            flatten_res = broker.flatten_position(
                                local_symbol,
                                right,
                                qty,
                                reason="limit_unfilled_market_fallback",
                            )
                    else:
                        logger.warning(
                            f"Exit limit degraded for {local_symbol}: "
                            f"bid={current_bid:.2f} bid_vol={current_bid_vol} qty={qty}. "
                            "Falling back to market."
                        )
                        flatten_res = broker.flatten_position(
                            local_symbol,
                            right,
                            qty,
                            reason="illiquid_limit_market_fallback",
                        )

                    if flatten_res.get("status") != "executed":
                        logger.warning(
                            f"Exit still pending for {local_symbol}; leaving position open "
                            f"(status={flatten_res.get('status')})."
                        )
                        continue

                    mark_forecast_position_closed(
                        local_symbol,
                        exit_type="resolved_or_expired",
                        db_path=db_path,
                    )

                    # Notify via Telegram/DB
                    if flatten_res.get("order_id") != "ERR":
                        try:
                            from notifications.notification_engine import notify_trade_close
                            pnl_usd = flatten_res.get("pnl_usd", 0.0)
                            entry_price = flatten_res.get("entry_price", 0.0)
                            pnl_pct = (pnl_usd / (entry_price * qty)) if (entry_price > 0 and qty > 0) else 0.0
                            
                            notify_trade_close(
                                symbol=local_symbol,
                                direction="YES" if right == "C" else "NO",
                                pnl_usd=pnl_usd,
                                pnl_pct=pnl_pct,
                                exit_type="resolved_or_expired",
                                top_3=[],
                                features={},
                                regime="KALSHI",
                                score=0.0
                            )
                        except Exception as _ne_err:
                            logger.error(f"[ForecastRunner] Notification error: {_ne_err}")
                            
                except Exception as e:
                    logger.error(f"[ForecastRunner] Flatten failed {local_symbol}: {e}")

    except Exception as e:
        logger.error(f"[ForecastRunner] Position monitor error: {e}")

    # Heartbeat
    try:
        from runtime.runtime_state import mark_lane_heartbeat
        mark_lane_heartbeat("forecast")
    except Exception:
        pass


def _send_daily_token_burn_report():
    """Forensic report for daily LLM token consumption."""
    try:
        import sqlite3 as _sq
        import time as _time
        from config import DB_PATH as _DB_PATH
        from notifications.telegram_bot import send_message as _tg
        
        _cutoff = _time.time() - 86400
        with _sq.connect(_DB_PATH, timeout=30.0) as _conn:
            _conn.row_factory = _sq.Row
            rows = _conn.execute(
                """SELECT module, SUM(prompt_tokens) as p, SUM(completion_tokens) as c 
                   FROM api_telemetry WHERE ts >= ? 
                   GROUP BY module ORDER BY (p+c) DESC""", 
                (_cutoff,)
            ).fetchall()

            if not rows:
                return

            total_tokens = sum(int(r["p"] or 0) + int(r["c"] or 0) for r in rows)
            heaviest = rows[0]
            
            lines = [
                '📊 <b>Daily Token Burn Report</b> (Last 24h)',
                f'Total Tokens Burned: <b>{total_tokens:,}</b>',
                f'Heaviest Consumer: <b>{str(heaviest["module"])}</b> with <b>{int(heaviest["p"] or 0) + int(heaviest["c"] or 0):,}</b> tokens',
                '\n<b>Per-Module Breakdown:</b>'
            ]
            for r in rows:
                p, c = int(r["p"] or 0), int(r["c"] or 0)
                lines.append(f' • {str(r["module"])}: {p:,} prompt + {c:,} completion = {p+c:,}')
            
            _tg('\n'.join(lines))
    except Exception as _report_err:
        logger.warning(f"[ForecastRunner] Token report fail: {_report_err}")


def _cache_forecast_state():
    """v19.1.6: Caches rich broker-first forecast state for the HUD dashboard."""
    try:
        logger.info("[ForecastRunner] Starting forecast state cache cycle (v19.1.6)...")
        from logging_db.trade_logger import _conn

        broker = _get_broker()
        if not broker.is_connected():
            _publish_forecast_lane_state(
                broker=broker,
                connected=False,
                tradable=False,
                health="WARN",
                blocked_reason="broker_disconnected",
                action_needed="connect_kalshi",
                readiness_state="BROKER_DISCONNECTED",
            )
            return {}

        try:
            broker.sync_positions()
        except Exception as exc:
            logger.debug("[ForecastRunner] Broker sync failed before cache refresh: %s", exc)

        positions = broker.get_positions()
        enriched = []
        total_pnl = 0.0
        
        conn = _conn()
        cursor = conn.cursor()
        
        for p in positions:
            ticker = p.get('local_symbol')
            qty = float(p.get('qty', 0))
            side = str(p.get("side") or "YES").upper()
            
            current_px = 0.0
            try:
                quote = broker.get_quote(ticker)
                current_px = _held_mark_from_quote(p, quote)
            except Exception:
                pass
            
            entry_px = float(
                p.get('entry_price')
                or p.get('entry')
                or p.get('avg_entry')
                or 0.0
            )
            entered_at = "Unknown"
            event_title = ticker
            resolution_at = "Unknown"
            
            try:
                cursor.execute(
                    "SELECT price, ts FROM trades WHERE symbol=? AND action='BUY' AND broker='kalshi' ORDER BY ts DESC LIMIT 1",
                    (ticker,)
                )
                db_row = cursor.fetchone()
                if db_row:
                    entry_px = float(db_row[0])
                    entered_at = str(db_row[1])
                
                cursor.execute(
                    """SELECT COALESCE(c.contract_name, m.market_name), c.resolution_at
                       FROM forecast_contracts c
                       JOIN forecast_markets m ON c.market_id = m.id
                       WHERE c.local_symbol = ?
                       ORDER BY c.active DESC, c.last_seen_at DESC, c.id DESC
                       LIMIT 1""",
                    (ticker,)
                )
                meta = cursor.fetchone()
                if meta:
                    event_title = meta[0]
                    resolution_at = meta[1]
            except Exception:
                pass
            
            pnl = (current_px - entry_px) * qty
            potential = max(0.0, (1.0 - entry_px) * qty)
            
            if entry_px <= 0:
                pnl = 0.0
                potential = (1.0 - current_px) * qty if current_px > 0 else 0.0
            
            countdown = "N/A"
            if resolution_at != "Unknown":
                try:
                    from dateutil.parser import parse as date_parse
                    expiry = date_parse(resolution_at)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    delta = expiry - datetime.now(timezone.utc)
                    if delta.days > 0: countdown = f"{delta.days}d left"
                    else: countdown = f"{int(delta.seconds // 3600)}h left"
                except Exception:
                    pass

            enriched.append({
                "symbol": ticker,
                "title": event_title,
                "side": side,
                "qty": qty,
                "entry": round(entry_px, 4),
                "mark": round(current_px, 4),
                "pnl": round(pnl, 2),
                "potential": round(potential, 2),
                "countdown": countdown,
                "entered_at": entered_at,
                "sentiment": "Healthy" if pnl >= 0 else "Under Pressure"
            })
            total_pnl += pnl
            
        kalshi_equity = 0.0
        try:
            kalshi_equity = float(broker.get_account_balance() or 0.0)
        except Exception as e:
            logger.debug(f"[ForecastRunner] Failed to fetch Kalshi balance: {e}")

        snapshot = {
            "positions": enriched,
            "total_pnl": round(total_pnl, 2),
            "equity": round(kalshi_equity, 2),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        _publish_forecast_lane_state(
            broker=broker,
            snapshot=snapshot,
            connected=True,
            health="OK",
            blocked_reason="",
            action_needed="",
            readiness_state="OPERATIONAL",
        )
        return snapshot
    except Exception as e:
        logger.error(f"[ForecastRunner] Cache state error: {e}")
        return {}


def start_forecast_lane(bankroll: float = 100.0) -> None:
    """Initialize and start the forecast lane loops."""
    import schedule

    logger.info("[ForecastRunner] Starting lane...")
    try:
        from forecast.db import init_forecast_db
        logger.info("[ForecastRunner] Initializing DB...")
        init_forecast_db()
        logger.info("[ForecastRunner] DB initialized ✅")
    except Exception as e:
        logger.error(f"[ForecastRunner] DB init failed: {e}")
        return

    broker = _get_broker()
    logger.info("[ForecastRunner] Connecting to broker...")
    connected = broker.connect()
    if not connected:
        time.sleep(4)
        connected = broker.is_connected()
    
    logger.info(f"[ForecastRunner] Broker connected: {connected} ✅")

    try:
        from runtime.runtime_state import upsert_lane_state
        logger.info("[ForecastRunner] Updating lane state...")
        _snapshot = _forecast_runtime_snapshot(
            connected=bool(connected),
            contracts=0,
            stubs=0,
        )
        upsert_lane_state(
            "forecast",
            enabled=1,
            active=1,
            connected=_snapshot["connected"],
            tradable=_snapshot["tradable"],
            health=_snapshot["health"],
            blocked_reason=_snapshot["blocked_reason"],
            action_needed=_snapshot["action_needed"],
            readiness_state=_snapshot["readiness_state"],
        )
        logger.info("[ForecastRunner] Lane state updated ✅")
    except Exception as e:
        logger.error(f"[ForecastRunner] Lane state update failed: {e}")

    # Start quote harvester
    logger.info("[ForecastRunner] Starting QuoteHarvester...")
    harvester = _get_harvester()
    harvester.start()
    logger.info("[ForecastRunner] QuoteHarvester started ✅")

    # Initial discovery
    logger.info("[ForecastRunner] Starting initial discovery thread...")
    threading.Thread(target=run_discovery_cycle, daemon=True).start()

    # Register scheduler jobs
    # v19.1.6: Run heavy/blocking jobs in background threads to avoid loop starvation
    logger.info("[ForecastRunner] Registering scheduler jobs...")
    
    # Discovery loop (Background)
    def _bg_discovery(): threading.Thread(target=run_discovery_cycle, daemon=True).start()
    schedule.every(5).minutes.do(_bg_discovery)
    
    # Strategy cycle (Main thread is fine now that harvester is decoupled)
    schedule.every(2).minutes.do(run_strategy_cycle, bankroll=bankroll)
    
    # Monitor and Cache (Background)
    schedule.every(30).seconds.do(run_position_monitor)
    
    def _bg_cache(): threading.Thread(target=_cache_forecast_state, daemon=True).start()
    schedule.every(30).seconds.do(_bg_cache)
    
    # v19.4 Sovereign Balance: Bound maintenance
    def prune_forensic_data():
        """Clean up old quotes and bars to maintain dashboard performance."""
        from forecast.db import prune_old_quotes, prune_old_bars
        try:
            q_del = prune_old_quotes()
            b_del = prune_old_bars()
            logger.info(f"[ForensicPurge] Bound maintenance complete. Deleted {q_del} quotes, {b_del} bars.")
        except Exception as e:
            logger.error(f"[ForensicPurge] Cleanup error: {e}")

    schedule.every(6).hours.do(prune_forensic_data)
    
    schedule.every().day.at("08:00").do(_send_daily_token_burn_report)
    
    # v19.1.9: Catalyst - Every 6 hours, push an Analyst Briefing (SRE + Trading)
    from notifications.reports import send_sovereign_briefing
    schedule.every(6).hours.do(send_sovereign_briefing)

    # Manual trigger on startup
    logger.info("[ForecastRunner] Triggering initial strategy cycle...")
    threading.Thread(target=run_strategy_cycle, args=(bankroll,), daemon=True).start()

    logger.info(
        f"[ForecastRunner] Lane fully operational | bankroll=${bankroll:.0f} "
        f"| connected={connected} ✅"
    )


def stop_forecast_lane() -> None:
    """Stop the harvester thread. Scheduler jobs remain registered but harmless."""
    global _harvester
    if _harvester:
        _harvester.stop()
        _harvester = None
    logger.info("[ForecastRunner] Forecast lane stopped")


if __name__ == "__main__":
    import schedule
    from config import FORECAST_LOG_PATH

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(FORECAST_LOG_PATH, encoding="utf-8"),
        ],
    )
    start_forecast_lane(bankroll=100.0)
    logger.info("[ForecastRunner] Scheduler loop running — Ctrl+C to stop")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        stop_forecast_lane()
        logger.info("[ForecastRunner] Stopped")
