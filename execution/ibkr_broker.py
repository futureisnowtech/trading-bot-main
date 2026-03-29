"""
execution/ibkr_broker.py — Interactive Brokers futures execution via ib_insync.

Replaces tradovate_broker.py. Connects to Trader Workstation (TWS) running
locally. Same public API: buy_mes / sell_mes / short_mes / get_position /
get_account_balance.

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
"""
import os
import sys
import uuid
import threading
import time
from typing import Optional
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING, MARKET_TIMEZONE, FUTURES_NUM_CONTRACTS
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

# TWS connection settings
IBKR_HOST = os.getenv('IBKR_HOST', '127.0.0.1')
IBKR_PORT = int(os.getenv('IBKR_PORT', '7497'))   # 7497=paper TWS, 7496=live TWS
IBKR_CLIENT_ID = int(os.getenv('IBKR_CLIENT_ID', '2'))

# MES contract — update expiry each quarter
# Q1 Jan-Mar: 20260320  Q2 Apr-Jun: 20260619  Q3 Jul-Sep: 20260918  Q4 Oct-Dec: 20261218
MES_EXPIRY = os.getenv('MES_EXPIRY', '20260619')   # Current: June 2026
MES_POINT_VALUE = 5.00    # $ per full point
MES_TICK_SIZE   = 0.25    # minimum price increment
MES_TICK_VALUE  = 1.25    # $ per tick
IBKR_COMMISSION = 0.47    # $ per contract per side


def _get_mes_contract():
    from ib_insync import Future
    return Future(
        symbol='MES',
        lastTradeDateOrContractMonth=MES_EXPIRY,
        exchange='CME',
        currency='USD',
    )


