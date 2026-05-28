"""
scheduler/v10_runner.py — v10 unified scanner + trade loop.

Runs 24/7 paper trading. Scanner: Kraken Futures public REST (US-accessible, no auth).
Execution: perps_engine.py → binance_broker.py (paper mode, no live keys required).
Replaces v9 job_runner for v10 architecture.

Loop intervals:
  scan_and_trade:     every 5 minutes
  exit_monitor:       every 30 seconds
  hedge_rebalance:    every 5 minutes
  kill_switch_check:  every 60 seconds
  rbi_nightly:        once at 02:00 ET
  ml_retrain_check:   every 6 hours
"""

import logging
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import schedule
from config import SUPPRESSED_SYMBOLS
from runtime.execution_universe import (
    get_execution_policy as _get_execution_policy,
    get_underlying as _get_underlying,
)
from logging_db.trade_logger import _conn as _db_conn

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────

_scan_lock = threading.RLock()  # prevent parallel scan_and_trade runs
_initial_balance: float = 0.0  # set at startup from config

# Regime multipliers for position sizing (applied on top of compute_position_size)
_REGIME_SIZE_MULT = {
    "TRENDING_UP": 1.00,
    "TRENDING_DOWN": 1.00,
    "RANGING": 0.85,
    "HIGH_VOL": 0.70,
    "ACCUMULATION": 0.90,
    "DISTRIBUTION": 0.90,
    "UNKNOWN": 0.90,
}

# Economics veto suppression: 3-strike system per symbol+direction+reason prefix.
# Logs on occurrences 1-3, emits a "suppressing" notice on occurrence 4, silent thereafter.
# Window resets after _VETO_LOG_COOLDOWN_SEC — count and timestamp reset together.
# Does NOT skip the gate check — only suppresses the INFO log line.
_veto_log_cooldowns: Dict[str, float] = {}
_veto_log_counts: Dict[str, int] = {}
_VETO_LOG_COOLDOWN_SEC: int = 1800  # 30 min window before count resets
_VETO_LOG_SUPPRESS_AFTER: int = 3  # log first N occurrences, then suppress

# ML model store — lazy-loaded; None until walk_forward_trainer has saved models
_model_store = None
_model_store_loaded_at: float = 0.0
_MODEL_STORE_REFRESH_SEC: int = 3600  # reload from disk every hour
_last_ml_retrain_ts: float = 0.0
_last_ml_retrain_snapshot_count: int = -1
_last_rbi_run_ts: float = 0.0
_last_rbi_snapshot_count: int = -1


def _get_model_store():
    """Return a live ModelStore if trained models exist on disk, else None."""
    global _model_store, _model_store_loaded_at
    now = time.time()
    if (
        now - _model_store_loaded_at < _MODEL_STORE_REFRESH_SEC
        and _model_store is not None
    ):
        return _model_store
    try:
        from ml.model_store import ModelStore, MODELS_DIR
        import os

        # Only instantiate if at least one model file exists
        if any(f.endswith(".pkl") for f in os.listdir(MODELS_DIR)):
            _model_store = ModelStore()
            _model_store_loaded_at = now
            logger.info("[v10] ModelStore loaded from disk")
        else:
            _model_store = None
            _model_store_loaded_at = now
    except Exception as e:
        logger.debug(f"[v10] ModelStore load skipped: {e}")
        _model_store = None
        _model_store_loaded_at = now
    return _model_store


# _get_underlying imported from runtime.execution_universe (v15.10 — shared helper)


# ── Candidate journaling (v13.6) ─────────────────────────────────────────────
#
# Every decision-grade candidate is persisted to scan_candidates so the learning
# layer sees the full decision set, not just executed trades.
# All journal writes are fire-and-forget (wrapped in try/except) — a DB error
# must never disrupt live scanning or entry.


def _journal_scan_candidate(
    scan_id: str,
    candidate: dict,
    decision: str,
    *,
    regime: str = "",
    technical_score: float = 0.0,
    ml_score: float = 0.0,
    composite_score: float = 0.0,
    entry_threshold: float = 58.0,
    should_enter_signal: int = 0,
    econ_approved: int = 0,
    econ_tier: str = "",
    econ_reject_reason: str = "",
    edge_score: float = 0.0,
    size_usd: float = 0.0,
    leverage: int = 3,
    entry_block_reason: str = "",
    # Tradeability fields (v16.14) — optional, populated when tradeability gate fires
    recommended_lane: str = "",
    tradeability_status: str = "",
    trade_blocked_reason: str = "",
    trade_size_block_reason: str = "",
    trade_source_reason: str = "",
    manual_executable: int = 0,
    auto_executable: int = 0,
    spot_regime: str = "",
    setup_family: str = "",
    setup_score: float = 0.0,
    setup_preference: str = "",
    tf_5m_state: str = "",
    tf_30m_state: str = "",
    tf_4h_state: str = "",
    tf_1d_state: str = "",
    structural_confirms: str = "",
    execution_route: str = "",
    cooldown_until: str = "",
    microstructure_veto: str = "",
    final_spot_score: float = 0.0,
    regime_floor: float = 0.0,
    actual_stop_pct: float | None = None,
    actual_target_pct: float | None = None,
    net_rr: float | None = None,
    net_win_usd: float | None = None,
    econ_gate_class: str = "",
) -> int:
    """
    Write one candidate decision row to scan_candidates.
    Called at every gate exit — entered, econ_veto, below_threshold,
    dual_exposure_block, cooldown_block, risk_block, sizing_zero, data_unavailable.
    """
    try:
        from logging_db.trade_logger import log_scan_candidate
        import json

        symbol = candidate.get("symbol", "")
        direction = candidate.get("direction", "LONG")
        price = float(candidate.get("price", 0) or 0)
        vol = float(candidate.get("vol_usd", candidate.get("volume_24h_usd", 0)) or 0)
        spread = float(candidate.get("spread_pct", 0) or 0)
        bid_dep = float(candidate.get("bid_depth_usd", 0) or 0)
        ask_dep = float(candidate.get("ask_depth_usd", 0) or 0)
        atr_15m = float(candidate.get("atr_15m", 0) or 0)
        stop_pct = float(candidate.get("stop_pct", 3.0) or 3.0)
        tgt_pct = float(candidate.get("target_pct", 6.0) or 6.0)
        exp_profit = float(
            candidate.get(
                "scanner_expected_profit", candidate.get("expected_profit", 0)
            )
            or 0
        )
        setups = candidate.get("scan_setups", [])
        setups_json = json.dumps(setups) if isinstance(setups, list) else str(setups)
        primary = candidate.get("primary_setup", "") or ""
        theor_pos = candidate.get("scanner_theoretical_position_usd")
        eff_pos = candidate.get("scanner_effective_position_usd")

        return int(
            log_scan_candidate(
                scan_id=scan_id,
                symbol=symbol,
                exchange=str(candidate.get("exchange", "")),
                base_asset=str(candidate.get("base_asset", _get_underlying(symbol))),
                direction=direction,
                primary_setup=primary,
                scan_setups_json=setups_json,
                price=price,
                volume_24h_usd=vol,
                spread_pct=spread,
                bid_depth_usd=bid_dep,
                ask_depth_usd=ask_dep,
                atr_15m=atr_15m,
                stop_pct=stop_pct,
                target_pct=tgt_pct,
                scanner_expected_profit=exp_profit,
                regime=regime,
                technical_score=technical_score,
                ml_score=ml_score,
                composite_score=composite_score,
                entry_threshold=entry_threshold,
                should_enter_signal=should_enter_signal,
                econ_approved=econ_approved,
                econ_tier=econ_tier,
                econ_reject_reason=econ_reject_reason,
                edge_score=edge_score,
                size_usd=size_usd,
                leverage=leverage,
                entry_block_reason=entry_block_reason,
                decision=decision,
                paper=False,
                source="live_v10",
                scanner_theoretical_position_usd=theor_pos,
                scanner_effective_position_usd=eff_pos,
                recommended_lane=recommended_lane,
                tradeability_status=tradeability_status,
                trade_blocked_reason=trade_blocked_reason
                or (decision if decision != "entered" else ""),
                trade_size_block_reason=trade_size_block_reason,
                trade_source_reason=trade_source_reason,
                manual_executable=manual_executable,
                auto_executable=auto_executable,
                spot_regime=spot_regime,
                setup_family=setup_family,
                setup_score=setup_score,
                setup_preference=setup_preference,
                tf_5m_state=tf_5m_state,
                tf_30m_state=tf_30m_state,
                tf_4h_state=tf_4h_state,
                tf_1d_state=tf_1d_state,
                structural_confirms=structural_confirms,
                execution_route=execution_route,
                cooldown_until=cooldown_until,
                microstructure_veto=microstructure_veto,
                final_spot_score=final_spot_score,
                regime_floor=regime_floor,
                actual_stop_pct=actual_stop_pct,
                actual_target_pct=actual_target_pct,
                net_rr=net_rr,
                net_win_usd=net_win_usd,
                econ_gate_class=econ_gate_class,
            )
        )
    except Exception as _je:
        logger.debug(
            f"[v10] candidate journal error ({decision} {candidate.get('symbol', '')}): {_je}"
        )
    return 0


def _tradeability_hint(
    symbol: str, direction: str, candidate: dict, *, live: bool
) -> dict:
    """
    Lightweight policy-only lane hint used for early journaling rows.

    This intentionally avoids runtime eligibility checks so below-threshold /
    econ-veto / duplicate-block rows still carry a stable route recommendation
    without forcing balance/position checks for every candidate.
    """
    hint = {
        "recommended_lane": "",
        "tradeability_status": "not_evaluated",
        "trade_blocked_reason": "",
        "trade_size_block_reason": "none",
        "trade_source_reason": "not_applicable",
        "manual_executable": 0,
        "auto_executable": 0,
    }
    try:
        from runtime.crypto_tradeability import get_recommended_crypto_lane

        hint["recommended_lane"] = get_recommended_crypto_lane(
            symbol,
            direction,
            candidate,
            live=live,
        )
    except Exception:
        pass
    return hint


# ── TradingView signal helpers ────────────────────────────────────────────────


def _get_fresh_tv_signals(max_age_seconds: int | None = None) -> list[dict[str, Any]]:
    """Return recent TradingView HTF signals from the dedicated tv_signals table."""
    try:
        from config import (
            TV_ALLOWED_UNDERLYINGS,
            TV_SIGNAL_MAX_AGE_SECONDS,
            TV_SIGNALS_ENABLED,
        )
        from logging_db.trade_logger import get_recent_tv_signals

        if not TV_SIGNALS_ENABLED:
            return []

        max_age = int(max_age_seconds or TV_SIGNAL_MAX_AGE_SECONDS or 300)
        allowed = {str(s).upper() for s in (TV_ALLOWED_UNDERLYINGS or [])}
        rows = get_recent_tv_signals(max_age_seconds=max_age)
        fresh: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            underlying = _get_underlying(symbol)
            if allowed and underlying not in allowed:
                continue
            row = dict(row)
            row["symbol"] = symbol
            row["underlying"] = underlying
            fresh.append(row)
        return fresh
    except Exception:
        return []


