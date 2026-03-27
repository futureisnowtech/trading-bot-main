"""
execution/tradovate_broker.py

Tradovate futures execution — ES and MES contracts.
No PDT (Pattern Day Trader) restriction on futures accounts.
You can make unlimited trades per day.

Tradovate offers:
  - Free paper trading account
  - ~$0.59/contract commission (cheapest available for ES/MES)
  - Official WebSocket API
  - Web-based account: https://app.tradovate.com

To get API access:
  1. Sign up at tradovate.com (free)
  2. Account → API Access → Generate credentials
  3. Add to .env: TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_APP_ID, TRADOVATE_APP_VERSION

ES contract:  $50 per point, min tick $12.50 (0.25 points)
MES contract: $5 per point, min tick $1.25 (0.25 points)  ← use this with $500 account

With $500 account: USE MES ONLY. Never ES.
  - MES margin: ~$40 intraday (varies by broker)
  - 1 MES point = $5 profit/loss
  - Realistic daily target: 2-4 points = $10-20 per contract
"""
import json
import os
import sys
import time
import uuid
import threading
from typing import Optional
from datetime import datetime
import pytz
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING, MARKET_TIMEZONE, FUTURES_NUM_CONTRACTS
from logging_db.trade_logger import log_trade, log_event
from alerts.telegram_alert import alert_trade_opened, alert_trade_closed

TRADOVATE_USERNAME = os.getenv('TRADOVATE_USERNAME', '')
TRADOVATE_PASSWORD = os.getenv('TRADOVATE_PASSWORD', '')
TRADOVATE_APP_ID = os.getenv('TRADOVATE_APP_ID', '')
TRADOVATE_APP_VERSION = os.getenv('TRADOVATE_APP_VERSION', '1.0')
TRADOVATE_DEVICE_ID = os.getenv('TRADOVATE_DEVICE_ID', str(uuid.uuid4()))

# Paper vs Live endpoints
TRADOVATE_DEMO_URL = 'https://demo.tradovateapi.com/v1'
TRADOVATE_LIVE_URL = 'https://live.tradovateapi.com/v1'

# MES (Micro E-mini S&P 500)
MES_SYMBOL = 'MESM6'     # Quarterly expiry — update this each quarter (current: June 2026)
MES_TICK_SIZE = 0.25     # Minimum price movement
MES_TICK_VALUE = 1.25    # $ per tick
MES_POINT_VALUE = 5.00   # $ per full point

# ES (E-mini S&P 500) — DO NOT USE WITH $500 ACCOUNT
ES_SYMBOL = 'ESM6'
ES_TICK_VALUE = 12.50
ES_POINT_VALUE = 50.00


