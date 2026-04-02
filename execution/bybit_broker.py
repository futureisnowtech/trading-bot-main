"""
execution/bybit_broker.py — Bybit USDT perpetual futures execution.

Uses pybit v5 unified account API.
All positions use ISOLATED margin (never CROSS).
Server-side SL/TP set on every order.

Requires env vars:
  BYBIT_API_KEY   — from Bybit account settings
  BYBIT_SECRET    — from Bybit account settings
  BYBIT_TESTNET   — 'true' for testnet, 'false' for live (default: true)

Testnet: https://api-testnet.bybit.com
Live:    https://api.bybit.com

Symbol format: BTCUSDT, ETHUSDT (no hyphen — standard Bybit format)
Margin mode: ISOLATED on all positions (never CROSS)
Leverage: set per-symbol before order placement
Fee accounting (Bybit USDT perp, standard tier):
  Taker: 0.055%   Maker rebate: -0.025%
  Round-trip taker: ~0.110%
  With 3x leverage: 0.110% nominal = 0.330% of margin
"""

import logging
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Optional

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE

logger = logging.getLogger(__name__)

# ── Fee constants ─────────────────────────────────────────────────────────────
BYBIT_TAKER_FEE_PCT  =  0.00055   # 0.055% per side
BYBIT_MAKER_FEE_PCT  = -0.00025   # -0.025% rebate (maker)

# ── Environment ───────────────────────────────────────────────────────────────
_BYBIT_API_KEY  = os.getenv('BYBIT_API_KEY',  '')
_BYBIT_SECRET   = os.getenv('BYBIT_SECRET',   '')
_BYBIT_TESTNET  = os.getenv('BYBIT_TESTNET',  'true').lower() == 'true'

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    from pybit.unified_trading import HTTP as _PybitHTTP
    _PYBIT_AVAILABLE = True
except ImportError:
    _PybitHTTP = None
    _PYBIT_AVAILABLE = False
    logger.warning(
        '[BybitBroker] pybit not installed — paper mode only. '
        'Install with: pip install pybit'
    )

try:
    from logging_db.trade_logger import log_trade, log_event
except ImportError:
    def log_trade(*_a, **_kw): pass   # type: ignore[misc]
    def log_event(*_a, **_kw): pass   # type: ignore[misc]

try:
    from notifications.notification_engine import get_notification_engine
    _NOTIF_AVAILABLE = True
except Exception:
    _NOTIF_AVAILABLE = False


def _notify(title: str, message: str, category: str = 'TRADE_OPEN',
            severity: str = 'INFO', why: Optional[dict] = None) -> None:
    """Fire a dashboard notification — silently ignores if engine unavailable."""
    if not _NOTIF_AVAILABLE:
        return
    try:
        from notifications.notification_engine import (
            get_notification_engine, CAT_TRADE_OPEN, CAT_TRADE_CLOSE,
            CAT_SYSTEM, SEV_INFO, SEV_WARNING,
        )
        engine = get_notification_engine()
        engine.emit(
            category=category,
            severity=severity,
            title=title,
            message=message,
            why=why or {},
        )
    except Exception:
        pass


def _bybit_symbol_to_base(symbol: str) -> str:
    """BTCUSDT → BTC  (for yfinance price fallback)."""
    return symbol.replace('USDT', '').replace('USDC', '')