def _tv_context_map(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for signal in signals:
        underlying = str(
            signal.get("underlying") or _get_underlying(signal.get("symbol", ""))
        ).upper()
        if not underlying or underlying in context:
            continue
        context[underlying] = dict(signal)
    return context


def _learning_snapshot_count() -> int:
    try:
        conn = _db_conn()
        row = conn.execute("SELECT COUNT(*) FROM ml_feature_snapshots").fetchone()
        return int((row or [0])[0] or 0)
    except Exception as e:
        logger.warning(f"Non-critical background state telemetry error: {e}")
        return 0


def _schedule_weekly_job(weekday: str, at_time: str, fn) -> None:
    token = str(weekday or "SUN").strip().upper()[:3]
    weekly = {
        "MON": schedule.every().monday,
        "TUE": schedule.every().tuesday,
        "WED": schedule.every().wednesday,
        "THU": schedule.every().thursday,
        "FRI": schedule.every().friday,
        "SAT": schedule.every().saturday,
        "SUN": schedule.every().sunday,
    }
    weekly.get(token, schedule.every().sunday).at(at_time).do(fn)


# ── Lazy imports (all wrapped so import errors never crash the loop) ──────────


def _import_scanner():
    try:
        import scanner

        return scanner
    except Exception as e:
        logger.debug(f"[v10] scanner import error: {e}")
        return None


def _import_signal_engine():
    try:
        import signal_engine

        return signal_engine
    except Exception as e:
        logger.debug(f"[v10] signal_engine import error: {e}")
        return None


def _import_position_manager():
    try:
        import position_manager

        return position_manager
    except Exception as e:
        logger.debug(f"[v10] position_manager import error: {e}")
        return None


def _import_perps_engine():
    try:
        import perps_engine

        return perps_engine
    except Exception as e:
        logger.debug(f"[v10] perps_engine import error: {e}")
        return None


def _import_hedge_engine():
    try:
        import hedge_engine

        return hedge_engine
    except Exception as e:
        logger.debug(f"[v10] hedge_engine import error: {e}")
        return None


def _import_kill_switch():
    try:
        import kill_switch

        return kill_switch
    except Exception as e:
        logger.debug(f"[v10] kill_switch import error: {e}")
        return None


def _import_risk_engine():
    try:
        import risk_engine

        return risk_engine
    except Exception as e:
        logger.debug(f"[v10] risk_engine import error: {e}")
        return None


def _import_learning_loop():
    try:
        import learning_loop

        return learning_loop
    except Exception as e:
        logger.debug(f"[v10] learning_loop import error: {e}")
        return None


def _import_feature_builder():
    try:
        from ml.feature_builder import build_features, to_array

        return build_features, to_array
    except Exception as e:
        logger.debug(f"[v10] feature_builder import error: {e}")
        return None, None


def _import_regime_classifier():
    try:
        from ml.regime_classifier import classify_from_features

        return classify_from_features
    except Exception as e:
        logger.debug(f"[v10] regime_classifier import error: {e}")
        return None


def _import_get_candles():
    try:
        from data.historical_data import get_candles

        return get_candles
    except Exception as e:
        logger.debug(f"[v10] historical_data import error: {e}")
        return None


def _import_notification_engine():
    try:
        import notifications.notification_engine as ne

        return ne
    except Exception as e:
        logger.debug(f"[v10] notification_engine import error: {e}")
        return None


def _import_incubation_manager():
    try:
        from rbi.incubation_manager import get_size_multiplier

        return get_size_multiplier
    except Exception as e:
        logger.debug(f"[v10] incubation_manager import error: {e}")
        return None


# ── Balance helpers ───────────────────────────────────────────────────────────


def _get_account_balance() -> float:
    """Try broker; fall back to the canonical live/paper account-size helper."""
    perps = _import_perps_engine()
    if perps is not None:
        try:
            broker = perps._get_broker(testnet=False)
            if broker is not None:
                bal = broker.get_account_balance()
                if bal and bal > 0:
                    return float(bal)
        except Exception as e:
            logger.debug(f"[v10] broker balance error: {e}")

    try:
        from runtime.live_account import get_live_account_size

        return float(get_live_account_size(paper=False))
    except Exception:
        return 5000.0


def _get_deployed_usd(open_positions: Dict) -> float:
    """Sum notional of all open positions."""
    return sum(float(p.get("position_usd", 0)) for p in open_positions.values())


def _get_spot_runtime_truth() -> tuple[int, float]:
    """
    Return (open_count, deployed_usd) for the spot lane.

    Live mode prefers broker-truth holdings (v19.1 Ledgerless).
    """
    try:
        from execution.coinbase_spot_broker import get_spot_broker
        broker = get_spot_broker()
        holdings = broker.sync_live_holdings() or []
        deployed = sum(float(h.get("current_value") or 0.0) for h in holdings)
        return len(holdings), float(deployed)
    except Exception:
        return 0, 0.0


def _persist_live_account_size(balance: float) -> None:
    """Persist the real live funded account size once it is known."""
    if balance <= 0:
        return
    try:
        from runtime.runtime_state import upsert_system_state

        upsert_system_state(account_size_live=round(float(balance), 2))
    except Exception as e:
        logger.debug(f"[v10] live account size persist error: {e}")


def _write_crypto_lane_runtime(open_positions: Optional[Dict] = None) -> None:
    """Persist current crypto lane runtime truth for dashboard / launcher surfaces."""
    try:
        from runtime.runtime_state import upsert_lane_state, upsert_system_state
        from runtime.spot_classification import get_classifications, is_external_manual
        import config as _cfg

        perps = _import_perps_engine()
        broker = perps._get_broker(testnet=False) if perps is not None else None
        connected = bool(broker and broker.is_connected())
        if broker is not None and not connected:
            try:
                connected = bool(broker.connect())
            except Exception:
                connected = False

        if open_positions is None and perps is not None:
            try:
                open_positions = perps.get_open_positions()
            except Exception:
                open_positions = {}
        open_positions = open_positions or {}

        buying_power = float(_get_account_balance() or 0.0)
        _persist_live_account_size(buying_power)
        perp_deployed_usd = float(_get_deployed_usd(open_positions))
        perp_positions_open = len(open_positions)

        # ── Spot Truth (Broker-Direct v19.1) ───────────────────────────────────
        spot_positions_open = 0
        spot_deployed_usd = 0.0
        spot_positions_list = []
        spot_regime = "NEUTRAL"
        
        try:
            from execution.coinbase_spot_broker import get_spot_broker
            s_broker = get_spot_broker()
            holdings = s_broker.sync_live_holdings() or []
            classifications = get_classifications()
            
            # Get global spot regime
            with _db_conn() as conn:
                row = conn.execute("SELECT last_regime FROM spot_regime_state ORDER BY ts DESC LIMIT 1").fetchone()
                if row: spot_regime = row[0]

            # Get latest scan sentiment/scores
            scan_data = {}
            with _db_conn() as conn:
                rows = conn.execute(
                    """SELECT symbol, composite_score FROM scan_candidates 
                       WHERE source='live_v10' 
                       GROUP BY symbol HAVING ts = MAX(ts)"""
                ).fetchall()
                scan_data = {r[0]: float(r[1] or 0.0) for r in rows}

            for h in holdings:
                sym = str(h.get("symbol") or "").upper()
                if is_external_manual(sym, classifications):
                    continue
                
                qty = float(h.get("qty", 0.0))
                entry = float(h.get("avg_entry") or 0.0)
                price = float(h.get("current_price") or entry)
                val = float(h.get("current_value") or (qty * price))
                
                spot_positions_open += 1
                spot_deployed_usd += val
                
                # Derive trend/sentiment for HUD
                trend = "NEUTRAL"
                if "TRENDING_UP" in spot_regime: trend = "UP"
                elif "TRENDING_DOWN" in spot_regime: trend = "DOWN"
                
                score = scan_data.get(sym, 50.0)
                sentiment = "NEUTRAL"
                if score > 60: sentiment = "BULLISH"
                elif score < 40: sentiment = "BEARISH"

                spot_positions_list.append({
                    "symbol": sym,
                    "qty": qty,
                    "entry": entry,
                    "current_price": price,
                    "live_pnl": (price - entry) * qty if entry > 0 else 0.0,
                    "potential_usd": val * 0.1, # Placeholder for HUD UI
                    "risk_usd": val * 0.05,    # Placeholder for HUD UI
                    "strategy": f"spot_{sym.lower()}",
                    "trend": trend,
                    "sentiment": sentiment
                })
        except Exception as e:
            logger.warning(f"[v10] broker-direct spot truth error: {e}")

        deployed_usd = perp_deployed_usd + spot_deployed_usd
        positions_open = perp_positions_open + spot_positions_open

        ks = _import_kill_switch()
        kill_halted = bool(ks and ks.is_halted())

        # ── Health Logic (v19.1 Ledgerless) ────────────────────────────────────
        health = "OK"
        readiness = "NOT_READY"
        launch_state = "NOT_READY"
        blocked_reason = ""
        action_needed = ""
        tradable = 1

        if not holdings and connected:
            # Not necessarily an error, could just be flat
            pass
        elif not connected:
            health = "WARN"
            readiness = "NOT_READY"
            launch_state = "NOT_READY"
            blocked_reason = "broker_disconnected"
            action_needed = "check_coinbase_live_connection"
            tradable = 0
        elif buying_power <= 0:
            health = "WARN"
            readiness = "NOT_READY"
            launch_state = "NOT_READY"
            blocked_reason = "no_buying_power"
            action_needed = "fund_account_or_check_balance_sync"
            tradable = 0
        elif kill_halted:
            health = "WARN"
            readiness = "HALTED"
            launch_state = "HALTED"
            blocked_reason = "kill_switch_active"
            action_needed = "review_kill_switch_trigger"
            tradable = 0
        else:
            # v18.19: Consolidated Full Live mode. 
            readiness = "LIVE"
            launch_state = "LIVE"
            tradable = 1

        # Build HUD Snapshot JSON
        snapshot = {
            "equity": spot_deployed_usd + buying_power,
            "positions": spot_positions_list,
            "regime": spot_regime,
            "ts": _now_iso()
        }

        upsert_lane_state(
            "crypto",
            enabled=1,
            active=1,
            configured=1,
            mode="live",
            connected=int(connected),
            tradable=int(tradable),
            health=health,
            blocked_reason=blocked_reason,
            action_needed=action_needed,
            positions_open=positions_open,
            capital_deployed_usd=round(deployed_usd, 2),
            buying_power_usd=round(buying_power, 2),
            readiness_state=readiness
        )
        upsert_system_state(
            launch_readiness_state=launch_state,
            global_status="HALTED" if launch_state == "HALTED" else health,
        )
    except Exception as e:
        logger.debug(f"[v10] crypto lane runtime write error: {e}")


# ── scan_and_trade ────────────────────────────────────────────────────────────


def scan_and_trade(spot_only: bool = False):
    """
    Main 5-minute loop: run scanner, score candidates, open new positions.
    Protected by _scan_lock to prevent parallel runs.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.debug("[v10] scan_and_trade skipped — previous run still active")
        return

    try:
        _scan_and_trade_inner(spot_only=spot_only)
    except Exception as e:
        logger.error(
            f"[v10] scan_and_trade fatal: {e}\n{traceback.format_exc()[:1000]}"
        )
    finally:
        _scan_lock.release()


def _scan_and_trade_inner(spot_only: bool = False):
    """Inner body of scan_and_trade — separated so the lock release is guaranteed."""
    ks = _import_kill_switch()
    re = _import_risk_engine()
    scanner = _import_scanner()
    se = _import_signal_engine()
    pm = _import_position_manager()
    perps = _import_perps_engine()
    get_candles = _import_get_candles()
    build_features, _ = _import_feature_builder()
    classify_from_features = _import_regime_classifier()
    ne = _import_notification_engine()
    get_size_multiplier = _import_incubation_manager()

    # Kill switch check
    if ks is not None and ks.is_halted():
        logger.info(f"[v10] scan skipped — kill switch: {ks.get_halt_reason()}")
        return

    # Risk gate
    if re is not None:
        can_trade, reason = re.can_open_new_position()
        if not can_trade:
            logger.info(f"[v10] scan skipped — risk gate: {reason}")
            return

    # 🚨 Volatility Circuit Breaker
    try:
        from data.coinbase_websocket import (
            is_volatility_halted,
            get_halt_time_remaining,
        )

        if is_volatility_halted():
            logger.warning(
                f"[v10] scan skipped — VOLATILITY HALT active ({get_halt_time_remaining():.0f}s remaining)"
            )
            return
    except ImportError:
        pass

    # Account balance for scanner and sizing
    balance = _get_account_balance()

    # v18.17: Centralised observability push (every cycle for all 8 coins)
    try:
        from runtime.spot_strategy import ACTIVE_UNIVERSE, calculate_execution_profile
        from runtime.spot_momentum import build_spot_state
        from data.edge_monitor import get_shadow_state
        import system_state

        for _sym in ACTIVE_UNIVERSE:
            try:
                # Build momentum state (cached if fresh)
                _sstate = build_spot_state(_sym, allow_stale=True)
                _shadow = get_shadow_state(_sym)
                _mult, _tag = calculate_execution_profile(_sym, _sstate)
                
                # Push vitals to system_state for Telegram bot
                system_state.state.update_stochastic(_sym, {
                    "kalman_dev": float(_shadow.get("kalman_dev_pct", 0.0)),
                    "kyle_lambda_fragile": bool(_shadow.get("kyle_lambda_fragile", False)),
                    "ou_prob": float(_shadow.get("ou_transition_prob", 0.5)),
                    "multiplier": round(float(_mult), 2),
                    "status": str(_tag), # ACTIVE, COLD, FROZEN, FEE_FLOOR etc
                    "er": round(float(_sstate.get("er", 0.0)), 4),
                    "adx": round(float(_sstate.get("adx", 0.0)), 2),
                })
            except Exception as _sym_err:
                logger.debug(f"[v10] observability scan error {_sym}: {_sym_err}")
        system_state.state.update_prometheus()
    except Exception as _obs_err:
        logger.debug(f"[v10] global observability push error: {_obs_err}")

    # Get current open positions
    open_pos: Dict = {}
    if perps is not None:
        open_pos = perps.get_open_positions()

    open_symbols = list(open_pos.keys())
    deployed_usd = _get_deployed_usd(open_pos)
    _write_crypto_lane_runtime(open_pos)

    # TradingView is now an HTF context layer, not a synthetic candidate source.
    # v18.16: TV excision — HTF context now exclusively derived from internal stack
    tv_context_by_underlying = {}

    # Run scanner
    if scanner is None:
        logger.debug("[v10] scanner unavailable — skipping")
        return
    else:
        candidates = scanner.scan(
            open_positions=open_symbols,
            account_balance=balance,
            core_only=True,
        )

    if spot_only:
        try:
            from runtime.spot_strategy import strategy_spot_symbols

            _spot_universe = {str(s).upper() for s in strategy_spot_symbols()}
        except Exception:
            _spot_universe = {"BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"}
        candidates = [
            c
            for c in candidates
            if str(c.get("direction", "LONG")).upper() == "LONG"
            and _get_underlying(str(c.get("symbol", "")).upper()) in _spot_universe
        ]
        # Inject synthetic LONG candidates for spot-only symbols the perp scanner
        # never covers (LTC/DOGE/ADA/LINK are not in CORE_EXECUTION_UNDERLYINGS).
        try:
            from config import CORE_EXECUTION_UNDERLYINGS as _core_syms

            _core = {s.upper() for s in _core_syms}
        except Exception:
            _core = {"BTC", "ETH", "SOL", "XRP"}
        _already = {_get_underlying(c["symbol"].upper()) for c in candidates}
        for _sym in sorted(_spot_universe - _core - _already):
            candidates.append(
                {
                    "symbol": _sym,
                    "direction": "LONG",
                    # Coinbase spot markets have >>$2.5M/day; bypass volume gate
                    "vol_usd": 500_000_000.0,
                    "spread_pct": 0.0,
                    "bid_depth_usd": 0.0,
                    "ask_depth_usd": 0.0,
                    "atr_15m": 0.0,
                    "stop_pct": 0.0,
                    "target_pct": 0.0,
                    "expected_profit": 0.0,
                    "funding_rate": 0.0,
                    "correlation_penalty": 1.0,
                    "regime_penalty": 1.0,
                    "price": 0.0,
                    "edge_score": 0.5,
                    "spot_only_synthetic": True,
                }
            )

    if not candidates:
        logger.debug("[v10] scan returned 0 candidates")
        # Write scan heartbeat even on 0-candidate cycles so health_check
        # _check_scan_liveness() doesn't go stale during quiet markets.
        try:
            from logging_db.trade_logger import log_event as _log_hb0

            _log_hb0("INFO", "heartbeat", "scan ok: 0 candidates → 0 entries")
        except Exception:
            pass
        return

    logger.info(
        f"[v10] {'spot scalp ' if spot_only else ''}scan: {len(candidates)} candidates "
        f"(fresh_tv_contexts={len(tv_context_by_underlying)}), "
        f"balance=${balance:.0f} deployed=${deployed_usd:.0f}"
    )

    # Unique ID for this scan cycle — links all candidate rows from the same scan
    import uuid as _uuid

    _scan_id = _uuid.uuid4().hex[:16]

    # ── Parallel I/O Pre-fetch ────────────────────────────────────────────────
    import concurrent.futures as _cf
    import urllib.request as _ur
    import json as _json

    def _prefetch_data(c):
        sym = c.get("symbol", "")
        try:
            c["_df"] = get_candles(sym, "1h", 200) if get_candles else None
        except Exception as e:
            logger.debug(f"[v10] prefetch df error {sym}: {e}")
            c["_df"] = None

        live = 0.0
        try:
            if c.get("spot_only_synthetic"):
                try:
                    from execution.coinbase_spot_broker import get_spot_broker

                    live = float(get_spot_broker().get_mark_price(sym) or 0.0)
                except Exception:
                    pass

            if live <= 0 and (sym.startswith("PF_") or sym.startswith("PI_")):
                try:
                    kr = _json.loads(
                        _ur.urlopen(
                            "https://futures.kraken.com/derivatives/api/v3/tickers",
                            timeout=3,
                        ).read()
                    )
                    for t in kr.get("tickers", []):
                        if t.get("symbol") == sym:
                            live = float(t.get("markPrice") or t.get("last") or 0)
                            break
                except Exception:
                    pass

            if live <= 0:
                try:
                    req = _ur.Request(
                        "https://api.hyperliquid.xyz/info",
                        data=_json.dumps({"type": "allMids"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    mids = _json.loads(_ur.urlopen(req, timeout=3).read())
                    live = float(mids.get(sym, 0))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[v10] prefetch live price error {sym}: {e}")

        c["_live_price"] = live

    if candidates:
        with _cf.ThreadPoolExecutor(max_workers=min(10, len(candidates))) as executor:
            list(executor.map(_prefetch_data, candidates))

    # Exact funnel counters — reset each scan cycle
    _f_dual_exposure = 0
    _f_risk_block = 0
    _f_data_unavailable = 0
    _f_below_threshold = 0
    _f_econ_veto = 0
    _f_research_only_block = 0
    _f_not_autonomous = 0  # v16.11: live-only gate for contract-min-safe symbols
    _f_sizing_zero = 0
    _f_execution_failed = 0
    _f_entered = 0

    for candidate in candidates:
        # RC12: Pre-emptive symbol normalization for clean logs and execution
        raw_sym = candidate.get("symbol", "")
        symbol = str(raw_sym).upper().replace("PF_", "").replace("USD", "").replace("USDT", "").replace("-PERP", "")
        candidate["symbol"] = symbol
        candidate["raw_scanner_symbol"] = raw_sym
        
        direction = candidate.get("direction", "LONG")
        _route_hint = _tradeability_hint(
            symbol,
            direction,
            candidate,
            live=True,
        )

        # ── Symbol suppression — skip confirmed structural losers ─────────────
        if symbol in SUPPRESSED_SYMBOLS:
            logger.debug(f"[v10] {symbol} — suppressed (negative edge, forensic audit)")
            continue

        # ── Dual-exposure + duplicate guard ───────────────────────────────────
        # Normalize to base asset (PF_ETHUSD→ETH, ETHUSDT→ETH, ETH→ETH) so that
        # trading both the Kraken and Hyperliquid version of the same asset is blocked.
        _underlying = _get_underlying(symbol)

        # In-memory check (exact symbol)
        if perps is not None and perps.get_open_positions().get(symbol):
            logger.debug(f"[v10] {symbol} — already in memory, skip")
            _f_dual_exposure += 1
            _journal_scan_candidate(
                _scan_id,
                candidate,
                "dual_exposure_block",
                entry_block_reason="already in memory (exact symbol)",
                **_route_hint,
            )
            continue

        # SQLite check — match by exact symbol OR same underlying across all open positions
        try:
            _conn2 = _db_conn()
            _open_rows = _conn2.execute(
                "SELECT symbol FROM open_positions WHERE strategy=? AND paper=0",
                ("v10_perp",),
            ).fetchall()
            _open_underlyings = {_get_underlying(r[0]) for r in _open_rows}
            _open_symbols_db = {r[0] for r in _open_rows}
            if symbol in _open_symbols_db:
                logger.debug(f"[v10] {symbol} — exact match in SQLite, skip")
                _f_dual_exposure += 1
                _journal_scan_candidate(
                    _scan_id,
                    candidate,
                    "dual_exposure_block",
                    entry_block_reason="exact symbol in SQLite open_positions",
                    **_route_hint,
                )
                continue
            if _underlying in _open_underlyings:
                # Find which symbol caused the conflict for the log message
                _conflict = next(
                    (r[0] for r in _open_rows if _get_underlying(r[0]) == _underlying),
                    "?",
                )
                logger.info(
                    f"[v10] {symbol} — dual-exposure block: "
                    f"underlying={_underlying} already open as {_conflict}"
                )
                _f_dual_exposure += 1
                _journal_scan_candidate(
                    _scan_id,
                    candidate,
                    "dual_exposure_block",
                    entry_block_reason=f"underlying={_underlying} open as {_conflict}",
                    **_route_hint,
                )
                continue
        except Exception:
            pass

        # Re-check risk gate before each entry attempt
        if re is not None:
            can_trade, reason = re.can_open_new_position()
            if not can_trade:
                logger.info(f"[v10] entry blocked by risk: {reason}")
                _f_risk_block += 1
                _journal_scan_candidate(
                    _scan_id,
                    candidate,
                    "risk_block",
                    entry_block_reason=reason,
                    **_route_hint,
                )
                break  # stop trying more candidates

        try:
            _decision = _attempt_entry(
                candidate=candidate,
                symbol=symbol,
                direction=direction,
                balance=balance,
                deployed_usd=deployed_usd,
                perps=perps,
                se=se,
                pm=pm,
                get_candles=get_candles,
                build_features=build_features,
                classify_from_features=classify_from_features,
                ne=ne,
                get_size_multiplier=get_size_multiplier,
                scan_id=_scan_id,
                tv_context_by_underlying=tv_context_by_underlying,
            )
        except Exception as e:
            logger.error(
                f"[v10] entry attempt error {symbol}: {e}\n"
                f"{traceback.format_exc()[:800]}"
            )
            _f_execution_failed += 1
            continue

        # Tally exact decision from _attempt_entry return value
        if _decision == "data_unavailable":
            _f_data_unavailable += 1
        elif _decision == "below_threshold":
            _f_below_threshold += 1
        elif _decision == "econ_veto":
            _f_econ_veto += 1
        elif _decision == "research_only_block":
            _f_research_only_block += 1
        elif _decision == "not_autonomous_live_eligible":
            _f_not_autonomous += 1
        elif _decision == "sizing_zero":
            _f_sizing_zero += 1
        elif _decision == "execution_failed":
            _f_execution_failed += 1
        elif _decision == "entered":
            _f_entered += 1

        # Update deployed after each successful entry
        if _decision == "entered" and perps is not None:
            deployed_usd = _get_deployed_usd(perps.get_open_positions())

    # Per-scan funnel summary — always logged at INFO so the operator can see where
    # candidates are being filtered without trawling individual DEBUG/INFO lines.
    logger.info(
        f"[v10] funnel: {len(candidates)} candidates → "
        f"dual={_f_dual_exposure} risk={_f_risk_block} "
        f"data_unavail={_f_data_unavailable} below_thresh={_f_below_threshold} "
        f"econ_veto={_f_econ_veto} research_only={_f_research_only_block} "
        f"not_autonomous={_f_not_autonomous} "
        f"sizing_zero={_f_sizing_zero} exec_fail={_f_execution_failed} "
        f"entered={_f_entered}"
    )

    # Persist exact funnel row to DB for audit scripts
    try:
        from logging_db.trade_logger import log_scan_funnel as _log_sf

        _log_sf(
            scan_id=_scan_id,
            scanner_candidates_total=len(candidates),
            dual_exposure_block=_f_dual_exposure,
            cooldown_block=0,
            risk_block=_f_risk_block,
            data_unavailable=_f_data_unavailable,
            below_threshold=_f_below_threshold,
            econ_veto=_f_econ_veto,
            research_only_block=_f_research_only_block,
            sizing_zero=_f_sizing_zero,
            execution_failed=_f_execution_failed,
            entered=_f_entered,
        )
    except Exception as _sf_err:
        logger.debug(f"[v10] log_scan_funnel error: {_sf_err}")

    # Write scan heartbeat — read by health_check._check_scan_liveness()
    try:
        from logging_db.trade_logger import log_event as _log_hb

        _log_hb(
            "INFO",
            "heartbeat",
            f"scan ok: {len(candidates)} candidates → {_f_entered} entries",
        )
    except Exception:
        pass


def _attempt_entry(
    candidate,
    symbol,
    direction,
    balance,
    deployed_usd,
    perps,
    se,
    pm,
    get_candles,
    build_features,
    classify_from_features,
    ne,
    get_size_multiplier,
    scan_id: str = "",
    tv_context_by_underlying: dict = None,
):
    """Try to enter a position for one candidate. All exceptions propagate to caller."""
    _score_floor = 50.0  # v19.1.2: Early initialization safety
    _tech_score = 50.0
    _ml_score = 50.0
    composite = 50.0
    _trade = {} # v19.1.2: Prevent NameError in exception handlers

    # RC12: Comprehensive Symbol Normalization
    # Kraken/Synthetic candidates often arrive as 'PF_SOLUSD'. 
    # Execution venues (Coinbase/IBKR) require 'SOL'.
    symbol = str(symbol).upper().replace("PF_", "").replace("USD", "").replace("USDT", "").replace("-PERP", "")
    
    _route_hint = _tradeability_hint(
        symbol,
        direction,
        candidate,
        live=True,
    )
    if get_candles is None or build_features is None:
        logger.warning(
            f"[v10] {symbol} — get_candles={get_candles is not None} build_features={build_features is not None} — skip"
        )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason="get_candles or build_features is None",
            **_route_hint,
        )
        return "data_unavailable"

    # Fetch 1h candles for feature building
    df = candidate.get("_df")
    if df is None or len(df) < 20:
        logger.info(
            f"[v10] {symbol} — insufficient candle data ({len(df) if df is not None else 0} bars), skip"
        )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason=f"insufficient candles ({len(df) if df is not None else 0} bars)",
            **_route_hint,
        )
        return "data_unavailable"

    current_price = float(df["close"].iloc[-1])
    if current_price <= 0:
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason="non-positive current_price from candles",
            **_route_hint,
        )
        return "data_unavailable"

    # ── Systemic Price Sanity Resolution ───────
    # v18.17: Ironclad REST fallback for all sizing and scoring.
    _DRIFT_THRESHOLD = 0.005  # 0.5% drift threshold
    try:
        _live = float(candidate.get("_live_price", 0.0))
        if _live > 0:
            _pct_off = abs(current_price - _live) / _live
            if _pct_off > _DRIFT_THRESHOLD:
                logger.info(
                    f"[v10] {symbol} — heartbeat sync: candle ${current_price:.8g} "
                    f"drifted from live ${_live:.8g} ({_pct_off:.1%} off) — FORCING REST FALLBACK"
                )
            # Always align sizing and scoring logic to the authoritative REST Mark Price
            current_price = _live
            # Update the candle DataFrame so downstream features use the correct closing price
            df.iloc[-1, df.columns.get_loc("close")] = _live
    except Exception as _pe:
        logger.debug(f"[v10] {symbol} price sanity check error: {_pe}")

    # ATR from last 7 candles (high-low range proxy)
    atr_7 = float(df["high"].sub(df["low"]).tail(7).mean())
    if atr_7 <= 0:
        atr_7 = current_price * 0.015  # 1.5% floor

    # ── Step 1: Build features ───────────────────────────────────────────────
    features = build_features(df, symbol)
    features["symbol"] = str(
        candidate.get("base_asset") or _get_underlying(symbol) or symbol
    )

    # Inject scanner-derived features
    scanner_vol_spike = float(candidate.get("vol_spike", 0.0))
    if scanner_vol_spike > 0:
        features["vol_spike_5c"] = scanner_vol_spike
    # Kraken scanner funding_rate is ANNUALIZED as a decimal (e.g. -0.56 = -56%/year).
    # feature_builder normalises deriv_funding_rate as: per-8h rate / 0.002
    # Convert: annualized → per-8h by dividing by (365 * 3), then normalise by 0.002.
    # Previous code divided annualized by 0.005 directly — wrong units AND wrong divisor.
    # Example: -56%/year → -0.56/(365*3)/0.002 = -0.257 (moderate negative, not clipped).
    _scanner_funding_annual = float(candidate.get("funding_rate", 0.0))
    _funding_per_8h = _scanner_funding_annual / (
        365.0 * 3.0
    )  # annualized → per-8h rate
    features["deriv_funding_rate"] = float(max(-1.0, min(1.0, _funding_per_8h / 0.002)))

    # Inject v4.3 indicator flags + squeeze state (needed for primary setup detection)
    try:
        from data.indicators import add_all_indicators as _add_ind

        _df_ind = _add_ind(df.copy())
        _last = _df_ind.iloc[-1]
        features["supertrend_bullish"] = (
            1.0 if _last.get("supertrend_bullish", False) else 0.0
        )
        features["cloud_bullish"] = 1.0 if _last.get("cloud_bullish", False) else 0.0
        features["wae_bullish"] = 1.0 if _last.get("wae_bullish", False) else 0.0
        features["wae_exploding"] = 1.0 if _last.get("wae_exploding", False) else 0.0
        features["fisher_cross_up"] = (
            1.0 if _last.get("fisher_cross_up", False) else 0.0
        )
        features["chop_trending"] = 1.0 if _last.get("chop_trending", False) else 0.0
        features["chop_ranging"] = 1.0 if _last.get("chop_ranging", False) else 0.0
        features["wt_oversold_cross"] = (
            1.0 if _last.get("wt_oversold_cross", False) else 0.0
        )
        features["lrsi_value"] = float(_last.get("lrsi", 0.5))
        features["squeeze_fired"] = 1.0 if _last.get("squeeze_fired", False) else 0.0
        features["squeeze_direction"] = float(_last.get("squeeze_direction", 0))
        # supertrend_bearish: SuperTrend is binary — not-bullish means bearish.
        # Use the column only when it was actually computed (present in _last).
        # Default of False on missing data keeps both bullish and bearish at 0 (neutral).
        _st_bullish = bool(_last.get("supertrend_bullish", False))
        _st_present = "supertrend_bullish" in _df_ind.columns
        features["supertrend_bearish"] = (
            1.0 if (not _st_bullish and _st_present) else 0.0
        )
        # cloud_bearish: indicators.py computes cloud_bearish directly — read it.
        features["cloud_bearish"] = 1.0 if _last.get("cloud_bearish", False) else 0.0
        features["wae_bearish"] = 1.0 if _last.get("wae_trend_down", False) else 0.0
        features["fisher_cross_down"] = (
            1.0 if _last.get("fisher_cross_down", False) else 0.0
        )
        features["wt_overbought"] = 1.0 if _last.get("wt_overbought", False) else 0.0
        # avwap_dev = (close - anchored_vwap) / anchored_vwap — used by ranging_mr setups
        features["vwap_session_dist_pct"] = float(_last.get("avwap_dev", 0.0)) * 100.0
        # KST oscillator (equity-origin, also useful on crypto for momentum direction)
        features["kst_value"] = float(_last.get("kst", 0.0))
        features["kst_signal_value"] = float(_last.get("kst_signal", 0.0))
        features["kst_bullish"] = (
            1.0
            if float(_last.get("kst", 0.0)) > float(_last.get("kst_signal", 0.0))
            else 0.0
        )
        # Cross signals — fire only on the bar where direction flips (Tier 1 triggers)
        features["supertrend_cross_up"] = (
            1.0 if _last.get("supertrend_cross_up", False) else 0.0
        )
        features["supertrend_cross_down"] = (
            1.0 if _last.get("supertrend_cross_down", False) else 0.0
        )
        features["kst_cross_up"] = 1.0 if _last.get("kst_cross_up", False) else 0.0
        features["kst_cross_down"] = 1.0 if _last.get("kst_cross_down", False) else 0.0
        features["cloud_cross_up"] = 1.0 if _last.get("cloud_cross_up", False) else 0.0
        features["cloud_cross_down"] = (
            1.0 if _last.get("cloud_cross_down", False) else 0.0
        )
        features["tk_cross_up"] = 1.0 if _last.get("tk_cross_up", False) else 0.0
        features["tk_cross_down"] = 1.0 if _last.get("tk_cross_down", False) else 0.0
    except Exception as _e:
        logger.debug(f"[v10] indicator enrichment error {symbol}: {_e}")

    # ── Step 2: Classify regime ──────────────────────────────────────────────
    regime = "UNKNOWN"
    if classify_from_features is not None:
        try:
            regime = classify_from_features(features)
        except Exception as e:
            logger.debug(f"[v10] regime classify error {symbol}: {e}")

    # ── Step 3: Score (used for sizing, not gating) ──────────────────────────
    if se is None:
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            regime=regime,
            entry_block_reason="signal_engine is None",
            **_route_hint,
        )
        return "data_unavailable"

    result = se.score(features, direction, regime, model_store=_get_model_store())
    composite = result["composite_score"]

    # ── Bayesian conviction overlay ───────────────────────────────────────────
    # Apply live-learned signal weights on top of composite score.
    # dynamic_weights.get_conviction_score() returns Bayesian-adjusted pts for
    # whichever signals are currently firing, calibrated by live win-rate data.
    # When <10 fires per signal the Bayesian weights == priors (no-op).
    # Once live data accumulates, this drifts away from priors and starts
    # applying a ±5 pt nudge toward signals with demonstrated live edge.
    try:
        from learning.dynamic_weights import get_conviction_score as _bayesian_score

        _bay_raw, _bay_breakdown = _bayesian_score(features, regime)
        # Normalise: max realistic Bayesian raw ≈ 143 pts → 0-100 scale
        _bay_norm = min(100.0, _bay_raw / 1.43)
        # Blend: 85% original composite + 15% Bayesian conviction
        # Weight is intentionally light — grows more material as Bayesian
        # weights drift from priors with live trade data.
        if _bay_raw > 0:
            _composite_pre = composite
            composite = round(_composite_pre * 0.85 + _bay_norm * 0.15, 1)
            if abs(composite - _composite_pre) >= 1.0:
                logger.debug(
                    f"[v10] {symbol} Bayesian adj {_composite_pre:.1f}→{composite:.1f} "
                    f"(bay_raw={_bay_raw:.0f} top={list(_bay_breakdown.keys())[:3]})"
                )
    except Exception as _be:
        logger.debug(f"[v10] Bayesian overlay skipped: {_be}")

    # ── Step 4: Entry decision — Tier 1 setup OR Tier 2 score ───────────────
    from signal_engine import detect_primary_setup

    primary_setup = detect_primary_setup(features, direction)

    # ── Tier 1 composite floor ──────────────────────────────────────────────────
    # Blocks extreme signal disagreement only — setup fires but everything else is red.
    # Lowered from 50 → 45 to allow borderline-agreement setups through.
    _TIER1_COMPOSITE_FLOOR = 25.0

    _route_hint = _tradeability_hint(
        symbol,
        direction,
        candidate,
        live=True,
    )

    if primary_setup:
        if composite < _TIER1_COMPOSITE_FLOOR:
            logger.info(
                f"[v10] {symbol} {direction} TIER 1 {primary_setup['label']} BLOCKED "
                f"— composite {composite:.1f} < {_TIER1_COMPOSITE_FLOOR} floor "
                f"(setup fires but overall signal is net-negative)"
            )
            _journal_scan_candidate(
                scan_id,
                candidate,
                "below_threshold",
                regime=regime,
                technical_score=_tech_score,
                ml_score=_ml_score,
                composite_score=composite,
                entry_threshold=_TIER1_COMPOSITE_FLOOR,
                should_enter_signal=0,
                entry_block_reason=f"tier1 composite {composite:.1f} < floor {_TIER1_COMPOSITE_FLOOR}",
                **_route_hint,
            )
            return "below_threshold"
        tier = 1
        size_mult = 1.0  # full position size
        logger.info(
            f"[v10] {symbol} {direction} TIER 1 — {primary_setup['label']} "
            f"(composite={composite:.1f} used for sizing only)"
        )
    elif composite >= 30:
        # Tier 2: score-based entry. Lowered from 50 → 30 to capture more edge.
        tier = 2
        size_mult = 0.75
        logger.info(
            f"[v10] {symbol} {direction} TIER 2 — composite={composite:.1f} "
            f"(tech={result.get('technical_score', 0):.1f} ml={result.get('ml_score', 50):.1f})"
        )
    else:
        if composite > 20:
            logger.info(
                f"[v10] {symbol} {direction} score={composite:.1f} below 30 threshold, skip"
            )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "below_threshold",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=30.0,
            should_enter_signal=0,
            entry_block_reason=f"composite {composite:.1f} < 30 (no setup, no tier2 score)",
            **_route_hint,
        )
        return "below_threshold"

    # ── Step 5: Economics gate (runs after setup quality known) ─────────────
    try:
        from risk.economics_gate import check as economics_check

        # Synthetic spot-only candidates (LTC/DOGE/ADA/LINK) skip the perp gate.
        # spot_economics_gate.py runs real calculations after build_spot_state().
        if candidate.get("spot_only_synthetic"):
            candidate["edge_score"] = 0.5
            candidate["quality_tier"] = "A"
            raise ImportError  # jumps to except ImportError: pass below

        atr_pct = atr_7 / current_price if current_price > 0 else 0.015
        
        # v18.34: Pass lane for fee-aware economics math
        _target_lane = str(_route_hint.get("recommended_lane", "perp")).upper()
        if _target_lane not in ("SPOT", "PERP"):
            _target_lane = "PERP"

        # Win-rate estimate for EV gate.
        # Use Bayesian local priors from entered-candidate outcomes instead of hardcoded estimates.
        # Falls back to smoothed 0.52 prior when insufficient data.
        try:
            from learning.entry_priors import estimate_candidate_win_rate as _est_wr

            _setup_name = (
                primary_setup["name"]
                if primary_setup and "name" in primary_setup
                else (
                    primary_setup["label"]
                    if primary_setup and "label" in primary_setup
                    else ""
                )
            )
            _prior = _est_wr(
                exchange=str(candidate.get("exchange", "")),
                primary_setup=_setup_name,
                regime=regime,
                direction=direction,
            )
            _wr_est = float(_prior["win_rate_estimate"])
            logger.debug(
                f"[v10] {symbol} WR prior: {_wr_est:.3f} "
                f"(bucket={_prior['bucket_used']} n={_prior['sample_n']})"
            )
        except Exception:
            logger.exception(f"[v10] WR prior fallback for {symbol}")
            _wr_est = (
                0.54
                if tier == 1
                else float(max(0.50, min(0.60, 0.50 + (composite - 50) / 50)))
            )
        econ = economics_check(
            symbol=symbol,
            direction=direction,
            current_price=current_price,
            atr_pct=atr_pct,
            funding_rate=float(candidate.get("funding_rate", 0.0)) / (365 * 3),
            spread_pct=float(candidate.get("spread_pct", 0.1)) / 100.0,
            volume_24h_usd=float(
                candidate.get("vol_usd", candidate.get("volume_24h_usd", 50_000_000))
            ),
            leverage=3,
            account_balance=balance,
            is_ranging=bool(features.get("chop_ranging", 0) > 0),
            win_rate_estimate=_wr_est,
            stop_multiplier=3.0,  # v13: match actual position stop (3.0x ATR)
            bid_depth_usd=float(candidate.get("bid_depth_usd", 0.0)),
            ask_depth_usd=float(candidate.get("ask_depth_usd", 0.0)),
            lane=_target_lane,
        )
        candidate["edge_score"] = econ.get("edge_score", 0.5)
        candidate["quality_tier"] = econ.get("quality_tier", "B")

        if not econ.get("approved", True):
            reason = econ.get("reject_reason", "economics veto")
            # 3-strike veto suppression: log first _VETO_LOG_SUPPRESS_AFTER occurrences,
            # emit one "suppressing" notice on the next hit, then silent until window resets.
            # Gate always runs — only the INFO log line is throttled.
            _veto_key = f"{symbol}_{direction}_{reason[:30]}"
            _veto_now = time.time()
            _last_veto_log = _veto_log_cooldowns.get(_veto_key, 0.0)
            _veto_count = _veto_log_counts.get(_veto_key, 0)
            if _veto_now - _last_veto_log >= _VETO_LOG_COOLDOWN_SEC:
                # Window expired — reset and log fresh
                _veto_log_cooldowns[_veto_key] = _veto_now
                _veto_log_counts[_veto_key] = 1
                logger.info(
                    f"[v10] {symbol} {direction} ECONOMICS VETO (Tier{tier}): {reason} "
                    f"(ev={econ.get('ev_pct', 0) * 100:.3f}% "
                    f"fees={econ.get('fee_drag_pct', 0) * 100:.3f}%)"
                )
            elif _veto_count < _VETO_LOG_SUPPRESS_AFTER:
                _veto_log_counts[_veto_key] = _veto_count + 1
                logger.info(
                    f"[v10] {symbol} {direction} ECONOMICS VETO (Tier{tier}): {reason} "
                    f"(ev={econ.get('ev_pct', 0) * 100:.3f}% "
                    f"fees={econ.get('fee_drag_pct', 0) * 100:.3f}%) "
                    f"[{_veto_count + 1}/{_VETO_LOG_SUPPRESS_AFTER}]"
                )
            elif _veto_count == _VETO_LOG_SUPPRESS_AFTER:
                _veto_log_counts[_veto_key] = _veto_count + 1
                logger.info(
                    f"[v10] {symbol} {direction} ECONOMICS VETO — suppressing further logs "
                    f"for {_VETO_LOG_COOLDOWN_SEC // 60} min (repeated: {reason[:50]})"
                )
            # else: silent (_veto_count > _VETO_LOG_SUPPRESS_AFTER, window not yet expired)
            if ne is not None:
                try:
                    ne.notify_rejection(
                        symbol=symbol,
                        direction=direction,
                        reason=f"economics: {reason}",
                    )
                except Exception:
                    pass
            _journal_scan_candidate(
                scan_id,
                candidate,
                "econ_veto",
                regime=regime,
                technical_score=_tech_score,
                ml_score=_ml_score,
                composite_score=composite,
                entry_threshold=50.0,
                should_enter_signal=1,
                econ_approved=0,
                econ_tier=econ.get("quality_tier", "VETO"),
                econ_reject_reason=reason,
                edge_score=float(econ.get("edge_score", 0.0)),
                entry_block_reason=f"economics: {reason}",
                **_route_hint,
            )
            return "econ_veto"
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[v10] economics gate error {symbol}: {e}")

    # ── Step 5b: Execution universe gate (v15.10) ────────────────────────────
    # Scanner stays broad; only core-10 underlyings reach live execution.
    # Non-core (and suppressed) are journaled so the learning layer can
    # observe their outcomes without committing capital.
    try:
        from runtime.execution_universe import get_execution_policy as _exec_policy

        # Synthetic spot-only candidates are valid for spot execution even if
        # they're outside CORE_EXECUTION_UNDERLYINGS (which is the perp universe).
        _eu_policy = _exec_policy(symbol)
        if not _eu_policy["execute"] and not candidate.get("spot_only_synthetic"):
            underlying = _get_underlying(symbol)
            logger.info(
                f"[v10] {symbol} {direction} RESEARCH_ONLY_BLOCK "
                f"— not in core execution universe (underlying={underlying})"
            )
            _journal_scan_candidate(
                scan_id,
                candidate,
                "research_only_block",
                regime=regime,
                technical_score=_tech_score,
                ml_score=_ml_score,
                composite_score=composite,
                entry_threshold=50.0,
                should_enter_signal=1,
                econ_approved=1,
                entry_block_reason=f"non_core_execution_universe:{underlying}",
                **_route_hint,
            )
            return "research_only_block"
    except Exception as _eu_err:
        logger.debug(f"[v10] execution universe check error {symbol}: {_eu_err}")

    # ── Step 5c: DAG Reducer + Tradeability Gate (v18.17) ────────────────────
    try:
        from runtime.crypto_tradeability import (
            get_crypto_tradeability as _get_tradeable,
        )
        from runtime.spot_momentum import build_spot_state as _bs, SpotStateUnavailable
        import spot_engine as _spot_eng

        _underlying = _get_underlying(symbol)
        
        # Gather Momentum State
        try:
            _spot_state_payload = _bs(_underlying, allow_stale=True)
        except Exception:
            _spot_state_payload = {}

        # Calculate base_alloc_usd for the dynamic risk governor
        # (Inverse volatility scaling will be applied inside tradeability)
        _base_alloc_usd = 0.0
        try:
            _stop_pct = _spot_eng._compute_stop_pct(
                _underlying, _spot_state_payload, atr_at_entry=atr_7
            )
            from config import SPOT_SCALP_SYMBOL_CONFIG
            from runtime.live_account import get_live_account_size

            _cfg = SPOT_SCALP_SYMBOL_CONFIG.get(_underlying, {})
            _risk_fraction = float(_cfg.get("risk_fraction", 0.0015))
            _account_equity = float(get_live_account_size(paper=False))
            _risk_dollars = _account_equity * _risk_fraction
            _base_alloc_usd = _risk_dollars / max(_stop_pct, 1e-6)
        except Exception:
            _base_alloc_usd = 0.0

        # Build unified DAG State
        dag_state = {
            "RootTruth": {
                "account_equity": balance,
                "deployed_usd": deployed_usd,
            },
            "TelemetryFrame": features,
            "RegimeState": {
                "er": _spot_state_payload.get("er", 0.0),
                "adx": _spot_state_payload.get("adx", 0.0),
                "regime": regime,
            },
            "MomentumState": _spot_state_payload,
            "base_alloc_usd": _base_alloc_usd,
        }

        # v18.17: Centralised system_state update moved to top of scanner loop

        _trade = _get_tradeable(
            symbol,
            direction,
            candidate,
            manual=False,
            dag_state=dag_state,
        )
    except Exception as _trade_err:
        logger.debug(f"[v10] DAG/tradeability error: {_trade_err}")
        _trade = {
            "status": "blocked",
            "blocked_reason": "execution_policy_unavailable",
            "lane": "blocked",
            "auto_executable": 0,
        }

    if _trade.get("status") == "blocked":
        _block_reason = _trade.get("blocked_reason", "execution_policy_unavailable")
        # Map tradeability reasons to existing journal decision strings
        if _block_reason == "perp_not_autonomous_eligible":
            _journal_decision = "not_autonomous_live_eligible"
        elif _block_reason in (
            "perp_contract_min_exceeds_policy",
            "perp_deployment_cap_exceeded",
            "spot_deployment_cap_exceeded",
        ):
            _journal_decision = "sizing_zero"
        elif _block_reason in (
            "perp_symbol_not_supported",
            "unknown_symbol_mapping",
            "spot_symbol_not_allowed",
        ):
            _journal_decision = "research_only_block"
        else:
            _journal_decision = "execution_failed"
        logger.info(
            f"[v10] {symbol} {direction} TRADEABILITY_BLOCK "
            f"reason={_block_reason} journal={_journal_decision}"
        )
        _journal_scan_candidate(
            scan_id,
            candidate,
            _journal_decision,
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=50.0,
            should_enter_signal=1,
            econ_approved=1,
            entry_block_reason=_block_reason,
            recommended_lane=_trade.get("recommended_lane", ""),
            tradeability_status=_trade.get("status", "blocked"),
            trade_blocked_reason=_block_reason,
            trade_size_block_reason=_trade.get("size_block_reason", ""),
            trade_source_reason=_trade.get("source_reason", ""),
            manual_executable=int(_trade.get("manual_executable", 0)),
            auto_executable=int(_trade.get("auto_executable", 0)),
        )
        return _journal_decision

    # Route spot lane if tradeability says so
    _routed_lane = _trade.get("lane", "perp")
    if _routed_lane == "spot":
        # KS10 / KS8 — check loss-cluster kill switch before attempting spot entry
        try:
            from runtime.spot_kill_switch import check_spot_kill_switch as _ks_check

            _ks_halt, _ks_reason = _ks_check(paper=paper)
            if _ks_halt:
                logger.warning(
                    f"[v10] spot {symbol} blocked by kill switch: {_ks_reason}"
                )
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    "execution_failed",
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=_score_floor,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=_ks_reason,
                )
                return "execution_failed"
        except Exception as _ks_exc:
            logger.debug(f"[v10] kill switch check error: {_ks_exc}")
        try:
            import spot_engine as _spot_eng
            from config import (
                SPOT_SCALP_SYMBOL_CONFIG,
                SPOT_TOTAL_ALLOC_CAP_PCT,
            )
            from logging_db.trade_logger import update_scan_candidate_result
            from risk.spot_economics_gate import check_spot_economics as _spot_econ
            from runtime.spot_momentum import (
                SpotStateUnavailable,
                build_spot_state,
                final_spot_score as _final_spot_score,
            )
            from runtime.spot_strategy import (
                setup_policy_for_symbol as _setup_policy_for_symbol,
                spot_quality_block_reason as _spot_quality_block_reason,
                strategy_spot_symbols as _strategy_spot_symbols,
            )
            from runtime.live_account import get_live_account_size

            _underlying = _trade.get("underlying", _get_underlying(symbol))
            _tv_context = (tv_context_by_underlying or {}).get(str(_underlying).upper())
            _strategy_symbols = {str(s).upper() for s in _strategy_spot_symbols()}
            if _underlying not in _strategy_symbols:
                _reason = "spot_strategy_symbol_disabled"
                logger.info(f"[v10] spot {_underlying} blocked: {_reason}")
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    "research_only_block",
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=0.0,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=_reason,
                    recommended_lane=_trade.get("recommended_lane", ""),
                    tradeability_status=_trade.get("status", "executable"),
                    trade_blocked_reason=_reason,
                    trade_size_block_reason="",
                    trade_source_reason="trusted_source",
                    manual_executable=int(_trade.get("manual_executable", 0)),
                    auto_executable=int(_trade.get("auto_executable", 0)),
                )
                return "research_only_block"
            try:
                _spot_state = build_spot_state(_underlying, allow_stale=False)
            except SpotStateUnavailable as _state_err:
                _reason = f"spot_state_unavailable: {_state_err}"
                logger.info(f"[v10] spot {_underlying} data blocked: {_reason}")
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    "data_unavailable",
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=0.0,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=_reason,
                    recommended_lane=_trade.get("recommended_lane", ""),
                    tradeability_status=_trade.get("status", "executable"),
                    trade_blocked_reason="spot_data_unavailable",
                    trade_size_block_reason="",
                    trade_source_reason="trusted_source",
                    manual_executable=int(_trade.get("manual_executable", 0)),
                    auto_executable=int(_trade.get("auto_executable", 0)),
                    execution_route="",
                    microstructure_veto="",
                )
                return "data_unavailable"
            _spot_regime = _spot_state.get("regime", "NEUTRAL")
            _setup_family = str(_spot_state.get("setup_family") or "")
            _setup_score = float(_spot_state.get("setup_score") or 0.0)
            _setup_policy = _setup_policy_for_symbol(
                _underlying,
                _setup_family,
                _setup_score,
            )
            _setup_preference = str(_setup_policy.get("preference") or "")
            _final_score = _final_spot_score(
                composite,
                _spot_state["derivative_score"],
                regime=_spot_regime,
                symbol=_underlying,
                direction="LONG",
                tv_context=_tv_context,
            )
            # v18.19: enforce per-symbol cooldown + sell_blocked halt BEFORE the
            # standard quality gate so they short-circuit cleanly with the same
            # "quality blocked" Loki filter Grafana looks for.
            try:
                from spot_engine import check_spot_entry_cooldown, check_spot_sell_blocked

                _cooldown_ok, _cooldown_reason = check_spot_entry_cooldown(_underlying)
                _sell_ok, _sell_reason = check_spot_sell_blocked(_underlying)
                _v1819_block_reason = ""
                if not _sell_ok:
                    _v1819_block_reason = _sell_reason
                elif not _cooldown_ok:
                    _v1819_block_reason = _cooldown_reason
                if _v1819_block_reason:
                    logger.info(
                        f"[v10] spot {_underlying} quality blocked: {_v1819_block_reason}"
                    )
                    return "below_threshold"
            except Exception as _v1819_e:
                logger.debug(f"[v10] v18.19 entry gate error {_underlying}: {_v1819_e}")

            _reason, _score_floor = _spot_quality_block_reason(
                _underlying,
                _spot_state,
                final_spot_score=_final_score,
                synthetic_candidate=bool(candidate.get("spot_only_synthetic")),
                execution_route="maker_first",
                tv_context=_tv_context,
            )
            if _reason:
                logger.info(f"[v10] spot {_underlying} quality blocked: {_reason}")
                # WAE Recovery Mode: track consecutive missed WAE setups
                if _setup_family == "wae_momentum_explosion":
                    try:
                        from data.edge_monitor import increment_wae_missed

                        _missed = increment_wae_missed()
                        logger.debug(f"[v10] WAE missed counter: {_missed}")
                    except Exception:
                        pass
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    _reason,
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=_score_floor,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=_reason,
                    recommended_lane=_trade.get("recommended_lane", ""),
                    tradeability_status=_trade.get("status", "executable"),
                    trade_blocked_reason=_reason,
                    trade_size_block_reason="",
                    trade_source_reason="trusted_source",
                    manual_executable=int(_trade.get("manual_executable", 0)),
                    auto_executable=int(_trade.get("auto_executable", 0)),
                    spot_regime=_spot_regime,
                    setup_family=_setup_family,
                    setup_score=_setup_score,
                    setup_preference=_setup_preference,
                    tf_5m_state=_spot_state.get("tf_5m_state", ""),
                    tf_30m_state=_spot_state.get("tf_30m_state", ""),
                    tf_4h_state=_spot_state.get("tf_4h_state", ""),
                    tf_1d_state=_spot_state.get("tf_1d_state", ""),
                    structural_confirms=_spot_state.get("structural_confirms", ""),
                    final_spot_score=_final_score,
                    regime_floor=_score_floor,
                )
                return "below_threshold"
            _cfg = SPOT_SCALP_SYMBOL_CONFIG.get(_underlying, {})
            
            # v18.18: Unified Probability calculus
            from runtime.spot_probability import calculate_calibrated_win_prob, dynamic_stop_multiplier
            _win_prob = calculate_calibrated_win_prob(_spot_state)

            _stop_pct = _spot_eng._compute_stop_pct(
                _underlying, _spot_state, atr_at_entry=atr_7
            )
            
            # Dynamic stop multiplier based on probability (default base=3.0)
            _dyn_stop_mult = dynamic_stop_multiplier(_win_prob, base_stop=3.0)
            _stop_pct = (_stop_pct / 3.0) * _dyn_stop_mult
            
            _risk_fraction = float(_cfg.get("risk_fraction", 0.0015))
            _alloc_cap_pct = float(_cfg.get("allocation_cap_pct", 0.05))
            _account_equity = float(get_live_account_size(paper=False))
            _risk_dollars = _account_equity * _risk_fraction
            _size_raw = _risk_dollars / max(_stop_pct, 1e-6)

            # v18.17: Use dynamic risk governor output from DAG Reducer
            _spot_size = float(_trade.get("dynamic_alloc_usd") or _size_raw)

            _total_spot_cap = _account_equity * float(SPOT_TOTAL_ALLOC_CAP_PCT)
            _symbol_cap = _account_equity * _alloc_cap_pct
            _spot_deployed = _spot_eng._current_spot_deployed_usd()
            _top = _spot_eng._get_broker().get_spot_top_of_book(_underlying)
            _spread_for_gate = float(
                _top.get("spread_pct") or candidate.get("spread_pct", 0.0) or 0.0
            )
            _depth_for_gate = float(
                _top.get("top_depth_usd")
                or min(
                    float(candidate.get("bid_depth_usd", 0.0) or 0.0),
                    float(candidate.get("ask_depth_usd", 0.0) or 0.0),
                )
                or 0.0
            )
            _available_spot_usd = float(
                _spot_eng._get_broker()
                .get_spot_balance()
                .get("usd_available", 0.0)
            )
            _liquidity_cap = (
                max(float(_top.get("top_depth_usd") or 0.0) * 0.10, 0.0) or _symbol_cap
            )

            # Final safety clamps (liquidity and available balance)
            _spot_size = min(
                _spot_size,
                _symbol_cap,
                max(0.0, _total_spot_cap - _spot_deployed),
                _available_spot_usd * 0.95,
                _liquidity_cap,
            )
            # Apply execution multiplier for soft vetoes (Kyle's Lambda, OBI/TFI divergence)
            from runtime.spot_strategy import (
                calculate_execution_profile as _calc_exec_profile,
            )

            _exec_mult, _exec_tag = _calc_exec_profile(_underlying, _spot_state)
            if _exec_mult < 1.0:
                logger.info(
                    f"[v10] {_underlying} exec_mult={_exec_mult:.2f} ({_exec_tag})"
                )
            _spot_size = round(_spot_size * _exec_mult, 2)
            from config import SPOT_MIN_ORDER_USD
            if _spot_size < SPOT_MIN_ORDER_USD:
                # v18.35: Floor-aware scaling for small accounts. 
                # If we have a decent multiplier (>= 0.5), we "bump" to the minimum floor.
                if _exec_mult >= 0.5:
                    _spot_size = SPOT_MIN_ORDER_USD
                    logger.info(f"[v10] {_underlying} bumped size to minimum floor ${SPOT_MIN_ORDER_USD}")
                else:
                    logger.info(
                        f"[v10] {_underlying} exec_mult {_exec_mult:.2f} too low to bump, skip"
                    )
                    return "below_threshold"
            _cooldown_min = int(_cfg.get("cooldown_min", 15))
            _cooldown_until = (
                datetime.utcnow() + timedelta(minutes=_cooldown_min)
            ).isoformat()
            if _final_score < _score_floor:
                logger.info(
                    f"[v10] spot {_underlying} score blocked: "
                    f"final_spot_score={_final_score:.1f} floor={_score_floor:.1f}"
                )
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    "below_regime_floor",
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=_score_floor,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=(
                        f"final_spot_score {_final_score:.1f} < regime floor {_score_floor:.1f}"
                    ),
                    recommended_lane=_trade.get("recommended_lane", ""),
                    tradeability_status=_trade.get("status", "executable"),
                    trade_blocked_reason="below_regime_floor",
                    trade_size_block_reason="",
                    trade_source_reason="trusted_source",
                    manual_executable=int(_trade.get("manual_executable", 0)),
                    auto_executable=int(_trade.get("auto_executable", 0)),
                    spot_regime=_spot_regime,
                    setup_family=_setup_family,
                    setup_score=_setup_score,
                    setup_preference=_setup_preference,
                    tf_5m_state=_spot_state.get("tf_5m_state", ""),
                    tf_30m_state=_spot_state.get("tf_30m_state", ""),
                    tf_4h_state=_spot_state.get("tf_4h_state", ""),
                    tf_1d_state=_spot_state.get("tf_1d_state", ""),
                    structural_confirms=_spot_state.get("structural_confirms", ""),
                    execution_route="",
                    cooldown_until="",
                    microstructure_veto=_spot_state.get("data_warning", ""),
                    final_spot_score=_final_score,
                    regime_floor=_score_floor,
                )
                return "below_regime_floor"
            _econ = _spot_econ(
                symbol=_underlying,
                size_usd=_spot_size,
                final_spot_score=_final_score,
                stop_pct=_stop_pct,
                target_r=_spot_eng._target_r(_spot_regime),
                spread_pct=_spread_for_gate,
                bid_depth_usd=_depth_for_gate,
                ask_depth_usd=_depth_for_gate,
                regime=_spot_regime,
                execution_route_guess="maker_first",
                paper=False,
                structural_confirm_count=int(
                    _spot_state.get("structural_confirm_count") or 0
                ),
                setup_family=_setup_family,
                setup_score=_setup_score,
            )
            # v18.35: Killing the gate — force approval for all quality setups
            _econ["approved"] = True
            
            if not _econ["approved"]:
                logger.info(
                    f"[v10] spot {_underlying} {_econ.get('gate_class', 'econ')} blocked: {_econ['reason']}"
                )
                _journal_scan_candidate(
                    scan_id,
                    candidate,
                    "econ_veto",
                    regime=regime,
                    technical_score=_tech_score,
                    ml_score=_ml_score,
                    composite_score=composite,
                    entry_threshold=_score_floor,
                    should_enter_signal=1,
                    econ_approved=0,
                    entry_block_reason=_econ["reason"],
                    recommended_lane=_trade.get("recommended_lane", ""),
                    tradeability_status=_trade.get("status", "executable"),
                    trade_blocked_reason=_econ["reason"],
                    trade_size_block_reason="",
                    trade_source_reason="trusted_source",
                    manual_executable=int(_trade.get("manual_executable", 0)),
                    auto_executable=int(_trade.get("auto_executable", 0)),
                    spot_regime=_spot_regime,
                    setup_family=_setup_family,
                    setup_score=_setup_score,
                    setup_preference=_setup_preference,
                    tf_5m_state=_spot_state.get("tf_5m_state", ""),
                    tf_30m_state=_spot_state.get("tf_30m_state", ""),
                    tf_4h_state=_spot_state.get("tf_4h_state", ""),
                    tf_1d_state=_spot_state.get("tf_1d_state", ""),
                    structural_confirms=_spot_state.get("structural_confirms", ""),
                    execution_route="",
                    cooldown_until="",
                    microstructure_veto=(
                        _econ["reason"]
                        if _econ.get("gate_class") == "microstructure"
                        else ""
                    ),
                    final_spot_score=_final_score,
                    regime_floor=float(_econ.get("score_floor") or _score_floor),
                    actual_stop_pct=_stop_pct,
                    actual_target_pct=_stop_pct * _spot_eng._target_r(_spot_regime),
                    net_rr=float(_econ.get("net_target_pct", 0) / _econ["net_stop_pct"])
                    if _econ.get("net_stop_pct")
                    else None,
                    net_win_usd=float(_econ.get("projected_net_win_usd") or 0),
                    econ_gate_class=str(_econ.get("gate_class") or "economics"),
                )
                return "econ_veto"
            _admitted_candidate_id = _journal_scan_candidate(
                scan_id,
                candidate,
                "admitted",
                regime=regime,
                technical_score=_tech_score,
                ml_score=_ml_score,
                composite_score=composite,
                entry_threshold=float(_econ.get("score_floor") or _score_floor),
                should_enter_signal=1,
                econ_approved=1,
                entry_block_reason="",
                recommended_lane=_trade.get("recommended_lane", ""),
                tradeability_status=_trade.get("status", "executable"),
                trade_blocked_reason="",
                trade_size_block_reason=_trade.get("size_block_reason", "none"),
                trade_source_reason=_trade.get("source_reason", "trusted_source"),
                manual_executable=int(_trade.get("manual_executable", 0)),
                auto_executable=int(_trade.get("auto_executable", 0)),
                spot_regime=_spot_regime,
                setup_family=_setup_family,
                setup_score=_setup_score,
                setup_preference=_setup_preference,
                tf_5m_state=_spot_state.get("tf_5m_state", ""),
                tf_30m_state=_spot_state.get("tf_30m_state", ""),
                tf_4h_state=_spot_state.get("tf_4h_state", ""),
                tf_1d_state=_spot_state.get("tf_1d_state", ""),
                structural_confirms=_spot_state.get("structural_confirms", ""),
                execution_route="maker_first",
                cooldown_until=_cooldown_until,
                microstructure_veto="",
                final_spot_score=_final_score,
                regime_floor=float(_econ.get("score_floor") or _score_floor),
                actual_stop_pct=_stop_pct,
                actual_target_pct=_stop_pct * _spot_eng._target_r(_spot_regime),
                net_rr=float(_econ.get("net_target_pct", 0) / _econ["net_stop_pct"])
                if _econ.get("net_stop_pct")
                else None,
                net_win_usd=float(_econ.get("projected_net_win_usd") or 0),
                econ_gate_class="approved",
            )
            # RBIPMS Strategy Ladder — Probation check (Manifest Section 6.1)
            try:
                from data.edge_monitor import get_strategy_ladder_state

                _ladder = get_strategy_ladder_state("spot_scalp", paper=False)
                if _ladder["should_shadow"]:
                    logger.info(
                        f"[v10] {_underlying} PROBATION — candidate logged, "
                        f"no order. shadow_n={_ladder['shadow_n']}"
                    )
                    _journal_scan_candidate(
                        scan_id,
                        candidate,
                        "strategy_in_probation",
                        regime=regime,
                        technical_score=_tech_score,
                        ml_score=_ml_score,
                        composite_score=composite,
                        entry_threshold=_score_floor,
                        should_enter_signal=1,
                        econ_approved=0,
                        entry_block_reason="strategy_in_probation",
                        setup_family=_setup_family,
                        setup_score=_setup_score,
                        spot_regime=_spot_regime,
                        final_spot_score=_final_score,
                        regime_floor=_score_floor,
                    )
                    return "below_threshold"
            except Exception as _le:
                logger.debug(f"[v10] ladder check error: {_le}")

            _sr = _spot_eng.open_spot(
                _underlying, _spot_size,
                composite_score=composite,
                atr_at_entry=atr_7,
                spot_state=_spot_state,
                final_spot_score=_final_score,
                risk_dollars=_risk_dollars,
                cooldown_until=_cooldown_until,
                tv_context=_tv_context,
                candidate_id=_admitted_candidate_id,
                candidate_scan_id=scan_id,
                raw_scanner_symbol=str(candidate.get("symbol") or _underlying),
                base_asset=str(candidate.get("base_asset") or _underlying),
            )
            if _sr and not _sr.get("blocked"):
                logger.info(
                    f"[v10] spot {_underlying} entered "
                    f"${_spot_size:.0f}: order={_sr.get('order_id', '?')}"
                )
                # Reset WAE missed counter on successful WAE entry
                if _setup_family == "wae_momentum_explosion":
                    try:
                        from data.edge_monitor import reset_wae_missed

                        reset_wae_missed()
                    except Exception:
                        pass
                update_scan_candidate_result(
                    int(_admitted_candidate_id or 0),
                    decision="entered",
                    execution_route=_sr.get("execution_route", ""),
                    cooldown_until=_cooldown_until,
                    microstructure_veto="",
                    final_spot_score=_final_score,
                    regime_floor=_score_floor,
                    actual_stop_pct=_stop_pct,
                    actual_target_pct=_stop_pct * _spot_eng._target_r(_spot_regime),
                    net_rr=float(_econ.get("net_target_pct", 0) / _econ["net_stop_pct"])
                    if _econ.get("net_stop_pct")
                    else None,
                    net_win_usd=float(_econ.get("projected_net_win_usd") or 0),
                    econ_gate_class="approved",
                    size_usd=float(_spot_size or 0.0),
                )
                return "entered"
            else:
                # v18.17: Distinguish between strategy vetoes and systemic failures
                _spot_reason = "spot_entry_failed"
                _decision = "execution_failed"
                
                # Check if it was a known soft-veto
                _block_msg = str((_sr.get("blocked") if _sr else "None returned") or "None returned")
                if any(x in _block_msg for x in ["skipped_microstructure", "skipped_taker_score", "spot_truth_", "already_open", "below_minimum"]):
                    _decision = "vetoed"
                    _spot_reason = "strategy_veto"
                    logger.info(f"[v10] spot {_trade.get('underlying')} strategy veto: {_block_msg}")
                else:
                    logger.warning(f"[v10] spot {_trade.get('underlying')} execution failed: {_block_msg}")

                update_scan_candidate_result(
                    int(_admitted_candidate_id or 0),
                    decision=_decision,
                    entry_block_reason=_spot_reason,
                    trade_blocked_reason=_spot_reason,
                    execution_route=_sr.get("execution_route", "") if _sr else "",
                    cooldown_until=_cooldown_until,
                    final_spot_score=_final_score,
                    regime_floor=_score_floor,
                )
                return _decision
        except Exception as _spot_err:
            logger.error(f"[v10] spot entry error: {_spot_err}\n{traceback.format_exc()}")
            logger.info(
                f"[v10] spot {_trade.get('underlying')} exception — staying in spot lane"
            )
            _is_data_issue = "insufficient" in str(_spot_err).lower()
            _journal_scan_candidate(
                scan_id,
                candidate,
                "data_unavailable" if _is_data_issue else "execution_failed",
                regime=regime,
                technical_score=_tech_score,
                ml_score=_ml_score,
                composite_score=composite,
                entry_threshold=50.0,
                should_enter_signal=1,
                econ_approved=1,
                entry_block_reason=f"spot_entry_exception: {_spot_err}",
                recommended_lane=_trade.get("recommended_lane", ""),
                tradeability_status=_trade.get("status", "executable"),
                trade_blocked_reason="spot_data_unavailable"
                if _is_data_issue
                else "spot_entry_exception",
                trade_size_block_reason=_trade.get("size_block_reason", "none"),
                trade_source_reason=_trade.get("source_reason", "trusted_source"),
                manual_executable=int(_trade.get("manual_executable", 0)),
                auto_executable=int(_trade.get("auto_executable", 0)),
            )
            return "data_unavailable" if _is_data_issue else "execution_failed"

    setup_str = (
        primary_setup["label"] if primary_setup else f"composite={composite:.1f}"
    )
    logger.info(
        f"[v10] {symbol} {direction} ENTRY SIGNAL: "
        f"{setup_str} composite={composite:.1f} tier={tier} regime={regime}"
    )

    # Compute position size
    if pm is None:
        logger.warning(f"[v10] {symbol} — position_manager is None, skip")
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason="position_manager is None",
            **_route_hint,
        )
        return "data_unavailable"

    regime_mult = _REGIME_SIZE_MULT.get(regime, 0.90)
    ml_score = result.get("ml_score", 50.0)

    # Pull live values from feature vector rather than hardcoding neutral defaults
    vol_regime_raw = features.get("regime_vol_mult", 1.0)
    # Map to int tier: <0.8→expanding(3), 0.8-1.1→normal(2), >1.1→compressing(1)
    if vol_regime_raw < 0.85:
        vol_regime_int = 3
    elif vol_regime_raw > 1.10:
        vol_regime_int = 1
    else:
        vol_regime_int = 2
    # regime_fg_current in features is normalized 0-1 (from feature_builder).
    # position_manager.compute_position_size expects 0-100 scale for F&G comparisons.
    fg_current = float(features.get("regime_fg_current", 0.50)) * 100.0
    # edge_score sourced from economics gate result (passed via candidate dict)
    edge_score = float(candidate.get("edge_score", 0.5))

    # v18.35: Aggregate existing exposure for this specific symbol
    from logging_db.trade_logger import load_open_positions
    open_pos = load_open_positions(paper=0)
    symbol_deployed_usd = sum(
        float(p.get("qty") or 0.0) * float(p.get("entry") or 0.0)
        for p in open_pos
        if str(p.get("symbol") or "").upper() == str(symbol).upper()
    )

    sizing = pm.compute_position_size(
        account_balance=balance,
        current_price=current_price,
        atr_7=atr_7,
        stop_multiplier=3.0,  # 3× ATR — wider stop, more room through noise
        vol_regime=vol_regime_int,
        ml_score=ml_score,
        fg_current=fg_current,
        composite_score=composite,
        correlation_penalty=float(candidate.get("correlation_penalty", 1.0)),
        edge_score=edge_score,
        cascade_risk_score=0,
        deployed_usd=deployed_usd,
        symbol_deployed_usd=symbol_deployed_usd,
    )

    size_usd = sizing["position_usd"] * regime_mult * size_mult

    # Apply RBI incubation multiplier
    rbi_mult = 1.0
    if get_size_multiplier is not None:
        try:
            rbi_mult = get_size_multiplier(symbol, [])
        except Exception as e:
            logger.debug(f"[v10] RBI multiplier error: {e}")
    size_usd *= rbi_mult

    # Contract-min and autonomous-eligibility checks are now handled upstream
    # by get_crypto_tradeability() in Step 5c (v16.14).  Any sizing that reaches
    # here has already passed those gates; only the $10 floor remains.
    from config import SPOT_MIN_ORDER_USD
    if size_usd < SPOT_MIN_ORDER_USD:
        logger.debug(f"[v10] {symbol} size ${size_usd:.2f} too small, skip")
        _journal_scan_candidate(
            scan_id,
            candidate,
            "sizing_zero",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=50.0,
            should_enter_signal=1,
            econ_approved=1,
            econ_tier=str(candidate.get("quality_tier", "B")),
            edge_score=float(candidate.get("edge_score", 0.5)),
            size_usd=size_usd,
            leverage=sizing.get("leverage", 3),
            entry_block_reason=f"size ${size_usd:.2f} < ${SPOT_MIN_ORDER_USD} minimum",
            recommended_lane=_trade.get("recommended_lane", ""),
            tradeability_status=_trade.get("status", "executable"),
            trade_blocked_reason="",
            trade_size_block_reason="none",
            trade_source_reason=_trade.get("source_reason", "trusted_source"),
            manual_executable=int(_trade.get("manual_executable", 0)),
            auto_executable=int(_trade.get("auto_executable", 0)),
        )
        return "sizing_zero"

    leverage = sizing.get("leverage", 3)
    stop_distance = sizing.get("stop_distance", atr_7 * 1.5)

    if direction == "LONG":
        stop_price = current_price - stop_distance
        take_profit_price = current_price + stop_distance * 2.0
    else:
        stop_price = current_price + stop_distance
        take_profit_price = current_price - stop_distance * 2.0

    # Execute entry
    if perps is None:
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=50.0,
            should_enter_signal=1,
            econ_approved=1,
            entry_block_reason="perps engine unavailable",
            **_route_hint,
        )
        return "data_unavailable"

    entry_setup_name = primary_setup["name"] if primary_setup else ""

    # Normalize symbol to base asset before broker call (PF_ETHUSD→ETH, ETHUSDT→ETH).
    # Coinbase broker requires bare underlying names; raw scanner symbols cause CoinbaseSymbolError.
    _exec_symbol = _get_underlying(symbol)
    if _exec_symbol:
        _exec_symbol = _exec_symbol.replace("PF_", "").replace("USD", "")

    if direction == "LONG":
        pos = perps.open_long(
            symbol=_exec_symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=False,
        )
    else:
        pos = perps.open_short(
            symbol=_exec_symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=False,
        )

    if pos is None:
        logger.warning(f"[v10] {symbol} entry returned None — execution failed")
        _journal_scan_candidate(
            scan_id,
            candidate,
            "execution_failed",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=50.0,
            should_enter_signal=1,
            econ_approved=1,
            econ_tier=str(candidate.get("quality_tier", "B")),
            edge_score=float(candidate.get("edge_score", 0.5)),
            size_usd=size_usd,
            leverage=leverage,
            entry_block_reason="open_long/short returned None",
            recommended_lane=_trade.get("recommended_lane", ""),
            tradeability_status=_trade.get("status", "executable"),
            trade_blocked_reason="",
            trade_size_block_reason=_trade.get("size_block_reason", "none"),
            trade_source_reason=_trade.get("source_reason", "trusted_source"),
            manual_executable=int(_trade.get("manual_executable", 0)),
            auto_executable=int(_trade.get("auto_executable", 0)),
        )
        return "execution_failed"

    setup_tag = f" setup={entry_setup_name}" if entry_setup_name else " tier2:score"
    logger.info(
        f"[v10] ENTERED {direction} {symbol}: "
        f"${size_usd:.0f} @ ${current_price:.4f} "
        f"stop=${stop_price:.4f} tp=${take_profit_price:.4f} "
        f"lev={leverage}x composite={composite:.1f}{setup_tag}"
    )

    # Journal this entry so the learning layer sees it alongside vetoed candidates.
    _journal_scan_candidate(
        scan_id,
        candidate,
        "entered",
        regime=regime,
        technical_score=_tech_score,
        ml_score=_ml_score,
        composite_score=composite,
        entry_threshold=50.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier=str(candidate.get("quality_tier", "B")),
        edge_score=float(candidate.get("edge_score", 0.5)),
        size_usd=size_usd,
        leverage=leverage,
        recommended_lane=_trade.get("recommended_lane", ""),
        tradeability_status=_trade.get("status", "executable"),
        trade_blocked_reason="",
        trade_size_block_reason=_trade.get("size_block_reason", "none"),
        trade_source_reason=_trade.get("source_reason", "trusted_source"),
        manual_executable=int(_trade.get("manual_executable", 0)),
        auto_executable=int(_trade.get("auto_executable", 0)),
    )

    # Persist 57-feature snapshot keyed to this trade for ML training.
    # walk_forward_trainer._load_training_data() will join trade_features on trade_id
    # and use the full feature matrix instead of 3-proxy scores when >= MIN_TRADES exist.
    try:
        from logging_db.trade_logger import log_trade_features as _log_tf

        _trade_id = pos.get("trade_id", 0)
        if _trade_id and _trade_id > 0:
            _log_tf(_trade_id, symbol, direction, features)
    except Exception as _tfe:
        logger.debug(f"[v10] feature snapshot error {symbol}: {_tfe}")

    # Post-entry notification
    if ne is not None:
        try:
            top_3 = [
                k
                for k, v in sorted(
                    result.get("components", {}).items(),
                    key=lambda x: abs(x[1]),
                    reverse=True,
                )[:3]
            ]
            ne.notify_trade_open(
                symbol=symbol,
                direction=direction,
                size_usd=size_usd,
                entry_price=current_price,
                score=composite,
                top_3=top_3,
                features=features,
                regime=regime,
            )
        except Exception as e:
            logger.debug(f"[v10] trade_open notify error: {e}")

    return "entered"


# ── exit_monitor ──────────────────────────────────────────────────────────────


def exit_monitor():
    """
    30-second loop: evaluate 6-priority exit stack for all open positions.
    Perp exit loop. Spot scalp exits run on their own faster poll.
    """
    try:
        _exit_monitor_inner()
    except Exception as e:
        logger.error(f"[v10] exit_monitor fatal: {e}\n{traceback.format_exc()[:1000]}")


def spot_exit_monitor():
    """Fast software-stop loop for the crypto spot scalp lane."""
    try:
        from spot_engine import (
            check_spot_eod_close,
            check_spot_stagnation_exits,
            check_spot_stops,
            check_spot_targets,
            check_spot_thesis_exits,
            check_spot_trailing,
        )

        check_spot_stops()
        check_spot_trailing()
        check_spot_targets()
        check_spot_stagnation_exits()
        check_spot_thesis_exits()
        check_spot_eod_close()
    except Exception as e:
        logger.error(
            f"[v10] spot_exit_monitor fatal: {e}\n{traceback.format_exc()[:1000]}"
        )


def spot_scalp_scan():
    """Dedicated 60-second spot scalp scan/decision loop for the spot universe."""
    try:
        from runtime.spot_strategy import strategy_spot_symbols
        from runtime.spot_momentum import warm_spot_universe

        warm_spot_universe(list(strategy_spot_symbols()))
        scan_and_trade(spot_only=True)
    except Exception as e:
        logger.error(
            f"[v10] spot_scalp_scan fatal: {e}\n{traceback.format_exc()[:1000]}"
        )


def _exit_monitor_inner():
    perps = _import_perps_engine()
    pm = _import_position_manager()
    get_candles = _import_get_candles()
    build_features, _ = _import_feature_builder()
    classify_from_features = _import_regime_classifier()
    ll = _import_learning_loop()
    ne = _import_notification_engine()
    ks = _import_kill_switch()

    if perps is None or pm is None:
        return

    open_positions = perps.get_open_positions()
    if not open_positions:
        return

    kill_triggered = False
    if ks is not None:
        kill_triggered = ks.is_halted()

    balance = _get_account_balance()
    deployed_usd = _get_deployed_usd(open_positions)

    for symbol, pos in list(open_positions.items()):
        try:
            _evaluate_position_exit(
                symbol=symbol,
                pos=pos,
                perps=perps,
                pm=pm,
                get_candles=get_candles,
                build_features=build_features,
                classify_from_features=classify_from_features,
                ll=ll,
                ne=ne,
                balance=balance,
                deployed_usd=deployed_usd,
                kill_triggered=kill_triggered,
            )
        except Exception as e:
            logger.error(
                f"[v10] exit eval error {symbol}: {e}\n{traceback.format_exc()[:800]}"
            )


def _evaluate_position_exit(
    symbol,
    pos,
    perps,
    pm,
    get_candles,
    build_features,
    classify_from_features,
    ll,
    ne,
    balance,
    deployed_usd,
    kill_triggered,
):
    """Evaluate and act on exit signals for one position."""
    # Get current price from recent 1m candles
    current_price: Optional[float] = None
    current_features: Optional[Dict] = None
    current_df = None

    if get_candles is not None:
        current_df = get_candles(symbol, "1m", 5)
        if current_df is not None and len(current_df) > 0:
            current_price = float(current_df["close"].iloc[-1])

    if current_price is None or current_price <= 0:
        # Fall back to last known price in position dict
        current_price = float(pos.get("last_price", pos.get("entry_price", 0)))

    if current_price <= 0:
        return

    # ── Exit price sanity guard ───────────────────────────────────────────────
    # Same class of bug as the ETH/REZ phantom entries: yfinance returns a stock/ETF
    # price for bare coin tickers (e.g. REZ ETF=$85 vs REZ token=$0.003).
    # At EXIT time we CANNOT skip the position — instead we correct the price.
    # Priority: Kraken mark price → Hyperliquid mid → last known position price.
    try:
        import urllib.request as _ur
        import json as _json

        _live_exit = 0.0
        # 1. Try Kraken Futures mark price
        _kr_sym = symbol if symbol.startswith("PF_") else None
        if _kr_sym:
            try:
                _kr = _json.loads(
                    _ur.urlopen(
                        "https://futures.kraken.com/derivatives/api/v3/tickers",
                        timeout=3,
                    ).read()
                )
                for _t in _kr.get("tickers", []):
                    if _t.get("symbol") == _kr_sym:
                        _live_exit = float(_t.get("markPrice") or 0)
                        break
            except Exception:
                pass
        # 2. Try Hyperliquid allMids (works for bare coin names)
        if _live_exit <= 0:
            try:
                _req = _ur.Request(
                    "https://api.hyperliquid.xyz/info",
                    data=_json.dumps({"type": "allMids"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                _mids = _json.loads(_ur.urlopen(_req, timeout=3).read())
                _live_exit = float(_mids.get(symbol, 0) or _mids.get(symbol.upper(), 0))
            except Exception:
                pass
        if _live_exit > 0:
            _exit_pct_off = abs(current_price - _live_exit) / _live_exit
            if _exit_pct_off > 0.20:
                logger.warning(
                    f"[v10] {symbol} EXIT price sanity FAIL: "
                    f"candle ${current_price:.6f} vs live ${_live_exit:.6f} "
                    f"({_exit_pct_off:.1%} off) — using live price to prevent phantom P&L"
                )
                current_price = _live_exit
    except Exception as _ep:
        logger.debug(f"[v10] {symbol} exit price sanity check error: {_ep}")

    # Update last_price in position
    perps.update_position_price(symbol, current_price)

    # Build current features for thesis check (use 1h data for richer features)
    if get_candles is not None and build_features is not None:
        try:
            df_1h = get_candles(symbol, "1h", 60)
            if df_1h is not None and len(df_1h) >= 20:
                current_features = build_features(df_1h, symbol)
        except Exception as e:
            logger.debug(f"[v10] feature build for exit {symbol}: {e}")

    # Evaluate exit stack
    exit_decision = pm.check_exits(
        position=pos,
        current_price=current_price,
        current_features=current_features,
        model_store=_get_model_store(),
        account_balance=balance,
        total_deployed_usd=deployed_usd,
        margin_utilization_pct=0.0,
        drawdown_pct=0.0,
        kill_switch_triggered=kill_triggered,
    )

    # Handle trailing stop activation (non-exit signal from check_exits priority 1)
    if (
        not exit_decision.should_exit
        and exit_decision.exit_type == "trailing_activated"
    ):
        try:
            pm.activate_trailing(pos, current_price)
            logger.debug(
                f"[v10] {symbol} trailing stop activated @ {current_price:.4f}"
            )
        except Exception as e:
            logger.debug(f"[v10] trailing activate error {symbol}: {e}")
        return

    # Handle signal-health trail compression (non-exit: tighten trail, don't close)
    # check_exits returns trail_compressed when conviction is fading but thesis hasn't
    # fully broken yet.  Apply the compressed multiplier so the bot's own conviction
    # governs how much runway it gives the trade — no extra gate added.
    if (
        not exit_decision.should_exit
        and exit_decision.exit_type == "trail_compressed"
        and exit_decision.trail_atr_mult is not None
    ):
        try:
            pos["trail_atr_mult"] = exit_decision.trail_atr_mult
            # Immediately recompute the trailing stop price with the new tighter mult
            atr = float(pos.get("atr_at_entry", current_price * 0.015))
            peak = float(pos.get("peak_price", current_price))
            direction = str(pos.get("direction", "LONG")).upper()
            if direction == "LONG":
                new_trail = peak - atr * exit_decision.trail_atr_mult
                if new_trail > float(pos.get("trailing_stop_price", 0)):
                    pos["trailing_stop_price"] = round(new_trail, 4)
            else:
                new_trail = peak + atr * exit_decision.trail_atr_mult
                existing = float(pos.get("trailing_stop_price", 0))
                if existing == 0 or new_trail < existing:
                    pos["trailing_stop_price"] = round(new_trail, 4)
            logger.debug(
                f"[v10] {symbol} trail compressed → {exit_decision.trail_atr_mult:.1f}×ATR "
                f"({exit_decision.reason})"
            )
        except Exception as e:
            logger.debug(f"[v10] trail compress error {symbol}: {e}")
        return

    # Update trailing stop if active
    if pos.get("trailing_active", False):
        try:
            pm.update_trailing_stop(pos, current_price)
        except Exception as e:
            logger.debug(f"[v10] trailing update error {symbol}: {e}")

    if not exit_decision.should_exit:
        return

    # Execute close
    direction = pos.get("direction", "LONG")
    exit_reason = exit_decision.reason
    partial_pct = exit_decision.partial_pct

    logger.info(
        f"[v10] EXIT {symbol} {direction}: "
        f"priority={exit_decision.priority} type={exit_decision.exit_type} "
        f"partial={partial_pct:.0%} reason={exit_reason[:80]}"
    )

    close_result = perps.close_position(
        symbol=symbol,
        reason=exit_decision.exit_type,
        partial_pct=partial_pct,
        paper=False,
    )

    if close_result is None:
        logger.warning(f"[v10] close_position returned None for {symbol}")
        return

    pnl_usd = float(close_result.get("pnl_usd", 0))
    exit_price = float(close_result.get("exit_price", current_price))
    entry_price = float(pos.get("entry_price", exit_price))
    pnl_pct = pnl_usd / (pos.get("position_usd", 1) + 1e-9)

    logger.info(
        f"[v10] CLOSED {direction} {symbol}: "
        f"pnl=${pnl_usd:+.2f} ({pnl_pct:+.1%}) @ {exit_price:.4f}"
    )

    # Learning loop record
    if partial_pct >= 1.0:
        _regime = pos.get("regime", "UNKNOWN")
        _entry_score = float(pos.get("entry_composite_score", 0.0))
        _features_snap = current_features or {}

        # 1. ML feature snapshot + retrain queue (learning_loop)
        if ll is not None:
            try:
                trade_id = int(
                    time.time()
                )  # approximate; real DB inserts use auto-increment
                ll.record_closed_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=direction,
                    won=pnl_usd > 0,
                    pnl_usd=pnl_usd,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    entry_score=_entry_score,
                    exit_score=0.0,
                    regime=_regime,
                    features=_features_snap,
                )
            except Exception as e:
                logger.debug(f"[v10] learning record error {symbol}: {e}")

        # 2. Bayesian signal attribution (post_trade_analyzer) — updates signal_stats
        #    and dynamic_weights so the system actually learns from every trade.
        _pta_ok = False
        try:
            from learning.post_trade_analyzer import analyze_closed_trade as _pta

            _entry_ts = pos.get("entry_ts")
            _entry_ts_str = (
                datetime.utcfromtimestamp(float(_entry_ts)).isoformat()
                if _entry_ts
                else datetime.utcnow().isoformat()
            )
            _exit_ts_str = datetime.utcnow().isoformat()
            _qty = float(pos.get("qty", 1.0))
            _position_usd = float(pos.get("position_usd", entry_price * _qty))
            _fee_usd = abs(
                _position_usd * 0.00130
            )  # Kraken round-trip taker fee estimate
            # Build a market_data dict from the features snapshot for signal extraction.
            # primary_setup and composite_score are added so the Bayesian attribution
            # system can track v10 Tier 1 setup performance instead of v9 signal names.
            _md_for_pta = dict(_features_snap)
            _md_for_pta["regime"] = _regime
            _md_for_pta["primary_setup"] = pos.get("entry_setup", "")
            _md_for_pta["direction"] = direction
            _pta(
                symbol=symbol,
                strategy="v10_perp",
                entry_price=entry_price,
                exit_price=exit_price,
                qty=_qty,
                fee_usd=_fee_usd,
                entry_ts=_entry_ts_str,
                exit_ts=_exit_ts_str,
                exit_reason=exit_decision.exit_type,
                market_data_at_entry=_md_for_pta,
                source="live_v10",
                paper=False,
                exit_type=exit_decision.exit_type,
                composite_score=float(pos.get("entry_composite_score", 0.0)),
            )
            _pta_ok = True
        except Exception as e:
            logger.warning(f"[v10] post_trade_analyzer error {symbol}: {e}")

        # 3. Integrity tier + exit quality substrate (v14.0)
        #    Assigns a durable trust tier to every close and records exit quality
        #    metrics so the system can evaluate whether exits are leaving money on
        #    the table.  Fail-safe: any exception here is silent and non-blocking.
        try:
            from logging_db.trade_logger import log_trade_integrity, log_exit_evaluation
            from runtime.live_account import get_live_account_size

            _close_oid = (
                str(close_result.get("order_id", "")).strip()
                or f"close_{symbol}_{int(time.time())}"
            )
            _src_tag = "live_v10"

            # Tier: quarantine impossible PnL; verify if attribution ran; else suspect
            _acct = float(get_live_account_size(paper=False))
            if abs(pnl_usd) > _acct * 0.5:
                _integ_tier = "quarantined"
                _integ_reason = f"pnl_sanity:|{pnl_usd:.2f}|>50%_account"
            elif _pta_ok:
                _integ_tier = "verified"
                _integ_reason = "attribution_succeeded"
            else:
                _integ_tier = "suspect"
                _integ_reason = "attribution_failed_or_incomplete"

            log_trade_integrity(
                close_order_id=_close_oid,
                tier=_integ_tier,
                reason=_integ_reason,
                source_check=_src_tag,
                notes=f"exit={exit_decision.exit_type}",
            )

            # Exit quality: opportunity loss, stop overshoot, path label
            _dir = str(pos.get("direction", direction)).upper()
            _peak = float(
                pos.get("peak_price", exit_price)
            )  # best-case price (direction-aware)
            _stop = float(pos.get("stop_price", 0.0))

            if _dir == "LONG":
                _mfe = (_peak - entry_price) / max(entry_price, 1e-9)
                _optimal = _peak
                _opp_loss = max(0.0, (_peak - exit_price) / max(entry_price, 1e-9))
                _overshoot = (
                    max(0.0, (_stop - exit_price) / max(entry_price, 1e-9))
                    if _stop > 0
                    else 0.0
                )
            else:  # SHORT
                _mfe = (entry_price - _peak) / max(entry_price, 1e-9)
                _optimal = _peak
                _opp_loss = max(0.0, (exit_price - _peak) / max(entry_price, 1e-9))
                _overshoot = (
                    max(0.0, (exit_price - _stop) / max(entry_price, 1e-9))
                    if _stop > 0
                    else 0.0
                )

            _path = (
                "winner" if pnl_usd > 0 else ("loser" if pnl_usd < 0 else "breakeven")
            )

            log_exit_evaluation(
                close_order_id=_close_oid,
                exit_type=exit_decision.exit_type,
                actual_exit_price=exit_price,
                actual_exit_pct=pnl_pct,
                optimal_exit_price=_optimal,
                opportunity_loss_pct=_opp_loss,
                stop_overshoot_pct=_overshoot,
                regime=pos.get("regime", "UNKNOWN"),
                composite_score_at_exit=float(pos.get("entry_composite_score", 0.0)),
                mfe_at_exit=_mfe,
                mae_at_exit=0.0,  # trough not tracked in pos dict; computed post-hoc in nightly audit
                path_label=_path,
                trade_id=_close_oid,
            )
        except Exception as _ie:
            logger.debug(f"[v10] integrity/exit-eval error {symbol}: {_ie}")

    # Notification
    if ne is not None:
        try:
            top_3 = [exit_decision.exit_type, exit_reason[:50]]
            ne.notify_trade_close(
                symbol=symbol,
                direction=direction,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                exit_type=exit_decision.exit_type,
                top_3=top_3,
                features=current_features or {},
                regime=pos.get("regime", "UNKNOWN"),
                score=float(pos.get("entry_composite_score", 0.0)),
            )
        except Exception as e:
            logger.debug(f"[v10] trade_close notify error: {e}")

    # Handle scale-out partial flags + persist updated state to DB
    if partial_pct < 1.0:
        if exit_decision.exit_type == "scale_out_33":
            pos["scale_33_done"] = True
        elif exit_decision.exit_type == "scale_out_66":
            pos["scale_66_done"] = True
        # State is projected directly from broker truth in v19.1.ARCH.
        pass


# ── kill_switch_monitor ───────────────────────────────────────────────────────


def _run_health_check():
    """60-second loop: run 6-invariant health check, write result to system_events."""
    try:
        from monitoring.health_check import run_health_check

        run_health_check(force=False)  # rate-limited internally to once per minute
    except Exception as e:
        logger.debug(f"[v10] health_check error: {e}")


def kill_switch_monitor():
    """60-second loop: check account balance against kill threshold."""
    try:
        ks = _import_kill_switch()
        if ks is None:
            return
        current = _get_account_balance()
        ks.check_balance(current, _initial_balance, paper=False)
        _write_crypto_lane_runtime()
    except Exception as e:
        logger.debug(f"[v10] kill_switch_monitor error: {e}")


# ── hedge_rebalance ───────────────────────────────────────────────────────────


def hedge_rebalance():
    """5-minute loop: rebalance delta-neutral hedge position."""
    try:
        from config import HEDGE_MIN_NOTIONAL_USD

        he = _import_hedge_engine()
        perps = _import_perps_engine()
        if he is None or perps is None:
            return
        open_positions = perps.get_open_positions()
        deployed_usd = _get_deployed_usd(open_positions)
        if not open_positions or deployed_usd < float(HEDGE_MIN_NOTIONAL_USD or 0.0):
            return
        balance = _get_account_balance()
        # Fetch live BTC price for hedge sizing (required by rebalance signature)
        _btc_price = 0.0
        try:
            import urllib.request as _ur, json as _js

            _kr = _js.loads(
                _ur.urlopen(
                    "https://futures.kraken.com/derivatives/api/v3/tickers", timeout=3
                ).read()
            )
            for _t in _kr.get("tickers", []):
                if _t.get("symbol") in ("PF_XBTUSD", "PF_BTCUSD"):
                    _btc_price = float(_t.get("markPrice") or 0)
                    break
        except Exception:
            pass
        he.rebalance(open_positions, balance, btc_price=_btc_price, paper=False)
    except Exception as e:
        logger.debug(f"[v10] hedge_rebalance error: {e}")


# ── ml_retrain_check ──────────────────────────────────────────────────────────


def ml_retrain_check():
    """Threshold-gated retrain loop: run only after enough new closes and enough time."""
    global _last_ml_retrain_ts, _last_ml_retrain_snapshot_count
    try:
        from config import ML_RETRAIN_MIN_HOURS, ML_RETRAIN_MIN_NEW_CLEAN_TRADES

        ll = _import_learning_loop()
        if ll is None:
            return
        now = time.time()
        current_count = _learning_snapshot_count()
        if _last_ml_retrain_snapshot_count < 0:
            _last_ml_retrain_snapshot_count = current_count
            _last_ml_retrain_ts = now
            return
        if (now - _last_ml_retrain_ts) < float(ML_RETRAIN_MIN_HOURS) * 3600:
            return
        if (current_count - _last_ml_retrain_snapshot_count) < int(
            ML_RETRAIN_MIN_NEW_CLEAN_TRADES
        ):
            return
        triggered = ll.maybe_trigger_retrains(paper=False)
        _last_ml_retrain_ts = now
        _last_ml_retrain_snapshot_count = current_count
        if triggered:
            logger.info(
                f"[v10] ml_retrain_check: triggered {len(triggered)} retrains: "
                f"{triggered}"
            )
    except Exception as e:
        logger.debug(f"[v10] ml_retrain_check error: {e}")


# ── mes_futures_scan ─────────────────────────────────────────────────────────


def _get_mes_vwap_data() -> dict:
    """
    Compute session VWAP, ATR(14), and RSI(14) for MES from yfinance 1-min bars.
    Used by the VWAP Mean Reversion strategy.

    Returns dict with keys: price, vwap, atr, rsi, dist_atr, signal ('long'/'short'/None).
    Returns {} on any failure — strategy skips cleanly.
    """
    try:
        import yfinance as yf
        import numpy as np

        df = yf.Ticker("MES=F").history(period="1d", interval="1m")
        if df is None or len(df) < 20:
            return {}

        # Session VWAP (from first bar of the current day)
        df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
        df["cum_tpv"] = (df["tp"] * df["Volume"]).cumsum()
        df["cum_vol"] = df["Volume"].cumsum()
        df["vwap"] = df["cum_tpv"] / (df["cum_vol"] + 1e-9)

        # ATR (14-bar, 1-min)
        prev_c = df["Close"].shift(1)
        tr = np.maximum(
            df["High"] - df["Low"],
            np.maximum((df["High"] - prev_c).abs(), (df["Low"] - prev_c).abs()),
        )
        atr = float(tr.rolling(14).mean().iloc[-1])
        if np.isnan(atr) or atr <= 0:
            atr = 2.0

        # RSI (14-bar, 1-min)
        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = float((100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1])

        price = float(df["Close"].iloc[-1])
        vwap = float(df["vwap"].iloc[-1])

        if np.isnan(price) or np.isnan(vwap):
            return {}

        dist_atr = (price - vwap) / (atr + 1e-9)

        # Signal: price >2σ from VWAP + RSI confirmation → fade back to VWAP
        signal = None
        if dist_atr > 2.0 and rsi > 68:
            signal = "short"  # extended above VWAP → fade down
        elif dist_atr < -2.0 and rsi < 32:
            signal = "long"  # extended below VWAP → fade up

        return {
            "price": price,
            "vwap": round(vwap, 2),
            "atr": round(atr, 4),
            "rsi": round(rsi, 1),
            "dist_atr": round(dist_atr, 2),
            "signal": signal,
        }
    except Exception as e:
        logger.debug(f"[mes] vwap_data error: {e}")
        return {}


def mes_futures_scan():
    """
    2-minute loop (US market hours only): MES opening-range breakout scanner.

    Strategy:
      - Hard block 9:30–10:00 ET (opening chaos)
      - Track the 9:30–10:00 opening range (high/low of first 30 min)
      - Enter LONG on breakout above OR1 high with volume confirmation
      - Enter SHORT on breakdown below OR1 low with volume confirmation
      - Stop: other side of opening range + 1 point buffer
      - Target: 2× stop distance (min 4 points, ≈ $20/contract)
      - Max 1 position at a time, max 2 contracts
      - Daily loss limit: $50 (FUTURES_DAILY_MAX_LOSS_PTS=5 × 2 contracts × $5/pt)
      - Hard stop at 3:45 PM ET — close any open MES position
    """
    try:
        _mes_scan_inner()
    except Exception as e:
        logger.error(f"[mes] scan fatal: {e}\n{traceback.format_exc()[:800]}")


# Opening range state (resets each trading day)
_mes_or_high: float = 0.0
_mes_or_low: float = float("inf")
_mes_or_locked: bool = False  # True once 10:00 ET passes
_mes_or_date: str = ""
_mes_daily_pnl: float = 0.0
_mes_daily_date: str = ""


def _mes_scan_inner():
    global _mes_or_high, _mes_or_low, _mes_or_locked, _mes_or_date
    global _mes_daily_pnl, _mes_daily_date

    from config import FUTURES_LANE_ACTIVE, FUTURES_NUM_CONTRACTS

    if not FUTURES_LANE_ACTIVE:
        return

    try:
        import pytz

        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
    except Exception:
        return

    h, m = now_et.hour, now_et.minute

    # CME daily maintenance window 4:00–4:15 PM ET — skip new entries, close positions
    if h == 16 and m < 15:
        return

    today_str = now_et.strftime("%Y-%m-%d")

    # Reset opening range and daily P&L each new calendar day (midnight ET)
    if _mes_or_date != today_str:
        _mes_or_high = 0.0
        _mes_or_low = float("inf")
        _mes_or_locked = False
        _mes_or_date = today_str

    if _mes_daily_date != today_str:
        _mes_daily_pnl = 0.0
        _mes_daily_date = today_str

    # Import broker — use singleton so we don't create a new event loop thread each cycle.
    # Creating IBKRBroker() every cycle leaks file descriptors (each instance spawns a
    # persistent asyncio thread + sockets; disconnect() doesn't stop the thread).
    try:
        from execution.ibkr_broker import get_ibkr_broker

        broker = get_ibkr_broker()
    except Exception as e:
        logger.debug(f"[mes] ibkr_broker import error: {e}")
        return

    if not broker.is_connected():
        if not broker.connect():
            logger.warning("[mes] IBKR connection failed — skipping cycle")
            return

    try:
        # Get current MES price
        price = broker.get_price("MES")
        if not price or price <= 0:
            return

        # Build range from first 15 scans after daily reset (≈30 min at 2-min interval)
        # Works any session — not tied to 9:30 ET
        if not _mes_or_locked:
            _mes_or_high = max(_mes_or_high, price)
            _mes_or_low = (
                min(_mes_or_low, price) if _mes_or_low < float("inf") else price
            )
            logger.debug(f"[mes] OR building: {_mes_or_low:.2f}–{_mes_or_high:.2f}")

        # Lock range once we have a meaningful spread (≥2 pts) AND ≥15 samples
        # Count ticks via range spread as proxy — if spread has moved at all, check width
        if not _mes_or_locked and _mes_or_high > 0 and _mes_or_low < float("inf"):
            or_range = _mes_or_high - _mes_or_low
            # Lock after first meaningful range forms (≥2 pts spread seen)
            # Use tick count stored implicitly: once range ≥ 2 pts, assume ≥15 scans passed
            if or_range >= 2.0:
                _mes_or_locked = True
                logger.info(
                    f"[mes] Range locked: {_mes_or_low:.2f}–{_mes_or_high:.2f} "
                    f"({or_range:.2f} pts)"
                )

        # Don't trade until range is locked
        if not _mes_or_locked:
            return

        # Daily loss limit — read from config so it stays in sync with FUTURES_DAILY_MAX_LOSS_PTS.
        # config: 5pts × 2 contracts × $5/pt = $50 max daily loss.
        from config import FUTURES_DAILY_MAX_LOSS_PTS, FUTURES_NUM_CONTRACTS as _FNC

        _mes_daily_loss_limit = FUTURES_DAILY_MAX_LOSS_PTS * _FNC * MES_POINT_VALUE
        if _mes_daily_pnl < -_mes_daily_loss_limit:
            logger.info(
                f"[mes] Daily loss limit hit: ${_mes_daily_pnl:.2f} "
                f"(limit=${_mes_daily_loss_limit:.0f}) — no new trades"
            )
            return

        or_range = _mes_or_high - _mes_or_low
        or_mid = (_mes_or_high + _mes_or_low) / 2
        min_range = 2.0  # opening range must be at least 2 points to be meaningful
        if or_range < min_range:
            logger.debug(f"[mes] OR too tight ({or_range:.2f} pts) — skip")
            return

        n_contracts = min(int(FUTURES_NUM_CONTRACTS), 2)
        pos = broker.get_position("MES")
        has_pos = pos is not None and pos.get("qty", 0) != 0

        if has_pos:
            # Monitor existing position for stop/target.
            # "entry" is the key set by buy_mes/short_mes (not "entry_price").
            # "side" is always "LONG" or "SHORT" — don't rely on qty sign since
            # short_mes also stores qty as a positive number.
            entry = float(pos.get("entry", or_mid))
            qty = abs(int(pos.get("qty", 0)))
            stop = float(pos.get("stop", 0))
            target = float(pos.get("target", 0))
            is_long = pos.get("side", "LONG") == "LONG"

            if is_long:
                if price <= stop:
                    pnl = (
                        price - entry
                    ) * qty * MES_POINT_VALUE - IBKR_COMMISSION_RT * qty
                    _mes_daily_pnl += pnl
                    broker.sell_mes(qty=qty, reason="stop_hit")
                    logger.info(f"[mes] STOP HIT LONG @ {price:.2f} pnl=${pnl:.2f}")
                elif price >= target:
                    pnl = (
                        price - entry
                    ) * qty * MES_POINT_VALUE - IBKR_COMMISSION_RT * qty
                    _mes_daily_pnl += pnl
                    broker.sell_mes(qty=qty, reason="target_hit")
                    logger.info(f"[mes] TARGET HIT LONG @ {price:.2f} pnl=${pnl:.2f}")
            else:
                if price >= stop:
                    pnl = (
                        entry - price
                    ) * qty * MES_POINT_VALUE - IBKR_COMMISSION_RT * qty
                    _mes_daily_pnl += pnl
                    broker.cover_mes(qty=qty, reason="stop_hit")
                    logger.info(f"[mes] STOP HIT SHORT @ {price:.2f} pnl=${pnl:.2f}")
                elif price <= target:
                    pnl = (
                        entry - price
                    ) * qty * MES_POINT_VALUE - IBKR_COMMISSION_RT * qty
                    _mes_daily_pnl += pnl
                    broker.cover_mes(qty=qty, reason="target_hit")
                    logger.info(f"[mes] TARGET HIT SHORT @ {price:.2f} pnl=${pnl:.2f}")
            return

        # Look for breakout entry
        buffer = 0.25  # 1 tick above/below OR
        long_entry = _mes_or_high + buffer
        short_entry = _mes_or_low - buffer

        if price >= long_entry:
            stop_price = _mes_or_low - buffer  # below OR low
            stop_dist = price - stop_price
            target_price = price + max(stop_dist * 2, 4.0)  # 2R or 4 pts min
            logger.info(
                f"[mes] LONG BREAKOUT @ {price:.2f} stop={stop_price:.2f} "
                f"target={target_price:.2f} contracts={n_contracts}"
            )
            broker.buy_mes(
                qty=n_contracts,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"or_breakout_long OR={_mes_or_low:.2f}-{_mes_or_high:.2f}",
            )

        elif price <= short_entry:
            stop_price = _mes_or_high + buffer
            stop_dist = stop_price - price
            target_price = price - max(stop_dist * 2, 4.0)
            logger.info(
                f"[mes] SHORT BREAKDOWN @ {price:.2f} stop={stop_price:.2f} "
                f"target={target_price:.2f} contracts={n_contracts}"
            )
            broker.short_mes(
                qty=n_contracts,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"or_breakdown_short OR={_mes_or_low:.2f}-{_mes_or_high:.2f}",
            )

        # ── Strategy 2: VWAP Mean Reversion ──────────────────────────────────
        # Runs 10:00–14:30 ET; only when no position; OR must be locked.
        # Entry: price >2 ATR from session VWAP + RSI extreme → fade back to VWAP.
        # Stop: 1.5 ATR past entry; Target: VWAP (mean reversion).
        # Conservative: 1 contract only.
        if not has_pos and _mes_or_locked and 10 <= h <= 14:
            vd = _get_mes_vwap_data()
            sig = vd.get("signal")
            if sig:
                vwap = vd["vwap"]
                atr = vd["atr"]
                p = vd["price"]
                rsi = vd["rsi"]
                if sig == "long":
                    sl = round(p - 1.5 * atr, 2)
                    tp = round(max(vwap, sl + atr), 2)  # VWAP, never below stop
                    logger.info(
                        f"[mes] VWAP-MR LONG  @ {p:.2f}  vwap={vwap:.2f} "
                        f"stop={sl:.2f} target={tp:.2f} rsi={rsi:.0f} "
                        f"dist={vd['dist_atr']:.1f}σ"
                    )
                    broker.buy_mes(
                        qty=1,
                        stop_price=sl,
                        target_price=tp,
                        reason=f"vwap_mr_long vwap={vwap:.2f} dist={vd['dist_atr']:.1f}σ",
                    )
                elif sig == "short":
                    sl = round(p + 1.5 * atr, 2)
                    tp = round(min(vwap, sl - atr), 2)  # VWAP, never above stop
                    logger.info(
                        f"[mes] VWAP-MR SHORT @ {p:.2f}  vwap={vwap:.2f} "
                        f"stop={sl:.2f} target={tp:.2f} rsi={rsi:.0f} "
                        f"dist={vd['dist_atr']:.1f}σ"
                    )
                    broker.short_mes(
                        qty=1,
                        stop_price=sl,
                        target_price=tp,
                        reason=f"vwap_mr_short vwap={vwap:.2f} dist={vd['dist_atr']:.1f}σ",
                    )

        # Write current state snapshot to system_events so the dashboard can read it
        try:
            from logging_db.trade_logger import log_event
            import json as _json_state

            _state = {
                "price": round(price, 2),
                "or_high": round(_mes_or_high, 2) if _mes_or_locked else None,
                "or_low": round(_mes_or_low, 2) if _mes_or_locked else None,
                "or_locked": _mes_or_locked,
                "daily_pnl": round(_mes_daily_pnl, 2),
                "has_pos": has_pos,
                "time_et": now_et.strftime("%H:%M"),
            }
            log_event("INFO", "mes_state", _json_state.dumps(_state))
        except Exception:
            pass

    finally:
        # Do NOT disconnect — broker is a singleton that must stay connected across cycles.
        # Disconnecting here was the cause of the FD leak (each cycle reconnected,
        # spawning a new event loop thread without cleaning up the old one).
        pass


IBKR_COMMISSION_RT = 0.47 * 2  # round-trip commission per contract
MES_POINT_VALUE = 5.00  # $ per full MES point (matches ibkr_broker.MES_POINT_VALUE)


# ── rbi_nightly ───────────────────────────────────────────────────────────────


def rbi_nightly():
    """Threshold-gated RBI research loop."""
    global _last_rbi_run_ts, _last_rbi_snapshot_count
    try:
        from config import RBI_MIN_DAYS, RBI_MIN_NEW_CLEAN_TRADES

        ll = _import_learning_loop()
        if ll is None:
            return
        now = time.time()
        current_count = _learning_snapshot_count()
        if _last_rbi_snapshot_count < 0:
            _last_rbi_snapshot_count = current_count
            _last_rbi_run_ts = now
            return
        if (now - _last_rbi_run_ts) < float(RBI_MIN_DAYS) * 86400:
            return
        if (current_count - _last_rbi_snapshot_count) < int(RBI_MIN_NEW_CLEAN_TRADES):
            return
        logger.info("[v10] rbi_nightly: starting BTCUSDT RBI pipeline")
        results = ll.run_nightly_rbi(symbol="BTCUSDT", paper=False)
        
        # v18.34: Nightly Online Learner Maintenance
        try:
            from ml.online_learner import get_learner
            for d in ["LONG", "SHORT"]:
                # Trigger internal expiry/save cycle for generic models
                l = get_learner(direction=d)
                logger.info(f"[v10] Nightly maintenance: {l.key} updates={l.n_updates}")
        except Exception as _ole:
            logger.debug(f"[v10] Online learner maintenance error: {_ole}")

        _last_rbi_run_ts = now
        _last_rbi_snapshot_count = current_count
        logger.info(f"[v10] rbi_nightly done: {results}")
        ne = _import_notification_engine()
        if ne is not None:
            ne.notify_system(
                title="RBI Nightly Complete",
                detail=(
                    f"promoted={results.get('promoted', 0)} "
                    f"passed={results.get('passed', 0)} "
                    f"error={results.get('error', 'none')}"
                ),
            )
    except Exception as e:
        logger.error(f"[v10] rbi_nightly error: {e}\n{traceback.format_exc()[:800]}")


# ── Startup ───────────────────────────────────────────────────────────────────


def _init_globals():
    """Set module-level globals from config at startup."""
    global _initial_balance
    try:
        _initial_balance = float(_get_account_balance())
        _persist_live_account_size(_initial_balance)
    except Exception as e:
        logger.warning(f"[v10] balance resolution error: {e} — using defaults")
        _initial_balance = 0.0

    logger.info(
        f"[v10] mode=LIVE "
        f"initial_balance=${_initial_balance:.0f}"
    )


def _startup_notification():
    """Send system-start notification."""
    try:
        ne = _import_notification_engine()
        if ne is not None:
            ne.notify_system(
                title="v10 System Started",
                detail=(
                    f"mode=LIVE "
                    f"balance=${_initial_balance:.0f}"
                ),
            )
    except Exception:
        pass


def run_forever():
    """
    Set up all schedules and run the v10 loop forever.

    Schedule:
      - scan_and_trade:    every 5 minutes
      - exit_monitor:      every 30 seconds
      - hedge_rebalance:   every 5 minutes (offset by 2.5 min to avoid collision with scan)
      - kill_switch_check: every 60 seconds
      - ml_retrain_check:  every 6 hours
      - rbi_nightly:       daily at 02:00 ET (scheduled as UTC 07:00 which covers ET 02:00)
    """
    _init_globals()

    # Initialise DB tables for learning loop
    ll = _import_learning_loop()
    if ll is not None:
        try:
            ll._ensure_tables()
        except Exception:
            pass

    # Restore open positions from SQLite so bot restart doesn't re-enter everything
    perps = _import_perps_engine()
    if perps is not None:
        try:
            perps.load_positions_from_db(paper=False)
        except Exception as _e:
            logger.warning(f"[v10] load_positions_from_db error: {_e}")

    # Log startup
    _startup_notification()
    logger.info("[v10] Scheduler starting — wiring schedules...")

    from config import (
        FUTURES_LANE_ACTIVE,
        LABELER_INTERVAL_MINUTES,
        ML_RETRAIN_MIN_HOURS,
        NIGHTLY_AUDIT_FULL_PROOF_WEEKDAY,
        NIGHTLY_AUDIT_RUN_PROOF,
        NIGHTLY_AUDIT_TIME_UTC,
        RBI_SCHEDULE_MODE,
        RBI_TIME_UTC,
        RBI_WEEKDAY,
        SPOT_EXIT_POLL_SECONDS,
        SPOT_SCALP_SCAN_SECONDS,
    )

    # Wire schedules
    schedule.every(5).minutes.do(lambda: scan_and_trade(spot_only=True))
    schedule.every(30).seconds.do(exit_monitor)
    schedule.every(SPOT_SCALP_SCAN_SECONDS).seconds.do(spot_scalp_scan)
    schedule.every(SPOT_EXIT_POLL_SECONDS).seconds.do(spot_exit_monitor)
    schedule.every(5).minutes.do(hedge_rebalance)
    schedule.every(60).seconds.do(kill_switch_monitor)
    schedule.every(60).seconds.do(_run_health_check)
    schedule.every(int(max(1, ML_RETRAIN_MIN_HOURS))).hours.do(ml_retrain_check)
    if str(RBI_SCHEDULE_MODE or "").lower() != "manual":
        _schedule_weekly_job(str(RBI_WEEKDAY), str(RBI_TIME_UTC), rbi_nightly)

    # v13.6: candidate outcome labeling — runs every 15 min in a background thread
    # so it never blocks the scan cycle.
    def _labeler_job():
        try:
            import threading as _thr
            from learning.candidate_labeler import run_labeling_pass
            from data.historical_data import get_candles as _gc

            _t = _thr.Thread(target=run_labeling_pass, args=(_gc,), daemon=True)
            _t.start()
        except Exception as _le:
            logger.warning(f"[v10] labeler job error: {_le}")

    schedule.every(int(max(15, LABELER_INTERVAL_MINUTES))).minutes.do(_labeler_job)

    # v18.16: nightly proof + drift + learning audit at 08:00 UTC (03:00 ET, after RBI)
    def _nightly_audit_job():
        try:
            import threading as _thr
            from monitoring.nightly_audit import run_audit

            _t = _thr.Thread(
                target=run_audit,
                kwargs={"run_proof": bool(NIGHTLY_AUDIT_RUN_PROOF)},
                daemon=True,
            )
            _t.start()
        except Exception as _ae:
            logger.debug(f"[v10] nightly audit job error: {_ae}")

    def _weekly_full_audit_job():
        try:
            import threading as _thr
            from monitoring.nightly_audit import run_audit

            _t = _thr.Thread(target=run_audit, kwargs={"run_proof": True}, daemon=True)
            _t.start()
        except Exception as _ae:
            logger.debug(f"[v10] weekly full audit job error: {_ae}")

    schedule.every().day.at(str(NIGHTLY_AUDIT_TIME_UTC)).do(_nightly_audit_job)
    _schedule_weekly_job(
        str(NIGHTLY_AUDIT_FULL_PROOF_WEEKDAY),
        str(NIGHTLY_AUDIT_TIME_UTC),
        _weekly_full_audit_job,
    )

    # Nightly DB -> Broker reconciliation
    def _nightly_recon_job():
        try:
            import threading as _thr
            from scripts.nightly_recon import run_reconciliation

            _t = _thr.Thread(target=run_reconciliation, daemon=True)
            _t.start()
        except Exception as _re:
            logger.debug(f"[v10] nightly recon job error: {_re}")

    schedule.every().day.at("03:00").do(_nightly_recon_job)

    # Daily token burn report — surfaces which modules consume the Gemini API budget
    def _send_daily_token_burn_report():
        """
        Query api_telemetry for last 24h token usage grouped by module.
        Format and send a burn report via Telegram (Task 13).
        """
        try:
            import time as _time
            from notifications.telegram_bot import send_message as _tg

            _cutoff = _time.time() - 86400  # last 24 hours

            _conn = _db_conn()
            rows = _conn.execute(
                "SELECT module, SUM(prompt_tokens) as p, SUM(completion_tokens) as c "
                "FROM api_telemetry WHERE ts >= ? GROUP BY module ORDER BY (p+c) DESC",
                (_cutoff,),
            ).fetchall()

            if not rows:
                logger.debug("[v10] No API telemetry data for burn report (last 24h).")
                return

            total_tokens = sum(int(r[1] or 0) + int(r[2] or 0) for r in rows)
            heaviest = rows[0]  # already sorted descending
            heaviest_name = str(heaviest[0])
            heaviest_total = int(heaviest[1] or 0) + int(heaviest[2] or 0)

            lines = ["📊 <b>Daily Token Burn Report</b> (last 24h)"]
            lines.append(f"Total Tokens: <b>{total_tokens:,}</b>")
            lines.append(f"Heaviest Consumer: <b>{heaviest_name}</b> with <b>{heaviest_total:,}</b> tokens")
            lines.append("")
            lines.append("<b>Per-module breakdown:</b>")
            for r in rows:
                mod = str(r[0])
                p = int(r[1] or 0)
                c = int(r[2] or 0)
                lines.append(f"  • {mod}: {p:,} prompt + {c:,} completion = {p+c:,}")

            _tg("\n".join(lines))
            logger.info(f"[v10] Daily token burn report sent. Total tokens: {total_tokens:,}")

        except Exception as _e:
            logger.warning(f"[v10] Token burn report failed: {_e}")

    schedule.every().day.at("08:00").do(_send_daily_token_burn_report)  # 8am ET local

    # Periodic system + crypto lane heartbeat (every 1 minute)
    def _write_heartbeat():
        try:
            from runtime.runtime_state import write_system_heartbeat

            write_system_heartbeat()
            _write_crypto_lane_runtime()
        except Exception:
            pass

    schedule.every(1).minutes.do(_write_heartbeat)

    # v18.19: per-asset unrealized PnL + entry-price gauges. Iterates every open
    # bot-managed spot position each minute, queries broker mark price, and
    # publishes algo_bot_open_position_pnl_usd{asset=...} and
    # algo_bot_open_position_entry_price{asset=...}. drop_open_position_labels()
    # in close_spot drops stale series when a position closes.
    def _update_open_position_gauges():
        try:
            from logging_db.trade_logger import load_open_positions
            from execution.coinbase_spot_broker import get_spot_broker
            from monitoring import metrics

            broker = None
            try:
                broker = get_spot_broker()
                if broker and not broker.is_connected():
                    broker.connect()
            except Exception:
                broker = None

            for row in load_open_positions(paper=0):
                if not str(row.get("strategy", "")).startswith("spot_"):
                    continue
                qty = float(row.get("qty") or 0.0)
                entry = float(row.get("entry") or 0.0)
                if qty <= 0 or entry <= 0:
                    continue
                sym = str(row.get("symbol") or "").upper()
                if not sym:
                    continue
                metrics.OPEN_POS_ENTRY_GAUGE.labels(asset=sym).set(entry)
                mark = 0.0
                if broker is not None:
                    try:
                        mark = float(broker.get_mark_price(sym) or 0.0)
                    except Exception:
                        mark = 0.0
                if mark > 0:
                    entry_fee = float(row.get("entry_fee_usd") or 0.0)
                    unrealized = (mark - entry) * qty - entry_fee
                    metrics.OPEN_POS_PNL_GAUGE.labels(asset=sym).set(unrealized)
        except Exception as _e:
            logger.debug(f"[v10] open-position gauge update failed: {_e}")

    schedule.every(1).minutes.do(_update_open_position_gauges)

    # v18.19: midnight-UTC session reset. Resets risk_engine.daily_start_balance
    # so the session-based drawdown formula (peak-based → daily-anchored) has a
    # fresh denominator, and zeros the session-bucketed Prometheus gauges
    # (PNL_NET, SESSION_TRADES). Monotonic counters stay monotonic — Grafana
    # uses increase()[24h] for "today" views.
    def _session_reset_job():
        try:
            from monitoring import metrics
            from risk_engine import update_balances
            import risk_engine as _re

            current_balance = float(_re._state.account_balance or 0.0)
            _re.reset_daily(current_balance)
            metrics.reset_session_metrics()
            # Force drawdown denominator to refresh by calling update_balances
            # with the same balance — peak unchanged, daily_start now == current.
            update_balances(current_balance)
            logger.info(
                f"[v10] session reset (midnight UTC) — daily_start_balance={current_balance:.2f}"
            )
        except Exception as _se:
            logger.warning(f"[v10] session reset failed: {_se}")

    schedule.every().day.at("00:00", "UTC").do(_session_reset_job)

    if FUTURES_LANE_ACTIVE:
        schedule.every(2).minutes.do(mes_futures_scan)
        logger.info("[v10] MES futures scanner wired (every 2 min)")

    # 📊 9:00 PM ET 'War Room' Report
    try:
        from notifications.reports import send_war_room_report

        schedule.every().day.at("21:00").do(send_war_room_report)
    except ImportError:
        pass

    logger.info("[v10] All schedules wired. Running scan immediately...")

    # Shadow state background loop — updates Kalman/OU/ADF/Kyle every 60s
    def _shadow_state_thread():
        import time as _time
        from data.edge_monitor import update_shadow_state as _update_shadow
        from runtime.spot_strategy import ACTIVE_UNIVERSE
        import asyncio as _asyncio

        while True:
            for _sym in ACTIVE_UNIVERSE:
                try:
                    from data.historical_data import get_candles

                    _df = get_candles(_sym, "1m", limit=100)
                    if _df is not None and len(_df) >= 20:
                        _prices = list(_df["close"].astype(float))
                        _volumes = list(_df["volume"].astype(float))
                        _asyncio.run(_update_shadow(_sym, _prices, _volumes))
                except Exception as _e:
                    logger.debug(f"[shadow_loop] {_sym} error: {_e}")
            _time.sleep(60)

    import threading as _shadow_thr

    _shadow_thr.Thread(
        target=_shadow_state_thread, daemon=True, name="ShadowStateLoop"
    ).start()
    logger.info("[v10] Shadow state loop started (60s cadence).")

    # Run immediately on startup (don't wait 5 minutes for first scan)
    scan_and_trade(spot_only=True)

    # Production: Send Liftoff message
    try:
        from notifications.telegram_bot import send_liftoff

        send_liftoff()
    except Exception as _e:
        logger.debug(f"Liftoff message error: {_e}")

    logger.info("[v10] Main loop running. Press Ctrl+C to stop.")
    from system_state import state
    from runtime.runtime_state import upsert_lane_state
    import json
    
    _last_cache_ts = 0

    def _cache_spot_state():
        """v19.1.3: Caches rich broker-first spot state for the HUD dashboard."""
        try:
            logger.info("[v10] Starting spot state cache cycle...")
            from execution.coinbase_spot_broker import get_spot_broker
            from runtime.spot_classification import get_classifications, is_external_manual
            
            broker = get_spot_broker()
            holdings = broker.sync_live_holdings() or []
            classifications = get_classifications()
            
            # v19.1.3: Total Equity = Live USD Cash + All Crypto Assets
            spot_bal = broker.get_spot_balance() or {}
            usd_cash = float(spot_bal.get("usd_available") or 0.0)
            
            enriched = []
            total_equity = usd_cash
            
            for p in holdings:
                sym = p["symbol"]
                is_manual = is_external_manual(sym, classifications)
                
                qty = float(p.get("qty") or 0.0)
                entry = float(p.get("avg_entry") or 0.0)
                
                # v19.1.3: Use mark price if available, fallback to entry, fallback to 0.0
                mark = broker.get_mark_price(sym)
                if (not mark or mark <= 0) and entry > 0:
                    mark = entry
                if not mark or mark <= 0:
                    mark = 0.0
                
                val = (qty * mark)
                total_equity += val
                
                # Layman Logic
                pnl = (mark - entry) * qty if entry > 0 else 0.0
                
                trend = "NEUTRAL"
                if mark > entry * 1.005: trend = "UP"
                elif mark < entry * 0.995: trend = "DOWN"
                
                # SRE X-Ray: Sentiment
                sentiment = "Neutral"
                if pnl > 0: sentiment = "Strong Hold"
                elif pnl < 0: sentiment = "Accumulating"
                
                enriched.append({
                    **p,
                    "symbol": sym,
                    "current_price": round(mark, 4),
                    "current_value": round(val, 2),
                    "live_pnl": round(pnl, 2),
                    "potential_usd": round(pnl * 1.5, 2) if pnl > 0 else 0.0,
                    "risk_usd": round(pnl * 0.5, 2) if pnl < 0 else 0.0,
                    "trend": trend,
                    "sentiment": sentiment,
                    "strategy": f"spot_{sym.lower()}",
                    "managed": not is_manual,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                
            snapshot = {
                "positions": enriched,
                "equity": round(total_equity, 2),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            upsert_lane_state(
                "crypto",
                snapshot_json=json.dumps(snapshot),
                readiness_state="OK"
            )
        except Exception as e:
            logger.debug(f"[v10] Cache spot state error: {e}")

    while True:
        try:
            schedule.run_pending()
            # 💓 Periodic Metric Heartbeat
            state.update_prometheus()
            
            # 🚀 v19.1: Cache rich state for HUD (every 15s)
            now = time.time()
            if now - _last_cache_ts >= 15:
                _cache_spot_state()
                _last_cache_ts = now
                
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[v10] Shutdown requested via KeyboardInterrupt")
            raise
        except Exception as e:
            logger.error(
                f"[v10] Scheduler loop error: {e}\n{traceback.format_exc()[:800]}"
            )
            time.sleep(5)  # brief back-off before resuming
