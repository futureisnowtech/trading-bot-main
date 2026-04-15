"""
forecast/runner.py — ForecastEx lane scheduler loop.

Loop cadences:
  discovery      every 30 min  — refresh market/contract cache from IBKR
  quote harvest  every 60 sec  — collect bid/ask/mid for all active contracts
  strategy eval  every 5 min   — run strategy engine, submit approved entries
  position mon   every 30 sec  — monitor open positions, flatten resolved ones

Architecture:
  - All loops run on daemon threads via schedule library (same pattern as v10_runner).
  - ForecastExBroker singleton (client ID 3) shared across all loops.
  - QuoteHarvester starts its own background thread.
  - Never touches crypto or MES lanes.
  - Paper mode: all order logic executes exactly as live; zero API calls on
    paper (forecastex_broker.is_connected() returns False when TWS not available,
    and orders are logged with FX_PAPER_ prefix).

Risk guardrails (hardcoded, no override):
  - max concurrent positions: 2
  - max deployed capital: 35% of account
  - max risk per event: 10% of account
  - no same-contract doubling down
  - no same-event hedge spaghetti (two positions on same market forbidden)
  - contracts_from_fraction() always returns 0 when caps are hit
"""

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger(__name__)

# ── Lazy imports (avoid heavy deps at module load time) ────────────────────────
_broker = None
_harvester = None
_discovery_lock = threading.Lock()
_eval_lock = threading.Lock()


def _get_broker():
    from execution.forecastex_broker import get_forecastex_broker

    return get_forecastex_broker()


def _get_harvester():
    global _harvester
    if _harvester is None:
        from forecast.quote_harvester import QuoteHarvester

        _harvester = QuoteHarvester(broker=_get_broker())
    return _harvester


# ── Discovery loop ─────────────────────────────────────────────────────────────


def run_discovery_cycle() -> dict:
    """
    30-min cycle: refresh market/contract list from IBKR FORECASTX.
    Idempotent — upserts only; never deletes.
    """
    with _discovery_lock:
        try:
            from forecast.discovery import run_discovery
            from forecast.db import init_forecast_db

            init_forecast_db()
            broker = _get_broker()
            result = run_discovery(broker=broker)
            logger.info(
                f"[ForecastRunner] Discovery: found={result['found']} "
                f"persisted={result['persisted']} active={result['active_in_db']}"
            )
            return result
        except Exception as e:
            logger.error(f"[ForecastRunner] Discovery cycle error: {e}")
            return {"found": 0, "persisted": 0, "errors": [str(e)]}


# ── Strategy evaluation loop ───────────────────────────────────────────────────


