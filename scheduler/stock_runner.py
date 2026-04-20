"""
scheduler/stock_runner.py — US equity swing-trading lane.

Scans STOCK_UNIVERSE every 30 minutes during market hours (9:30–16:00 ET, weekdays).
Standalone signal tower (no imports from signal_engine, scanner, v10_runner, etc.).

Signal scoring (0–100 technical tower):
  EMA9 > EMA21 > EMA50:            +20 pts
  SuperTrend bullish:               +15 pts
  MACD hist > 0 AND rising:         +15 pts
  RSI 14 between 45 and 70:         +15 pts
  Volume > 1.5× 20-bar SMA:         +10 pts
  Price > VWAP (20-bar rolling):    +10 pts
  CHOP index < 50 (trending):       +10 pts
  Price > 20-day SMA:               +5 pts

Position sizing: 2% account risk, ATR-based stop (1.5×ATR), 3R target.
Max position = 15% of account per name, max 3 concurrent positions.
"""

import os
import sys
import time
import threading
import schedule
from datetime import datetime, date
from typing import Optional

import pytz

_RUNNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RUNNER_ROOT not in sys.path:
    sys.path.insert(0, _RUNNER_ROOT)

from logging_db.trade_logger import log_event

# ── Config ────────────────────────────────────────────────────────────────────

try:
    from config import (
        PAPER_TRADING,
        STOCKS_LANE_ACTIVE,
        STOCK_UNIVERSE,
        STOCKS_MAX_POSITIONS,
        STOCKS_RISK_PCT,
        STOCKS_MAX_POSITION_PCT,
    )
except ImportError:
    PAPER_TRADING = True
    STOCKS_LANE_ACTIVE = False
    STOCK_UNIVERSE = [
        "AMD",
        "GOOGL",
        "AAPL",
        "AMZN",
        "TSLA",
        "COIN",
        "IWM",
        "XLF",
        "XLE",
        "XLK",
        "NFLX",
        "UBER",
    ]
    STOCKS_MAX_POSITIONS = 3
    STOCKS_RISK_PCT = 0.02
    STOCKS_MAX_POSITION_PCT = 0.15

_ET_TZ = pytz.timezone("America/New_York")
_ENTRY_SCORE_THRESHOLD = 60
_MAX_PDT_WARN_THRESHOLD = 3  # warn (not block) at 3+ day trades in rolling 5 days
_CANDLE_PERIOD = "3mo"
_CANDLE_INTERVAL = "1d"

# Module-level broker singleton — created lazily
_broker = None
_broker_lock = threading.Lock()


def _get_broker():
    global _broker
    with _broker_lock:
        if _broker is None:
            from execution.ibkr_stock_broker import IBKRStockBroker

            _broker = IBKRStockBroker()
        return _broker


# ── Market-hours gate ─────────────────────────────────────────────────────────


def _is_market_hours() -> bool:
    """True if current ET time is within regular market hours (9:30–16:00 Mon–Fri)."""
    now_et = datetime.now(_ET_TZ)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et < market_close


# ── Indicator helpers (standalone — no dependency on indicators.py) ────────────


def _ema(series, span: int):
    """Exponential moving average via pandas ewm."""
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series, period: int = 14):
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _macd(series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: returns (macd_line, signal_line, histogram)."""
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(df, period: int = 14):
    """Average True Range."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = (
        (high - low)
        .combine((high - prev_close).abs(), max)
        .combine((low - prev_close).abs(), max)
    )
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _supertrend(df, period: int = 10, multiplier: float = 3.0):
    """
    Simple SuperTrend. Returns a boolean Series: True = bullish.
    direction > 0 means price is above the supertrend line (bullish).
    """
    atr = _atr(df, period)
    hl2 = (df["High"] + df["Low"]) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = [True] * len(df)
    for i in range(1, len(df)):
        close = df["Close"].iloc[i]
        prev_close = df["Close"].iloc[i - 1]
        prev_upper = upper_band.iloc[i - 1]
        prev_lower = lower_band.iloc[i - 1]

        if close > prev_upper:
            supertrend[i] = True
        elif close < prev_lower:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i - 1]

    import pandas as pd

    return pd.Series(supertrend, index=df.index)


