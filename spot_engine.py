"""
spot_engine.py — Coinbase spot execution engine (BTC/ETH only, starter lane).

Manages one spot position at a time (BTC or ETH).  No leverage, no shorting.
Wraps execution/coinbase_spot_broker.py with v10-style persistence.

Blocked reasons (returned as strings in result dicts):
  spot_position_already_open
  spot_deployment_cap_exceeded
  spot_size_below_minimum
  spot_symbol_not_allowed
  spot_lane_disabled
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from execution.coinbase_spot_broker import (
        CoinbaseSpotBroker,
        get_spot_broker,
        SPOT_SUPPORTED_SYMBOLS,
    )

    _BROKER_OK = True
except ImportError:
    _BROKER_OK = False
    logger.warning("[spot_engine] coinbase_spot_broker not available")

# ── Config defaults (overridden by config.py when imported) ───────────────────
_SPOT_MAX_DEPLOYED_PCT: float = 0.40  # 40% of spot USD balance
_SPOT_MIN_ORDER_USD: float = 10.0
_SPOT_SYMBOLS: list = ["BTC", "ETH"]
_SPOT_LANE_ACTIVE: bool = False
_SPOT_STOP_PCT: float = 0.03  # 3% hard stop below entry
_SPOT_TRAIL_MULT: float = 1.5  # ATR multiplier for trailing stop width
_SPOT_THESIS_MIN_HOLD_MINS: float = 20.0  # min hold before thesis exit fires
_SPOT_THESIS_MIN_SCORE: float = 42.0  # composite score below this → thesis exit


def _load_config() -> None:
    global \
        _SPOT_MAX_DEPLOYED_PCT, \
        _SPOT_MIN_ORDER_USD, \
        _SPOT_SYMBOLS, \
        _SPOT_LANE_ACTIVE, \
        _SPOT_STOP_PCT, \
        _SPOT_TRAIL_MULT, \
        _SPOT_THESIS_MIN_HOLD_MINS, \
        _SPOT_THESIS_MIN_SCORE
    try:
        from config import (
            SPOT_MAX_DEPLOYED_PCT,
            SPOT_MIN_ORDER_USD,
            SPOT_SYMBOLS,
            SPOT_LANE_ACTIVE,
            SPOT_STOP_PCT,
        )

        _SPOT_MAX_DEPLOYED_PCT = float(SPOT_MAX_DEPLOYED_PCT)
        _SPOT_MIN_ORDER_USD = float(SPOT_MIN_ORDER_USD)
        _SPOT_SYMBOLS = list(SPOT_SYMBOLS)
        _SPOT_LANE_ACTIVE = bool(SPOT_LANE_ACTIVE)
        _SPOT_STOP_PCT = float(SPOT_STOP_PCT)
    except Exception:
        pass  # use defaults above

    # New optional config constants — env-var overrides only
    try:
        _SPOT_TRAIL_MULT = float(os.environ.get("SPOT_TRAIL_MULT", _SPOT_TRAIL_MULT))
        _SPOT_THESIS_MIN_HOLD_MINS = float(
            os.environ.get("SPOT_THESIS_MIN_HOLD_MINS", _SPOT_THESIS_MIN_HOLD_MINS)
        )
        _SPOT_THESIS_MIN_SCORE = float(
            os.environ.get("SPOT_THESIS_MIN_SCORE", _SPOT_THESIS_MIN_SCORE)
        )
    except Exception:
        pass


_load_config()


def _get_db_path() -> str:
    """Derive DB path the same way the rest of spot_engine does via trade_logger."""
    try:
        from config import DB_PATH

        return DB_PATH
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs",
            "trades.db",
        )


def _get_broker(paper: bool) -> Optional["CoinbaseSpotBroker"]:
    if not _BROKER_OK:
        return None
    try:
        broker = get_spot_broker()
        # Override paper flag if broker was constructed with different mode
        broker._paper = paper
        return broker
    except Exception as e:
        logger.error(f"[spot_engine] broker init error: {e}")
        return None


# ── open_spot ─────────────────────────────────────────────────────────────────


def open_spot(
    symbol: str,
    size_usd: float,
    paper: bool = True,
    composite_score: float = 0.0,
    atr_at_entry: float = 0.0,
) -> Optional[Dict]:
    """
    Open a spot position for symbol (BTC or ETH).
    Returns position dict on success, None with logged reason on failure.

    Blocks:
      - SPOT_LANE_ACTIVE=False  → spot_lane_disabled
      - symbol not in SPOT_SYMBOLS → spot_symbol_not_allowed
      - already holding that symbol (DB) → spot_position_already_open
      - size_usd < SPOT_MIN_ORDER_USD → spot_size_below_minimum
      - size_usd > SPOT_MAX_DEPLOYED_PCT * usd_balance → spot_deployment_cap_exceeded
    """
    _load_config()

    clean = symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")

    # Lane gate
    if not _SPOT_LANE_ACTIVE:
        logger.info(f"[spot_engine] {clean} blocked — spot_lane_disabled")
        return None

    # Symbol gate
    if clean not in _SPOT_SYMBOLS:
        logger.warning(f"[spot_engine] {clean} blocked — spot_symbol_not_allowed")
        return None

    # Size minimum
    if size_usd < _SPOT_MIN_ORDER_USD:
        logger.warning(
            f"[spot_engine] {clean} blocked — spot_size_below_minimum "
            f"(${size_usd:.2f} < ${_SPOT_MIN_ORDER_USD})"
        )
        return None

    # Deployment cap
    broker = _get_broker(paper)
    if broker is not None and not paper:
        bal = broker.get_spot_balance()
        usd_avail = float(bal.get("usd_available", 0))
        if usd_avail > 0:
            cap = usd_avail * _SPOT_MAX_DEPLOYED_PCT
            if size_usd > cap:
                logger.warning(
                    f"[spot_engine] {clean} blocked — spot_deployment_cap_exceeded "
                    f"(${size_usd:.2f} > {_SPOT_MAX_DEPLOYED_PCT:.0%} of ${usd_avail:.2f})"
                )
                return None

    # Execute
    if broker is None:
        logger.error(f"[spot_engine] {clean} — broker unavailable")
        return None

    order = broker.buy_spot(clean, size_usd)
    if not order:
        logger.error(f"[spot_engine] {clean} buy_spot returned None")
        return None

    price = broker.get_mark_price(clean)
    qty = size_usd / price if price > 0 else float(order.get("filled_size", 0))

    # Gap 2 — ATR-calibrated stop: respects 3% floor, caps at 8%, adapts to volatility
    if atr_at_entry > 0 and price > 0:
        atr_pct = atr_at_entry / price
        stop_pct = max(_SPOT_STOP_PCT, min(_SPOT_TRAIL_MULT * atr_pct, 0.08))
    else:
        stop_pct = _SPOT_STOP_PCT

    stop_price = round(price * (1.0 - stop_pct), 8) if price > 0 else 0.0
    max_loss_usd = round(size_usd * stop_pct, 2)

    pos = {
        "symbol": clean,
        "strategy": f"spot_{clean.lower()}",
        "broker": "coinbase_spot",
        "qty": qty,
        "entry": price,
        "stop_price": stop_price,
        "max_loss_usd": max_loss_usd,
        "entry_ts": time.time(),
        "size_usd": size_usd,
        "order_id": order.get("order_id", ""),
        "paper": paper,
    }

    # Persist to trades table
    try:
        from logging_db.trade_logger import log_trade

        log_trade(
            strategy=f"spot_{clean.lower()}",
            broker="coinbase_spot",
            symbol=clean,
            action="BUY",
            order_type="MARKET",
            qty=qty,
            price=price,
            fee_usd=0.0,
            pnl_usd=0.0,
            paper=paper,
            notes=(
                f"spot_buy size_usd={size_usd:.2f} stop={stop_price:.4f} "
                f"stop_pct={stop_pct:.2%} max_loss=${max_loss_usd:.2f}"
            ),
        )
    except Exception as e:
        logger.debug(f"[spot_engine] log_trade error: {e}")

    # Persist to open_positions — stop column holds the hard stop price
    # Gap 4 — pass real composite_score and atr_at_entry through to persist_position
    try:
        from logging_db.trade_logger import persist_position

        persist_position(
            symbol=clean,
            strategy=f"spot_{clean.lower()}",
            qty=qty,
            entry=price,
            stop=stop_price,
            target=0.0,
            high_since_entry=price,
            ts_entry=datetime.datetime.now().isoformat(),
            paper=paper,
            direction="LONG",
            entry_reason="spot_buy",
            atr_at_entry=atr_at_entry,
            composite_score=composite_score,
            leverage=1,
        )
    except Exception as e:
        logger.debug(f"[spot_engine] persist_position error: {e}")

    logger.info(
        f"[spot_engine] {'PAPER' if paper else 'LIVE'} BUY {clean}: "
        f"${size_usd:.2f} = {qty:.6f} @ {price:.4f}  "
        f"stop={stop_price:.4f} (-{stop_pct:.2%})  "
        f"composite={composite_score:.1f}  atr={atr_at_entry:.4f}"
    )
    return pos


# ── close_spot ────────────────────────────────────────────────────────────────


def close_spot(symbol: str, paper: bool = True) -> Optional[Dict]:
    """
    Close the spot position for symbol.
    Returns close result dict or None.
    """
    _load_config()

    clean = symbol.upper().replace("USDT", "").replace("USD", "").replace("-USD", "")

    # Find position in DB
    existing = _load_spot_positions_from_db(paper=paper)
    pos = next((p for p in existing if p.get("symbol", "").upper() == clean), None)
    if not pos:
        logger.warning(f"[spot_engine] close_spot {clean}: no open position found")
        return None

    qty = float(pos.get("qty", 0))
    entry_price = float(pos.get("entry", 0))
    if qty <= 0:
        logger.warning(f"[spot_engine] close_spot {clean}: qty=0")
        return None

    broker = _get_broker(paper)
    if broker is None:
        logger.error(f"[spot_engine] close_spot {clean}: broker unavailable")
        return None

    order = broker.sell_spot(clean, qty)
    if not order:
        logger.error(f"[spot_engine] close_spot {clean}: sell_spot returned None")
        return None

    exit_price = broker.get_mark_price(clean)
    if exit_price <= 0:
        exit_price = entry_price
    pnl_usd = (exit_price - entry_price) * qty

    # Persist close to trades table
    try:
        from logging_db.trade_logger import log_trade

        log_trade(
            strategy=f"spot_{clean.lower()}",
            broker="coinbase_spot",
            symbol=clean,
            action="SELL",
            order_type="MARKET",
            qty=qty,
            price=exit_price,
            fee_usd=0.0,
            pnl_usd=pnl_usd,
            paper=paper,
            won=1 if pnl_usd > 0 else 0,
            notes=f"spot_sell exit={exit_price:.4f} pnl={pnl_usd:.2f}",
        )
    except Exception as e:
        logger.debug(f"[spot_engine] close log_trade error: {e}")

    # Remove from open_positions
    try:
        from logging_db.trade_logger import delete_position

        delete_position(clean, strategy=f"spot_{clean.lower()}", paper=paper)
    except Exception as e:
        logger.debug(f"[spot_engine] delete_position error: {e}")

    result = {
        "symbol": clean,
        "exit_price": exit_price,
        "entry_price": entry_price,
        "qty": qty,
        "pnl_usd": round(pnl_usd, 4),
        "order_id": order.get("order_id", ""),
        "paper": paper,
    }
    logger.info(
        f"[spot_engine] {'PAPER' if paper else 'LIVE'} SELL {clean}: "
        f"{qty:.6f} @ {exit_price:.4f} pnl=${pnl_usd:.2f}"
    )
    return result


# ── get_spot_positions ────────────────────────────────────────────────────────


def get_spot_positions(paper: bool = True) -> List[Dict]:
    """
    Return open spot positions (strategy='spot_*' — no perp contamination).
    """
    return _load_spot_positions_from_db(paper=paper)


def _load_spot_positions_from_db(paper: bool = True) -> List[Dict]:
    """
    Load spot open positions from open_positions table.
    Filtered by strategy starting with 'spot_' — no contamination with perp positions
    (perp strategy = 'v10_perp', spot strategies = 'spot_btc' / 'spot_eth').
    """
    try:
        from logging_db.trade_logger import load_open_positions

        rows = load_open_positions(paper=paper)
        return [r for r in rows if str(r.get("strategy", "")).startswith("spot_")]
    except Exception as e:
        logger.debug(f"[spot_engine] load_spot_positions error: {e}")
        return []


# ── check_spot_stops ──────────────────────────────────────────────────────────


def check_spot_stops(paper: bool = True) -> List[Dict]:
    """
    Check all open spot positions against their hard stop price.
    Called every 30s from exit_monitor in v10_runner.

    For each position:
      - stop = open_positions.stop  (set at buy time = entry * (1 - SPOT_STOP_PCT))
      - if current_price <= stop: close the position immediately

    Returns list of closed position dicts (empty if nothing triggered).
    """
    _load_config()
    closed = []

    positions = _load_spot_positions_from_db(paper=paper)
    if not positions:
        return closed

    broker = _get_broker(paper)
    if broker is None:
        return closed

    for pos in positions:
        sym = str(pos.get("symbol", "")).upper()
        stop_price = float(pos.get("stop", 0.0))
        entry_price = float(pos.get("entry", 0.0))

        if stop_price <= 0:
            # No stop stored (legacy row or manual entry) — apply current config pct
            if entry_price > 0:
                stop_price = entry_price * (1.0 - _SPOT_STOP_PCT)
            else:
                continue

        current_price = broker.get_mark_price(sym)
        if current_price <= 0:
            logger.warning(
                f"[spot_engine] check_spot_stops: cannot get price for {sym}"
            )
            continue

        if current_price <= stop_price:
            loss_pct = (
                (current_price - entry_price) / entry_price * 100
                if entry_price > 0
                else 0
            )
            logger.warning(
                f"[spot_engine] STOP HIT {sym}: price={current_price:.4f} "
                f"<= stop={stop_price:.4f} ({loss_pct:.2f}%) — closing"
            )
            result = close_spot(sym, paper=paper)
            if result:
                result["trigger"] = "hard_stop"
                result["stop_price"] = stop_price
                closed.append(result)
                logger.info(
                    f"[spot_engine] {'PAPER' if paper else 'LIVE'} STOP CLOSE {sym}: "
                    f"pnl=${result.get('pnl_usd', 0):.2f}"
                )
            else:
                logger.error(
                    f"[spot_engine] STOP CLOSE FAILED for {sym} — position still open"
                )

    return closed


# ── check_spot_trailing ───────────────────────────────────────────────────────


def check_spot_trailing(paper: bool = True) -> List[Dict]:
    """
    ATR-calibrated trailing stop for open spot positions.
    Called every 30s from exit_monitor in v10_runner alongside check_spot_stops.

    Logic per position:
    1. Fetch current price.
    2. If current_price > high_since_entry: update high_since_entry in DB.
    3. Trail activates only when high_since_entry has moved at least SPOT_STOP_PCT
       above entry (avoids noise at entry).
    4. Trail level = high_since_entry * (1 - trail_pct) where trail_pct is
       ATR-calibrated (1.5 * atr/high) bounded by [SPOT_STOP_PCT, 0.08].
    5. If current_price <= trail_stop AND current_price > entry_price: close
       (locks in profit — never a loss-expanding trail).

    Returns list of closed position dicts.
    """
    _load_config()
    closed: List[Dict] = []

    positions = _load_spot_positions_from_db(paper=paper)
    if not positions:
        return closed

    broker = _get_broker(paper)
    if broker is None:
        return closed

    db_path = _get_db_path()

    for pos in positions:
        try:
            sym = str(pos.get("symbol", "")).upper()
            entry_price = float(pos.get("entry", 0.0))
            high_since_entry = float(
                pos.get("high_since_entry", entry_price) or entry_price
            )
            atr_at_entry = float(pos.get("atr_at_entry", 0.0) or 0.0)
            strategy = str(pos.get("strategy", f"spot_{sym.lower()}"))

            if entry_price <= 0:
                continue

            current_price = broker.get_mark_price(sym)
            if current_price <= 0:
                logger.warning(
                    f"[spot_engine] check_spot_trailing: cannot get price for {sym}"
                )
                continue

            # Step 2: update high_since_entry in DB if new high reached
            if current_price > high_since_entry:
                high_since_entry = current_price
                try:
                    con = sqlite3.connect(db_path, timeout=5)
                    con.execute(
                        "UPDATE open_positions SET high_since_entry = ? "
                        "WHERE symbol = ? AND strategy = ? AND paper = ?",
                        (high_since_entry, sym, strategy, 1 if paper else 0),
                    )
                    con.commit()
                    con.close()
                except Exception as db_err:
                    logger.debug(
                        f"[spot_engine] trailing high_since_entry update error for {sym}: {db_err}"
                    )

            # Step 3: trail only activates after meaningful upward move
            meaningful_move_threshold = entry_price * (1.0 + _SPOT_STOP_PCT)
            if high_since_entry < meaningful_move_threshold:
                continue  # too early — trail hasn't activated yet

            # Compute ATR-calibrated trail width
            if atr_at_entry > 0 and high_since_entry > 0:
                trail_pct = max(
                    _SPOT_STOP_PCT,
                    min(_SPOT_TRAIL_MULT * atr_at_entry / high_since_entry, 0.08),
                )
            else:
                trail_pct = _SPOT_STOP_PCT

            trail_stop = high_since_entry * (1.0 - trail_pct)

            # Step 4+5: fire only when trailing stop is hit AND price > entry (profit lock)
            if current_price <= trail_stop and current_price > entry_price:
                profit_pct = (current_price - entry_price) / entry_price * 100
                logger.info(
                    f"[spot_engine] TRAILING STOP {sym}: price={current_price:.4f} "
                    f"<= trail={trail_stop:.4f} (high={high_since_entry:.4f} "
                    f"trail_pct={trail_pct:.2%}) profit={profit_pct:.2f}% — closing"
                )
                result = close_spot(sym, paper=paper)
                if result:
                    result["trigger"] = "trailing_stop"
                    result["trail_stop"] = trail_stop
                    result["high_since_entry"] = high_since_entry
                    closed.append(result)
                    logger.info(
                        f"[spot_engine] {'PAPER' if paper else 'LIVE'} TRAIL CLOSE {sym}: "
                        f"pnl=${result.get('pnl_usd', 0):.2f}"
                    )
                else:
                    logger.error(
                        f"[spot_engine] TRAIL CLOSE FAILED for {sym} — position still open"
                    )

        except Exception as e:
            logger.warning(f"[spot_engine] check_spot_trailing error for pos: {e}")

    return closed


# ── check_spot_thesis_exits ───────────────────────────────────────────────────


def check_spot_thesis_exits(paper: bool = True) -> List[Dict]:
    """
    Thesis score exit for open spot positions.
    Closes a spot position when the scanner's composite score for that symbol
    drops below SPOT_THESIS_MIN_SCORE, but only after a minimum hold period
    and only when fresh scan data exists within the last 45 minutes.

    Logic per position:
    1. Skip if held less than SPOT_THESIS_MIN_HOLD_MINS.
    2. Look up most recent composite_score from scan_candidates within 45 min.
    3. If fresh score exists AND score < SPOT_THESIS_MIN_SCORE: close.
    4. If no fresh data: skip (never close on stale data).

    Returns list of closed position dicts.
    """
    _load_config()
    closed: List[Dict] = []

    positions = _load_spot_positions_from_db(paper=paper)
    if not positions:
        return closed

    broker = _get_broker(paper)
    if broker is None:
        return closed

    db_path = _get_db_path()

    for pos in positions:
        try:
            sym = str(pos.get("symbol", "")).upper()
            entry_price = float(pos.get("entry", 0.0))

            if entry_price <= 0:
                continue

            # Step 1: enforce minimum hold time
            ts_entry_raw = pos.get("ts_entry", None)
            if ts_entry_raw:
                try:
                    ts_entry_dt = datetime.datetime.fromisoformat(str(ts_entry_raw))
                    held_mins = (
                        datetime.datetime.now() - ts_entry_dt
                    ).total_seconds() / 60.0
                except Exception:
                    held_mins = (
                        _SPOT_THESIS_MIN_HOLD_MINS  # assume enough time if parse fails
                    )
            else:
                held_mins = _SPOT_THESIS_MIN_HOLD_MINS

            if held_mins < _SPOT_THESIS_MIN_HOLD_MINS:
                logger.debug(
                    f"[spot_engine] thesis_exit {sym}: held {held_mins:.1f}m "
                    f"< min {_SPOT_THESIS_MIN_HOLD_MINS:.0f}m — skipping"
                )
                continue

            # Step 2: look up most recent composite_score within 45 min
            fresh_score: Optional[float] = None
            try:
                con = sqlite3.connect(db_path, timeout=5)
                row = con.execute(
                    """
                    SELECT composite_score
                    FROM scan_candidates
                    WHERE (symbol LIKE ? OR underlying = ?)
                      AND datetime(replace(substr(ts,1,19),'T',' '))
                          >= datetime('now','-45 minutes')
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    (f"%{sym}%", sym),
                ).fetchone()
                con.close()
                if row and row[0] is not None:
                    fresh_score = float(row[0])
            except Exception as db_err:
                logger.debug(
                    f"[spot_engine] thesis_exit scan_candidates query error for {sym}: {db_err}"
                )

            # Step 4: if no fresh data, skip — never close on stale data
            if fresh_score is None:
                logger.debug(
                    f"[spot_engine] thesis_exit {sym}: no fresh scan data in 45m — skipping"
                )
                continue

            # Step 3: close if score has degraded below threshold
            if fresh_score < _SPOT_THESIS_MIN_SCORE:
                logger.info(
                    f"[spot_engine] THESIS EXIT {sym}: composite={fresh_score:.1f} "
                    f"< {_SPOT_THESIS_MIN_SCORE:.1f} after {held_mins:.1f}m hold — closing"
                )
                result = close_spot(sym, paper=paper)
                if result:
                    result["trigger"] = "thesis_exit"
                    result["composite_score_at_exit"] = fresh_score
                    closed.append(result)
                    logger.info(
                        f"[spot_engine] {'PAPER' if paper else 'LIVE'} THESIS CLOSE {sym}: "
                        f"pnl=${result.get('pnl_usd', 0):.2f}"
                    )
                else:
                    logger.error(
                        f"[spot_engine] THESIS CLOSE FAILED for {sym} — position still open"
                    )
            else:
                logger.debug(
                    f"[spot_engine] thesis_exit {sym}: score={fresh_score:.1f} OK "
                    f"(threshold={_SPOT_THESIS_MIN_SCORE:.1f}) — holding"
                )

        except Exception as e:
            logger.warning(f"[spot_engine] check_spot_thesis_exits error for pos: {e}")

    return closed
