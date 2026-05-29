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
            stubs = result.get("stubs_persisted", 0)
            contracts = result.get("persisted", 0)
            active_in_db = result.get("active_in_db", 0)
            logger.info(
                f"[ForecastRunner] Discovery: found={result['found']} "
                f"persisted={contracts} "
                f"stubs={stubs} "
                f"active={active_in_db}"
            )
            # Update lane readiness_state to reflect post-discovery truth.
            try:
                from runtime.runtime_state import upsert_lane_state as _uls

                _connected = bool(broker.is_connected())
                _snapshot = _forecast_runtime_snapshot(
                    connected=_connected,
                    contracts=contracts,
                    stubs=stubs,
                    active_markets=active_in_db
                )
                _uls(
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
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"[ForecastRunner] Discovery cycle error: {e}")
            try:
                from runtime.runtime_state import upsert_lane_state as _uls

                _uls(
                    "forecast",
                    enabled=1,
                    active=1,
                    health="ERROR",
                    blocked_reason=str(e)[:160],
                    action_needed="inspect_forecast_runner",
                )
            except Exception:
                pass
            return {"found": 0, "persisted": 0, "errors": [str(e)]}


# ── Strategy evaluation loop ───────────────────────────────────────────────────


def run_strategy_cycle(bankroll: float = 100.0) -> list[dict]:
    """
    5-min cycle: evaluate all active contracts, submit approved entries.

    Returns list of entry results (empty if nothing qualified).
    """
    with _eval_lock:
        logger.info(f"[ForecastRunner] Starting strategy cycle (bankroll=${bankroll:.2f})...")
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

            if open_count >= MAX_CONCURRENT_POSITIONS:
                logger.warning(
                    f"[ForecastRunner] Max concurrent positions reached ({open_count}/{MAX_CONCURRENT_POSITIONS}) — skip eval"
                )
                return []

            # Deployed capital fraction
            deployed_value = sum(
                (p.get("entry_price") or 0) * (p.get("qty") or 0) * 100
                for p in open_positions
            )
            deployed_pct = min(1.0, deployed_value / max(bankroll, 1.0))

            if deployed_pct >= MAX_DEPLOYED_PCT:
                logger.warning(
                    f"[ForecastRunner] Deployed cap hit ({deployed_pct:.1%}/{MAX_DEPLOYED_PCT:.1%}) — skip eval"
                )
                return []

            # Open event families (to detect same-event exposure)
            from collections import defaultdict
            open_event_families_counts = defaultdict(int)
            for p in open_positions:
                family = p.get("local_symbol", "").split("_")[0]
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

            candidates = evaluate_all_contracts(
                active_contracts=active,
                get_bars_fn=_get_bars_fn,
                get_quotes_fn=_get_quotes_fn,
                bankroll=bankroll,
                deployed_pct=deployed_pct,
                open_positions_count=open_count,
                open_event_families=open_event_families_counts,
                macro_context=macro_ctx,
            )

            if not candidates:
                logger.info("[ForecastRunner] No trade candidates qualified in this cycle.")

            for candidate in candidates:
                result = candidate["result"]
                contract = candidate["contract"]
                local_sym = contract.get("local_symbol", "")

                # Only enter if econ approved AND contracts > 0
                if not result.econ_approved or result.position_contracts <= 0:
                    veto_msg = f"[ForecastRunner] {local_sym} vetoed: {result.veto_reason or 'sizing_zero'}"
                    if result.veto_reason and "concurrent_cap" in result.veto_reason:
                        logger.warning(veto_msg)
                        log_event("INFO", "ForecastRunner", veto_msg)
                    else:
                        logger.debug(veto_msg)
                        # Still log to DB for X-Ray observability
                        log_event("INFO", "ForecastRunner", veto_msg)
                    continue

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

                try:
                    # Guardrail 3: Taker-Override Friction Controls
                    # If edge >= 22% and is_short_term, use market order.
                    order_type = "market" if result.is_taker_override else "limit"
                    
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
                        type=order_type,
                        reason=f"{result.strategy_family}_ev={result.ev:.4f}_taker={result.is_taker_override}",
                        strategy=f"forecast_{result.strategy_family}",
                    )
                    
                    # Notify via Telegram/DB
                    if entry_result.get("order_id") != "ERR":
                        entry_msg = f"[ForecastRunner] Entry: {contract.get('local_symbol')} {result.side.upper()} @ {ask_price} (ev={result.ev:.4f})"
                        log_event("INFO", "ForecastRunner", entry_msg)
                        try:
                            from notifications.notification_engine import notify_trade_open
                            notify_trade_open(
                                symbol=contract.get("local_symbol", ""),
                                direction=result.side.upper(),
                                size_usd=result.position_contracts * ask_price,
                                entry_price=ask_price,
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
                    # v19.1.6: Support ISO format and standard format
                    from dateutil.parser import parse as date_parse
                    expiry = date_parse(last_trade)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
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
                        if now >= expiry:
                            resolved = True
                    except: pass

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
                    flatten_res = broker.flatten_position(
                        local_symbol=local_symbol,
                        right=right,
                        qty=qty,
                        strategy="forecast_monitor",
                        reason="resolved_or_expired",
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
        from runtime.runtime_state import upsert_lane_state
        from logging_db.trade_logger import _conn
        import json

        broker = _get_broker()
        if not broker.is_connected(): return
        
        positions = broker.get_positions()
        enriched = []
        total_pnl = 0.0
        
        conn = _conn()
        cursor = conn.cursor()
        
        for p in positions:
            ticker = p.get('local_symbol')
            qty = float(p.get('qty', 0))
            
            current_px = 0.0
            try:
                quote = broker.get_quote(ticker)
                bid = float(quote.get('bid', 0) or 0)
                ask = float(quote.get('ask', 0) or 0)
                current_px = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (bid or ask or 0)
            except: pass
            
            entry_px = float(p.get('avg_entry') or 0.0)
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
                    "SELECT m.market_name, c.resolution_at FROM forecast_contracts c JOIN forecast_markets m ON c.market_id = m.id WHERE c.local_symbol = ? LIMIT 1",
                    (ticker,)
                )
                meta = cursor.fetchone()
                if meta:
                    event_title = meta[0]
                    resolution_at = meta[1]
            except: pass
            
            mult = 1 if p.get('side') == 'YES' else -1
            pnl = (current_px - entry_px) * qty * mult
            potential = (1.0 - entry_px) * qty
            
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
                except: pass

            enriched.append({
                "symbol": ticker,
                "title": event_title,
                "qty": qty,
                "entry": round(entry_px, 4),
                "mark": round(current_px, 4),
                "pnl": round(pnl, 2),
                "potential": round(potential, 2),
                "countdown": countdown,
                "entered_at": entered_at,
                "sentiment": "Healthy" if pnl > -0.01 else "Under Pressure"
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
        
        upsert_lane_state("forecast", snapshot_json=json.dumps(snapshot))
    except Exception as e:
        logger.error(f"[ForecastRunner] Cache state error: {e}")


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
    
    schedule.every().day.at("08:00").do(_send_daily_token_burn_report)

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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(_ROOT, "logs", "forecast.log"), encoding="utf-8"
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