class IBKRBroker:
    """
    Interactive Brokers broker for MES futures.
    Connects to Trader Workstation via ib_insync socket API.
    """

    def __init__(self):
        self._ib = None
        self._connected = False
        self._open_positions: dict = {}
        self._lock = threading.Lock()

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        import asyncio
        import threading

        result = {'ib': None, 'error': None}

        def _thread_connect():
            # Run in a completely fresh thread with its own event loop.
            # Python 3.14 requires asyncio.timeout() to be inside a Task;
            # asyncio.run() guarantees that by wrapping in create_task().
            async def _inner():
                from ib_insync import IB
                ib = IB()
                await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
                return ib
            try:
                result['ib'] = asyncio.run(_inner())
            except Exception as exc:
                result['error'] = exc

        t = threading.Thread(target=_thread_connect, daemon=True)
        t.start()
        t.join(timeout=15)

        try:
            if result['error']:
                raise result['error']
            if result['ib'] is None:
                raise RuntimeError("Connection thread timed out")
            self._ib = result['ib']

            self._connected = self._ib.isConnected()
            mode = 'PAPER' if PAPER_TRADING else 'LIVE'
            if self._connected:
                acct = self._ib.managedAccounts()[0] if self._ib.managedAccounts() else 'unknown'
                print(f"[IBKRBroker] Connected to TWS ({mode}) account={acct} port={IBKR_PORT} ✅")
                log_event('INFO', 'IBKRBroker', f"Connected ({mode}) account={acct}")
                self._sync_positions()
            else:
                print("[IBKRBroker] ⚠️ Could not connect to TWS — is it running?")
            return self._connected
        except Exception as e:
            print(f"[IBKRBroker] Connection error: {e}")
            print("[IBKRBroker] ⚠️ TWS not reachable — make sure TWS is open and API is enabled")
            log_event('ERROR', 'IBKRBroker', f"Connection failed: {e}")
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
                if pos.contract.symbol == 'MES' and pos.position != 0:
                    side = 'LONG' if pos.position > 0 else 'SHORT'
                    self._open_positions['MES'] = {
                        'qty': abs(pos.position),
                        'entry': pos.avgCost / MES_POINT_VALUE,  # avgCost is in USD
                        'stop': 0.0,
                        'target': 0.0,
                        'side': side,
                        'order_id': 'SYNCED',
                    }
                    print(f"[IBKRBroker] Synced existing {side} {abs(pos.position)} MES position")
        except Exception as e:
            log_event('WARN', 'IBKRBroker', f"Position sync error: {e}")

    # ── Price helper ──────────────────────────────────────────────────────────

    def _get_mes_price(self, side: str = 'mid') -> float:
        """Get current MES price from TWS market data. Falls back to yfinance."""
        if self.is_connected():
            try:
                contract = _get_mes_contract()
                self._ib.qualifyContracts(contract)
                ticker = self._ib.reqMktData(contract, '', False, False)
                self._ib.sleep(1.0)
                if side == 'ask' and ticker.ask and ticker.ask > 0:
                    return float(ticker.ask)
                if side == 'bid' and ticker.bid and ticker.bid > 0:
                    return float(ticker.bid)
                if ticker.last and ticker.last > 0:
                    return float(ticker.last)
                if ticker.close and ticker.close > 0:
                    return float(ticker.close)
            except Exception as e:
                log_event('WARN', 'IBKRBroker', f"Market data error: {e}")

        # yfinance fallback
        try:
            import yfinance as yf
            hist = yf.Ticker('MES=F').history(period='1d', interval='1m')
            if hist is not None and not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass
        try:
            import yfinance as yf
            hist = yf.download('ES=F', period='2d', interval='5m',
                               auto_adjust=True, progress=False)
            if hist is not None and not hist.empty:
                cols = [c[0].lower() if isinstance(c, tuple) else c.lower()
                        for c in hist.columns]
                hist.columns = cols
                return float(hist['close'].iloc[-1])
        except Exception:
            pass
        return 5750.0

    # ── Order placement ───────────────────────────────────────────────────────

    def buy_mes(
        self,
        num_contracts: int = FUTURES_NUM_CONTRACTS,
        order_type: str = 'Market',
        limit_price: Optional[float] = None,
        stop_loss_pts: float = 4.0,
        take_profit_pts: float = 8.0,
        strategy: str = 'futures_scalper',
    ) -> Optional[dict]:
        """Go long MES. Bracket order: market entry + server-side SL + TP."""
        current_price = self._get_mes_price('ask')
        stop_price   = round(current_price - stop_loss_pts, 2)
        target_price = round(current_price + take_profit_pts, 2)
        commission   = IBKR_COMMISSION * num_contracts * 2

        if self.is_connected():
            try:
                from ib_insync import Future, Order, BracketOrder
                contract = _get_mes_contract()
                self._ib.qualifyContracts(contract)

                # Bracket order: parent market + attached SL + TP
                bracket = self._ib.bracketOrder(
                    action='BUY',
                    quantity=num_contracts,
                    limitPrice=current_price,
                    takeProfitPrice=target_price,
                    stopLossPrice=stop_price,
                )
                trades = []
                for order in bracket:
                    order.outsideRth = False
                    trade = self._ib.placeOrder(contract, order)
                    trades.append(trade)

                order_id = str(trades[0].order.orderId) if trades else f'IBKR_{uuid.uuid4().hex[:8]}'
                print(f"[IBKRBroker] BUY {num_contracts} MES @ ~{current_price:.2f} | SL={stop_price} TP={target_price}")
            except Exception as e:
                log_event('ERROR', 'IBKRBroker', f"buy_mes order error: {e}")
                order_id = f'IBKR_ERR_{uuid.uuid4().hex[:8]}'
        else:
            order_id = f'IBKR_OFFLINE_{uuid.uuid4().hex[:8]}'
            print(f"[IBKRBroker] ⚠️ Not connected — paper-logging BUY {num_contracts} MES @ {current_price:.2f}")

        with self._lock:
            self._open_positions['MES'] = {
                'qty': num_contracts, 'entry': current_price,
                'stop': stop_price, 'target': target_price,
                'side': 'LONG', 'order_id': order_id,
            }

        log_trade(
            strategy=strategy, broker='ibkr' if not PAPER_TRADING else 'ibkr_paper',
            symbol='MES', action='BUY', order_type=order_type,
            qty=num_contracts, price=current_price, fee_usd=commission,
            paper=PAPER_TRADING, order_id=order_id,
            notes=f"SL={stop_price} TP={target_price} risk=${stop_loss_pts*MES_POINT_VALUE*num_contracts:.2f}",
        )
        alert_trade_opened(strategy, 'MES', 'BUY', float(num_contracts),
                           current_price, stop_price, target_price)
        return {'order_id': order_id, 'price': current_price}

    def sell_mes(
        self,
        num_contracts: int = 1,
        strategy: str = 'futures_scalper',
        reason: str = 'Signal',
        entry_price: float = 0.0,
    ) -> Optional[dict]:
        """Close a long MES position at market."""
        exit_price = self._get_mes_price('bid')
        entry = entry_price or self._open_positions.get('MES', {}).get('entry', exit_price)
        pnl = (exit_price - entry) * MES_POINT_VALUE * num_contracts

        if self.is_connected():
            try:
                from ib_insync import Future, MarketOrder
                contract = _get_mes_contract()
                self._ib.qualifyContracts(contract)
                order = MarketOrder('SELL', num_contracts)
                self._ib.placeOrder(contract, order)
                print(f"[IBKRBroker] SELL {num_contracts} MES @ {exit_price:.2f} | P&L: ${pnl:+.2f}")
            except Exception as e:
                log_event('ERROR', 'IBKRBroker', f"sell_mes error: {e}")
        else:
            print(f"[IBKRBroker] ⚠️ Not connected — paper-logging SELL MES @ {exit_price:.2f}")

        with self._lock:
            self._open_positions.pop('MES', None)

        log_trade(
            strategy=strategy, broker='ibkr' if not PAPER_TRADING else 'ibkr_paper',
            symbol='MES', action='SELL', order_type='Market',
            qty=num_contracts, price=exit_price,
            fee_usd=IBKR_COMMISSION * num_contracts,
            pnl_usd=pnl, paper=PAPER_TRADING,
            order_id=f'IBKR_{uuid.uuid4().hex[:8]}',
            notes=f"reason={reason}",
        )
        alert_trade_closed(strategy, 'MES', 'SELL', float(num_contracts),
                           entry, exit_price, pnl, reason)
        return {'exit_price': exit_price, 'pnl': pnl}

    def short_mes(
        self,
        num_contracts: int = 1,
        order_type: str = 'Market',
        limit_price: Optional[float] = None,
        stop_loss_pts: float = 4.0,
        take_profit_pts: float = 8.0,
        strategy: str = 'futures_scalper',
    ) -> Optional[dict]:
        """Go short MES. Bracket order: market entry + server-side SL + TP."""
        current_price = self._get_mes_price('bid')
        stop_price   = round(current_price + stop_loss_pts, 2)
        target_price = round(current_price - take_profit_pts, 2)
        commission   = IBKR_COMMISSION * num_contracts * 2

        if self.is_connected():
            try:
                from ib_insync import Future
                contract = _get_mes_contract()
                self._ib.qualifyContracts(contract)
                bracket = self._ib.bracketOrder(
                    action='SELL',
                    quantity=num_contracts,
                    limitPrice=current_price,
                    takeProfitPrice=target_price,
                    stopLossPrice=stop_price,
                )
                trades = []
                for order in bracket:
                    order.outsideRth = False
                    trade = self._ib.placeOrder(contract, order)
                    trades.append(trade)
                order_id = str(trades[0].order.orderId) if trades else f'IBKR_{uuid.uuid4().hex[:8]}'
                print(f"[IBKRBroker] SHORT {num_contracts} MES @ ~{current_price:.2f} | SL={stop_price} TP={target_price}")
            except Exception as e:
                log_event('ERROR', 'IBKRBroker', f"short_mes error: {e}")
                order_id = f'IBKR_ERR_{uuid.uuid4().hex[:8]}'
        else:
            order_id = f'IBKR_OFFLINE_{uuid.uuid4().hex[:8]}'
            print(f"[IBKRBroker] ⚠️ Not connected — paper-logging SHORT {num_contracts} MES @ {current_price:.2f}")

        with self._lock:
            self._open_positions['MES'] = {
                'qty': num_contracts, 'entry': current_price,
                'stop': stop_price, 'target': target_price,
                'side': 'SHORT', 'order_id': order_id,
            }

        log_trade(
            strategy=strategy, broker='ibkr' if not PAPER_TRADING else 'ibkr_paper',
            symbol='MES', action='SHORT', order_type=order_type,
            qty=num_contracts, price=current_price, fee_usd=commission,
            paper=PAPER_TRADING, order_id=order_id,
            notes=f"SL={stop_price} TP={target_price}",
        )
        alert_trade_opened(strategy, 'MES', 'SHORT', float(num_contracts),
                           current_price, stop_price, target_price)
        return {'order_id': order_id, 'price': current_price}

    def cover_mes(
        self,
        num_contracts: int = 1,
        strategy: str = 'futures_scalper',
        reason: str = 'Signal',
        entry_price: float = 0.0,
    ) -> Optional[dict]:
        """Cover (close) a short MES position."""
        exit_price = self._get_mes_price('ask')
        entry = entry_price or self._open_positions.get('MES', {}).get('entry', exit_price)
        pnl = (entry - exit_price) * MES_POINT_VALUE * num_contracts

        if self.is_connected():
            try:
                from ib_insync import MarketOrder
                contract = _get_mes_contract()
                self._ib.qualifyContracts(contract)
                self._ib.placeOrder(contract, MarketOrder('BUY', num_contracts))
                print(f"[IBKRBroker] COVER {num_contracts} MES @ {exit_price:.2f} | P&L: ${pnl:+.2f}")
            except Exception as e:
                log_event('ERROR', 'IBKRBroker', f"cover_mes error: {e}")

        with self._lock:
            self._open_positions.pop('MES', None)

        log_trade(
            strategy=strategy, broker='ibkr' if not PAPER_TRADING else 'ibkr_paper',
            symbol='MES', action='COVER', order_type='Market',
            qty=num_contracts, price=exit_price,
            fee_usd=IBKR_COMMISSION * num_contracts,
            pnl_usd=pnl, paper=PAPER_TRADING,
            order_id=f'IBKR_{uuid.uuid4().hex[:8]}',
            notes=f"reason={reason}",
        )
        alert_trade_closed(strategy, 'MES', 'COVER', float(num_contracts),
                           entry, exit_price, pnl, reason)
        return {'exit_price': exit_price, 'pnl': pnl}

    # ── Account info ──────────────────────────────────────────────────────────

    def get_position(self, symbol: str = 'MES') -> Optional[dict]:
        return self._open_positions.get(symbol)

    def get_account_balance(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            vals = self._ib.accountValues()
            for v in vals:
                if v.tag == 'NetLiquidation' and v.currency == 'USD':
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