def run_strategy_cycle(bankroll: float = 100.0) -> list[dict]:
    """
    5-min cycle: evaluate all active contracts, submit approved entries.

    Returns list of entry results (empty if nothing qualified).
    """
    with _eval_lock:
        entries = []
        try:
            from forecast.db import get_active_contracts, get_bars, get_recent_quotes
            from forecast.quote_harvester import get_paired_quotes
            from forecast.strategy_engine import (
                MAX_CONCURRENT_POSITIONS,
                MAX_DEPLOYED_PCT,
                evaluate_all_contracts,
            )

            broker = _get_broker()
            active = get_active_contracts()
            if not active:
                return []

            # Current position state
            open_positions = broker.get_positions() if broker.is_connected() else []
            open_count = len(open_positions)

            if open_count >= MAX_CONCURRENT_POSITIONS:
                logger.debug(
                    "[ForecastRunner] Max concurrent positions reached — skip eval"
                )
                return []

            # Deployed capital fraction
            deployed_value = sum(
                (p.get("entry_price") or 0) * (p.get("qty") or 0) * 100
                for p in open_positions
            )
            deployed_pct = min(1.0, deployed_value / max(bankroll, 1.0))

            if deployed_pct >= MAX_DEPLOYED_PCT:
                logger.debug(
                    f"[ForecastRunner] Deployed cap hit ({deployed_pct:.1%}) — skip eval"
                )
                return []

            # Open event families (to detect same-event exposure)
            open_event_families: set = {
                p.get("local_symbol", "").split("_")[0] for p in open_positions
            }

            def _get_bars_fn(contract_id: int, interval: str) -> list[dict]:
                return get_bars(contract_id, interval, limit=200)

            def _get_quotes_fn(
                market_id: int, strike: float, last_trade_at: str
            ) -> dict:
                return get_paired_quotes(market_id, strike, last_trade_at)

            candidates = evaluate_all_contracts(
                active_contracts=active,
                get_bars_fn=_get_bars_fn,
                get_quotes_fn=_get_quotes_fn,
                bankroll=bankroll,
                deployed_pct=deployed_pct,
                open_positions_count=open_count,
                open_event_families=open_event_families,
            )

            for candidate in candidates:
                result = candidate["result"]

                # Only enter if econ approved AND contracts > 0
                if not result.econ_approved or result.position_contracts <= 0:
                    continue

                contract = candidate["contract"]

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

                # Determine limit price (cheapest qualifying contract heuristic)
                ask_price = result.ask_yes if result.side == "YES" else result.ask_no
                if not ask_price or ask_price <= 0:
                    continue

                # --- LIVE TEST TRADE RULE ---
                # Only place if readiness validator says GREEN (checked in validate.py)
                # For now, execute directly (validator is called by launch script)

                try:
                    entry_result = broker.place_buy_order(
                        contract_dict={
                            "conid": contract.get("conid", 0),
                            "local_symbol": contract.get("local_symbol", ""),
                            "right": contract.get("right", "C"),
                            "strike": contract.get("strike", 0.0),
                            "last_trade_at": contract.get("last_trade_at", ""),
                        },
                        qty=result.position_contracts,
                        limit_price=ask_price,
                        reason=f"{result.strategy_family}_ev={result.ev:.4f}",
                        strategy=f"forecast_{result.strategy_family}",
                    )
                    entries.append(
                        {
                            "contract": contract,
                            "result": result,
                            "entry": entry_result,
                        }
                    )
                    logger.info(
                        f"[ForecastRunner] ENTERED {contract.get('local_symbol')} "
                        f"{result.side} × {result.position_contracts} @ {ask_price:.4f} "
                        f"| strategy={result.strategy_family} ev={result.ev:.4f} "
                        f"q_hat={result.q_hat:.4f}"
                    )
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

    Resolution check:
    - If contract has passed its last_trade_at, it has resolved.
    - If we have a matching row in forecast_resolutions, we know the outcome.
    - In either case, flatten by buying the opposite side (or mark as resolved).

    Also logs any positions held beyond 96h (dead-money backstop for event lane).
    """
    try:
        broker = _get_broker()
        if not broker.is_connected():
            return

        positions = broker.get_positions()
        now = datetime.now(timezone.utc)

        for pos in positions:
            local_symbol = pos.get("local_symbol", "")
            right = pos.get("right", "C")
            qty = pos.get("qty", 0)
            last_trade = pos.get("last_trade_at", "")
            entered_at = pos.get("entered_at", "")

            if not qty:
                continue

            # Check resolution: has the contract expired?
            resolved = False
            if last_trade:
                try:
                    fmt = "%Y%m%d %H:%M:%S" if " " in last_trade else "%Y%m%d"
                    expiry = datetime.strptime(last_trade, fmt).replace(
                        tzinfo=timezone.utc
                    )
                    if now >= expiry:
                        resolved = True
                        logger.info(
                            f"[ForecastRunner] Contract expired: {local_symbol} — flattening"
                        )
                except Exception:
                    pass

            # Dead-money backstop: > 96h open
            if not resolved and entered_at:
                try:
                    entered = datetime.fromisoformat(entered_at)
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
                    broker.flatten_position(
                        local_symbol=local_symbol,
                        right=right,
                        qty=qty,
                        strategy="forecast_monitor",
                        reason="resolved_or_expired",
                    )
                except Exception as e:
                    logger.error(f"[ForecastRunner] Flatten failed {local_symbol}: {e}")

    except Exception as e:
        logger.error(f"[ForecastRunner] Position monitor error: {e}")

    # Heartbeat — run_position_monitor is the most frequent forecast loop (30s)
    try:
        from runtime.runtime_state import mark_lane_heartbeat

        mark_lane_heartbeat("forecast")
    except Exception:
        pass


# ── Startup / teardown ─────────────────────────────────────────────────────────


def start_forecast_lane(bankroll: float = 100.0) -> None:
    """
    Start all forecast lane loops using schedule.

    Call this from main.py or a dedicated forecast launcher.
    Blocks until the caller's scheduler loop runs (schedule.run_pending()).

    Loops registered:
      every 30 min  → run_discovery_cycle()
      every 60 sec  → harvester runs internally
      every 5 min   → run_strategy_cycle(bankroll)
      every 30 sec  → run_position_monitor()
    """
    import schedule

    try:
        from forecast.db import init_forecast_db

        init_forecast_db()
        logger.info("[ForecastRunner] DB initialised")
    except Exception as e:
        logger.error(f"[ForecastRunner] DB init failed: {e}")
        return

    # Connect broker — ib_insync connect() is async; the return value from the
    # synchronous wrapper may be False even though the connection completes moments
    # later.  Re-check is_connected() after a short grace period.
    broker = _get_broker()
    connected = broker.connect()
    if not connected:
        # Give the async connection up to 4s to complete before giving up.
        time.sleep(4)
        connected = broker.is_connected()
    if not connected:
        logger.warning(
            "[ForecastRunner] ForecastEx broker not connected — "
            "running in paper/offline mode (no live orders)"
        )

    try:
        from runtime.runtime_state import upsert_lane_state

        upsert_lane_state(
            "forecast",
            active=1,
            connected=int(connected),
            readiness_state="BROKER_DISCONNECTED" if not connected else "NO_UNDERLIERS",
        )
    except Exception:
        pass

    # Start quote harvester
    harvester = _get_harvester()
    harvester.start()

    # Initial discovery
    run_discovery_cycle()

    # Register scheduler jobs
    schedule.every(30).minutes.do(run_discovery_cycle)
    schedule.every(5).minutes.do(lambda: run_strategy_cycle(bankroll))
    schedule.every(30).seconds.do(run_position_monitor)

    logger.info(
        f"[ForecastRunner] Lane started | bankroll=${bankroll:.0f} "
        f"| connected={connected}"
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(_ROOT, "logs", "forecastex.log"), encoding="utf-8"
            ),
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
