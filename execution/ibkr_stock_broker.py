"""
execution/ibkr_stock_broker.py — IBKR US equity execution via ib_insync.

Connects to TWS live account U250288849 on port 7496 (or IBKR_PORT env).
clientId=4 — must not collide with clientId=2 (MES) or clientId=3 (ForecastEx).

Architecture: same async-on-background-thread pattern as ibkr_broker.py.
- One persistent asyncio event loop on a daemon thread.
- All ib_insync calls are submitted via asyncio.run_coroutine_threadsafe().
- Returns None (not raises) on order rejection — caller checks None.

Bracket order structure for stock buys:
  entry : LimitOrder(BUY, qty, limit=ask)   tif="DAY", outsideRth=False
  target: LimitOrder(SELL, qty, lmt=target) tif="GTC"
  stop  : StopOrder(SELL, qty, stop=stop)   tif="GTC"
"""

import os
import sys
import uuid
import threading
import asyncio
import time
from typing import Optional
from datetime import datetime, date

# eventkit (ib_insync dep) calls asyncio.get_event_loop() at import time.
# Python 3.10+ no longer auto-creates a loop — set one on the main thread.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING
from logging_db.trade_logger import log_trade, log_event

try:
    from notifications.notification_engine import get_notification_engine as _get_ne
except Exception:
    _get_ne = None

# Connection settings — read from env, not hardcoded
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7496"))  # 7496=live TWS, 7497=paper TWS
IBKR_STOCK_CLIENT_ID = 4  # fixed — must not collide with MES(2) or ForecastEx(3)

# PDT rolling window (trading days)
_PDT_WINDOW_DAYS = 5


