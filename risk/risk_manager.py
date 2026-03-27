"""
risk/risk_manager.py — Thin orchestrator. The amygdala removal layer.
Delegates to focused sub-modules (Sprint 1, Task 3 refactor):
  position_sizer.py   — Kelly sizing
  stop_loss_manager.py — stop/target/trailing math
  drawdown_controller.py — daily loss + fee-drag halts
  var_calculator.py   — VaR 95%/99%
  risk_limits.py      — position limits, correlation, deployment cap, fee gate

Public API is unchanged — nothing else in the codebase needs to change.
Positions persisted to SQLite on every write. System restart never loses position state.
"""
import os
import sys
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ACCOUNT_SIZE, MARKET_TIMEZONE, PAPER_TRADING, MAX_DEPLOYED_PCT

from logging_db.trade_logger import (
    log_event, persist_position, delete_position, load_open_positions,
    get_todays_pnl, get_todays_fees,
)
from risk.position_sizer import size_from_kelly
from risk.stop_loss_manager import calc_stop_loss, calc_take_profit, should_exit
from risk.drawdown_controller import check_daily_loss, check_fee_drag, get_heat_level
from risk.risk_limits import (
    RiskCheckResult, check_market_hours, check_position_limits,
    check_deployment_cap, check_crypto_fee_gate,
)


