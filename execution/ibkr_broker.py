"""
execution/ibkr_broker.py — Interactive Brokers futures execution via ib_insync.

Replaces tradovate_broker.py. Connects to Trader Workstation (TWS) running
locally. Same public API: buy_mes / sell_mes / short_mes / cover_mes /
get_price / get_position / get_account_balance.

TWS must be running with API enabled:
  TWS → Edit → Global Configuration → API → Settings
  ✓ Enable ActiveX and Socket Clients
  ✓ Socket port: 7497 (paper) / 7496 (live)
  ✗ Read-Only API  (must be unchecked)

Client ID 2 is used — avoids colliding with other bots using ID 0 or 1.

MES contract specs:
  - Micro E-mini S&P 500 futures
  - $5 per point, $1.25 per tick (0.25 point)
  - IBKR commission: ~$0.47/contract (cheaper than Tradovate $0.59)
  - No PDT restriction on futures accounts

Architecture note:
  ib_insync's IB object is tightly coupled to an asyncio event loop.
  We run ONE persistent event loop on a daemon background thread for the
  entire process lifetime.  All ib_insync async calls (connect, qualify,
  place order, market data) are submitted to that loop via
  asyncio.run_coroutine_threadsafe() and block the calling thread until
  done.  This eliminates the "coroutine never awaited" / dead-loop warnings
  that appear when asyncio.run() is used (which creates and then closes a
  temporary loop).
"""

import os
import sys
import uuid
import threading
import asyncio
import time
import logging
from typing import Optional
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

# eventkit (ib_insync dep) calls asyncio.get_event_loop() at import time.
# Python 3.10+ no longer auto-creates a loop — set one on the main thread.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MARKET_TIMEZONE, FUTURES_NUM_CONTRACTS
from logging_db.trade_logger import log_trade, log_event

try:
    from notifications.notification_engine import get_notification_engine as _get_ne
except Exception:
    _get_ne = None

# TWS connection settings
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7496"))  # 7497=paper TWS, 7496=live TWS
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "2"))

# MES contract — update expiry each quarter
# Q1 Jan-Mar: 20260320  Q2 Apr-Jun: 20260619  Q3 Jul-Sep: 20260918  Q4 Oct-Dec: 20261218
MES_EXPIRY = os.getenv("MES_EXPIRY", "20260619")  # Current: June 2026
MES_POINT_VALUE = 5.00  # $ per full point
MES_TICK_SIZE = 0.25  # minimum price increment
MES_TICK_VALUE = 1.25  # $ per tick
IBKR_COMMISSION = 0.47  # $ per contract per side


def _get_mes_contract():
    from ib_insync import Future

    # Primary: symbol+expiry approach for account U250288849 (live).
    # localSymbol='MESM26' returns Error 200 on this account regardless of exchange.
    # Use lastTradeDateOrContractMonth (YYYYMM) which works when secdefil farm is up.
    expiry_ym = MES_EXPIRY[:6]  # '20260619' → '202606'
    return Future(
        symbol="MES",
        lastTradeDateOrContractMonth=expiry_ym,
        exchange="CME",
        currency="USD",
        multiplier="5",
    )