class IBKRStockBroker:
    """
    IBKR broker for US equity swing trades.

    Uses a persistent asyncio event loop in a daemon thread so that all
    ib_insync calls have a live loop available throughout the process.
    clientId=4 — never collides with IBKRBroker (MES=2) or ForecastEx (3).
    """

    def __init__(self):
        self._ib = None
        self._connected = False
        self._open_positions: dict = {}  # {symbol: {"qty", "entry", "stop", "target", "side", "order_id"}}
        self._lock = threading.Lock()

        # Start the persistent event loop — same pattern as ibkr_broker.py.
        # Python 3.10+ requires asyncio.set_event_loop() inside the new thread.
        self._loop = asyncio.new_event_loop()

        def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_start_loop,
            args=(self._loop,),
            daemon=True,
            name="ibkr-stocks-event-loop",
        )
        self._loop_thread.start()

    # ── Event loop bridge ─────────────────────────────────────────────────────

    def _run(self, coro, timeout: float = 15.0):
        """Submit a coroutine to the persistent event loop and block until result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        from ib_insync import IB

        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None

        self._ib = IB()
        try:
            self._run(
                self._ib.connectAsync(
                    IBKR_HOST, IBKR_PORT, clientId=IBKR_STOCK_CLIENT_ID
                ),
                timeout=15,
            )
            self._connected = self._ib.isConnected()
            mode = "PAPER" if PAPER_TRADING else "LIVE"
            if self._connected:
                acct = (
                    self._ib.managedAccounts()[0]
                    if self._ib.managedAccounts()
                    else "unknown"
                )
                print(
                    f"[IBKRStockBroker] Connected to TWS ({mode}) account={acct} "
                    f"port={IBKR_PORT} clientId={IBKR_STOCK_CLIENT_ID}"
                )
                log_event(
                    "INFO", "IBKRStockBroker", f"Connected ({mode}) account={acct}"
                )
                self._sync_positions()
            else:
                print("[IBKRStockBroker] Could not connect to TWS — is it running?")
            return self._connected
        except Exception as e:
            print(f"[IBKRStockBroker] Connection error: {e}")
            log_event("ERROR", "IBKRStockBroker", f"Connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self):
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._connected = False

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    # ── Account value ─────────────────────────────────────────────────────────

    def get_account_value(self) -> float:
        """Read NetLiquidation from IBKR account summary. Returns 0.0 on failure."""
        if not self.is_connected():
            return 0.0
        try:
            acct_values = self._run(self._ib.accountSummaryAsync(), timeout=10)
            for av in acct_values:
                if av.tag == "NetLiquidation" and av.currency == "USD":
                    return float(av.value)
            return 0.0
        except Exception as e:
            log_event("WARN", "IBKRStockBroker", f"get_account_value error: {e}")
            return 0.0

    # ── Price ─────────────────────────────────────────────────────────────────

    async def _fetch_price_async(self, symbol: str) -> Optional[float]:
        """Fetch delayed stock price from TWS. Uses reqMarketDataType(3)."""
        from ib_insync import Stock

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        self._ib.reqMarketDataType(3)  # delayed data — no subscription needed
        ticker = self._ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(2.0)  # allow snapshot data to arrive

        price = None
        if ticker.ask and ticker.ask > 0:
            price = float(ticker.ask)
        elif ticker.last and ticker.last > 0:
            price = float(ticker.last)
        elif ticker.close and ticker.close > 0:
            price = float(ticker.close)
        self._ib.cancelMktData(contract)
        return price

    def get_price(self, symbol: str) -> float:
        """Get current ask price for a stock. Falls back to yfinance on TWS failure."""
        if self.is_connected():
            try:
                price = self._run(self._fetch_price_async(symbol), timeout=12)
                if price and price > 0:
                    return float(price)
            except Exception as e:
                log_event(
                    "WARN",
                    "IBKRStockBroker",
                    f"TWS price fetch failed for {symbol}: {e}",
                )

        # yfinance fallback
        try:
            import yfinance as yf

            hist = yf.Ticker(symbol).history(period="2d", interval="1d", progress=False)
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    # ── Position sync ─────────────────────────────────────────────────────────

    def _sync_positions(self):
        """Pull any open stock positions already in TWS into local state."""
        if not self.is_connected():
            return
        try:
            for pos in self._ib.positions():
                sym = pos.contract.symbol
                if pos.contract.secType == "STK" and pos.position != 0:
                    with self._lock:
                        self._open_positions[sym] = {
                            "qty": int(abs(pos.position)),
                            "entry": float(pos.avgCost) if pos.avgCost else 0.0,
                            "stop": 0.0,
                            "target": 0.0,
                            "side": "LONG" if pos.position > 0 else "SHORT",
                            "order_id": "SYNCED",
                        }
                    print(
                        f"[IBKRStockBroker] Synced {sym} position: {pos.position} shares"
                    )
        except Exception as e:
            log_event("WARN", "IBKRStockBroker", f"Position sync error: {e}")

    # ── Bracket order ─────────────────────────────────────────────────────────

    async def _place_stock_bracket_async(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        target_price: float,
    ) -> list:
        """
        Place a bracket order for a stock via TWS.
        entry : LimitOrder(BUY, qty, limit=ask)   tif="DAY", outsideRth=False
        target: LimitOrder(SELL, qty, lmt=target) tif="GTC"
        stop  : StopOrder(SELL, qty, stop=stop)   tif="GTC"
        """
        from ib_insync import Stock, LimitOrder, StopOrder

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        # Entry order
        entry_order = LimitOrder("BUY", qty, limit_price)
        entry_order.outsideRth = False  # stocks are RTH only
        entry_order.tif = "DAY"
        entry_order.transmit = False  # hold until children are set

        entry_trade = self._ib.placeOrder(contract, entry_order)
        parent_id = entry_order.orderId

        # Target (take profit)
        target_order = LimitOrder("SELL", qty, target_price)
        target_order.parentId = parent_id
        target_order.outsideRth = False
        target_order.tif = "GTC"
        target_order.transmit = False

        self._ib.placeOrder(contract, target_order)

        # Stop loss — transmit=True releases the whole bracket
        stop_order = StopOrder("SELL", qty, stop_price)
        stop_order.parentId = parent_id
        stop_order.outsideRth = False
        stop_order.tif = "GTC"
        stop_order.transmit = True  # sends all three orders together

        stop_trade = self._ib.placeOrder(contract, stop_order)

        # Wait briefly to detect immediate Cancelled (Error 460 / permissions)
        await asyncio.sleep(0.5)
        entry_status = entry_trade.orderStatus.status if entry_trade else ""
        if entry_status == "Cancelled":
            err_codes = [e.errorCode for e in entry_trade.log if e.errorCode]
            raise RuntimeError(
                f"Stock bracket entry cancelled by IBKR (Cancelled, codes={err_codes})"
            )

        return [entry_trade]

    # ── Market sell ───────────────────────────────────────────────────────────

    async def _place_market_sell_async(self, symbol: str, qty: int) -> object:
        """Place a market sell order and cancel any open bracket children."""
        from ib_insync import Stock, MarketOrder

        # Cancel pending bracket children (SL/TP) for this symbol
        for trade in self._ib.openTrades():
            try:
                if trade.contract.symbol == symbol and trade.order.action == "SELL":
                    self._ib.cancelOrder(trade.order)
            except Exception:
                pass
        await asyncio.sleep(0.3)

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        order = MarketOrder("SELL", qty)
        order.outsideRth = False
        order.tif = "DAY"
        return self._ib.placeOrder(contract, order)

    # ── Public trade methods ──────────────────────────────────────────────────

    def buy_stock(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        target_price: float,
        strategy: str = "stocks_swing",
    ) -> Optional[dict]:
        """
        Buy stock via bracket order (entry limit + server-side stop + target).
        Returns None on rejection.  All log_trade calls wrapped in try/except.
        """
        if not symbol or qty <= 0:
            log_event(
                "WARN",
                "IBKRStockBroker",
                f"buy_stock: invalid args symbol={symbol} qty={qty}",
            )
            return None

        # Paper mode mock fill
        if PAPER_TRADING:
            price = self.get_price(symbol) or stop_price * 1.02
            order_id = f"PAPER_STOCK_{uuid.uuid4().hex[:8]}"
            with self._lock:
                self._open_positions[symbol] = {
                    "qty": qty,
                    "entry": price,
                    "stop": stop_price,
                    "target": target_price,
                    "side": "LONG",
                    "order_id": order_id,
                }
            try:
                log_trade(
                    strategy=strategy,
                    broker="ibkr_stocks",
                    symbol=symbol,
                    action="BUY",
                    order_type="Bracket",
                    qty=qty,
                    price=price,
                    fee_usd=0.0,
                    pnl_usd=0.0,
                    paper=True,
                    order_id=order_id,
                    notes=f"stop={stop_price} target={target_price} mode=paper",
                )
            except Exception as e:
                log_event(
                    "WARN", "IBKRStockBroker", f"log_trade error (paper buy): {e}"
                )
            return {"order_id": order_id, "price": price, "qty": qty}

        # Live mode
        if not self.is_connected():
            log_event(
                "WARN", "IBKRStockBroker", f"buy_stock: not connected for {symbol}"
            )
            return None

        current_price = self.get_price(symbol)
        if not current_price or current_price <= 0:
            log_event(
                "WARN", "IBKRStockBroker", f"buy_stock: price unavailable for {symbol}"
            )
            return None

        limit_price = round(current_price * 1.0005, 2)  # just above ask for quick fill

        try:
            trades = self._run(
                self._place_stock_bracket_async(
                    symbol, qty, limit_price, stop_price, target_price
                ),
                timeout=12,
            )
        except RuntimeError as e:
            log_event(
                "ERROR", "IBKRStockBroker", f"buy_stock bracket rejected {symbol}: {e}"
            )
            return None
        except Exception as e:
            log_event("ERROR", "IBKRStockBroker", f"buy_stock error {symbol}: {e}")
            return None

        order_id = (
            str(trades[0].order.orderId)
            if trades
            else f"IBKR_STOCK_{uuid.uuid4().hex[:8]}"
        )

        with self._lock:
            self._open_positions[symbol] = {
                "qty": qty,
                "entry": limit_price,
                "stop": stop_price,
                "target": target_price,
                "side": "LONG",
                "order_id": order_id,
            }

        try:
            log_trade(
                strategy=strategy,
                broker="ibkr_stocks",
                symbol=symbol,
                action="BUY",
                order_type="Bracket",
                qty=qty,
                price=limit_price,
                fee_usd=0.0,
                pnl_usd=0.0,
                paper=False,
                order_id=order_id,
                notes=f"stop={stop_price} target={target_price}",
            )
        except Exception as e:
            log_event("WARN", "IBKRStockBroker", f"log_trade error (live buy): {e}")

        log_event(
            "INFO",
            "IBKRStockBroker",
            f"BUY {qty} {symbol} @ {limit_price:.2f} stop={stop_price:.2f} target={target_price:.2f}",
        )
        return {"order_id": order_id, "price": limit_price, "qty": qty}

    def sell_stock(
        self,
        symbol: str,
        qty: int,
        strategy: str = "stocks_swing",
        reason: str = "signal",
    ) -> Optional[dict]:
        """
        Sell (close) a stock position via market order.
        Computes P&L from open_positions entry price.
        Returns None on failure.  All log_trade calls wrapped in try/except.
        """
        if not symbol or qty <= 0:
            log_event(
                "WARN",
                "IBKRStockBroker",
                f"sell_stock: invalid args symbol={symbol} qty={qty}",
            )
            return None

        with self._lock:
            pos = self._open_positions.get(symbol)

        entry_price = float(pos.get("entry", 0)) if pos else 0.0

        # Paper mode mock fill
        if PAPER_TRADING:
            exit_price = self.get_price(symbol) or entry_price
            pnl = (exit_price - entry_price) * qty if entry_price else 0.0
            order_id = f"PAPER_STOCK_SELL_{uuid.uuid4().hex[:8]}"
            try:
                log_trade(
                    strategy=strategy,
                    broker="ibkr_stocks",
                    symbol=symbol,
                    action="SELL",
                    order_type="Market",
                    qty=qty,
                    price=exit_price,
                    fee_usd=0.0,
                    pnl_usd=pnl,
                    paper=True,
                    order_id=order_id,
                    notes=f"reason={reason} mode=paper",
                )
            except Exception as e:
                log_event(
                    "WARN", "IBKRStockBroker", f"log_trade error (paper sell): {e}"
                )
            with self._lock:
                self._open_positions.pop(symbol, None)
            return {"exit_price": exit_price, "pnl": pnl}

        # Live mode
        if not self.is_connected():
            log_event(
                "WARN", "IBKRStockBroker", f"sell_stock: not connected for {symbol}"
            )
            return None

        try:
            trade = self._run(
                self._place_market_sell_async(symbol, qty),
                timeout=12,
            )
        except Exception as e:
            log_event("ERROR", "IBKRStockBroker", f"sell_stock error {symbol}: {e}")
            return None

        exit_price = self.get_price(symbol) or entry_price
        pnl = (exit_price - entry_price) * qty if entry_price else 0.0
        order_id = (
            str(trade.order.orderId)
            if trade
            else f"IBKR_STOCK_SELL_{uuid.uuid4().hex[:8]}"
        )

        try:
            log_trade(
                strategy=strategy,
                broker="ibkr_stocks",
                symbol=symbol,
                action="SELL",
                order_type="Market",
                qty=qty,
                price=exit_price,
                fee_usd=0.0,
                pnl_usd=pnl,
                paper=False,
                order_id=order_id,
                notes=f"reason={reason}",
            )
        except Exception as e:
            log_event("WARN", "IBKRStockBroker", f"log_trade error (live sell): {e}")

        with self._lock:
            self._open_positions.pop(symbol, None)

        log_event(
            "INFO",
            "IBKRStockBroker",
            f"SELL {qty} {symbol} @ {exit_price:.2f} pnl={pnl:+.2f} reason={reason}",
        )
        return {"exit_price": exit_price, "pnl": pnl}

    # ── State accessors ───────────────────────────────────────────────────────

    def get_open_positions(self) -> dict:
        """Return a copy of the in-memory open positions dict."""
        with self._lock:
            return dict(self._open_positions)

    def get_pdt_count(self) -> int:
        """
        Count day trades (open+close same day) in last 5 trading days from trades table.
        A day trade = BUY and SELL of same symbol on the same calendar day.
        """
        try:
            import sqlite3 as _sqlite3

            _db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs",
                "trades.db",
            )
            conn = _sqlite3.connect(_db_path, timeout=5)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(DISTINCT date(ts)) FROM (
                    SELECT date(ts) AS day, symbol
                    FROM trades
                    WHERE broker='ibkr_stocks'
                      AND ts >= date('now', '-7 days')
                    GROUP BY day, symbol
                    HAVING SUM(CASE WHEN action='BUY' THEN 1 ELSE 0 END) >= 1
                       AND SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) >= 1
                )
                """,
            )
            row = cur.fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            log_event("WARN", "IBKRStockBroker", f"get_pdt_count error: {e}")
            return 0
