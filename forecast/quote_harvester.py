"""
forecast/quote_harvester.py — Real-time ForecastEx quote polling and bar building.

Runs on a background thread (started by forecast/runner.py).

Behaviour:
  - Every POLL_INTERVAL_SEC (60s): fetch bid/ask/mid for all active contracts.
  - Persist raw quotes to forecast_quotes.
  - Aggregate into OHLC bars for all 5 required intervals:
      5m, 30m, 1h, 4h, 1d
  - Prune old quotes every PRUNE_INTERVAL_MIN (60 min).
  - Never crashes caller; logs all errors and continues.

Canonical pricing rule: midpoint (bid+ask)/2 is the OHLC price series.
  o = first mid in bar window
  h = max mid
  l = min mid
  c = last mid
  mid_mean  = mean(mid)
  spread_mean = mean(spread)
  vol_proxy = std(mid) within bar  (proxy for realised volatility of implied probability)

All timestamps are UTC ISO-8601 strings.
"""

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

from forecast.db import (
    get_active_contracts,
    get_recent_quotes,
    insert_quote,
    prune_old_bars,
    prune_old_quotes,
    upsert_bar,
)
from logging_db.trade_logger import log_event

logger = logging.getLogger(__name__)

# ── Timing constants ───────────────────────────────────────────────────────────
POLL_INTERVAL_SEC: int = 60  # quote polling cadence
PRUNE_INTERVAL_MIN: int = 60  # how often to prune old quotes/bars
BAR_INTERVALS: dict[str, int] = {  # interval_name → bar_width_seconds
    "5m": 300,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _floor_ts(ts: datetime, bar_sec: int) -> datetime:
    """Truncate a UTC datetime to the nearest bar boundary."""
    epoch = int(ts.timestamp())
    floored = (epoch // bar_sec) * bar_sec
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def _resample_candles_to_bars(
    contract_id: int,
    candles: list[dict],
    interval: str,
    bar_sec: int,
    db_path: Optional[str] = None,
) -> int:
    """
    Resample a list of higher-frequency candles (e.g. 1m) into a target interval bar.
    Upserts into forecast_bars. Returns number of bars written.
    """
    if not candles:
        return 0

    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        try:
            ts_dt = datetime.fromisoformat(c["ts_open"])
            bar_floor = _floor_ts(ts_dt, bar_sec)
            bar_key = bar_floor.isoformat()
            buckets[bar_key].append(c)
        except Exception:
            continue

    written = 0
    for bar_key, bucket_candles in buckets.items():
        if not bucket_candles:
            continue
        
        # Open is the 'o' of the first candle
        o = bucket_candles[0]["o"]
        # Close is the 'c' of the last candle
        c_ = bucket_candles[-1]["c"]
        # High/Low are max/min across all candles in bucket
        h = max(c["h"] for c in bucket_candles)
        l = min(c["l"] for c in bucket_candles)
        
        mids = [c["c"] for c in bucket_candles]
        mid_mean = float(np.mean(mids))
        
        bar_floor_dt = datetime.fromisoformat(bar_key)
        ts_close_dt = bar_floor_dt + timedelta(seconds=bar_sec)

        try:
            upsert_bar(
                contract_id=contract_id,
                interval=interval,
                ts_open=bar_floor_dt.isoformat(),
                ts_close=ts_close_dt.isoformat(),
                o=float(o),
                h=float(h),
                l=float(l),
                c_=float(c_),
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


def _build_bars_for_contract(
    contract_id: int,
    interval: str,
    bar_sec: int,
    db_path: Optional[str] = None,
) -> int:
    """
    Aggregate raw forecast_quotes into bars for one contract/interval.

    Reads the most recent QUOTE_LOOKBACK quotes, groups by bar window,
    and upserts into forecast_bars.  Returns number of bars written.
    """
    # Look back far enough to build all bar sizes (1d needs ~1440 1-min ticks)
    lookback = max(500, bar_sec // POLL_INTERVAL_SEC + 10)
    quotes = get_recent_quotes(contract_id, limit=lookback, db_path=db_path)
    if not quotes:
        return 0

    # Group quotes by floored bar timestamp
    buckets: dict[str, list[dict]] = defaultdict(list)
    for q in quotes:
        if not q.get("mid"):
            continue
        try:
            ts_dt = datetime.fromisoformat(q["ts"])
        except Exception:
            continue
        bar_floor = _floor_ts(ts_dt, bar_sec)
        bar_key = bar_floor.isoformat()
        buckets[bar_key].append(q)

    written = 0
    for bar_key, bucket_quotes in buckets.items():
        mids = [q["mid"] for q in bucket_quotes if q.get("mid") is not None]
        spreads = [q["spread"] for q in bucket_quotes if q.get("spread") is not None]
        if not mids:
            continue

        mids_arr = np.array(mids, dtype=float)
        bar_floor_dt = datetime.fromisoformat(bar_key)
        ts_close_dt = bar_floor_dt + timedelta(seconds=bar_sec)

        try:
            upsert_bar(
                contract_id=contract_id,
                interval=interval,
                ts_open=bar_floor_dt.isoformat(),
                ts_close=ts_close_dt.isoformat(),
                o=float(mids_arr[0]),
                h=float(mids_arr.max()),
                l=float(mids_arr.min()),
                c_=float(mids_arr[-1]),
                mid_mean=float(mids_arr.mean()),
                spread_mean=float(np.mean(spreads)) if spreads else 0.0,
                vol_proxy=float(mids_arr.std()) if len(mids_arr) > 1 else 0.0,
                db_path=db_path,
            )
            written += 1
        except Exception as e:
            logger.warning(f"upsert_bar failed cid={contract_id} {interval}: {e}")

    return written


def _build_all_bars(contract_id: int, db_path: Optional[str] = None) -> dict:
    """Build all 5 bar intervals for one contract. Returns {interval: bars_written}."""
    results = {}
    for interval, bar_sec in BAR_INTERVALS.items():
        try:
            n = _build_bars_for_contract(contract_id, interval, bar_sec, db_path)
            results[interval] = n
        except Exception as e:
            logger.warning(f"Bar build error cid={contract_id} {interval}: {e}")
            results[interval] = 0
    return results


class QuoteHarvester:
    """
    Background quote collector for ForecastEx contracts.

    Usage:
        harvester = QuoteHarvester(broker=get_forecastex_broker())
        harvester.start()
        # runs until harvester.stop() is called
    """

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

    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="forecast-harvester",
        )
        self._thread.start()
        logger.info("[QuoteHarvester] Started (poll_interval=%ds)", self._poll_sec)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[QuoteHarvester] Stopped")

    def _run_loop(self) -> None:
        while self._running:
            cycle_start = time.time()
            try:
                self._poll_and_build()
            except Exception as e:
                logger.error(f"[QuoteHarvester] cycle error: {e}")

            # Prune once per hour
            if time.time() - self._last_prune > PRUNE_INTERVAL_MIN * 60:
                try:
                    n_q = prune_old_quotes(db_path=self._db_path)
                    n_b = prune_old_bars(db_path=self._db_path)
                    logger.info(f"[QuoteHarvester] Pruned {n_q} quotes, {n_b} bars")
                except Exception as e:
                    logger.warning(f"[QuoteHarvester] Prune error: {e}")
                self._last_prune = time.time()

            elapsed = time.time() - cycle_start
            sleep_for = max(0.0, self._poll_sec - elapsed)
            time.sleep(sleep_for)

    def backfill_bars(self, contract_id: int, ticker: str) -> None:
        """Fetch historical candles from broker and backfill all bar intervals."""
        if not self._broker or not self._broker.is_connected():
            return

        logger.info(f"[QuoteHarvester] Backfilling bars for {ticker} (cid={contract_id})...")
        
        # 1. Fetch 1m candles for 5m and 30m bars (and 1m bars if we had them)
        try:
            c1m = self._broker.get_historical_candles(ticker, interval_min=1, limit=1000)
            if c1m:
                _resample_candles_to_bars(contract_id, c1m, "5m", 300, self._db_path)
                _resample_candles_to_bars(contract_id, c1m, "30m", 1800, self._db_path)
        except Exception as e:
            logger.debug(f"1m backfill failed for {ticker}: {e}")

        # 2. Fetch 1h candles for 1h and 4h bars
        try:
            c1h = self._broker.get_historical_candles(ticker, interval_min=60, limit=200)
            if c1h:
                _resample_candles_to_bars(contract_id, c1h, "1h", 3600, self._db_path)
                _resample_candles_to_bars(contract_id, c1h, "4h", 14400, self._db_path)
        except Exception as e:
            logger.debug(f"1h backfill failed for {ticker}: {e}")

        # 3. Fetch 1d candles for 1d bars
        try:
            c1d = self._broker.get_historical_candles(ticker, interval_min=1440, limit=50)
            if c1d:
                _resample_candles_to_bars(contract_id, c1d, "1d", 86400, self._db_path)
        except Exception as e:
            logger.debug(f"1d backfill failed for {ticker}: {e}")

    def _poll_and_build(self) -> None:
        """
        One poll cycle: fetch quotes for all active contracts in parallel, 
        persist, and build bars.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        try:
            contracts = get_active_contracts(db_path=self._db_path)
        except Exception as e:
            logger.warning(f"[QuoteHarvester] get_active_contracts failed: {e}")
            return

        if not contracts:
            return

        def _harvest_one(contract):
            contract_id = contract.get("id")
            local_symbol = contract.get("local_symbol", "")
            right = contract.get("right", "C")
            side = "YES" if right == "C" else "NO"

            if not contract_id or not local_symbol:
                return None

            # v18.34: One-time backfill for new contracts
            if contract_id not in self._backfilled_cids:
                try:
                    self.backfill_bars(contract_id, local_symbol)
                    self._backfilled_cids.add(contract_id)
                except Exception as e:
                    logger.debug(f"Backfill trigger error {local_symbol}: {e}")

            # Fetch quote
            if self._broker and self._broker.is_connected():
                try:
                    q = self._broker.get_quote(local_symbol)
                    if q.get("mid") is not None:
                        ts = datetime.now(timezone.utc).isoformat()
                        insert_quote(
                            contract_id=contract_id,
                            ts=ts,
                            bid=q.get("bid"),
                            ask=q.get("ask"),
                            bid_size=q.get("bid_size"),
                            ask_size=q.get("ask_size"),
                            mid=q.get("mid"),
                            spread=q.get("spread"),
                            implied_prob=q.get("implied_prob"),
                            side=side,
                            db_path=self._db_path,
                        )
                        _build_all_bars(contract_id, db_path=self._db_path)
                        return True
                    return False
                except Exception as e:
                    logger.debug(f"harvest error {local_symbol}: {e}")
                    return None
            return None

        total_quotes = 0
        skipped_no_depth = 0
        
        # v19.1.6: Parallel poll to ensure freshness SLA (<120s)
        # 10 workers for 55 contracts ~ 6 batches.
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_harvest_one, c) for c in contracts]
            for future in as_completed(futures):
                res = future.result()
                if res is True: total_quotes += 1
                elif res is False: skipped_no_depth += 1

        # Log cycle summary to dashboard
        msg = f"[QuoteHarvester] Cycle complete: {total_quotes} saved, {skipped_no_depth} skipped (no depth) across {len(contracts)} active contracts"
        logger.debug(msg)
        log_event("INFO", "QuoteHarvester", msg)


def build_bars_now(contract_id: int, db_path: Optional[str] = None) -> dict:
    """
    Trigger immediate bar build for one contract across all intervals.
    Useful for backfill or test fixtures.
    Returns {interval: bars_written}.
    """
    return _build_all_bars(contract_id, db_path=db_path)


def get_paired_quotes(
    market_id: int,
    strike: float,
    last_trade_at: str,
    db_path: Optional[str] = None,
) -> dict:
    """
    Return the most recent YES and NO quotes for a paired contract set.

    Used to compute Ω_t (overround) and G_t (parity gap) which require
    both sides of the same contract.

    Returns:
        {yes_quote: dict|None, no_quote: dict|None,
         omega_t: float|None, g_t: float|None}
    """
    try:
        from forecast.db import _conn, DB_PATH

        path = db_path or DB_PATH
        with _conn(path) as c:
            # YES contract (right='C')
            yes_row = c.execute(
                """SELECT fq.mid, fq.ask, fq.bid, fq.spread, fq.ts
                   FROM forecast_quotes fq
                   JOIN forecast_contracts fc ON fc.id = fq.contract_id
                   WHERE fc.market_id=? AND fc.right='C'
                     AND fc.strike=? AND fc.last_trade_at=?
                   ORDER BY fq.ts DESC LIMIT 1""",
                (market_id, strike, last_trade_at),
            ).fetchone()

            # NO contract (right='P')
            no_row = c.execute(
                """SELECT fq.mid, fq.ask, fq.bid, fq.spread, fq.ts
                   FROM forecast_quotes fq
                   JOIN forecast_contracts fc ON fc.id = fq.contract_id
                   WHERE fc.market_id=? AND fc.right='P'
                     AND fc.strike=? AND fc.last_trade_at=?
                   ORDER BY fq.ts DESC LIMIT 1""",
                (market_id, strike, last_trade_at),
            ).fetchone()

    except Exception as e:
        logger.warning(f"get_paired_quotes failed: {e}")
        return {"yes_quote": None, "no_quote": None, "omega_t": None, "g_t": None}

    yes_q = dict(yes_row) if yes_row else None
    no_q = dict(no_row) if no_row else None

    omega_t = g_t = None
    if yes_q and no_q:
        try:
            from forecast.primitives import overround, parity_gap

            ask_yes = yes_q.get("ask") or 0.0
            ask_no = no_q.get("ask") or 0.0
            mid_yes = yes_q.get("mid") or 0.0
            mid_no = no_q.get("mid") or 0.0
            if ask_yes and ask_no:
                omega_t = overround(ask_yes, ask_no)
            if mid_yes and mid_no:
                g_t = parity_gap(mid_yes, mid_no)
        except Exception:
            pass

    return {
        "yes_quote": yes_q,
        "no_quote": no_q,
        "omega_t": omega_t,
        "g_t": g_t,
    }


if __name__ == "__main__":
    from execution.kalshi_broker import get_kalshi_broker

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logger.info("Starting QuoteHarvester in standalone daemon mode...")

    broker = get_kalshi_broker()
    if not broker.connect():
        logger.error("Could not connect to broker. Exiting.")
        sys.exit(1)

    harvester = QuoteHarvester(broker=broker)
    harvester.start()

    # Keep main thread alive
    while True:
        time.sleep(1)