def _vwap_rolling(df, window: int = 20):
    """Rolling VWAP over the last `window` bars using daily typical price * volume."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = typical * df["Volume"]
    return pv.rolling(window).sum() / df["Volume"].rolling(window).sum()


def _chop(df, period: int = 14):
    """
    Choppiness Index = 100 * log10(sum(ATR14, N) / (highest_high - lowest_low)) / log10(N)
    < 50 = trending, > 61.8 = choppy.
    """
    import math
    import pandas as pd

    atr14 = _atr(df, 14)
    atr_sum = atr14.rolling(period).sum()
    highest_high = df["High"].rolling(period).max()
    lowest_low = df["Low"].rolling(period).min()
    range_hl = highest_high - lowest_low
    range_hl = range_hl.replace(0, float("nan"))
    chop = (
        100
        * (atr_sum / range_hl).apply(lambda x: math.log10(x) if x > 0 else float("nan"))
        / math.log10(period)
    )
    return chop


def _score_symbol(df) -> tuple[int, dict]:
    """
    Compute technical signal score (0–100) and a breakdown dict.
    Returns (score, signals_fired).
    """
    if df is None or len(df) < 55:
        return 0, {}

    close = df["Close"]
    volume = df["Volume"]

    ema9 = _ema(close, 9)
    ema21 = _ema(close, 21)
    ema50 = _ema(close, 50)
    rsi = _rsi(close, 14)
    _, _, macd_hist = _macd(close)
    vol_sma20 = volume.rolling(20).mean()
    vwap = _vwap_rolling(df, 20)
    chop = _chop(df, 14)
    sma20 = close.rolling(20).mean()
    st_bullish = _supertrend(df, period=10, multiplier=3.0)

    last = -1  # most recent bar
    score = 0
    signals = {}

    # EMA alignment (+20)
    e9, e21, e50 = ema9.iloc[last], ema21.iloc[last], ema50.iloc[last]
    if e9 > e21 > e50:
        score += 20
        signals["ema_aligned"] = True

    # SuperTrend bullish (+15)
    if bool(st_bullish.iloc[last]):
        score += 15
        signals["supertrend_bullish"] = True

    # MACD hist > 0 and rising (+15)
    h_now = macd_hist.iloc[last]
    h_prev = macd_hist.iloc[-2] if len(macd_hist) > 1 else 0
    if h_now > 0 and h_now > h_prev:
        score += 15
        signals["macd_rising"] = True

    # RSI 45–70 (+15)
    r = rsi.iloc[last]
    if 45 <= r <= 70:
        score += 15
        signals["rsi_healthy"] = True

    # Volume > 1.5× SMA20 (+10)
    v_now = volume.iloc[last]
    v_sma = vol_sma20.iloc[last]
    if v_sma and v_sma > 0 and v_now > 1.5 * v_sma:
        score += 10
        signals["vol_surge"] = True

    # Price > VWAP (+10)
    c_now = close.iloc[last]
    vwap_now = vwap.iloc[last]
    if vwap_now and vwap_now > 0 and c_now > vwap_now:
        score += 10
        signals["above_vwap"] = True

    # CHOP < 50 (+10)
    chop_now = chop.iloc[last]
    if chop_now and chop_now < 50:
        score += 10
        signals["low_chop"] = True

    # Price > SMA20 (+5)
    sma_now = sma20.iloc[last]
    if sma_now and sma_now > 0 and c_now > sma_now:
        score += 5
        signals["above_sma20"] = True

    return score, signals


# ── Data fetching ─────────────────────────────────────────────────────────────


def _fetch_daily_bars(symbol: str):
    """Fetch daily OHLCV bars from yfinance. Returns DataFrame or None."""
    try:
        import yfinance as yf

        df = yf.download(
            symbol,
            period=_CANDLE_PERIOD,
            interval=_CANDLE_INTERVAL,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if df is None or df.empty or len(df) < 30:
            return None
        # Flatten MultiIndex columns if present (yfinance v0.2+)
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        log_event("WARN", "StockRunner", f"yfinance fetch failed for {symbol}: {e}")
        return None


# ── Position sizing ───────────────────────────────────────────────────────────


def _compute_size(df, account_value: float, price: float) -> tuple[int, float, float]:
    """
    Returns (shares, stop_price, target_price).
    Returns (0, 0, 0) if sizing fails or capital check fails.
    """
    if df is None or price <= 0 or account_value <= 0:
        return 0, 0.0, 0.0

    try:
        atr = _atr(df, 14).iloc[-1]
        stop_dist = 1.5 * atr
        risk_usd = account_value * STOCKS_RISK_PCT
        shares = max(1, int(risk_usd / stop_dist))
        max_shares = max(1, int(account_value * STOCKS_MAX_POSITION_PCT / price))
        shares = min(shares, max_shares)
        stop_price = round(price - stop_dist, 2)
        target_price = round(price + 3 * stop_dist, 2)

        # Capital safety check: do not deploy more than 90% of account on one trade
        if shares * price > account_value * 0.90:
            return 0, 0.0, 0.0

        return shares, stop_price, target_price
    except Exception as e:
        log_event("WARN", "StockRunner", f"_compute_size error: {e}")
        return 0, 0.0, 0.0


# ── DB helpers ────────────────────────────────────────────────────────────────


def _get_stock_open_positions_db() -> dict:
    """Read open stock positions from the trades DB (strategy LIKE 'stocks_%')."""
    try:
        import sqlite3 as _sqlite3

        _db_path = os.path.join(_RUNNER_ROOT, "logs", "trades.db")
        conn = _sqlite3.connect(_db_path, timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, qty, entry, stop, target FROM open_positions "
            "WHERE strategy LIKE 'stocks_%'"
        )
        rows = cur.fetchall()
        conn.close()
        return {
            r[0]: {"qty": r[1], "entry": r[2], "stop": r[3], "target": r[4]}
            for r in rows
        }
    except Exception:
        return {}


def _log_scan_candidate_safe(symbol: str, score: int, decision: str, notes: str):
    """Log to scan_candidates with source='stocks'. Swallows all errors."""
    try:
        from logging_db.trade_logger import log_scan_candidate

        log_scan_candidate(
            symbol=symbol,
            exchange="stocks",
            direction="LONG",
            composite_score=float(score),
            decision=decision,
            notes=notes,
            source="stocks",
            underlying=symbol,
        )
    except Exception:
        try:
            log_event(
                "INFO", "StockRunner", f"candidate {symbol} score={score} {decision}"
            )
        except Exception:
            pass


# ── Scan cycle ────────────────────────────────────────────────────────────────


def run_scan_cycle():
    """
    Main scan loop:
    1. Gate on market hours.
    2. Score each symbol in STOCK_UNIVERSE.
    3. Enter if score >= threshold, not already held, position limit not reached.
    4. Monitor existing positions and exit if stop triggered.
    """
    if not _is_market_hours():
        return

    log_event("INFO", "StockRunner", "Stock scan cycle starting")

    broker = _get_broker()

    # Ensure broker is connected (lazy connect)
    if not broker.is_connected():
        connected = broker.connect()
        if not connected:
            log_event(
                "WARN", "StockRunner", "Cannot connect to IBKR TWS — skipping cycle"
            )
            return

    account_value = broker.get_account_value()
    if account_value <= 0:
        log_event(
            "WARN", "StockRunner", "Could not read account value — skipping cycle"
        )
        return

    # Reconcile in-memory positions against DB
    db_positions = _get_stock_open_positions_db()
    mem_positions = broker.get_open_positions()
    all_held = set(db_positions.keys()) | set(mem_positions.keys())

    # ── Monitor existing positions ────────────────────────────────────────────
    for sym in list(all_held):
        pos = mem_positions.get(sym) or db_positions.get(sym) or {}
        stop = float(pos.get("stop") or 0)
        entry = float(pos.get("entry") or 0)
        qty = int(pos.get("qty") or 1)

        current_price = broker.get_price(sym)
        if current_price and current_price > 0:
            pnl = (current_price - entry) * qty if entry else 0.0
            log_event(
                "INFO",
                "StockRunner",
                f"Position monitor: {sym} entry={entry:.2f} now={current_price:.2f} "
                f"stop={stop:.2f} pnl={pnl:+.2f}",
            )
            # Server-side bracket stop should fire, but check defensively
            if stop > 0 and current_price <= stop:
                log_event(
                    "WARN",
                    "StockRunner",
                    f"Stop triggered for {sym} current={current_price:.2f} <= stop={stop:.2f}",
                )
                broker.sell_stock(sym, qty, reason="stop_triggered")
                all_held.discard(sym)

    open_count = len(broker.get_open_positions())

    # PDT advisory (swing trades shouldn't trigger PDT, but log as warning)
    pdt_count = broker.get_pdt_count()
    if pdt_count >= _MAX_PDT_WARN_THRESHOLD:
        log_event(
            "WARN",
            "StockRunner",
            f"PDT advisory: {pdt_count} day trades in rolling 5 days. "
            "Swing trades should not close same day.",
        )

    # ── Scan for new entries ──────────────────────────────────────────────────
    for symbol in STOCK_UNIVERSE:
        if open_count >= STOCKS_MAX_POSITIONS:
            log_event(
                "INFO",
                "StockRunner",
                f"Max positions ({STOCKS_MAX_POSITIONS}) reached — skipping {symbol}",
            )
            break

        if symbol in all_held:
            _log_scan_candidate_safe(symbol, 0, "dual_exposure_block", "already_held")
            continue

        df = _fetch_daily_bars(symbol)
        if df is None:
            _log_scan_candidate_safe(symbol, 0, "data_unavailable", "no_bars")
            continue

        score, signals = _score_symbol(df)

        if score < _ENTRY_SCORE_THRESHOLD:
            _log_scan_candidate_safe(
                symbol,
                score,
                "below_threshold",
                f"score={score} threshold={_ENTRY_SCORE_THRESHOLD}",
            )
            continue

        current_price = broker.get_price(symbol)
        if not current_price or current_price <= 0:
            _log_scan_candidate_safe(symbol, score, "data_unavailable", "no_price")
            continue

        shares, stop_price, target_price = _compute_size(
            df, account_value, current_price
        )
        if shares <= 0:
            _log_scan_candidate_safe(
                symbol, score, "sizing_zero", f"price={current_price:.2f}"
            )
            continue

        log_event(
            "INFO",
            "StockRunner",
            f"Entry signal: {symbol} score={score}/100 "
            f"signals={list(signals.keys())} "
            f"price={current_price:.2f} qty={shares} "
            f"stop={stop_price:.2f} target={target_price:.2f}",
        )

        result = broker.buy_stock(
            symbol=symbol,
            qty=shares,
            stop_price=stop_price,
            target_price=target_price,
            strategy="stocks_swing",
        )

        if result:
            open_count += 1
            all_held.add(symbol)
            _log_scan_candidate_safe(
                symbol,
                score,
                "entered",
                f"score={score} price={result.get('price'):.2f} qty={shares} "
                f"stop={stop_price:.2f} target={target_price:.2f} "
                f"signals={'|'.join(signals.keys())}",
            )
            log_event(
                "INFO",
                "StockRunner",
                f"Entered {symbol}: {shares} shares @ {result.get('price'):.2f} "
                f"order={result.get('order_id')}",
            )
        else:
            _log_scan_candidate_safe(
                symbol,
                score,
                "execution_failed",
                f"broker returned None for {symbol}",
            )

    log_event("INFO", "StockRunner", "Stock scan cycle complete")


# ── Entry point ───────────────────────────────────────────────────────────────


def run_forever():
    """
    Set up schedule and loop forever. Called from main.py as a daemon thread.
    Uses a private schedule.Scheduler() instance (not the global default)
    to avoid race with v10_runner's schedule.run_pending() main loop.
    """
    import schedule as _sched_lib

    _s = _sched_lib.Scheduler()
    _s.every(30).minutes.do(run_scan_cycle)

    log_event(
        "INFO",
        "StockRunner",
        "Stock lane started — scanning every 30m during market hours",
    )

    # Run immediately on startup (if market is open)
    try:
        run_scan_cycle()
    except Exception as e:
        log_event("ERROR", "StockRunner", f"Initial scan cycle error: {e}")

    while True:
        try:
            _s.run_pending()
        except Exception as e:
            log_event("ERROR", "StockRunner", f"Schedule error: {e}")
        time.sleep(30)
