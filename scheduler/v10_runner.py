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
from datetime import datetime
from typing import Dict, Optional

import schedule
from config import SUPPRESSED_SYMBOLS
from runtime.execution_universe import (
    get_execution_policy as _get_execution_policy,
    get_underlying as _get_underlying,
)

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────

_scan_lock = threading.RLock()  # prevent parallel scan_and_trade runs
_initial_balance: float = 0.0  # set at startup from config
_paper: bool = True  # set at startup from config

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

# Deduplicate TradingView signals across scan cycles (symbol_direction_ts key)
_seen_tv_signal_keys: set = set()

# Cooldown after close: symbol → timestamp of last close.
# Prevents re-entering the same symbol immediately after thesis-invalidated exits.
_recent_closes: Dict[str, float] = {}
_COOLDOWN_THESIS_SEC: int = 7200  # 2 hours after thesis_invalidated
_COOLDOWN_OTHER_SEC: int = 1800  # 30 min after any other exit

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
) -> None:
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
            paper=_paper,
            source="clean_paper_v10" if _paper else "live_v10",
            scanner_theoretical_position_usd=theor_pos,
            scanner_effective_position_usd=eff_pos,
        )
    except Exception as _je:
        logger.debug(
            f"[v10] candidate journal error ({decision} {candidate.get('symbol', '')}): {_je}"
        )


# ── TradingView signal helpers ────────────────────────────────────────────────


def _get_fresh_tv_signals(max_age_seconds: int = 300) -> list:
    """
    Query system_events for TradingView signals received in the last max_age_seconds.
    Returns list of dicts with: symbol, direction, indicator, strength, price, ts
    """
    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()
        cutoff = time.time() - max_age_seconds
        rows = db.conn.execute(
            """
            SELECT message, ts FROM system_events
            WHERE source = 'tradingview'
              AND ts > datetime(?, 'unixepoch')
            ORDER BY ts DESC LIMIT 20
        """,
            (cutoff,),
        ).fetchall()

        signals = []
        for msg, ts in rows:
            try:
                import json

                data = json.loads(msg) if isinstance(msg, str) else msg
                symbol = data.get("symbol", "").upper()
                direction = data.get("direction", "LONG").upper()
                if not symbol or direction not in ("LONG", "SHORT"):
                    continue
                # Normalize symbol: BTCUSD → BTCUSDT, BTC-USDT → BTCUSDT etc.
                if not symbol.endswith("USDT"):
                    symbol = symbol.replace("-", "").replace("USD", "") + "USDT"
                signals.append(
                    {
                        "symbol": symbol,
                        "direction": direction,
                        "indicator": data.get("indicator", "tv_alert"),
                        "strength": data.get("strength", "moderate"),
                        "price": float(data.get("price", 0)),
                        "ts": ts,
                    }
                )
            except Exception:
                continue
        return signals
    except Exception:
        return []


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
    """Try broker; fall back to config ACCOUNT_SIZE."""
    perps = _import_perps_engine()
    if perps is not None:
        try:
            broker = perps._get_broker(testnet=True)
            if broker is not None:
                bal = broker.get_account_balance()
                if bal and bal > 0:
                    return float(bal)
        except Exception as e:
            logger.debug(f"[v10] broker balance error: {e}")

    try:
        from config import ACCOUNT_SIZE

        return float(ACCOUNT_SIZE)
    except Exception:
        return 5000.0


def _get_deployed_usd(open_positions: Dict) -> float:
    """Sum notional of all open positions."""
    return sum(float(p.get("position_usd", 0)) for p in open_positions.values())


# ── scan_and_trade ────────────────────────────────────────────────────────────


