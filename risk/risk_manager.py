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
    CRYPTO_MIN_PROFIT_FEE_MULTIPLE,
    COINBASE_MAKER_FEE_PCT, COINBASE_TAKER_FEE_PCT,
    MAX_DAILY_FEE_DRAG_PCT, PAPER_TRADING,
    EQUITY_STOP_LOSS_PCT, CRYPTO_STOP_LOSS_PCT, MARKET_TIMEZONE,
    PERP_MAX_POSITIONS, PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT,
)
from logging_db.trade_logger import (
    get_todays_pnl, get_todays_fees, get_daily_trade_count, log_event,
    persist_position, delete_position, load_open_positions, get_all_time_stats,
    get_kelly_stats,
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
        self._perp:   dict = {}   # Bybit perpetual positions (symbol → position dict)
        self._last_scan_ts: float = 0.0
        self._restore_positions()
        self._restore_halt_state()

    def _restore_positions(self) -> None:
        """On startup, restore open positions from SQLite.

        Validates each position against the trades table: if a close event (pnl_usd != 0)
        already exists for the symbol+strategy after the position's ts_entry, the bot was
        killed between log_trade and delete_position. Don't restore it — clean it up instead.
        """
        try:
            from logging_db.trade_logger import delete_position
            import sqlite3 as _sq
            from config import DB_PATH as _DB_PATH

            positions = load_open_positions(paper=PAPER_TRADING)
            restored = 0
            cleaned = 0
            for pos in positions:
                sym   = pos['symbol']
                strat = pos['strategy']
                ts_e  = pos.get('ts_entry', '1970-01-01')

                # Check if a close trade already exists for this position
                try:
                    conn = _sq.connect(_DB_PATH)
                    cur  = conn.cursor()
                    cur.execute(
                        "SELECT id FROM trades WHERE symbol=? AND strategy=? AND paper=? "
                        "AND pnl_usd != 0 AND ts > ? LIMIT 1",
                        (sym, strat, int(PAPER_TRADING), ts_e)
                    )
                    already_closed = cur.fetchone() is not None
                    conn.close()
                except Exception:
                    already_closed = False

                if already_closed:
                    # Position was closed but delete_position never ran (kill window) — clean up
                    delete_position(sym, strat, PAPER_TRADING)
                    cleaned += 1
                    print(f"[RiskManager] Cleaned orphaned position: {sym} ({strat}) — close trade exists")
                    continue

                p = {
                    'qty': pos['qty'], 'entry': pos['entry'],
                    'stop': pos['stop'], 'target': pos['target'],
                    'high_since_entry': pos['high_since_entry'],
                    'ts_entry': ts_e,
                    'direction': pos.get('direction', 'LONG'),
                    'entry_reason': pos.get('entry_reason', ''),
                }
                if 'equity' in strat or 'futures' in strat:
                    self._equity[sym] = p
                elif 'perp' in strat:
                    self._perp[sym] = p
                else:
                    self._crypto[sym] = p
                restored += 1

            if restored or cleaned:
                print(f"[RiskManager] Restored {restored} open positions"
                      + (f", cleaned {cleaned} orphaned" if cleaned else ""))
        except Exception as e:
            print(f"[RiskManager] Position restore error: {e}")

    def _restore_halt_state(self) -> None:
        """On startup, re-apply today's halt if one was triggered and the condition still holds.
        Uses system_events table (halt() already logs there) — survives bot restarts."""
        try:
            import sqlite3
            from config import DB_PATH as _DB
            today = datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d')
            conn = sqlite3.connect(_DB)
            cur  = conn.cursor()
            cur.execute(
                "SELECT message FROM system_events WHERE level='HALT' AND source='RiskManager' "
                "AND ts LIKE ? ORDER BY ts DESC LIMIT 1",
                (f'{today}%',)
            )
            row = cur.fetchone()
            # Also check if a RESUME was logged AFTER the halt
            cur.execute(
                "SELECT id FROM system_events WHERE level='INFO' AND source='RiskManager' "
                "AND message LIKE 'Halt cleared%' AND ts LIKE ? ORDER BY ts DESC LIMIT 1",
                (f'{today}%',)
            )
            resume_row = cur.fetchone()
            conn.close()

            if row and not resume_row:
                # Halt was triggered today and never cleared — re-apply it
                self._halted = True
                self._halt_reason = row[0]
                print(f"[RiskManager] Restored halt state: {self._halt_reason}")
        except Exception as e:
            print(f"[RiskManager] Halt restore error: {e}")

    def check_entry(self, strategy, symbol, side, requested_size_usd,
                    current_price, confidence=0.5) -> RiskCheckResult:

        if self._halted:
            return RiskCheckResult(False, f"System halted: {self._halt_reason}")

        is_eq   = 'equity' in strategy.lower() or 'futures' in strategy.lower()
        is_cr   = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
        is_perp = 'perp' in strategy.lower()
        min_conf = 0.35 if is_eq else 0.30
        if confidence < min_conf:
            return RiskCheckResult(False, f"Confidence {confidence:.0%} < {min_conf:.0%} minimum")

        daily_pnl = get_todays_pnl(paper=PAPER_TRADING)
        all_time = get_all_time_stats(paper=PAPER_TRADING)
        real_balance = ACCOUNT_SIZE + all_time['total_pnl']
        max_loss = real_balance * MAX_DAILY_LOSS_PCT
        if daily_pnl < -max_loss:
            reason = f"Daily loss limit hit: ${daily_pnl:.2f} (max ${max_loss:.2f})"
            self.halt(reason)
            return RiskCheckResult(False, reason)

        if is_eq:
            if not is_market_open():
                return RiskCheckResult(False, "Market closed")
            if is_in_no_trade_window() and side == 'BUY':
                return RiskCheckResult(False, "No trades 9:30–10:00 ET opening window")

        if is_cr:
            fees = get_todays_fees(paper=PAPER_TRADING)
            if fees > ACCOUNT_SIZE * MAX_DAILY_FEE_DRAG_PCT:
                return RiskCheckResult(False, f"Daily fee limit: ${fees:.2f} (max ${ACCOUNT_SIZE*MAX_DAILY_FEE_DRAG_PCT:.2f})")

        if side in ('BUY', 'SELL'):
            if is_eq and len(self._equity) >= MAX_POSITIONS_EQUITY:
                return RiskCheckResult(False, f"Max equity positions ({MAX_POSITIONS_EQUITY}) reached")
            if is_cr and len(self._crypto) >= MAX_POSITIONS_CRYPTO:
                return RiskCheckResult(False, f"Max crypto positions ({MAX_POSITIONS_CRYPTO}) reached")
            if is_perp and len(self._perp) >= PERP_MAX_POSITIONS:
                return RiskCheckResult(False, f"Max perp positions ({PERP_MAX_POSITIONS}) reached")

            existing = self._equity.get(symbol) or self._crypto.get(symbol) or self._perp.get(symbol)
            if existing:
                return RiskCheckResult(False, f"Already holding {symbol} — no double-entry")

            # Crypto correlation filter — block highly correlated pairs from running simultaneously
            if is_cr:
                _CORR_GROUPS = [
                    {'BTC-USDC', 'BTC-USD', 'LTC-USDC', 'BCH-USDC'},           # BTC/UTXO cluster
                    {'ETH-USDC', 'ETH-USD', 'LINK-USDC', 'UNI-USDC',
                     'ARB-USDC', 'OP-USDC', 'INJ-USDC'},                        # ETH ecosystem + DeFi L2s
                    {'SOL-USDC', 'AVAX-USDC', 'ADA-USDC', 'NEAR-USDC',
                     'APT-USDC', 'SUI-USDC', 'DOT-USDC'},                       # Alt-L1 cluster
                    {'PEPE-USDC', 'WIF-USDC', 'DOGE-USDC'},                     # Meme cluster
                    {'XRP-USDC'},                                                # XRP standalone
                ]
                for group in _CORR_GROUPS:
                    if symbol in group:
                        for held in self._crypto:
                            if held in group and held != symbol:
                                return RiskCheckResult(
                                    False,
                                    f"Correlation block: already holding {held} "
                                    f"(same cluster as {symbol} — concentrated risk, no diversification)"
                                )

        if side in ('BUY', 'SELL'):
            count = get_daily_trade_count(strategy, paper=PAPER_TRADING)
            max_t = MAX_TRADES_PER_DAY_EQUITY if is_eq else MAX_TRADES_PER_DAY_CRYPTO
            if count >= max_t:
                return RiskCheckResult(False, f"Max {max_t} trades/day reached ({strategy})")

            # ── Crypto: fee profitability gate ────────────────────────────────
            # Entry is only allowed if the take-profit target clears 2x round-trip fees.
            # Round-trip fee ≈ 2 × taker_fee_pct of position.  Required: profit > 2× that.
            if is_cr and current_price > 0:
                stop_price = self.calc_stop_loss(current_price, strategy)
                tp_price   = self.calc_take_profit(current_price, strategy)
                potential_pct     = (tp_price - current_price) / current_price
                round_trip_fee    = 2 * COINBASE_TAKER_FEE_PCT
                required_pct      = round_trip_fee * CRYPTO_MIN_PROFIT_FEE_MULTIPLE
                if potential_pct < required_pct:
                    return RiskCheckResult(
                        False,
                        f"Fee gate: take-profit only {potential_pct:.2%} away but need "
                        f"{required_pct:.2%} to clear {CRYPTO_MIN_PROFIT_FEE_MULTIPLE:.0f}× fees "
                        f"(stop=${stop_price:,.4f} tp=${tp_price:,.4f})"
                    )

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

        # ── Kelly-dynamic position sizing ─────────────────────────────────────
        # Use 25% fractional Kelly from rolling 50-trade history.
        # Requires 30+ trades for reliable Kelly (10 was too few — ±20% CI).
        # Kelly floor: 50% of base size. Kelly cap: 100%.
        # Losing streak clamp: 3+ consecutive losses → bypass Kelly, use 50% base.
        # This prevents pro-cyclical over-sizing right as a winning regime ends.
        kelly = get_kelly_stats(strategy=strategy, paper=PAPER_TRADING, window=50)

        # Check for recent losing streak (last 5 closed trades — 3 was too hair-trigger)
        losing_streak = False
        try:
            recent = get_kelly_stats(strategy=strategy, paper=PAPER_TRADING, window=5)
            if recent['n_trades'] >= 5 and recent['win_rate'] == 0.0:
                losing_streak = True
                log_event('INFO', 'risk', f"[Kelly] {strategy}/{symbol}: 5-trade losing streak — clamping size to 50%")
        except Exception:
            pass

        if losing_streak:
            size_factor = 0.50  # Clamp only on 5 consecutive losses, not 3
        elif kelly['n_trades'] >= 15 and kelly['kelly_25pct'] > 0:  # was 30 — activates sooner
            # Scale final_size by Kelly fraction
            kelly_factor = min(kelly['kelly_25pct'] / 0.10, 1.0)  # 0.10 = floor reference
            kelly_factor = max(0.50, kelly_factor)  # never below 50%
            size_factor  = kelly_factor
            log_event('INFO', 'risk', f"[Kelly] {strategy}/{symbol}: f*={kelly['kelly_full']:.3f} "
                      f"25%Kelly={kelly['kelly_25pct']:.3f} scale={size_factor:.2f} "
                      f"(p={kelly['win_rate']:.0%} b={kelly['b_ratio']:.2f} n={kelly['n_trades']})")
        else:
            # Fallback: confidence-proportional scaling (60–100%)
            size_factor = max(0.60, min(float(confidence), 1.0))

        final_size = round(final_size * size_factor, 2)
        return RiskCheckResult(True, "All checks passed", adjusted_size=final_size)

    def pre_check_entry(self, strategy: str, symbol: str, side: str,
                        current_price: float, confidence: float = 0.5) -> 'RiskCheckResult':
        """
        Fast pre-flight check before spending API budget on a debate.
        Runs all hard gates that don't require a position size.
        Returns RiskCheckResult — caller should skip debate if not approved.
        """
        if self._halted:
            return RiskCheckResult(False, f"System halted: {self._halt_reason}")

        is_eq   = 'equity' in strategy.lower() or 'futures' in strategy.lower()
        is_cr   = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
        is_perp = 'perp' in strategy.lower()
        min_conf = 0.35 if is_eq else 0.30
        if confidence < min_conf:
            return RiskCheckResult(False, f"Confidence {confidence:.0%} < {min_conf:.0%} minimum")

        daily_pnl = get_todays_pnl(paper=PAPER_TRADING)
        all_time  = get_all_time_stats(paper=PAPER_TRADING)
        real_balance = ACCOUNT_SIZE + all_time['total_pnl']
        max_loss = real_balance * MAX_DAILY_LOSS_PCT
        if daily_pnl < -max_loss:
            reason = f"Daily loss limit hit: ${daily_pnl:.2f} (max ${max_loss:.2f})"
            self.halt(reason)
            return RiskCheckResult(False, reason)

        if is_eq:
            if not is_market_open():
                return RiskCheckResult(False, "Market closed")
            if is_in_no_trade_window() and side == 'BUY':
                return RiskCheckResult(False, "No trades 9:30–10:00 ET opening window")

        if is_cr:
            fees = get_todays_fees(paper=PAPER_TRADING)
            if fees > ACCOUNT_SIZE * MAX_DAILY_FEE_DRAG_PCT:
                return RiskCheckResult(False, f"Daily fee limit: ${fees:.2f}")

        if side in ('BUY', 'SELL'):
            if is_eq and len(self._equity) >= MAX_POSITIONS_EQUITY:
                return RiskCheckResult(False, f"Max equity positions ({MAX_POSITIONS_EQUITY}) reached")
            if is_cr and len(self._crypto) >= MAX_POSITIONS_CRYPTO:
                return RiskCheckResult(False, f"Max crypto positions ({MAX_POSITIONS_CRYPTO}) reached")
            if is_perp and len(self._perp) >= PERP_MAX_POSITIONS:
                return RiskCheckResult(False, f"Max perp positions ({PERP_MAX_POSITIONS}) reached")

            existing = (self._equity.get(symbol) or self._crypto.get(symbol)
                        or self._perp.get(symbol))
            if existing:
                return RiskCheckResult(False, f"Already holding {symbol} — no double-entry")

            count = get_daily_trade_count(strategy, paper=PAPER_TRADING)
            max_t = MAX_TRADES_PER_DAY_EQUITY if is_eq else MAX_TRADES_PER_DAY_CRYPTO
            if count >= max_t:
                return RiskCheckResult(False, f"Max {max_t} trades/day reached ({strategy})")

        deployed = self._get_deployed()
        max_deploy = ACCOUNT_SIZE * MAX_DEPLOYED_PCT
        if deployed >= max_deploy:
            return RiskCheckResult(False, f"Max capital deployed (${deployed:.0f}/${max_deploy:.0f})")

        return RiskCheckResult(True, "Pre-check passed")

    def calc_stop_loss(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        pct = EQUITY_STOP_LOSS_PCT if 'equity' in strategy else CRYPTO_STOP_LOSS_PCT
        if atr > 0:
            pct = min(atr * 2 / entry, pct * 1.5)
        return entry * (1 - pct)

    def calc_take_profit(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        stop = self.calc_stop_loss(entry, strategy, atr)
        # Equity: 1:3 R/R — if stop is $5 away, target is $15 away
        # Crypto: 1:2 R/R — scalping targets are closer, 1:3 would almost never be hit
        rr = 3.0 if ('equity' in strategy and 'crypto' not in strategy) else 2.0
        return entry + (entry - stop) * rr

    def register_position(self, strategy, symbol, qty, entry, stop, target,
                          direction='LONG', entry_reason='') -> None:
        tz = pytz.timezone(MARKET_TIMEZONE)
        ts = datetime.now(tz).isoformat()
        pos = {
            'qty': qty, 'entry': entry, 'stop': stop, 'target': target,
            'high_since_entry': entry, 'ts_entry': ts, 'direction': direction,
            'entry_reason': entry_reason[:200] if entry_reason else '',
        }
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            self._equity[symbol] = pos
        elif 'perp' in strategy.lower():
            self._perp[symbol] = pos
        else:
            self._crypto[symbol] = pos
        persist_position(symbol, strategy, qty, entry, stop, target, entry, ts,
                         PAPER_TRADING, direction=direction, entry_reason=entry_reason)

    def close_position(self, strategy, symbol) -> Optional[dict]:
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            pos = self._equity.pop(symbol, None)
        elif 'perp' in strategy.lower():
            pos = self._perp.pop(symbol, None)
        else:
            pos = self._crypto.pop(symbol, None)
        delete_position(symbol, strategy, PAPER_TRADING)
        return pos

    def update_high(self, strategy, symbol, price) -> None:
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            d = self._equity
        elif 'perp' in strategy.lower():
            d = self._perp
        else:
            d = self._crypto
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
                                 direction=direction,
                                 entry_reason=d[symbol].get('entry_reason', ''))

    def get_position(self, strategy, symbol) -> Optional[dict]:
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            return self._equity.get(symbol)
        if 'perp' in strategy.lower():
            return self._perp.get(symbol)
        return self._crypto.get(symbol)

    def get_all_positions(self) -> dict:
        return {
            'equity': dict(self._equity),
            'crypto': dict(self._crypto),
            'perp':   dict(self._perp),
        }

    def should_exit(self, strategy, symbol, current_price) -> tuple:
        pos = self.get_position(strategy, symbol)
        if not pos:
            return False, ''
        direction = pos.get('direction', 'LONG')
        trail_pct = 0.07 if 'equity' in strategy else 0.04

        # Entry buffer before trailing kicks in:
        # Crypto: 0.5% — fast-moving, don't need price to run 3% before protecting profit
        # Equity: 2.0% — daily candles, needs room to breathe
        entry_buffer = 1.005 if 'crypto' in strategy or 'mean_reversion' in strategy else 1.02

        if direction == 'LONG':
            if current_price <= pos['stop']:
                return True, f"Hard stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
            if current_price >= pos['target']:
                return True, f"Take profit hit ${current_price:.4f} (target: ${pos['target']:.4f})"
            trailing = pos['high_since_entry'] * (1 - trail_pct)
            if current_price > pos['entry'] * entry_buffer and current_price <= trailing:
                return True, f"Trailing stop triggered ${current_price:.4f}"
        else:  # SHORT
            if current_price >= pos['stop']:
                return True, f"Short stop hit ${current_price:.4f} (stop: ${pos['stop']:.4f})"
            if current_price <= pos['target']:
                return True, f"Short target hit ${current_price:.4f} (target: ${pos['target']:.4f})"
            # Trailing: exit if price rises above lowest_point * (1 + trail_pct)
            trailing = pos['high_since_entry'] * (1 + trail_pct)
            short_buffer = 2 - entry_buffer  # mirror: 0.995 for crypto, 0.98 for equity
            if current_price < pos['entry'] * short_buffer and current_price >= trailing:
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
        # Perp: track margin deployed (notional / leverage), not full notional
        for p in self._perp.values():
            notional = p.get('qty', 0) * p.get('entry', 0)
            lev = p.get('leverage', 1) or 1
            total += notional / lev
        return total

    def status_report(self) -> dict:
        return {
            'halted': self._halted,
            'halt_reason': self._halt_reason,
            'open_equity': len(self._equity),
            'open_crypto': len(self._crypto),
            'open_perp': len(self._perp),
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