class TradovateBroker:
    """
    Tradovate futures broker.
    Handles MES (and ES if you upgrade capital later).
    """

    def __init__(self):
        self._access_token: str = ''
        self._account_id: int = 0
        self._connected = False
        self._open_positions: dict = {}
        self._base_url = TRADOVATE_DEMO_URL if PAPER_TRADING else TRADOVATE_LIVE_URL

    def connect(self) -> bool:
        if not TRADOVATE_USERNAME or not TRADOVATE_PASSWORD:
            print("[TradovateBroker] Credentials not set — using simulated paper mode")
            self._connected = True  # Allow paper trading without API
            return True

        try:
            resp = requests.post(
                f'{self._base_url}/auth/accesstokenrequest',
                json={
                    'name': TRADOVATE_USERNAME,
                    'password': TRADOVATE_PASSWORD,
                    'appId': TRADOVATE_APP_ID,
                    'appVersion': TRADOVATE_APP_VERSION,
                    'deviceId': TRADOVATE_DEVICE_ID,
                    'cid': 0,
                    'sec': ''
                },
                timeout=15
            )
            data = resp.json()

            if 'accessToken' not in data:
                print(f"[TradovateBroker] Auth failed: {data}")
                return False

            self._access_token = data['accessToken']

            # Get account ID
            accounts = self._get('/account/list')
            if accounts:
                self._account_id = accounts[0].get('id', 0)

            self._connected = True
            mode = 'DEMO' if PAPER_TRADING else 'LIVE'
            print(f"[TradovateBroker] Connected ({mode}) — Account ID: {self._account_id} ✅")
            log_event('INFO', 'TradovateBroker', f"Connected ({mode})")
            return True

        except Exception as e:
            print(f"[TradovateBroker] Connection error: {e}")
            # Fall back to simulated paper mode
            self._connected = True
            print("[TradovateBroker] Falling back to simulated paper mode")
            return True

    def is_connected(self) -> bool:
        return self._connected

    # ─── Order placement ──────────────────────────────────────────────────────

    def buy_mes(
        self,
        num_contracts: int = FUTURES_NUM_CONTRACTS,
        order_type: str = 'Market',
        limit_price: Optional[float] = None,
        stop_loss_pts: float = 4.0,    # 4 points = $20 risk per contract
        take_profit_pts: float = 8.0,  # 8 points = $40 per contract (2:1)
        strategy: str = 'futures_scalper'
    ) -> Optional[dict]:
        """
        Buy MES (Micro E-mini S&P 500) contracts.
        With $500 account: 1 contract max.
        stop_loss_pts: how many points below entry to set stop
        """
        if PAPER_TRADING or not self._access_token:
            return self._paper_trade(
                'MES', 'BUY', num_contracts,
                stop_loss_pts, take_profit_pts, strategy
            )

        try:
            # Get current MES price
            quote = self._get_quote(MES_SYMBOL)
            current_price = quote.get('ask', 0) if quote else 0

            payload = {
                'accountSpec': TRADOVATE_USERNAME,
                'accountId': self._account_id,
                'action': 'Buy',
                'symbol': MES_SYMBOL,
                'orderQty': num_contracts,
                'orderType': order_type,
                'timeInForce': 'Day',
                'isAutomated': True,
            }
            if order_type == 'Limit' and limit_price:
                payload['price'] = limit_price

            resp = self._post('/order/placeorder', payload)
            order_id = str(resp.get('orderId', ''))

            stop_price = current_price - stop_loss_pts
            target_price = current_price + take_profit_pts

            self._open_positions['MES'] = {
                'qty': num_contracts,
                'entry': current_price,
                'stop': stop_price,
                'target': target_price,
                'side': 'LONG',
                'order_id': order_id,
            }

            risk_usd = stop_loss_pts * MES_POINT_VALUE * num_contracts
            target_usd = take_profit_pts * MES_POINT_VALUE * num_contracts
            commission = 0.59 * num_contracts * 2  # Round trip

            log_trade(
                strategy=strategy, broker='tradovate',
                symbol='MES', action='BUY', order_type=order_type,
                qty=num_contracts, price=current_price,
                fee_usd=commission,
                paper=False, order_id=order_id,
                notes=f"SL={stop_price} TP={target_price} risk=${risk_usd}"
            )

            alert_trade_opened(
                strategy=strategy, symbol='MES', action='BUY',
                qty=float(num_contracts), price=current_price,
                stop_loss=stop_price, take_profit=target_price
            )

            print(f"[TradovateBroker] BUY {num_contracts} MES @ {current_price} | SL={stop_price} TP={target_price}")
            return resp

        except Exception as e:
            print(f"[TradovateBroker] Buy MES failed: {e}")
            log_event('ERROR', 'TradovateBroker', str(e))
            return None

    def sell_mes(
        self,
        num_contracts: int = 1,
        strategy: str = 'futures_scalper',
        reason: str = 'Signal',
        entry_price: float = 0.0
    ) -> Optional[dict]:
        """Close a long MES position."""
        if PAPER_TRADING or not self._access_token:
            return self._paper_close('MES', num_contracts, strategy, reason, entry_price)

        try:
            payload = {
                'accountSpec': TRADOVATE_USERNAME,
                'accountId': self._account_id,
                'action': 'Sell',
                'symbol': MES_SYMBOL,
                'orderQty': num_contracts,
                'orderType': 'Market',
                'timeInForce': 'Day',
                'isAutomated': True,
            }
            resp = self._post('/order/placeorder', payload)

            pos = self._open_positions.pop('MES', {})
            quote = self._get_quote(MES_SYMBOL)
            exit_price = quote.get('bid', entry_price) if quote else entry_price
            pnl = (exit_price - entry_price) * MES_POINT_VALUE * num_contracts

            log_trade(
                strategy=strategy, broker='tradovate',
                symbol='MES', action='SELL', order_type='Market',
                qty=num_contracts, price=exit_price,
                fee_usd=0.59 * num_contracts,
                pnl_usd=pnl, paper=False,
                notes=f"reason={reason}"
            )

            alert_trade_closed(
                strategy=strategy, symbol='MES', action='SELL',
                qty=float(num_contracts), entry_price=entry_price,
                exit_price=exit_price, pnl_usd=pnl, reason=reason
            )

            return resp
        except Exception as e:
            print(f"[TradovateBroker] Sell MES failed: {e}")
            return None

    def short_mes(
        self,
        num_contracts: int = 1,
        order_type: str = 'Limit',
        limit_price: Optional[float] = None,
        stop_loss_pts: float = 4.0,
        take_profit_pts: float = 8.0,
        strategy: str = 'futures_scalper'
    ) -> Optional[dict]:
        """
        Short MES (Micro E-mini S&P 500) contracts.
        In paper mode, simulates as a synthetic short using real price.
        In live mode, places a Sell order (short sell on futures account).
        """
        if PAPER_TRADING or not self._access_token:
            return self._paper_trade(
                'MES', 'SHORT', num_contracts,
                stop_loss_pts, take_profit_pts, strategy
            )

        try:
            quote = self._get_quote(MES_SYMBOL)
            current_price = quote.get('bid', 0) if quote else 0
            if not current_price:
                current_price = limit_price or self._get_real_es_price()

            payload = {
                'accountSpec': TRADOVATE_USERNAME,
                'accountId': self._account_id,
                'action': 'Sell',
                'symbol': MES_SYMBOL,
                'orderQty': num_contracts,
                'orderType': order_type,
                'timeInForce': 'Day',
                'isAutomated': True,
            }
            if order_type == 'Limit' and limit_price:
                payload['price'] = limit_price

            resp = self._post('/order/placeorder', payload)
            order_id = str(resp.get('orderId', ''))

            stop_price = current_price + stop_loss_pts
            target_price = current_price - take_profit_pts

            self._open_positions['MES'] = {
                'qty': num_contracts,
                'entry': current_price,
                'stop': stop_price,
                'target': target_price,
                'side': 'SHORT',
                'order_id': order_id,
            }

            commission = 0.59 * num_contracts * 2
            log_trade(
                strategy=strategy, broker='tradovate',
                symbol='MES', action='SHORT', order_type=order_type,
                qty=num_contracts, price=current_price,
                fee_usd=commission,
                paper=False, order_id=order_id,
                notes=f"SL={stop_price} TP={target_price}"
            )
            alert_trade_opened(strategy, 'MES', 'SHORT', float(num_contracts),
                               current_price, stop_price, target_price)
            print(f"[TradovateBroker] SHORT {num_contracts} MES @ {current_price} | SL={stop_price} TP={target_price}")
            return resp

        except Exception as e:
            print(f"[TradovateBroker] Short MES failed: {e}")
            log_event('ERROR', 'TradovateBroker', str(e))
            return None

    def get_position(self, symbol: str = 'MES') -> Optional[dict]:
        return self._open_positions.get(symbol)

    def get_account_balance(self) -> float:
        if not self._access_token:
            return 0.0
        try:
            data = self._get(f'/cashbalance/getcashbalancesnapshot?accountId={self._account_id}')
            return float(data.get('totalCashValue', 0)) if data else 0.0
        except Exception:
            return 0.0

    # ─── Paper trading simulation ─────────────────────────────────────────────

    def _get_real_es_price(self) -> float:
        """Fetch current ES/MES price from yfinance (free, no API key needed)."""
        try:
            import yfinance as yf
            ticker = yf.Ticker('ES=F')
            hist = ticker.history(period='1d', interval='1m')
            if hist is not None and not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass
        # Fallback: last known price from a slightly wider window
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
        return 5750.0  # Hard fallback — only used if yfinance is completely down

    def _paper_trade(self, symbol, side, qty, stop_pts, target_pts, strategy):
        """Simulate a futures trade using real ES market price from yfinance."""
        sim_price = self._get_real_es_price()
        stop = sim_price - stop_pts if side == 'BUY' else sim_price + stop_pts
        target = sim_price + target_pts if side == 'BUY' else sim_price - target_pts
        risk = stop_pts * MES_POINT_VALUE * qty

        self._open_positions[symbol] = {
            'qty': qty, 'entry': sim_price,
            'stop': stop, 'target': target, 'side': 'LONG' if side == 'BUY' else 'SHORT',
            'order_id': f'PAPER_{uuid.uuid4().hex[:8]}'
        }

        order_id = f'PAPER_{uuid.uuid4().hex[:8]}'
        log_trade(
            strategy=strategy, broker='tradovate_paper',
            symbol=symbol, action=side, order_type='MARKET',
            qty=qty, price=sim_price, paper=True,
            order_id=order_id,
            notes=f"SL={stop:.2f} TP={target:.2f} risk=${risk:.2f}"
        )

        print(f"[PAPER FUTURES] 🟢 {side} {qty} MES @ {sim_price:.2f} | SL={stop:.2f} TP={target:.2f}")
        alert_trade_opened(strategy, symbol, side, float(qty), sim_price, stop, target)
        return {'paper': True, 'price': sim_price}

    def _paper_close(self, symbol, qty, strategy, reason, entry_price):
        pos = self._open_positions.pop(symbol, {})
        entry = pos.get('entry', entry_price)
        # Use real current price for exit — same yfinance source
        exit_price = self._get_real_es_price()
        pnl = (exit_price - entry) * MES_POINT_VALUE * qty

        log_trade(
            strategy=strategy, broker='tradovate_paper',
            symbol=symbol, action='SELL', order_type='MARKET',
            qty=qty, price=exit_price, pnl_usd=pnl, paper=True,
            order_id=f'PAPER_{uuid.uuid4().hex[:8]}',
            notes=f"reason={reason}"
        )

        print(f"[PAPER FUTURES] 🔴 SELL {qty} MES @ {exit_price:.2f} | P&L: ${pnl:+.2f}")
        alert_trade_closed(strategy, symbol, 'SELL', float(qty), entry, exit_price, pnl, reason)
        return {'paper': True}

    # ─── HTTP helpers ─────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self._access_token}'
        }

    def _get(self, path: str) -> Optional[dict]:
        try:
            resp = requests.get(f'{self._base_url}{path}', headers=self._headers(), timeout=10)
            return resp.json()
        except Exception as e:
            print(f"[TradovateBroker] GET {path} error: {e}")
            return None

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(
            f'{self._base_url}{path}',
            json=payload,
            headers=self._headers(),
            timeout=10
        )
        return resp.json()

    def _get_quote(self, symbol: str) -> Optional[dict]:
        data = self._get(f'/marketdata/getQuote?symbol={symbol}')
        return data


# ─── Singleton ────────────────────────────────────────────────────────────────
_tradovate_broker: Optional[TradovateBroker] = None

def get_tradovate_broker() -> TradovateBroker:
    global _tradovate_broker
    if _tradovate_broker is None:
        _tradovate_broker = TradovateBroker()
    return _tradovate_broker