class BybitBroker:
    """
    Bybit USDT perpetual futures execution layer.

    Handles LONG and SHORT entries with ISOLATED margin + configurable leverage.
    Server-side SL/TP orders placed immediately after every fill.

    Paper mode:
      - Activated when PAPER_TRADING=true, BYBIT_TESTNET=true, or no API keys.
      - Fills use live mark price (yfinance fallback when pybit unavailable).
      - All activity logged to SQLite via log_trade / log_event.

    Live mode (BYBIT_TESTNET=false + valid keys):
      - Uses pybit v5 unified account HTTP client.
      - Orders: category=linear (USDT linear perps), positionIdx=0 (one-way mode).
    """

    def __init__(self):
        self._client: Optional['_PybitHTTP'] = None
        self._paper: bool = PAPER_TRADING or _BYBIT_TESTNET or not (_BYBIT_API_KEY and _BYBIT_SECRET)
        self._open_positions: dict = {}   # symbol → position dict
        self._lock = threading.Lock()
        self._tz = pytz.timezone(MARKET_TIMEZONE)

    # ─── Connection ───────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Initialise the Bybit client and verify connectivity.
        Returns True on success (including paper mode — which always succeeds).
        """
        if self._paper:
            mode = 'TESTNET (paper)' if _BYBIT_TESTNET else 'PAPER'
            logger.info('[BybitBroker] Running in %s mode — no live orders will be placed', mode)
            print(f'[BybitBroker] Running in {mode} mode — no live orders will be placed')
            log_event('INFO', 'BybitBroker', f'Startup: {mode} mode')
            return True

        if not _PYBIT_AVAILABLE:
            logger.warning('[BybitBroker] pybit not installed — forced paper mode')
            self._paper = True
            return True

        try:
            testnet = _BYBIT_TESTNET
            self._client = _PybitHTTP(
                testnet=testnet,
                api_key=_BYBIT_API_KEY,
                api_secret=_BYBIT_SECRET,
            )
            # Verify credentials by fetching wallet balance
            resp = self._client.get_wallet_balance(accountType='UNIFIED')
            if resp.get('retCode') != 0:
                raise RuntimeError(f"Bybit API error: {resp.get('retMsg')}")

            coins = resp.get('result', {}).get('list', [{}])[0].get('coin', [])
            usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
            bal_str = f"${float(usdt.get('equity', 0)):.2f}" if usdt else 'unknown'
            mode = 'TESTNET' if testnet else 'LIVE'
            msg = f'Connected to Bybit {mode} — USDT equity: {bal_str}'
            print(f'[BybitBroker] {msg}')
            log_event('INFO', 'BybitBroker', msg)
            return True

        except Exception as exc:
            logger.error('[BybitBroker] Connection failed: %s — falling back to paper mode', exc)
            print(f'[BybitBroker] Connection failed: {exc} — falling back to paper mode')
            log_event('WARNING', 'BybitBroker', f'Connection failed, paper mode: {exc}')
            self._paper = True
            return True   # still return True — we degrade gracefully

    # ─── Account ──────────────────────────────────────────────────────────────

    def get_account_balance(self) -> float:
        """Return USDT available equity. Falls back to ACCOUNT_SIZE in paper mode."""
        if self._paper or not self._client:
            return ACCOUNT_SIZE
        try:
            resp = self._client.get_wallet_balance(accountType='UNIFIED')
            coins = resp.get('result', {}).get('list', [{}])[0].get('coin', [])
            usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
            return float(usdt.get('availableToWithdraw', ACCOUNT_SIZE))
        except Exception as exc:
            logger.warning('[BybitBroker] get_account_balance failed: %s', exc)
            return ACCOUNT_SIZE

    # ─── Order placement ──────────────────────────────────────────────────────

    def open_long(
        self,
        symbol: str,
        position_usd: float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        leverage: int,
        composite_score: float,
        atr_at_entry: float,
        regime: str,
        paper: bool,
    ) -> Optional[dict]:
        """
        Open a leveraged LONG position (one-way mode, ISOLATED margin).

        Returns a position dict on success, None on failure.
        """
        if self._paper or paper:
            return self._paper_open(
                symbol, 'LONG', position_usd, entry_price, stop_price,
                take_profit_price, leverage, composite_score, atr_at_entry, regime,
            )
        if not self._client:
            return self._paper_open(
                symbol, 'LONG', position_usd, entry_price, stop_price,
                take_profit_price, leverage, composite_score, atr_at_entry, regime,
            )

        return self._live_open(
            symbol, 'Buy', position_usd, entry_price, stop_price,
            take_profit_price, leverage, composite_score, atr_at_entry, regime,
        )

    def open_short(
        self,
        symbol: str,
        position_usd: float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        leverage: int,
        composite_score: float,
        atr_at_entry: float,
        regime: str,
        paper: bool,
    ) -> Optional[dict]:
        """
        Open a leveraged SHORT position (one-way mode, ISOLATED margin).

        Returns a position dict on success, None on failure.
        """
        if self._paper or paper:
            return self._paper_open(
                symbol, 'SHORT', position_usd, entry_price, stop_price,
                take_profit_price, leverage, composite_score, atr_at_entry, regime,
            )
        if not self._client:
            return self._paper_open(
                symbol, 'SHORT', position_usd, entry_price, stop_price,
                take_profit_price, leverage, composite_score, atr_at_entry, regime,
            )

        return self._live_open(
            symbol, 'Sell', position_usd, entry_price, stop_price,
            take_profit_price, leverage, composite_score, atr_at_entry, regime,
        )

    def close_position(
        self,
        symbol: str,
        direction: str,
        qty: float,
        paper: bool,
        pos_fallback: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Close an open position at market price.

        direction : 'LONG' | 'SHORT' — determines close side.
        qty       : Number of contracts/coins to close.
        pos_fallback : Risk manager position dict used to restore broker state
                       after a restart (broker loses in-memory _open_positions).
        """
        with self._lock:
            pos = self._open_positions.get(symbol)
            if not pos:
                if pos_fallback:
                    # Bot restarted: reconstruct broker state from risk manager record.
                    # Use canonical key names (entry_price, not entry) so _paper_close
                    # and _live_close can read them without branching.
                    _fb_entry = pos_fallback.get('entry', 0.0)
                    _fb_qty   = pos_fallback.get('qty', qty)
                    self._open_positions[symbol] = {
                        'symbol':                symbol,
                        'direction':             pos_fallback.get('direction', direction),
                        'entry_price':           _fb_entry,
                        'qty':                   _fb_qty,
                        'position_usd':          _fb_qty * _fb_entry,
                        'leverage':              pos_fallback.get('leverage', 1),
                        'stop_price':            pos_fallback.get('stop', 0.0),
                        'take_profit_price':     pos_fallback.get('target', 0.0),
                        'entry_composite_score': pos_fallback.get('entry_composite_score', 0.0),
                        'atr_at_entry':          pos_fallback.get('atr_at_entry', 0.0),
                        'regime':                pos_fallback.get('regime', 'UNKNOWN'),
                        'paper':                 True,
                        'order_id':              pos_fallback.get('order_id', f'RESTORED_{uuid.uuid4().hex[:6]}'),
                        'ts':                    pos_fallback.get('ts_entry', datetime.now(self._tz).isoformat()),
                    }
                    pos = self._open_positions[symbol]
                    logger.info('[BybitBroker] close_position: restored state from pos_fallback for %s', symbol)
                else:
                    logger.warning('[BybitBroker] close_position: no open position for %s (and no fallback)', symbol)
                    return None

        if self._paper or paper:
            return self._paper_close(symbol)

        if not self._client:
            return self._paper_close(symbol)

        return self._live_close(symbol, direction, qty)

    # ─── Positions & market data ──────────────────────────────────────────────

    def get_open_positions(self) -> dict:
        """
        Return a copy of in-memory open positions.
        If not in paper mode, also reconciles with live Bybit positions.
        """
        if not self._paper and self._client:
            self._reconcile_live_positions()
        return dict(self._open_positions)

    def get_funding_rate(self, symbol: str) -> float:
        """
        Fetch the most recent funding rate for a symbol.
        Returns rate as a signed decimal (e.g. 0.0001 = 0.01%/8h).
        Falls back to 0.0 on any error.
        """
        if not self._client:
            return 0.0
        try:
            resp = self._client.get_funding_rate_history(
                category='linear',
                symbol=symbol,
                limit=1,
            )
            rows = resp.get('result', {}).get('list', [])
            if rows:
                return float(rows[0].get('fundingRate', 0.0))
        except Exception as exc:
            logger.debug('[BybitBroker] get_funding_rate %s: %s', symbol, exc)
        return 0.0

    def get_mark_price(self, symbol: str) -> float:
        """
        Fetch current mark price for a symbol.
        Falls back to yfinance if pybit unavailable.
        Returns 0.0 on failure.
        """
        return self._get_mark_price(symbol) or 0.0

    # ─── Live execution helpers ───────────────────────────────────────────────

    def _live_open(
        self,
        symbol: str,
        side: str,          # 'Buy' | 'Sell'
        position_usd: float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        leverage: int,
        composite_score: float,
        atr_at_entry: float,
        regime: str,
    ) -> Optional[dict]:
        """Place a live market order on Bybit with server-side SL/TP."""
        try:
            # 1. Set leverage (ISOLATED margin mode)
            self._set_leverage(symbol, leverage)

            # 2. Compute order quantity (base coin)
            price_for_qty = entry_price if entry_price > 0 else self._get_mark_price(symbol) or 0
            if price_for_qty <= 0:
                logger.error('[BybitBroker] _live_open: cannot determine price for %s', symbol)
                return None
            qty = round(position_usd / price_for_qty, 3)

            # 3. Place market order with SL/TP attached
            resp = self._client.place_order(
                category='linear',
                symbol=symbol,
                side=side,
                orderType='Market',
                qty=str(qty),
                stopLoss=str(round(stop_price, 4)),
                takeProfit=str(round(take_profit_price, 4)),
                positionIdx=0,   # one-way mode
            )
            if resp.get('retCode') != 0:
                err = resp.get('retMsg', 'unknown error')
                logger.error('[BybitBroker] place_order %s %s failed: %s', side, symbol, err)
                log_event('ERROR', 'BybitBroker', f'place_order {side} {symbol}: {err}')
                return None

            order_id = resp.get('result', {}).get('orderId', f'BY_{uuid.uuid4().hex[:8]}')
            fill_price = self._get_mark_price(symbol) or price_for_qty
            direction  = 'LONG' if side == 'Buy' else 'SHORT'
            fee        = position_usd * BYBIT_TAKER_FEE_PCT
            ts         = datetime.now(self._tz).isoformat()

            pos = {
                'symbol':               symbol,
                'direction':            direction,
                'entry_price':          fill_price,
                'qty':                  qty,
                'position_usd':         position_usd,
                'leverage':             leverage,
                'stop_price':           stop_price,
                'take_profit_price':    take_profit_price,
                'atr_at_entry':         atr_at_entry,
                'entry_composite_score': composite_score,
                'regime':               regime,
                'paper':                False,
                'ts':                   ts,
                'order_id':             str(order_id),
            }

            with self._lock:
                self._open_positions[symbol] = pos

            log_trade(
                strategy='bybit_perp', broker='bybit',
                symbol=symbol, action='BUY' if direction == 'LONG' else 'SELL',
                order_type='MARKET',
                qty=qty, price=fill_price, fee_usd=fee, paper=False,
                order_id=str(order_id),
                notes=(
                    f"{direction} lev={leverage}x "
                    f"SL={stop_price:.4f} TP={take_profit_price:.4f} "
                    f"score={composite_score:.1f} regime={regime}"
                ),
            )
            _notify(
                title=f'BYBIT {direction} {symbol}',
                message=(
                    f'Entry {fill_price:.4f} | qty {qty} | {leverage}x | '
                    f'SL {stop_price:.4f} | TP {take_profit_price:.4f}'
                ),
                category='TRADE_OPEN',
            )
            logger.info(
                '[BybitBroker] %s %s %s @ %.4f  lev=%dx  SL=%.4f  TP=%.4f',
                direction, qty, symbol, fill_price, leverage, stop_price, take_profit_price,
            )
            print(
                f'[BybitBroker] {direction} {qty} {symbol} @ {fill_price:.4f} '
                f'lev={leverage}x | SL={stop_price:.4f} TP={take_profit_price:.4f}'
            )
            return pos

        except Exception as exc:
            logger.error('[BybitBroker] _live_open %s %s: %s', side, symbol, exc)
            log_event('ERROR', 'BybitBroker', f'_live_open {side} {symbol}: {exc}')
            return None

    def _live_close(self, symbol: str, direction: str, qty: float) -> Optional[dict]:
        """Close a live position at market."""
        with self._lock:
            pos = self._open_positions.get(symbol)
        if not pos:
            return None

        try:
            # Cancel all open conditional orders first (SL/TP cleanup)
            try:
                self._client.cancel_all_orders(category='linear', symbol=symbol)
            except Exception:
                pass

            close_side = 'Sell' if direction == 'LONG' else 'Buy'
            resp = self._client.place_order(
                category='linear',
                symbol=symbol,
                side=close_side,
                orderType='Market',
                qty=str(round(qty, 3)),
                reduceOnly=True,
                positionIdx=0,
            )
            if resp.get('retCode') != 0:
                err = resp.get('retMsg', 'unknown error')
                logger.error('[BybitBroker] _live_close %s failed: %s', symbol, err)
                log_event('ERROR', 'BybitBroker', f'_live_close {symbol}: {err}')
                return None

            exit_price = self._get_mark_price(symbol) or pos.get('entry_price', 0.0)
            entry = pos.get('entry_price', 0.0)
            lev   = pos.get('leverage', 1)
            if direction == 'LONG':
                pnl = (exit_price - entry) * qty * lev
            else:
                pnl = (entry - exit_price) * qty * lev

            fee          = pos.get('position_usd', qty * entry) * BYBIT_TAKER_FEE_PCT
            close_action = 'SELL' if direction == 'LONG' else 'BUY'
            order_id     = resp.get('result', {}).get('orderId', f'BY_{uuid.uuid4().hex[:8]}')

            with self._lock:
                self._open_positions.pop(symbol, None)

            log_trade(
                strategy='bybit_perp', broker='bybit',
                symbol=symbol, action=close_action, order_type='MARKET',
                qty=qty, price=exit_price, fee_usd=fee, pnl_usd=pnl,
                paper=False, order_id=str(order_id),
            )
            _notify(
                title=f'BYBIT CLOSE {direction} {symbol}',
                message=f'Exit {exit_price:.4f} | P&L ${pnl:+.2f}',
                category='TRADE_CLOSE',
                severity='INFO' if pnl >= 0 else 'WARNING',
            )
            logger.info('[BybitBroker] CLOSED %s %s @ %.4f | P&L $%+.2f', direction, symbol, exit_price, pnl)
            print(f'[BybitBroker] CLOSE {direction} {symbol} @ {exit_price:.4f} | P&L: ${pnl:+.2f}')
            return {'symbol': symbol, 'direction': direction, 'pnl': pnl, 'exit_price': exit_price}

        except Exception as exc:
            logger.error('[BybitBroker] _live_close %s: %s', symbol, exc)
            log_event('ERROR', 'BybitBroker', f'_live_close {symbol}: {exc}')
            return None

    # ─── Paper trading ────────────────────────────────────────────────────────

    def _paper_open(
        self,
        symbol: str,
        direction: str,
        position_usd: float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        leverage: int,
        composite_score: float,
        atr_at_entry: float,
        regime: str,
    ) -> Optional[dict]:
        """Simulate an order open. Uses entry_price directly (no slippage model)."""
        price = entry_price if entry_price > 0 else self._get_mark_price(symbol) or 0.0
        if price <= 0:
            logger.warning('[BybitBroker] _paper_open: no price for %s — skipped', symbol)
            return None

        qty      = round(position_usd / price, 3)
        fee      = position_usd * BYBIT_TAKER_FEE_PCT
        order_id = f'PAPER_{uuid.uuid4().hex[:8]}'
        ts       = datetime.now(self._tz).isoformat()

        pos = {
            'symbol':                symbol,
            'direction':             direction,
            'entry_price':           price,
            'qty':                   qty,
            'position_usd':          position_usd,
            'leverage':              leverage,
            'stop_price':            stop_price,
            'take_profit_price':     take_profit_price,
            'atr_at_entry':          atr_at_entry,
            'entry_composite_score': composite_score,
            'regime':                regime,
            'paper':                 True,
            'ts':                    ts,
            'order_id':              order_id,
        }

        with self._lock:
            self._open_positions[symbol] = pos

        action = 'BUY' if direction == 'LONG' else 'SELL'
        log_trade(
            strategy='bybit_perp', broker='bybit_paper',
            symbol=symbol, action=action, order_type='MARKET',
            qty=qty, price=price, fee_usd=fee, paper=True,
            order_id=order_id,
            notes=(
                f'{direction} lev={leverage}x '
                f'SL={stop_price:.4f} TP={take_profit_price:.4f} '
                f'score={composite_score:.1f} regime={regime} '
                f'notional=${position_usd:.0f}'
            ),
        )
        _notify(
            title=f'[PAPER] BYBIT {direction} {symbol}',
            message=(
                f'Entry {price:.4f} | qty {qty} | {leverage}x | '
                f'SL {stop_price:.4f} | TP {take_profit_price:.4f}'
            ),
            category='TRADE_OPEN',
        )
        marker = '+' if direction == 'LONG' else '-'
        print(
            f'[PAPER BYBIT] {marker} {direction} {qty} {symbol} @ {price:.4f} '
            f'lev={leverage}x | SL={stop_price:.4f} TP={take_profit_price:.4f}'
        )
        return pos

    def _paper_close(self, symbol: str) -> Optional[dict]:
        """Simulate a position close at current mark price."""
        with self._lock:
            pos = self._open_positions.pop(symbol, None)
        if not pos:
            logger.warning('[BybitBroker] _paper_close: no open position for %s', symbol)
            return None

        entry      = pos['entry_price']
        exit_price = self._get_mark_price(symbol) or entry
        direction  = pos.get('direction', 'LONG')
        qty        = pos.get('qty', 0.0)
        lev        = pos.get('leverage', 1)
        position_usd = pos.get('position_usd', qty * entry)

        if direction == 'LONG':
            pnl = (exit_price - entry) * qty * lev
        else:
            pnl = (entry - exit_price) * qty * lev

        fee          = position_usd * BYBIT_TAKER_FEE_PCT
        close_action = 'SELL' if direction == 'LONG' else 'BUY'
        order_id     = f'PAPER_{uuid.uuid4().hex[:8]}'

        log_trade(
            strategy='bybit_perp', broker='bybit_paper',
            symbol=symbol, action=close_action, order_type='MARKET',
            qty=qty, price=exit_price, fee_usd=fee, pnl_usd=pnl,
            paper=True, order_id=order_id,
        )
        _notify(
            title=f'[PAPER] BYBIT CLOSE {direction} {symbol}',
            message=f'Exit {exit_price:.4f} | P&L ${pnl:+.2f}',
            category='TRADE_CLOSE',
            severity='INFO' if pnl >= 0 else 'WARNING',
        )
        print(f'[PAPER BYBIT] CLOSE {direction} {symbol} @ {exit_price:.4f} | P&L: ${pnl:+.2f}')
        return {'symbol': symbol, 'direction': direction, 'pnl': pnl, 'exit_price': exit_price}

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _get_mark_price(self, symbol: str) -> Optional[float]:
        """Get current mark price. Tries pybit first, yfinance as fallback."""
        if self._client:
            try:
                resp = self._client.get_tickers(category='linear', symbol=symbol)
                items = resp.get('result', {}).get('list', [])
                if items:
                    mark = float(items[0].get('markPrice', 0) or 0)
                    if mark > 0:
                        return mark
            except Exception:
                pass

        # yfinance fallback — always available
        try:
            import yfinance as yf
            base = _bybit_symbol_to_base(symbol)
            hist = yf.Ticker(f'{base}-USD').history(period='1d', interval='1m')
            if hist is not None and not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass

        return None

    def _set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol in ISOLATED margin mode."""
        if not self._client:
            return
        try:
            self._client.set_leverage(
                category='linear',
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as exc:
            # Bybit returns an error if leverage is already set — safe to ignore
            msg = str(exc)
            if 'leverage not modified' not in msg.lower() and '110043' not in msg:
                logger.warning('[BybitBroker] set_leverage %s %dx: %s', symbol, leverage, exc)

    def _reconcile_live_positions(self) -> None:
        """
        Pull open positions from Bybit and update in-memory dict.
        Adds any positions present on exchange but missing locally (e.g. after restart).
        """
        if not self._client:
            return
        try:
            resp = self._client.get_positions(category='linear', settleCoin='USDT')
            rows = resp.get('result', {}).get('list', [])
            with self._lock:
                for row in rows:
                    sym  = row.get('symbol', '')
                    size = float(row.get('size', 0) or 0)
                    if size <= 0 or not sym:
                        continue
                    if sym not in self._open_positions:
                        direction = 'LONG' if row.get('side') == 'Buy' else 'SHORT'
                        self._open_positions[sym] = {
                            'symbol':                sym,
                            'direction':             direction,
                            'entry_price':           float(row.get('avgPrice', 0) or 0),
                            'qty':                   size,
                            'position_usd':          float(row.get('positionValue', 0) or 0),
                            'leverage':              int(float(row.get('leverage', 1) or 1)),
                            'stop_price':            float(row.get('stopLoss', 0) or 0),
                            'take_profit_price':     float(row.get('takeProfit', 0) or 0),
                            'atr_at_entry':          0.0,
                            'entry_composite_score': 0.0,
                            'regime':                'UNKNOWN',
                            'paper':                 False,
                            'ts':                    datetime.now(self._tz).isoformat(),
                            'order_id':              row.get('orderId', ''),
                        }
                        logger.info('[BybitBroker] Reconciled position: %s %s', direction, sym)
        except Exception as exc:
            logger.warning('[BybitBroker] _reconcile_live_positions: %s', exc)


# ─── Module-level singleton ───────────────────────────────────────────────────

_broker_instance: Optional[BybitBroker] = None


def get_bybit_broker() -> BybitBroker:
    """Return the module-level BybitBroker singleton (creates it on first call)."""
    global _broker_instance
    if _broker_instance is None:
        _broker_instance = BybitBroker()
    return _broker_instance