def scan_and_trade():
    """
    Main 5-minute loop: run scanner, score candidates, open new positions.
    Protected by _scan_lock to prevent parallel runs.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.debug("[v10] scan_and_trade skipped — previous run still active")
        return

    try:
        _scan_and_trade_inner()
    except Exception as e:
        logger.error(
            f"[v10] scan_and_trade fatal: {e}\n{traceback.format_exc()[:1000]}"
        )
    finally:
        _scan_lock.release()


def _scan_and_trade_inner():
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

    # Account balance for scanner and sizing
    balance = _get_account_balance()

    # Get current open positions
    open_pos: Dict = {}
    if perps is not None:
        open_pos = perps.get_open_positions()

    open_symbols = list(open_pos.keys())
    deployed_usd = _get_deployed_usd(open_pos)

    # Check for fresh TradingView signals — promote them to priority candidates
    global _seen_tv_signal_keys
    tv_signals = _get_fresh_tv_signals(max_age_seconds=300)
    tv_candidates = []
    for tv in tv_signals:
        key = f"{tv['symbol']}_{tv['direction']}_{tv.get('ts', '')}"
        if key in _seen_tv_signal_keys:
            continue
        _seen_tv_signal_keys.add(key)
        # Keep set bounded
        if len(_seen_tv_signal_keys) > 500:
            _seen_tv_signal_keys.clear()
        _tv_policy = _get_execution_policy(tv["symbol"])
        if not _tv_policy.get("execute"):
            logger.info(
                f"[v10] TV signal skipped — {tv['symbol']} {tv['direction']} "
                f"outside live execution universe ({_tv_policy.get('reason', 'blocked')})"
            )
            continue
        # Build candidate dict matching scanner output format
        tv_candidates.append(
            {
                "symbol": tv["symbol"],
                "direction": tv["direction"],
                "vol_spike": 1.5,  # TV signal = elevated priority
                "adx_15m": 25.0,  # assume trending (TV only fires on structured setups)
                "price_move_4h_pct": 1.0,
                "atr_15m": 0.0,  # will be computed from candles in _attempt_entry
                "stop_pct": 1.5,
                "target_pct": 4.5,
                "expected_profit": 5.0,
                "correlation_penalty": 1.0,
                "regime_penalty": 1.0,
                "spread_pct": 0.15,  # percent units (÷100 → 0.0015 fraction at gate) — conservative default; no OB data for TV signals
                "tv_signal": True,
                "tv_strength": tv.get("strength", "moderate"),
                "tv_indicator": tv.get("indicator", "tv_alert"),
                "edge_score": 0.6,  # TV signal gets moderate edge score until validated
            }
        )
        logger.info(
            f"[v10] TV signal: {tv['symbol']} {tv['direction']} "
            f"indicator={tv.get('indicator')} strength={tv.get('strength')}"
        )

    # Run scanner
    if scanner is None:
        logger.debug("[v10] scanner unavailable — skipping")
        if not tv_candidates:
            return
        candidates = tv_candidates
    else:
        scanner_candidates = scanner.scan(
            open_positions=open_symbols,
            account_balance=balance,
            core_only=True,
        )
        # TV candidates take priority; skip scanner duplicate symbols
        tv_symbols = {c["symbol"] for c in tv_candidates}
        candidates = tv_candidates + [
            c for c in scanner_candidates if c["symbol"] not in tv_symbols
        ]

    if not candidates:
        logger.debug("[v10] scan returned 0 candidates")
        return

    logger.info(
        f"[v10] scan: {len(candidates)} candidates "
        f"(tv={len(tv_candidates)} scanner={len(candidates) - len(tv_candidates)}), "
        f"balance=${balance:.0f} deployed=${deployed_usd:.0f}"
    )

    # Unique ID for this scan cycle — links all candidate rows from the same scan
    import uuid as _uuid

    _scan_id = _uuid.uuid4().hex[:16]

    # Exact funnel counters — reset each scan cycle
    _f_dual_exposure = 0
    _f_cooldown = 0
    _f_risk_block = 0
    _f_data_unavailable = 0
    _f_below_threshold = 0
    _f_econ_veto = 0
    _f_research_only_block = 0
    _f_sizing_zero = 0
    _f_execution_failed = 0
    _f_entered = 0

    for candidate in candidates:
        symbol = candidate.get("symbol", "")
        direction = candidate.get("direction", "LONG")

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
            )
            continue

        # SQLite check — match by exact symbol OR same underlying across all open positions
        try:
            import sqlite3 as _sq
            from config import DB_PATH as _DB_PATH

            _conn2 = _sq.connect(_DB_PATH)
            _open_rows = _conn2.execute(
                "SELECT symbol FROM open_positions WHERE strategy=? AND paper=?",
                ("v10_perp", int(_paper)),
            ).fetchall()
            _conn2.close()
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
                )
                continue
        except Exception:
            pass

        # Cooldown: check both exact symbol AND underlying (catches PF_X / bare-X alternation)
        _cooldown_key = (
            _underlying  # use underlying so PF_ETHUSD cooldown covers ETH too
        )
        _last_close_ts = max(
            _recent_closes.get(symbol, 0),
            _recent_closes.get(_cooldown_key, 0),
        )
        if _last_close_ts > 0:
            _elapsed = time.time() - _last_close_ts
            _cooldown = _COOLDOWN_THESIS_SEC
            if _elapsed < _cooldown:
                logger.debug(
                    f"[v10] {symbol} — cooldown {_elapsed / 60:.0f}m/{_cooldown / 60:.0f}m, skip"
                )
                _f_cooldown += 1
                _journal_scan_candidate(
                    _scan_id,
                    candidate,
                    "cooldown_block",
                    entry_block_reason=f"cooldown {_elapsed / 60:.0f}m/{_cooldown / 60:.0f}m",
                )
                continue

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
        f"dual={_f_dual_exposure} cooldown={_f_cooldown} risk={_f_risk_block} "
        f"data_unavail={_f_data_unavailable} below_thresh={_f_below_threshold} "
        f"econ_veto={_f_econ_veto} research_only={_f_research_only_block} "
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
            cooldown_block=_f_cooldown,
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
):
    """Try to enter a position for one candidate. All exceptions propagate to caller."""
    if get_candles is None or build_features is None:
        logger.warning(
            f"[v10] {symbol} — get_candles={get_candles is not None} build_features={build_features is not None} — skip"
        )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason="get_candles or build_features is None",
        )
        return "data_unavailable"

    # Fetch 1h candles for feature building
    df = get_candles(symbol, "1h", 200)
    if df is None or len(df) < 20:
        logger.info(
            f"[v10] {symbol} — insufficient candle data ({len(df) if df is not None else 0} bars), skip"
        )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "data_unavailable",
            entry_block_reason=f"insufficient candles ({len(df) if df is not None else 0} bars)",
        )
        return "data_unavailable"

    current_price = float(df["close"].iloc[-1])
    if current_price <= 0:
        return "data_unavailable"

    # ── Price sanity: candle close must be within 5% of live mark price ───────
    # v13.2: tightened from 20% → 5% global fallback (20% missed ETH $19 vs $2130 case).
    # Kraken PF_ symbols → Kraken mark price first; all others → Hyperliquid allMids.
    # If live price is unavailable, skip check (don't block on network failures).
    _PRICE_SANITY_PCT = 0.05  # 5% global fallback threshold
    try:
        import urllib.request as _ur, json as _json

        _live = 0.0
        if symbol.startswith("PF_") or symbol.startswith("PI_"):
            _kr = _json.loads(
                _ur.urlopen(
                    "https://futures.kraken.com/derivatives/api/v3/tickers", timeout=3
                ).read()
            )
            for _t in _kr.get("tickers", []):
                if _t.get("symbol") == symbol:
                    _live = float(_t.get("markPrice") or _t.get("last") or 0)
                    break
        if _live <= 0:
            _req = _ur.Request(
                "https://api.hyperliquid.xyz/info",
                data=_json.dumps({"type": "allMids"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _mids = _json.loads(_ur.urlopen(_req, timeout=3).read())
            _live = float(_mids.get(symbol, 0))
        if _live > 0:
            _pct_off = abs(current_price - _live) / _live
            if _pct_off > _PRICE_SANITY_PCT:
                logger.warning(
                    f"[v10] {symbol} — price sanity FAIL: candle ${current_price:.8g} "
                    f"vs live ${_live:.8g} ({_pct_off:.1%} off) — SKIP (wrong data source)"
                )
                return "data_unavailable"
            current_price = _live  # Use live mark price for execution accuracy
    except Exception as _pe:
        logger.debug(f"[v10] {symbol} price sanity check error: {_pe}")

    # ATR from last 7 candles (high-low range proxy)
    atr_7 = float(df["high"].sub(df["low"]).tail(7).mean())
    if atr_7 <= 0:
        atr_7 = current_price * 0.015  # 1.5% floor

    # ── Step 1: Build features ───────────────────────────────────────────────
    features = build_features(df, symbol)

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

    if candidate.get("tv_signal"):
        features["tv_signal"] = 1.0

    # ── Step 2: Classify regime ──────────────────────────────────────────────
    regime = "UNKNOWN"
    if classify_from_features is not None:
        try:
            regime = classify_from_features(features)
        except Exception as e:
            logger.debug(f"[v10] regime classify error {symbol}: {e}")

    # ── Step 3: Score (used for sizing, not gating) ──────────────────────────
    if se is None:
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
    # Data: wae_explosion_short at composite < 50 had WR ~5% across 34 trades.
    # Even specific setup patterns need overall signal agreement >= 50.
    # This floor only blocks extreme signal disagreement — most Tier 1 setups
    # will naturally score > 50 when the underlying indicator conditions are met.
    _TIER1_COMPOSITE_FLOOR = 50.0

    _tech_score = float(result.get("technical_score", 0.0))
    _ml_score = float(result.get("ml_score", 50.0))

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
            )
            return "below_threshold"
        tier = 1
        size_mult = 1.0  # full position size
        logger.info(
            f"[v10] {symbol} {direction} TIER 1 — {primary_setup['label']} "
            f"(composite={composite:.1f} used for sizing only)"
        )
    elif composite >= 58:
        # Tier 2: score-based entry. Floor raised from 50 → 58 based on data:
        # scores 50-57 had WR=47%, avg_pnl=-$0.27 (88 trades, negative edge).
        # scores >= 58 had WR=64%, avg_pnl=+$0.23 (11 trades, positive edge).
        tier = 2
        size_mult = 0.75
        logger.info(
            f"[v10] {symbol} {direction} TIER 2 — composite={composite:.1f} "
            f"(tech={result.get('technical_score', 0):.1f} ml={result.get('ml_score', 50):.1f})"
        )
    else:
        if composite > 50:
            logger.info(
                f"[v10] {symbol} {direction} score={composite:.1f} in 50-57 "
                f"dead zone — no edge, skip (threshold=58)"
            )
        elif composite > 44:
            logger.info(
                f"[v10] {symbol} {direction} score={composite:.1f} < 58, "
                f"no primary setup — skip"
            )
        _journal_scan_candidate(
            scan_id,
            candidate,
            "below_threshold",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=58.0,
            should_enter_signal=0,
            entry_block_reason=f"composite {composite:.1f} < 58 (no setup, no tier2 score)",
        )
        return "below_threshold"

    # ── Step 5: Economics gate (runs after setup quality known) ─────────────
    try:
        from risk.economics_gate import check as economics_check

        atr_pct = atr_7 / current_price if current_price > 0 else 0.015
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
        except Exception as _wr_err:
            logger.debug(f"[v10] WR prior fallback: {_wr_err}")
            _wr_est = (
                0.54
                if tier == 1
                else float(max(0.50, min(0.60, 0.50 + (composite - 58) / 50)))
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
                entry_threshold=58.0,
                should_enter_signal=1,
                econ_approved=0,
                econ_tier=econ.get("quality_tier", "VETO"),
                econ_reject_reason=reason,
                edge_score=float(econ.get("edge_score", 0.0)),
                entry_block_reason=f"economics: {reason}",
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

        _eu_policy = _exec_policy(symbol)
        if not _eu_policy["execute"]:
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
                entry_threshold=58.0,
                should_enter_signal=1,
                econ_approved=1,
                entry_block_reason=f"non_core_execution_universe:{underlying}",
            )
            return "research_only_block"
    except Exception as _eu_err:
        logger.debug(f"[v10] execution universe check error {symbol}: {_eu_err}")

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
        paper=_paper,
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

    if size_usd < 10.0:
        logger.debug(f"[v10] {symbol} size ${size_usd:.2f} too small, skip")
        _journal_scan_candidate(
            scan_id,
            candidate,
            "sizing_zero",
            regime=regime,
            technical_score=_tech_score,
            ml_score=_ml_score,
            composite_score=composite,
            entry_threshold=58.0,
            should_enter_signal=1,
            econ_approved=1,
            econ_tier=str(candidate.get("quality_tier", "B")),
            edge_score=float(candidate.get("edge_score", 0.5)),
            size_usd=size_usd,
            leverage=sizing.get("leverage", 3),
            entry_block_reason=f"size ${size_usd:.2f} < $10 minimum",
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
        return "data_unavailable"

    entry_setup_name = primary_setup["name"] if primary_setup else ""

    if direction == "LONG":
        pos = perps.open_long(
            symbol=symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=_paper,
        )
    else:
        pos = perps.open_short(
            symbol=symbol,
            position_usd=size_usd,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            leverage=leverage,
            composite_score=composite,
            atr_at_entry=atr_7,
            regime=regime,
            entry_setup=entry_setup_name,
            paper=_paper,
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
            entry_threshold=58.0,
            should_enter_signal=1,
            econ_approved=1,
            econ_tier=str(candidate.get("quality_tier", "B")),
            edge_score=float(candidate.get("edge_score", 0.5)),
            size_usd=size_usd,
            leverage=leverage,
            entry_block_reason="open_long/short returned None",
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
        entry_threshold=58.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier=str(candidate.get("quality_tier", "B")),
        edge_score=float(candidate.get("edge_score", 0.5)),
        size_usd=size_usd,
        leverage=leverage,
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
    """
    try:
        _exit_monitor_inner()
    except Exception as e:
        logger.error(f"[v10] exit_monitor fatal: {e}\n{traceback.format_exc()[:1000]}")


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
        paper=_paper,
    )

    if close_result is None:
        logger.warning(f"[v10] close_position returned None for {symbol}")
        return

    # Record close for cooldown — store by BOTH exact symbol and underlying
    # so that closing ETH blocks PF_ETHUSD (and vice versa) during cooldown.
    global _recent_closes
    _ts_now = time.time()
    _recent_closes[symbol] = _ts_now
    _recent_closes[_get_underlying(symbol)] = _ts_now
    # Keep dict bounded
    if len(_recent_closes) > 200:
        cutoff = time.time() - max(_COOLDOWN_THESIS_SEC, _COOLDOWN_OTHER_SEC)
        _recent_closes = {s: t for s, t in _recent_closes.items() if t > cutoff}

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
                source="clean_paper_v10" if _paper else "live_v10",
                paper=_paper,
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
            from config import ACCOUNT_SIZE as _ACCT_SIZE

            _close_oid = (
                str(close_result.get("order_id", "")).strip()
                or f"close_{symbol}_{int(time.time())}"
            )
            _src_tag = "clean_paper_v10" if _paper else "live_v10"

            # Tier: quarantine impossible PnL; verify if attribution ran; else suspect
            _acct = float(_ACCT_SIZE)
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
        # Persist the updated position (reduced qty + updated flags) so a restart
        # won't re-fire scale-outs that already happened.
        try:
            from logging_db.trade_logger import persist_position as _pp
            import datetime as _dt

            _pp(
                symbol=symbol,
                strategy="v10_perp",
                qty=pos.get("qty", 0),
                entry=pos.get("entry_price", 0),
                stop=pos.get("stop_price", 0),
                target=pos.get("take_profit_price", 0),
                high_since_entry=pos.get("peak_price", pos.get("entry_price", 0)),
                ts_entry=_dt.datetime.fromtimestamp(pos.get("entry_ts", 0)).isoformat(),
                paper=_paper,
                direction=pos.get("direction", "LONG"),
                entry_reason=pos.get("entry_setup", ""),
                atr_at_entry=pos.get("atr_at_entry", 0.0),
                composite_score=pos.get("entry_composite_score", 0.0),
                trailing_active=pos.get("trailing_active", False),
                trailing_stop_price=pos.get("trailing_stop_price", 0.0),
                scale_33_done=pos.get("scale_33_done", False),
                scale_66_done=pos.get("scale_66_done", False),
                leverage=pos.get("leverage", 3),
            )
        except Exception as _pe:
            logger.debug(f"[v10] partial close persist error {symbol}: {_pe}")


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
        ks.check_balance(current, _initial_balance, paper=_paper)
    except Exception as e:
        logger.debug(f"[v10] kill_switch_monitor error: {e}")