class RiskManager:
    def __init__(self):
        self._halted: bool = False
        self._halt_reason: str = ''
        self._equity: dict = {}
        self._crypto: dict = {}
        self._perp:   dict = {}
        self._last_scan_ts: float = 0.0
        self._restore_positions()
        self._restore_halt_state()

    # ── Startup state restoration ─────────────────────────────────────────────

    def _restore_positions(self) -> None:
        """Restore open positions from SQLite on startup.

        Validates each against the trades table: if a close event already exists
        after ts_entry the bot was killed between log_trade and delete_position —
        clean up the orphan rather than restoring it.
        """
        try:
            import sqlite3 as _sq
            from config import DB_PATH as _DB_PATH

            positions = load_open_positions(paper=PAPER_TRADING)
            restored = cleaned = 0
            for pos in positions:
                sym   = pos['symbol']
                strat = pos['strategy']
                ts_e  = pos.get('ts_entry', '1970-01-01')

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
                    delete_position(sym, strat, PAPER_TRADING)
                    cleaned += 1
                    print(f"[RiskManager] Cleaned orphaned position: {sym} ({strat})")
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
        """Re-apply today's halt on startup if condition still holds."""
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
            cur.execute(
                "SELECT id FROM system_events WHERE level='INFO' AND source='RiskManager' "
                "AND message LIKE 'Halt cleared%' AND ts LIKE ? ORDER BY ts DESC LIMIT 1",
                (f'{today}%',)
            )
            resume_row = cur.fetchone()
            conn.close()

            if row and not resume_row:
                self._halted = True
                self._halt_reason = row[0]
                print(f"[RiskManager] Restored halt state: {self._halt_reason}")
        except Exception as e:
            print(f"[RiskManager] Halt restore error: {e}")

    # ── Entry checks ──────────────────────────────────────────────────────────

    def check_entry(self, strategy, symbol, side, requested_size_usd,
                    current_price, confidence=0.5) -> RiskCheckResult:
        """Full pre-trade check including Kelly-adjusted position sizing."""
        if self._halted:
            return RiskCheckResult(False, f"System halted: {self._halt_reason}")

        is_cr = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
        min_conf = 0.35 if ('equity' in strategy.lower() or 'futures' in strategy.lower()) else 0.30
        if confidence < min_conf:
            return RiskCheckResult(False, f"Confidence {confidence:.0%} < {min_conf:.0%} minimum")

        # Daily loss halt
        ok, reason = check_daily_loss(paper=PAPER_TRADING)
        if not ok:
            self.halt(reason)
            return RiskCheckResult(False, reason)

        # Market hours (equity only)
        result = check_market_hours(strategy, side)
        if not result:
            return result

        # Fee drag (crypto only)
        if is_cr:
            ok, reason = check_fee_drag(paper=PAPER_TRADING)
            if not ok:
                return RiskCheckResult(False, reason)

        # Position limits, correlation, daily trade count
        result = check_position_limits(
            strategy, symbol, side,
            self._equity, self._crypto, self._perp, PAPER_TRADING
        )
        if not result:
            return result

        # Crypto fee gate
        if is_cr and current_price > 0:
            stop_p = self.calc_stop_loss(current_price, strategy)
            tp_p   = self.calc_take_profit(current_price, strategy)
            result = check_crypto_fee_gate(strategy, current_price, stop_p, tp_p)
            if not result:
                return result

        # Deployment cap
        result = check_deployment_cap(requested_size_usd, self._get_deployed())
        if not result:
            return result

        final_size = result.adjusted_size

        # Kelly sizing
        final_size = size_from_kelly(strategy, symbol, final_size, confidence, PAPER_TRADING)
        return RiskCheckResult(True, "All checks passed", adjusted_size=final_size)

    def pre_check_entry(self, strategy: str, symbol: str, side: str,
                        current_price: float, confidence: float = 0.5) -> RiskCheckResult:
        """Fast pre-flight check before spending API budget on a debate.
        Runs all hard gates that don't require a position size."""
        if self._halted:
            return RiskCheckResult(False, f"System halted: {self._halt_reason}")

        is_cr = 'crypto' in strategy.lower() and 'perp' not in strategy.lower()
        min_conf = 0.35 if ('equity' in strategy.lower() or 'futures' in strategy.lower()) else 0.30
        if confidence < min_conf:
            return RiskCheckResult(False, f"Confidence {confidence:.0%} < {min_conf:.0%} minimum")

        ok, reason = check_daily_loss(paper=PAPER_TRADING)
        if not ok:
            self.halt(reason)
            return RiskCheckResult(False, reason)

        # Log heat level on every entry attempt (visible in scan feed)
        heat = get_heat_level(paper=PAPER_TRADING)
        if heat['level'] > 0:
            log_event('INFO', 'risk',
                      f"[Heat:{heat['label']}] day={heat['daily_pnl']:+.2f} "
                      f"({heat['pct_drawn']:.1%} drawn) → size×{heat['size_factor']:.2f}")

        result = check_market_hours(strategy, side)
        if not result:
            return result

        if is_cr:
            ok, reason = check_fee_drag(paper=PAPER_TRADING)
            if not ok:
                return RiskCheckResult(False, reason)

        result = check_position_limits(
            strategy, symbol, side,
            self._equity, self._crypto, self._perp, PAPER_TRADING
        )
        if not result:
            return result

        deployed = self._get_deployed()
        max_deploy = ACCOUNT_SIZE * MAX_DEPLOYED_PCT
        if deployed >= max_deploy:
            return RiskCheckResult(False, f"Max capital deployed (${deployed:.0f}/${max_deploy:.0f})")

        return RiskCheckResult(True, "Pre-check passed")

    # ── Price math (delegates to stop_loss_manager) ───────────────────────────

    def calc_stop_loss(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        return calc_stop_loss(entry, strategy, atr)

    def calc_take_profit(self, entry: float, strategy: str, atr: float = 0.0) -> float:
        return calc_take_profit(entry, strategy, atr)

    # ── Position management ───────────────────────────────────────────────────

    def register_position(self, strategy, symbol, qty, entry, stop, target,
                          direction='LONG', entry_reason='') -> None:
        tz = pytz.timezone(MARKET_TIMEZONE)
        ts = datetime.now(tz).isoformat()
        pos = {
            'qty': qty, 'entry': entry, 'stop': stop, 'target': target,
            'high_since_entry': entry, 'ts_entry': ts, 'direction': direction,
            'entry_reason': entry_reason[:200] if entry_reason else '',
            'strategy': strategy,
        }
        if 'equity' in strategy.lower() or 'futures' in strategy.lower():
            self._equity[symbol] = pos
        elif 'perp' in strategy.lower():
            self._perp[symbol] = pos
        else:
            self._crypto[symbol] = pos
        persist_position(symbol, strategy, qty, entry, stop, target, entry, ts,
                         PAPER_TRADING, direction=direction, entry_reason=entry_reason)

    def close_position(self, strategy, symbol, exit_reason: str = '') -> Optional[dict]:
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
        return should_exit(pos, strategy, current_price)

    # ── Halt / resume ─────────────────────────────────────────────────────────

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

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def ping(self) -> None:
        import time
        self._last_scan_ts = time.time()

    def watchdog_ok(self, max_gap_seconds: int = 900) -> bool:
        import time
        if self._last_scan_ts == 0:
            return True
        return (time.time() - self._last_scan_ts) < max_gap_seconds

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_deployed(self) -> float:
        total = 0.0
        for p in self._equity.values():
            total += p.get('qty', 0) * p.get('entry', 0)
        for p in self._crypto.values():
            total += p.get('qty', 0) * p.get('entry', 0)
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
