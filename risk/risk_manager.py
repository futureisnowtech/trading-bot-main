"""
risk/risk_manager.py — The amygdala removal layer.
Every pre-trade gate lives here. Positions persisted to SQLite on every write.
System restart never loses position state.
"""
import os
import sys
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ACCOUNT_SIZE, MAX_DEPLOYED_PCT, MAX_RISK_PER_TRADE_PCT,
    MAX_DAILY_LOSS_PCT, MAX_POSITIONS_EQUITY, MAX_POSITIONS_CRYPTO,
    MAX_TRADES_PER_DAY_EQUITY, MAX_TRADES_PER_DAY_CRYPTO,
    COINBASE_MAKER_FEE_PCT, COINBASE_TAKER_FEE_PCT,
    MAX_DAILY_FEE_DRAG_PCT, PAPER_TRADING,
    EQUITY_STOP_LOSS_PCT, CRYPTO_STOP_LOSS_PCT, MARKET_TIMEZONE
)
from logging_db.trade_logger import (
    get_todays_pnl, get_todays_fees, get_daily_trade_count, log_event,
    persist_position, delete_position, load_open_positions, get_all_time_stats,
)
from data.market_data import is_market_open, is_in_no_trade_window


class RiskCheckResult:
    def __init__(self, approved: bool, reason: str = '', adjusted_size: Optional[float] = None):
        self.approved = approved
        self.reason = reason
        self.adjusted_size = adjusted_size

    def __bool__(self):
        return self.approved

    def __repr__(self):
        s = '✅ APPROVED' if self.approved else '❌ BLOCKED'
        return f"RiskCheck[{s}: {self.reason}]"