# ── hedge_rebalance ───────────────────────────────────────────────────────────


def hedge_rebalance():
    """5-minute loop: rebalance delta-neutral hedge position."""
    try:
        he = _import_hedge_engine()
        perps = _import_perps_engine()
        if he is None or perps is None:
            return
        open_positions = perps.get_open_positions()
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
        he.rebalance(open_positions, balance, btc_price=_btc_price, paper=_paper)
    except Exception as e:
        logger.debug(f"[v10] hedge_rebalance error: {e}")


# ── ml_retrain_check ──────────────────────────────────────────────────────────


def ml_retrain_check():
    """6-hour loop: trigger walk-forward retrains for slots with enough new data."""
    try:
        ll = _import_learning_loop()
        if ll is None:
            return
        triggered = ll.maybe_trigger_retrains(paper=_paper)
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

    # Only run during US regular session 9:30–15:45 ET on weekdays
    if now_et.weekday() >= 5:
        return
    h, m = now_et.hour, now_et.minute
    if not ((h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 45)):
        return

    today_str = now_et.strftime("%Y-%m-%d")

    # Reset opening range and daily P&L each new day
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

        # Build / extend opening range (9:30–10:00 ET)
        if h == 9 and m < 60:  # still 9:xx
            if not _mes_or_locked:
                _mes_or_high = max(_mes_or_high, price)
                _mes_or_low = min(_mes_or_low, price)
                logger.debug(f"[mes] OR building: {_mes_or_low:.2f}–{_mes_or_high:.2f}")

        # Lock OR at 10:00 ET
        if (
            h >= 10
            and not _mes_or_locked
            and _mes_or_high > 0
            and _mes_or_low < float("inf")
        ):
            _mes_or_locked = True
            or_range = _mes_or_high - _mes_or_low
            logger.info(
                f"[mes] Opening range locked: {_mes_or_low:.2f}–{_mes_or_high:.2f} "
                f"({or_range:.2f} pts)"
            )

        # Don't trade before OR is locked
        if not _mes_or_locked:
            return

        # Hard stop at 15:45 — close any position
        if h == 15 and m >= 45:
            pos = broker.get_position("MES")
            if pos and pos.get("qty", 0) != 0:
                logger.info("[mes] EOD close — 15:45 ET hard stop")
                qty = abs(int(pos["qty"]))
                # Use "side" key (always set by buy_mes/short_mes) — qty is stored as
                # positive for both LONG and SHORT, so qty>0 would always be True.
                if pos.get("side", "LONG") == "LONG":
                    broker.sell_mes(qty=qty, reason="eod_close")
                else:
                    broker.cover_mes(qty=qty, reason="eod_close")
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
    """2:00 AM ET nightly: run RBI research + backtest pipeline on BTCUSDT."""
    logger.info("[v10] rbi_nightly: starting BTCUSDT RBI pipeline")
    try:
        ll = _import_learning_loop()
        if ll is None:
            return
        results = ll.run_nightly_rbi(symbol="BTCUSDT", paper=_paper)
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
    global _initial_balance, _paper
    try:
        from config import PAPER_TRADING, ACCOUNT_SIZE

        _paper = bool(PAPER_TRADING)
        _initial_balance = float(ACCOUNT_SIZE)
    except Exception as e:
        logger.warning(f"[v10] config read error: {e} — using defaults")
        _paper = True
        _initial_balance = 5000.0

    logger.info(
        f"[v10] mode={'PAPER' if _paper else 'LIVE'} "
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
                    f"mode={'PAPER' if _paper else 'LIVE'} "
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
            perps.load_positions_from_db(paper=_paper)
        except Exception as _e:
            logger.warning(f"[v10] load_positions_from_db error: {_e}")

    # Log startup
    _startup_notification()
    logger.info("[v10] Scheduler starting — wiring schedules...")

    # Wire schedules
    schedule.every(5).minutes.do(scan_and_trade)
    schedule.every(30).seconds.do(exit_monitor)
    schedule.every(5).minutes.do(hedge_rebalance)
    schedule.every(60).seconds.do(kill_switch_monitor)
    schedule.every(60).seconds.do(_run_health_check)
    schedule.every(6).hours.do(ml_retrain_check)
    schedule.every().day.at("07:00").do(rbi_nightly)  # 07:00 UTC ≈ 02:00 ET

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
            logger.debug(f"[v10] labeler job error: {_le}")

    schedule.every(15).minutes.do(_labeler_job)

    # v13.6: nightly proof + drift + learning audit at 08:00 UTC (03:00 ET, after RBI)
    def _nightly_audit_job():
        try:
            import threading as _thr
            from monitoring.nightly_audit import run_audit

            _t = _thr.Thread(target=run_audit, kwargs={"run_proof": True}, daemon=True)
            _t.start()
        except Exception as _ae:
            logger.debug(f"[v10] nightly audit job error: {_ae}")

    schedule.every().day.at("08:00").do(_nightly_audit_job)  # 08:00 UTC ≈ 03:00 ET

    # Periodic system + crypto lane heartbeat (every 1 minute)
    def _write_heartbeat():
        try:
            from runtime.runtime_state import write_system_heartbeat, upsert_lane_state
            from datetime import datetime, timezone

            write_system_heartbeat()
            upsert_lane_state(
                "crypto", last_heartbeat_at=datetime.now(timezone.utc).isoformat()
            )
        except Exception:
            pass

    schedule.every(1).minutes.do(_write_heartbeat)

    from config import FUTURES_LANE_ACTIVE

    if FUTURES_LANE_ACTIVE:
        schedule.every(2).minutes.do(mes_futures_scan)
        logger.info("[v10] MES futures scanner wired (every 2 min)")

    logger.info("[v10] All schedules wired. Running scan immediately...")

    # Run immediately on startup (don't wait 5 minutes for first scan)
    scan_and_trade()

    logger.info("[v10] Main loop running. Press Ctrl+C to stop.")
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[v10] Shutdown requested via KeyboardInterrupt")
            raise
        except Exception as e:
            logger.error(
                f"[v10] Scheduler loop error: {e}\n{traceback.format_exc()[:800]}"
            )
            time.sleep(5)  # brief back-off before resuming
