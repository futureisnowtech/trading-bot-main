"""
forecast/quote_harvester.py — Real-time Kalshi quote polling and bar building.

Runs on a background thread (started by forecast/runner.py).

Behaviour:
  - Every POLL_INTERVAL_SEC (60s): fetch bid/ask/mid for all active contracts.
  - v18.34: Automatic candle-based backfill for new contracts.
  - v19.1.5: Direct polling of Kalshi orderbook (v2 API).
  - v19.1.6: Decoupled live polling from historical backfilling.
"""

import logging
import os
import sys
import threading
import time
import sqlite3
import traceback
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from forecast.db import (
    get_active_contracts,
    insert_quote,
    upsert_bar,
    get_last_bar_ts,
    BAR_RETENTION_DAYS,
    DB_PATH,
)
from logging_db.trade_logger import log_event

logger = logging.getLogger("forecast.quote_harvester")

POLL_INTERVAL_SEC = 60
PRUNE_INTERVAL_MIN = 60
QUOTE_LOOKBACK = 1200 # approx 20m of 1s quotes? No, for forecast it is much slower.


# ── Internal Bar Building Logic ──────────────────────────────────────────────


def _build_all_bars(contract_id: int, db_path: Optional[str] = None):
    """v18.17: Master bar builder for all intervals."""
    # 5m, 30m, 1h, 4h, 1d
    for interval, seconds in [
        ("5m", 300),
        ("30m", 1800),
        ("1h", 3600),
        ("4h", 14400),
        ("1d", 86400),
    ]:
        _build_bars_for_interval(contract_id, interval, seconds, db_path=db_path)


def _build_bars_for_interval(contract_id: int, interval: str, seconds: int, db_path: Optional[str] = None):
    """Aggregate raw quotes into bars for one interval."""
    try:
        from forecast.db import get_recent_quotes_for_bar
        quotes = get_recent_quotes_for_bar(contract_id, seconds, db_path=db_path)
        if not quotes:
            return

        # Simple OHLC from quotes
        prices = [float(q["mid"]) for q in quotes]
        ts_open = quotes[0]["ts"]
        ts_close = quotes[-1]["ts"]
        
        upsert_bar(
            contract_id=contract_id,
            interval=interval,
            ts_open=ts_open,
            ts_close=ts_close,
            o=prices[0],
            h=max(prices),
            l=min(prices),
            c_=prices[-1],
            mid_mean=sum(prices) / len(prices),
            spread_mean=sum(float(q["spread"]) for q in quotes) / len(quotes),
            vol_proxy=len(quotes),
            db_path=db_path,
        )
    except Exception as e:
        logger.debug(f"Bar build failed {contract_id} {interval}: {e}")


def _resample_candles_to_bars(contract_id: int, candles: list[dict], interval: str, seconds: int, db_path: Optional[str] = None) -> int:
    """Helper for backfill: resample OHLC candles into database bars."""
    written = 0
    for c in candles:
        try:
            ts_open = c.get("start_time") or c.get("ts")
            # If it's a date string, ensure ISO
            if isinstance(ts_open, str) and "T" not in ts_open:
                # Assuming YYYYMMDD?
                pass
            
            mid_mean = (float(c["open"]) + float(c["close"])) / 2.0
            
            upsert_bar(
                contract_id=contract_id,
                interval=interval,
                ts_open=ts_open,
                ts_close=ts_open, # approximate for backfill
                o=float(c["open"]),
                h=float(c["high"]),
                l=float(c["low"]),
                c_=float(c["close"]),
                mid_mean=mid_mean,
                spread_mean=0.0, # Not available in candles
                vol_proxy=0.0,   # Hard to compute accurately from resampled candles
                db_path=db_path,
            )
            # v19.1.6: Add yield to prevent CPU/DB starvation
            time.sleep(0.01)
            written += 1
        except Exception as e:
            logger.warning(f"upsert_bar backfill failed cid={contract_id} {interval}: {e}")

    return written


# ── QuoteHarvester Engine ───────────────────────────────────────────────────