class RiskManager:
    def __init__(self):
        self._halted: bool = False
        self._halt_reason: str = ''
        self._equity: dict = {}   # symbol → position dict
        self._crypto: dict = {}
        self._last_scan_ts: float = 0.0
        self._restore_positions()

    def _restore_positions(self) -> None:
        """On startup, restore open positions from SQLite."""
        try:
            positions = load_open_positions(paper=PAPER_TRADING)
            for pos in positions:
                sym = pos['symbol']
                strat = pos['strategy']
                p = {
                    'qty': pos['qty'], 'entry': pos['entry'],
                    'stop': pos['stop'], 'target': pos['target'],
                    'high_since_entry': pos['high_since_entry'],
                    'ts_entry': pos['ts_entry'],
                    'direction': pos.get('direction', 'LONG'),
                }
                if 'equity' in strat:
                    self._equity[sym] = p
                else:
                    self._crypto[sym] = p
            if positions:
                print(f"[RiskManager] Restored {len(positions)} open positions from database")
        except Exception as e:
            print(f"[RiskManager] Position restore error: {e}")

    def check_entry(self, strategy, symbol, side, requested_size_usd,
                    current_price, confidence=0.5) -> RiskCheckResult:

        if self._halted:
            return RiskCheckResult(False, f"System halted: {self._halt_reason}")

        if confidence < 0.40:
            return RiskCheckResult(False, f"Confidence {confidence:.0%} < 40% minimum")

        daily_pnl = get_todays_pnl(paper=PAPER_TRADING)
        all_time = get_all_time_stats(paper=PAPER_TRADING)
        real_balance = ACCOUNT_SIZE + all_time['total_pnl']
        max_loss = real_balance * MAX_DAILY_LOSS_PCT
        if daily_pnl < -max_loss:
            reason = f"Daily loss limit hit: ${daily_pnl:.2f} (max ${max_loss:.2f})"
            self.halt(reason)
            return RiskCheckResult(False, reason)

        is_eq = 'equity' in strategy.lower() or 'futures' in strategy.lower()
        is_cr = 'crypto' in strategy.lower()

        if is_eq:
            if not is_market_open():
                return RiskCheckResult(False, "Market closed")
            if is_in_no_trade_window() and side == 'BUY':
                return RiskCheckResult(False, "No trades 9:30–10:00 ET opening window")

        if is_cr:
            fees = get_todays_fees(paper=PAPER_TRADING)
            if fees > ACCOUNT_SIZE * MAX_DAILY_FEE_DRAG_PCT:
                return RiskCheckResult(False, f"Daily fee limit: ${fees:.2f} (max ${ACCOUNT_SIZE*MAX_DAILY_FEE_DRAG_PCT:.2f})")

        if side == 'BUY':
            if is_eq and len(self._equity) >= MAX_POSITIONS_EQUITY:
                return RiskCheckResult(False, f"Max equity positions ({MAX_POSITIONS_EQUITY}) reached")
            if is_cr and len(self._crypto) >= MAX_POSITIONS_CRYPTO:
                return RiskCheckResult(False, f"Max crypto positions ({MAX_POSITIONS_CRYPTO}) reached")

            existing = self._equity.get(symbol) or self._crypto.get(symbol)
            if existing:
                return RiskCheckResult(False, f"Already holding {symbol} — no double-entry")

        if side == 'BUY':
            count = get_daily_trade_count(strategy, paper=PAPER_TRADING)
            max_t = MAX_TRADES_PER_DAY_EQUITY if is_eq else MAX_TRADES_PER_DAY_CRYPTO
            if count >= max_t:
                return RiskCheckResult(False, f"Max {max_t} trades/day reached ({strategy})")

        deployed = self._get_deployed()
        max_deploy = ACCOUNT_SIZE * MAX_DEPLOYED_PCT
        max_pos = ACCOUNT_SIZE * 0.20
        final_size = min(requested_size_usd, max_pos)

        if deployed + final_size > max_deploy:
            available = max_deploy - deployed
            if available < 10:
                return RiskCheckResult(False, f"Max capital deployed (${deployed:.0f}/${max_deploy:.0f})")
            final_size = available

        if final_size < 10:
            return RiskCheckResult(False, f"Position size ${final_size:.2f} too small")

        return RiskCheckResult(True, "All checks passed", adjusted_size=final_size)

    def calc_stop_loss(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        pct = EQUITY_STOP_LOSS_PCT if 'equity' in strategy else CRYPTO_STOP_LOSS_PCT
        if atr > 0:
            pct = min(atr * 2 / entry, pct * 1.5)
        return entry * (1 - pct)

    def calc_take_profit(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        stop = self.calc_stop_loss(entry, strategy, atr)
        return entry + (entry - stop) * 2.0

    def register_position(self, strategy, symbol, qty, entry, stop, target,
                          direction='LONG') -> None:
        tz = pytz.timezone(MARKET_TIMEZONE)
        ts = datetime.now(tz).isoformat()
        pos = {
            'qty': qty, 'entry': entry, 'stop': stop, 'target': target,
            'high_since_entry': entry, 'ts_entry': ts, 'direction': direction,
        }
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            self._equity[symbol] = pos
        else:
            self._crypto[symbol] = pos
        persist_position(symbol, strategy, qty, entry, stop, target, entry, ts,
                         PAPER_TRADING, direction=direction)

    def close_position(self, strategy, symbol) -> Optional[dict]:
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            pos = self._equity.pop(symbol, None)
        else:
            pos = self._crypto.pop(symbol, None)
        delete_position(symbol, strategy, PAPER_TRADING)
        return pos

    def update_high(self, strategy, symbol, price) -> None:
        d = self._equity if ('equity' in strategy.lower() or 'futures' in strategy.lower()) else self._crypto
        if symbol in d:
            direction = d[symbol].get('direction', 'LONG')
            old_extreme = d[symbol].get('high_since_entry', price)
            # LONG: track highest price. SHORT: track lowest price (stored in same field).
            new_extreme = max(old_extreme, price) if direction == 'LONG' else min(old_extreme, price)
            d[symbol]['high_since_entry'] = new_extreme
            if new_extreme != old_extreme:
                persist_position(symbol, strategy,
                                 d[symbol]['qty'], d[symbol]['entry'],
                                 d[symbol]['stop'], d[symbol]['target'],
                                 new_extreme, d[symbol]['ts_entry'], PAPER_TRADING,
                                 direction=direction)

    def get_position(self, strategy, symbol) -> Optional[dict]:
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            return self._equity.get(symbol)
        return self._crypto.get(symbol)

    def get_all_positions(self) -> dict:
        return {'equity': dict(self._equity), 'crypto': dict(self._crypto)}

    def should_exit(self, strategy, symbol, current_price) -> tuple:
        pos = self.get_position(strategy, symbol)
        if not pos:
            return False, ''
        direction = pos.get('direction', 'LONG')
        trail_pct = 0.07 if 'equity' in strategy else 0.04

        if direction == 'LONG':
            if current_price <= pos['stop']:
                return True, f"Hard stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
            if current_price >= pos['target']:
                return True, f"Take profit hit ${current_price:.4f} (target: ${pos['target']:.4f})"
            trailing = pos['high_since_entry'] * (1 - trail_pct)
            if current_price > pos['entry'] * 1.03 and current_price <= trailing:
                return True, f"Trailing stop triggered ${current_price:.4f}"
        else:  # SHORT
            if current_price >= pos['stop']:
                return True, f"Short stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
            if current_price <= pos['target']:
                return True, f"Short target hit ${current_price:.4f} (target: ${pos['target']:.4f})"
            # Trailing: exit if price rises above lowest_point * (1 + trail_pct)
            trailing = pos['high_since_entry'] * (1 + trail_pct)
            if current_price < pos['entry'] * 0.97 and current_price >= trailing:
                return True, f"Short trailing stop triggered ${current_price:.4f}"

        return False, ''

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        log_event('HALT', 'RiskManager', reason)
        print(f"\n🚨 RISK MANAGER HALT: {reason}\n")
        try:
            from alerts.telegram_alert import alert_risk_halt
            alert_risk_halt(reason)
        except Exception:
            pass

    def resume(self) -> None:
        self._halted = False
        self._halt_reason = ''
        log_event('INFO', 'RiskManager', 'Halt cleared — trading resumed')

    def ping(self) -> None:
        """Called after every successful scan to update watchdog timestamp."""
        import time
        self._last_scan_ts = time.time()

    def watchdog_ok(self, max_gap_seconds: int = 900) -> bool:
        import time
        if self._last_scan_ts == 0:
            return True  # Haven't started yet
        return (time.time() - self._last_scan_ts) < max_gap_seconds

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def _get_deployed(self) -> float:
        total = 0.0
        for p in self._equity.values():
            total += p.get('qty', 0) * p.get('entry', 0)
        for p in self._crypto.values():
            total += p.get('qty', 0) * p.get('entry', 0)
        return total

    def status_report(self) -> dict:
        return {
            'halted': self._halted,
            'halt_reason': self._halt_reason,
            'open_equity': len(self._equity),
            'open_crypto': len(self._crypto),
            'deployed_usd': self._get_deployed(),
            'todays_pnl': get_todays_pnl(paper=PAPER_TRADING),
            'todays_fees': get_todays_fees(paper=PAPER_TRADING),
        }


_rm: Optional[RiskManager] = None

def get_risk_manager() -> RiskManager:
    global _rm
    if _rm is None:
        _rm = RiskManager()
    return _rm