class IBKRBroker:
    """
    Interactive Brokers broker for MES futures.
    Uses a persistent asyncio event loop in a daemon thread so that all
    ib_insync calls have a live loop available throughout the process.
    """

    def __init__(self):
        self._ib = None
        self._connected = False
        self._open_positions: dict = {}
        self._lock = threading.Lock()

        # Start the persistent event loop that all ib_insync calls use.
        # Python 3.10+ no longer auto-sets the event loop per thread, so we
        # must call asyncio.set_event_loop() explicitly inside the thread
        # before run_forever(). Without this, ib_insync's connectAsync raises
        # "There is no current event loop in thread 'ibkr-event-loop'."
        self._loop = asyncio.new_event_loop()

        def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_start_loop,
            args=(self._loop,),
            daemon=True,
            name="ibkr-event-loop",
        )
        self._loop_thread.start()

    # ── Event loop bridge ─────────────────────────────────────────────────────

    def _run(self, coro, timeout: float = 15.0):
        """
        Submit a coroutine to the persistent event loop and block until result.
        Safe to call from any thread.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        from ib_insync import IB

        # Cleanup any previous IB object before creating a new one.
        # Skipping this leaks sockets from the old instance (OSError: too many open files).
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None

        self._ib = IB()
        try:
            self._run(
                self._ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID),
                timeout=15,
            )
            self._connected = self._ib.isConnected()
            if self._connected:
                acct = (
                    self._ib.managedAccounts()[0]
                    if self._ib.managedAccounts()
                    else "unknown"
                )
                logger.info(
                    f"[IBKRBroker] Connected to TWS (LIVE) account={acct} port={IBKR_PORT} ✅"
                )
                log_event("INFO", "IBKRBroker", f"Connected (LIVE) account={acct}")
                self._sync_positions()
            else:
                logger.info("[IBKRBroker] ⚠️ Could not connect to TWS — is it running?")
            return self._connected
        except Exception as e:
            logger.info(f"API connection failed: {e}\nMake sure API port on TWS/IBG is open")
            logger.info(f"[IBKRBroker] Connection error: {e}")
            logger.info(
                "[IBKRBroker] ⚠️ TWS not reachable — make sure TWS is open and API is enabled"
            )
            log_event("ERROR", "IBKRBroker", f"Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    def _sync_positions(self):
        """Pull any open MES positions already in TWS into local state."""
        if not self.is_connected():
            return
        try:
            for pos in self._ib.positions():
                if pos.contract.symbol == "MES" and pos.position != 0:
                    side = "LONG" if pos.position > 0 else "SHORT"
                    self._open_positions["MES"] = {
                        "qty": abs(pos.position),
                        "entry": pos.avgCost / MES_POINT_VALUE,
                        "stop": 0.0,
                        "target": 0.0,
                        "side": side,
                        "order_id": "SYNCED",
                    }
                    logger.info(
                        f"[IBKRBroker] Synced existing {side} {abs(pos.position)} MES position"
                    )
        except Exception as e:
            log_event("WARN", "IBKRBroker", f"Position sync error: {e}")

    # ── Price fetching ────────────────────────────────────────────────────────

    async def _fetch_price_async(self, side: str = "mid") -> Optional[float]:
        """Fetch live MES price from TWS market data (runs on persistent loop)."""
        contract = _get_mes_contract()
        await self._ib.qualifyContractsAsync(contract)
        self._ib.reqMarketDataType(3)  # delayed data — no subscription needed
        ticker = self._ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(1.5)  # allow snapshot data to arrive
        price = None
        if side == "ask" and ticker.ask and ticker.ask > 0:
            price = float(ticker.ask)
        elif side == "bid" and ticker.bid and ticker.bid > 0:
            price = float(ticker.bid)
        if price is None and ticker.last and ticker.last > 0:
            price = float(ticker.last)
        if price is None and ticker.close and ticker.close > 0:
            price = float(ticker.close)
        self._ib.cancelMktData(contract)
        return price

    def _get_mes_price(self, side: str = "mid") -> float:
        """Get MES price: TWS live data → yfinance → hard fallback."""
        if self.is_connected():
            try:
                price = self._run(self._fetch_price_async(side), timeout=10)
                if price:
                    return price
            except Exception as e:
                log_event("WARN", "IBKRBroker", f"Market data error: {e}")

        # yfinance fallback
        try:
            import yfinance as yf

            hist = yf.Ticker("MES=F").history(period="1d", interval="1m")
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        try:
            import yfinance as yf

            hist = yf.download(
                "ES=F", period="2d", interval="5m", auto_adjust=True, progress=False
            )
            if hist is not None and not hist.empty:
                cols = [
                    c[0].lower() if isinstance(c, tuple) else c.lower()
                    for c in hist.columns
                ]
                hist.columns = cols
                return float(hist["close"].iloc[-1])
        except Exception:
            pass
        return 5750.0

    def get_price(self, symbol: str = "MES") -> float:
        """Public price getter — delegates to _get_mes_price (TWS → yfinance fallback)."""
        return self._get_mes_price()

    # ── Async order helpers ───────────────────────────────────────────────────

    async def _place_bracket_async(
        self,
        action: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        target_price: float,
    ) -> list:
        """
        Place a bracket order (entry limit + SL + TP) on the persistent loop.
        Using limit at current ask/bid fills immediately for liquid instruments like MES.
        """
        contract = _get_mes_contract()
        await self._ib.qualifyContractsAsync(contract)
        bracket = self._ib.bracketOrder(
            action=action,
            quantity=qty,
            limitPrice=limit_price,
            takeProfitPrice=target_price,
            stopLossPrice=stop_price,
        )
        trades = []
        for order in bracket:
            order.outsideRth = True  # MES trades 24/7
            order.tif = "GTC"  # prevent DAY preset from cancelling after RTH
            trade = self._ib.placeOrder(contract, order)
            trades.append(trade)
        # Wait briefly so IBKR can reject the entry with Error 460 / permissions
        await asyncio.sleep(0.5)
        entry_status = trades[0].orderStatus.status if trades else ""
        if entry_status == "Cancelled":
            err_codes = [e.errorCode for e in trades[0].log if e.errorCode]
            raise RuntimeError(
                f"MES bracket entry cancelled by IBKR (status=Cancelled, codes={err_codes})"
            )
        return trades

    async def _place_market_async(self, action: str, qty: int) -> object:
        """Place a market order on the persistent loop and cancel any bracket children."""
        from ib_insync import MarketOrder

        # Cancel pending bracket children (SL/TP) before closing with market order
        for trade in self._ib.openTrades():
            try:
                if trade.contract.symbol == "MES":
                    self._ib.cancelOrder(trade.order)
            except Exception:
                pass
        await asyncio.sleep(0.3)  # give TWS time to ack cancellations

        contract = _get_mes_contract()
        await self._ib.qualifyContractsAsync(contract)
        order = MarketOrder(action, qty)
        order.outsideRth = True
        order.tif = "GTC"
        return self._ib.placeOrder(contract, order)

    # ── Order placement ───────────────────────────────────────────────────────

    def buy_mes(
        self,
        qty: int = FUTURES_NUM_CONTRACTS,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        reason: str = "signal",
        order_type: str = "Market",
        strategy: str = "futures_scalper",
    ) -> Optional[dict]:
        """Go long MES. Bracket order: limit entry + server-side SL + TP."""
        current_price = self._get_mes_price("ask")
        num_contracts = int(qty)
        if stop_price is None:
            stop_price = round(current_price - 4.0, 2)
        if target_price is None:
            target_price = round(current_price + 8.0, 2)
        commission = IBKR_COMMISSION * num_contracts * 2

        if self.is_connected():
            try:
                trades = self._run(
                    self._place_bracket_async(
                        "BUY", num_contracts, current_price, stop_price, target_price
                    ),
                    timeout=10,
                )
                order_id = (
                    str(trades[0].order.orderId)
                    if trades
                    else f"IBKR_{uuid.uuid4().hex[:8]}"
                )
                logger.info(
                    f"[IBKRBroker] BUY {num_contracts} MES @ ~{current_price:.2f} "
                    f"| SL={stop_price} TP={target_price}"
                )
            except Exception as e:
                log_event("ERROR", "IBKRBroker", f"buy_mes order error: {e}")
                return None  # order was rejected — do not log a fake position
        else:
            logger.info(
                f"[IBKRBroker] ⚠️ Not connected — cannot BUY {num_contracts} MES"
            )
            return None

        with self._lock:
            self._open_positions["MES"] = {
                "qty": num_contracts,
                "entry": current_price,
                "stop": stop_price,
                "target": target_price,
                "side": "LONG",
                "order_id": order_id,
            }

        try:
            log_trade(
                strategy=strategy,
                broker="ibkr",
                symbol="MES",
                action="BUY",
                order_type=order_type,
                qty=num_contracts,
                price=current_price,
                fee_usd=commission,
                order_id=order_id,
                notes=f"SL={stop_price} TP={target_price} reason={reason}",
            )
        except Exception as _e:
            log_event("ERROR", "IBKRBroker", f"buy_mes log_trade failed: {_e}")
        try:
            if _get_ne:
                _ne = _get_ne()
                _ne.notify_trade_open(
                    symbol="MES",
                    direction="LONG",
                    size_usd=float(num_contracts) * current_price * MES_POINT_VALUE,
                    entry_price=current_price,
                    score=0.0,
                    top_3=[],
                    features={},
                    regime="UNKNOWN",
                )
        except Exception:
            pass
        return {"order_id": order_id, "price": current_price}

    def sell_mes(
        self,
        qty: int = 1,
        strategy: str = "futures_scalper",
        reason: str = "Signal",
        entry_price: float = 0.0,
    ) -> Optional[dict]:
        """Close a long MES position at market (cancels bracket children first)."""
        exit_price = self._get_mes_price("bid")
        entry = entry_price or self._open_positions.get("MES", {}).get(
            "entry", exit_price
        )
        pnl = (exit_price - entry) * MES_POINT_VALUE * qty

        if self.is_connected():
            try:
                self._run(self._place_market_async("SELL", qty), timeout=10)
                logger.info(
                    f"[IBKRBroker] SELL {qty} MES @ {exit_price:.2f} | P&L: ${pnl:+.2f}"
                )
            except Exception as e:
                log_event("ERROR", "IBKRBroker", f"sell_mes error: {e}")
                return None
        else:
            logger.info(
                f"[IBKRBroker] ⚠️ Not connected — cannot SELL MES @ {exit_price:.2f}"
            )
            return None

        with self._lock:
            self._open_positions.pop("MES", None)

        log_trade(
            strategy=strategy,
            broker="ibkr",
            symbol="MES",
            action="SELL",
            order_type="Market",
            qty=qty,
            price=exit_price,
            fee_usd=IBKR_COMMISSION * qty,
            pnl_usd=pnl,
            order_id=f"IBKR_{uuid.uuid4().hex[:8]}",
            notes=f"reason={reason}",
        )
        try:
            if _get_ne:
                _ne = _get_ne()
                _ne.notify_trade_close(
                    symbol="MES",
                    direction="LONG",
                    pnl_usd=pnl,
                    pnl_pct=pnl / max(abs(entry * qty * MES_POINT_VALUE), 1),
                    exit_type=reason,
                    top_3=[],
                    features={},
                    regime="UNKNOWN",
                    score=0.0,
                )
        except Exception:
            pass
        return {"exit_price": exit_price, "pnl": pnl}

    def short_mes(
        self,
        qty: int = 1,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        reason: str = "signal",
        order_type: str = "Market",
        strategy: str = "futures_scalper",
    ) -> Optional[dict]:
        """Go short MES. Bracket order: limit entry + server-side SL + TP."""
        current_price = self._get_mes_price("bid")
        num_contracts = int(qty)
        if stop_price is None:
            stop_price = round(current_price + 4.0, 2)
        if target_price is None:
            target_price = round(current_price - 8.0, 2)
        commission = IBKR_COMMISSION * num_contracts * 2

        if self.is_connected():
            try:
                trades = self._run(
                    self._place_bracket_async(
                        "SELL", num_contracts, current_price, stop_price, target_price
                    ),
                    timeout=10,
                )
                order_id = (
                    str(trades[0].order.orderId)
                    if trades
                    else f"IBKR_{uuid.uuid4().hex[:8]}"
                )
                logger.info(
                    f"[IBKRBroker] SHORT {num_contracts} MES @ ~{current_price:.2f} "
                    f"| SL={stop_price} TP={target_price}"
                )
            except Exception as e:
                log_event("ERROR", "IBKRBroker", f"short_mes order error: {e}")
                return None  # order was rejected — do not log a fake position
        else:
            logger.info(
                f"[IBKRBroker] ⚠️ Not connected — cannot SHORT {num_contracts} MES"
            )
            return None

        with self._lock:
            self._open_positions["MES"] = {
                "qty": num_contracts,
                "entry": current_price,
                "stop": stop_price,
                "target": target_price,
                "side": "SHORT",
                "order_id": order_id,
            }

        try:
            log_trade(
                strategy=strategy,
                broker="ibkr",
                symbol="MES",
                action="SHORT",
                order_type=order_type,
                qty=num_contracts,
                price=current_price,
                fee_usd=commission,
                order_id=order_id,
                notes=f"SL={stop_price} TP={target_price} reason={reason}",
            )
        except Exception as _e:
            log_event("ERROR", "IBKRBroker", f"short_mes log_trade failed: {_e}")
        try:
            if _get_ne:
                _ne = _get_ne()
                _ne.notify_trade_open(
                    symbol="MES",
                    direction="SHORT",
                    size_usd=float(num_contracts) * current_price * MES_POINT_VALUE,
                    entry_price=current_price,
                    score=0.0,
                    top_3=[],
                    features={},
                    regime="UNKNOWN",
                )
        except Exception:
            pass
        return {"order_id": order_id, "price": current_price}

    def cover_mes(
        self,
        qty: int = 1,
        strategy: str = "futures_scalper",
        reason: str = "Signal",
        entry_price: float = 0.0,
    ) -> Optional[dict]:
        """Cover (close) a short MES position at market (cancels bracket children first)."""
        exit_price = self._get_mes_price("ask")
        entry = entry_price or self._open_positions.get("MES", {}).get(
            "entry", exit_price
        )
        pnl = (entry - exit_price) * MES_POINT_VALUE * qty

        if self.is_connected():
            try:
                self._run(self._place_market_async("BUY", qty), timeout=10)
                logger.info(
                    f"[IBKRBroker] COVER {qty} MES @ {exit_price:.2f} | P&L: ${pnl:+.2f}"
                )
            except Exception as e:
                log_event("ERROR", "IBKRBroker", f"cover_mes error: {e}")
                return None
        else:
            logger.info(
                f"[IBKRBroker] ⚠️ Not connected — cannot COVER MES @ {exit_price:.2f}"
            )
            return None

        with self._lock:
            self._open_positions.pop("MES", None)

        log_trade(
            strategy=strategy,
            broker="ibkr",
            symbol="MES",
            action="COVER",
            order_type="Market",
            qty=qty,
            price=exit_price,
            fee_usd=IBKR_COMMISSION * qty,
            pnl_usd=pnl,
            order_id=f"IBKR_{uuid.uuid4().hex[:8]}",
            notes=f"reason={reason}",
        )
        try:
            if _get_ne:
                _ne = _get_ne()
                _ne.notify_trade_close(
                    symbol="MES",
                    direction="SHORT",
                    pnl_usd=pnl,
                    pnl_pct=pnl / max(abs(entry * qty * MES_POINT_VALUE), 1),
                    exit_type=reason,
                    top_3=[],
                    features={},
                    regime="UNKNOWN",
                    score=0.0,
                )
        except Exception:
            pass
        return {"exit_price": exit_price, "pnl": pnl}

    # ── Account info ──────────────────────────────────────────────────────────

    def get_position(self, symbol: str = "MES") -> Optional[dict]:
        return self._open_positions.get(symbol)

    def get_account_balance(self) -> float:
        """Return IBKR account NetLiquidation in USD.  Returns 0.0 when not connected."""
        if not self.is_connected():
            return 0.0
        try:
            vals = self._ib.accountValues()
            for v in vals:
                if v.tag == "NetLiquidation" and v.currency == "USD":
                    return float(v.value)
        except Exception:
            pass
        return 0.0

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._connected = False


# ── Singleton ─────────────────────────────────────────────────────────────────
_ibkr_broker: Optional[IBKRBroker] = None


def get_ibkr_broker() -> IBKRBroker:
    global _ibkr_broker
    if _ibkr_broker is None:
        _ibkr_broker = IBKRBroker()
    return _ibkr_broker


# Drop-in alias so anything importing get_tradovate_broker still works
get_tradovate_broker = get_ibkr_broker