class QuoteHarvester:
    def __init__(
        self,
        broker=None,
        poll_interval: int = POLL_INTERVAL_SEC,
        db_path: Optional[str] = None,
    ) -> None:
        self._broker = broker
        self._poll_sec = poll_interval
        self._db_path = db_path
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_prune = time.time()
        self._backfilled_cids: set[int] = set()
        # v19.1.6: Throttling backfills to 1 every 10s
        self._backfill_lock = threading.Lock()
        self._last_backfill_ts = 0.0

    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"[QuoteHarvester] Started (poll_interval={self._poll_sec}s)")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("[QuoteHarvester] Stopped")

    def run_once(self) -> None:
        """Execute a single poll/build cycle without starting the daemon thread."""
        self._poll_and_build()

    def backfill_bars(self, contract_id: int, ticker: str) -> None:
        """v18.34: Backfill historical candles from broker to populate technical indicators."""
        if not self._broker or not self._broker.is_connected():
            return

        db_path = self._db_path
        try:
            # 1h bars backfill (main signal generator)
            c1h = self._broker.get_historical_candles(ticker, interval="1h", count=100)
            if c1h:
                _resample_candles_to_bars(contract_id, c1h, "1h", 3600, db_path=db_path)
            
            # 5m bars backfill (short-term momentum)
            c5m = self._broker.get_historical_candles(ticker, interval="5m", count=100)
            if c5m:
                _resample_candles_to_bars(contract_id, c5m, "5m", 300, db_path=db_path)

            # 1d bars backfill (regime context)
            c1d = self._broker.get_historical_candles(ticker, interval="1d", count=30)
            if c1d:
                _resample_candles_to_bars(contract_id, c1d, "1d", 86400, db_path=db_path)
        except Exception as e:
            logger.debug(f"Backfill failed for {ticker}: {e}")

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._poll_and_build()
                
                # Prune once per hour
                if time.time() - self._last_prune > PRUNE_INTERVAL_MIN * 60:
                    from forecast.db import prune_old_bars
                    count = prune_old_bars(db_path=self._db_path)
                    if count > 0:
                        logger.info(f"[QuoteHarvester] Pruned {count} old bars")
                    self._last_prune = time.time()

            except Exception as e:
                logger.error(f"[QuoteHarvester] Loop error: {e}")
                logger.error(traceback.format_exc())

            time.sleep(self._poll_sec)

    def _poll_and_build(self) -> None:
        """
        One poll cycle: fetch quotes for all active contracts in parallel.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(f"[QuoteHarvester] Starting parallel poll cycle...")
        
        try:
            contracts = get_active_contracts(db_path=self._db_path)
        except Exception as e:
            logger.warning(f"[QuoteHarvester] get_active_contracts failed: {e}")
            return

        if not contracts:
            return

        def _fetch_one(contract):
            local_symbol = contract.get("local_symbol", "")
            if self._broker and self._broker.is_connected():
                try:
                    q = self._broker.get_quote(local_symbol)
                    return {"contract": contract, "quote": q}
                except:
                    return None
            return None

        # v19.1.6: Parallel acquisition (No DB writes here!)
        fetched_results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_fetch_one, c) for c in contracts]
            for future in as_completed(futures):
                res = future.result()
                if res: fetched_results.append(res)

        # ── Sequential DB Persistence (Main thread only to avoid locking) ───
        total_quotes = 0
        skipped_no_depth = 0
        
        for item in fetched_results:
            contract = item["contract"]
            q = item["quote"]
            contract_id = contract.get("id")
            local_symbol = contract.get("local_symbol", "")
            right = contract.get("right", "C")
            
            # v19.1.6: Directional Price Inversion
            # Kalshi get_quote exposes both YES and NO top-of-book fields.
            if right == "P": # NO contract
                side = "NO"
                bid = q.get("no_bid")
                ask = q.get("no_ask")
                mid = q.get("no_mid")
                spread = q.get("no_spread")
                bid_size = q.get("no_bid_size") or q.get("no_bid_vol")
                ask_size = q.get("no_ask_size") or q.get("no_ask_vol")
            else:
                side = "YES"
                bid = q.get("bid")
                ask = q.get("ask")
                mid = q.get("mid")
                spread = q.get("spread")
                bid_size = q.get("bid_size") or q.get("bid_vol")
                ask_size = q.get("ask_size") or q.get("ask_vol")

            if mid is not None:
                try:
                    ts = datetime.now(timezone.utc).isoformat()
                    insert_quote(
                        contract_id=contract_id,
                        ts=ts,
                        bid=bid,
                        ask=ask,
                        bid_size=bid_size,
                        ask_size=ask_size,
                        mid=mid,
                        spread=spread,
                        implied_prob=mid,
                        side=side,
                        db_path=self._db_path,
                    )
                    _build_all_bars(contract_id, db_path=self._db_path)
                    total_quotes += 1
                except Exception as e:
                    logger.debug(f"persist error {local_symbol}: {e}")
            else:
                skipped_no_depth += 1

        # Log cycle summary
        msg = f"[QuoteHarvester] Cycle complete: {total_quotes} saved, {skipped_no_depth} skipped across {len(contracts)} contracts"
        logger.info(msg)
        log_event("INFO", "QuoteHarvester", msg)

    def _trigger_background_backfill(self, contract_id, symbol):
        """v19.1.6: Start backfill in a non-blocking background thread."""
        def _worker():
            with self._backfill_lock:
                if contract_id in self._backfilled_cids: return
                
                # Throttle to 1 every 20 seconds to be very safe
                now = time.time()
                elapsed = now - self._last_backfill_ts
                if elapsed < 20.0:
                    time.sleep(20.0 - elapsed)
                
                try:
                    logger.info(f"[QuoteHarvester] Background backfill starting for {symbol}...")
                    self.backfill_bars(contract_id, symbol)
                    self._backfilled_cids.add(contract_id)
                    self._last_backfill_ts = time.time()
                except Exception as e:
                    logger.debug(f"Backfill error {symbol}: {e}")
        
        threading.Thread(target=_worker, daemon=True).start()


def get_paired_quotes(market_id: int, strike: float, last_trade_at: str, db_path: Optional[str] = None) -> dict:
    """
    v18.17: Return the most recent YES and NO quotes for a specific strike/expiry.
    Used by strategy_engine for probability validation.
    """
    pair = {"yes_quote": None, "no_quote": None}

    # Simple fallback: find latest for this strike
    with sqlite3.connect(db_path or DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        res = conn.execute("""
            SELECT q.* FROM forecast_quotes q
            JOIN forecast_contracts c ON q.contract_id = c.id
            WHERE c.market_id = ? AND c.strike = ? AND c.last_trade_at = ?
            ORDER BY q.ts DESC LIMIT 2
        """, (market_id, strike, last_trade_at)).fetchall()

        for r in res:
            if r['side'] == 'YES': pair['yes_quote'] = dict(r)
            else: pair['no_quote'] = dict(r)

    if pair["yes_quote"] and pair["no_quote"]:
        yes_ask = float(pair["yes_quote"].get("ask") or 0.0)
        no_ask = float(pair["no_quote"].get("ask") or 0.0)
        yes_mid = float(pair["yes_quote"].get("mid") or 0.0)
        no_mid = float(pair["no_quote"].get("mid") or 0.0)
        pair["omega_t"] = yes_ask + no_ask - 1.0
        pair["g_t"] = yes_mid + no_mid - 1.0
    else:
        pair["omega_t"] = None
        pair["g_t"] = None

    return pair

def build_bars_now(
contract_id: int, db_path: Optional[str] = None) -> dict:
    """
    Trigger immediate bar build for one contract across all intervals.
    Useful for backfill or test fixtures.
    Returns {interval: bars_written}.
    """
    from forecast.db import get_bars

    _build_all_bars(contract_id, db_path=db_path)
    return {
        interval: len(get_bars(contract_id, interval, limit=1, db_path=db_path))
        for interval in ("5m", "30m", "1h", "4h", "1d")
    }


if __name__ == "__main__":
    from execution.kalshi_broker import get_kalshi_broker
    broker = get_kalshi_broker()
    if not broker.connect():
        logger.error("Could not connect to broker. Exiting.")
        sys.exit(1)

    harvester = QuoteHarvester(broker=broker)
    harvester.start()

    # Keep main thread alive
    while True:
        time.sleep(1)
