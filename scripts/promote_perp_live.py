#!/usr/bin/env python3
"""
scripts/promote_perp_live.py — Binance perp paper-to-live promotion checklist.

Run this before flipping BINANCE_TESTNET=false.
All 8 checks must pass. Any failure aborts with clear instructions.

Usage: python3 scripts/promote_perp_live.py
"""
import os
import sys
import sqlite3

# ── Resolve project root regardless of where script is called from ────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT  = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJ_ROOT)

DB_PATH = os.path.join(PROJ_ROOT, 'logs', 'trades.db')

MIN_PERP_TRADES  = 10     # minimum completed perp positions
MIN_PERP_WIN_RATE = 0.45  # 45% win rate on perp strategy

PASS = "[PASS]"
FAIL = "[FAIL]"


def _conn():
    if not os.path.exists(DB_PATH):
        return None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ── Individual checks ─────────────────────────────────────────────────────────

def check_api_keys() -> dict:
    """Check 1: BINANCE_API_KEY and BINANCE_API_SECRET are non-empty in .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(PROJ_ROOT, '.env'))
    except Exception:
        pass

    key    = os.getenv('BINANCE_API_KEY', '').strip()
    secret = os.getenv('BINANCE_API_SECRET', '').strip()

    if not key:
        return {'passed': False, 'detail': 'BINANCE_API_KEY is not set in .env'}
    if not secret:
        return {'passed': False, 'detail': 'BINANCE_API_SECRET is not set in .env'}
    if key.lower() in ('your_key_here', 'changeme', 'placeholder'):
        return {'passed': False, 'detail': 'BINANCE_API_KEY appears to be a placeholder'}
    return {'passed': True, 'detail': f'API key set ({key[:6]}...)'}


def check_testnet_connection() -> dict:
    """Check 2: Can connect to Binance testnet (BINANCE_TESTNET=true)."""
    try:
        from execution.binance_broker import get_binance_broker
        bb = get_binance_broker()
        connected = bb.is_connected()
        if not connected:
            connected = bb.connect()
        if connected:
            return {'passed': True, 'detail': 'Binance testnet connection OK'}
        return {'passed': False, 'detail': 'Binance broker connect() returned False — check testnet keys'}
    except Exception as e:
        return {'passed': False, 'detail': f'Connection error: {e}'}


def check_paper_trade_count() -> dict:
    """Check 3: At least 10 completed perp trades in SQLite."""
    conn = _conn()
    if conn is None:
        return {'passed': False, 'detail': f'Database not found at {DB_PATH}'}
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades "
            "WHERE strategy = 'crypto_perp' AND action IN ('SELL', 'CLOSE')"
        ).fetchone()
        count = row['cnt'] if row else 0
        if count >= MIN_PERP_TRADES:
            return {'passed': True, 'detail': f'{count} completed perp trades found'}
        return {
            'passed': False,
            'detail': f'Only {count} completed perp trades — need at least {MIN_PERP_TRADES}',
        }
    except Exception as e:
        return {'passed': False, 'detail': f'DB query error: {e}'}
    finally:
        conn.close()


def check_paper_win_rate() -> dict:
    """Check 4: Perp strategy win rate >= 45% from trades.db."""
    conn = _conn()
    if conn is None:
        return {'passed': False, 'detail': f'Database not found at {DB_PATH}'}
    try:
        rows = conn.execute(
            "SELECT pnl_usd FROM trades "
            "WHERE strategy = 'crypto_perp' AND action IN ('SELL', 'CLOSE')"
        ).fetchall()
        if not rows:
            return {'passed': False, 'detail': 'No completed perp trades to evaluate'}
        total  = len(rows)
        wins   = sum(1 for r in rows if r['pnl_usd'] is not None and r['pnl_usd'] > 0)
        wr     = wins / total
        if wr >= MIN_PERP_WIN_RATE:
            return {'passed': True, 'detail': f'Win rate {wr:.1%} ({wins}/{total}) — threshold {MIN_PERP_WIN_RATE:.0%}'}
        return {
            'passed': False,
            'detail': f'Win rate {wr:.1%} ({wins}/{total}) below threshold {MIN_PERP_WIN_RATE:.0%}',
        }
    except Exception as e:
        return {'passed': False, 'detail': f'DB query error: {e}'}
    finally:
        conn.close()


def check_risk_limits_configured() -> dict:
    """Check 5: PERP_MAX_POSITIONS, PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT are set in config."""
    try:
        from config import PERP_MAX_POSITIONS, PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT
        issues = []
        if PERP_MAX_POSITIONS <= 0:
            issues.append(f'PERP_MAX_POSITIONS={PERP_MAX_POSITIONS} (must be > 0)')
        if PERP_STOP_PCT <= 0:
            issues.append(f'PERP_STOP_PCT={PERP_STOP_PCT} (must be > 0)')
        if PERP_TAKE_PROFIT_PCT <= 0:
            issues.append(f'PERP_TAKE_PROFIT_PCT={PERP_TAKE_PROFIT_PCT} (must be > 0)')
        if issues:
            return {'passed': False, 'detail': '; '.join(issues)}
        return {
            'passed': True,
            'detail': (
                f'PERP_MAX_POSITIONS={PERP_MAX_POSITIONS} | '
                f'stop={PERP_STOP_PCT:.1%} | target={PERP_TAKE_PROFIT_PCT:.1%}'
            ),
        }
    except ImportError as e:
        return {'passed': False, 'detail': f'config import failed: {e}'}


def check_no_active_halt() -> dict:
    """Check 6: RiskManager is not currently halted."""
    try:
        from risk.risk_manager import get_risk_manager
        rm = get_risk_manager()
        if rm.is_halted:
            return {
                'passed': False,
                'detail': f'RiskManager is HALTED: {rm.halt_reason}. Resume before going live.',
            }
        return {'passed': True, 'detail': 'RiskManager is active (not halted)'}
    except Exception as e:
        return {'passed': False, 'detail': f'Could not load RiskManager: {e}'}


def check_fee_viability() -> dict:
    """Check 7: Expected round-trip fee < 5% of expected profit.

    Uses PERP_POSITION_SIZE_USD * 2 * 0.04% for round-trip Binance taker fee,
    compared against position_size * PERP_TAKE_PROFIT_PCT as the expected profit.
    """
    try:
        from config import PERP_POSITION_SIZE_USD, PERP_TAKE_PROFIT_PCT
        BINANCE_TAKER_FEE = 0.0004  # 0.04% per leg
        round_trip_fee_pct = 2 * BINANCE_TAKER_FEE  # entry + exit
        fee_as_pct_of_target = round_trip_fee_pct / PERP_TAKE_PROFIT_PCT

        if fee_as_pct_of_target < 0.05:
            return {
                'passed': True,
                'detail': (
                    f'Fee ({round_trip_fee_pct:.3%} round-trip) is '
                    f'{fee_as_pct_of_target:.1%} of target profit '
                    f'({PERP_TAKE_PROFIT_PCT:.1%}) — well within 5% threshold'
                ),
            }
        return {
            'passed': False,
            'detail': (
                f'Fee ({round_trip_fee_pct:.3%} round-trip) is '
                f'{fee_as_pct_of_target:.1%} of target profit '
                f'({PERP_TAKE_PROFIT_PCT:.1%}) — exceeds 5% threshold. '
                f'Raise PERP_TAKE_PROFIT_PCT or reduce leverage.'
            ),
        }
    except Exception as e:
        return {'passed': False, 'detail': f'Could not evaluate fees: {e}'}


def check_manual_confirmation() -> dict:
    """Check 8: User types CONFIRM to proceed."""
    print()
    print("=" * 60)
    print("  MANUAL CONFIRMATION REQUIRED")
    print("  You are about to enable LIVE perp trading on Binance.")
    print("  Real money will be at risk. There is no undo.")
    print("=" * 60)
    try:
        answer = input("\n  Type CONFIRM to proceed to live trading: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return {'passed': False, 'detail': 'Confirmation interrupted — aborted'}
    if answer == 'CONFIRM':
        return {'passed': True, 'detail': 'Manual confirmation received'}
    return {'passed': False, 'detail': f'Expected "CONFIRM", got "{answer}" — aborted'}


# ── Main runner ───────────────────────────────────────────────────────────────

CHECKS = [
    ('API keys configured',        check_api_keys),
    ('Testnet connection',          check_testnet_connection),
    ('Paper trade count >= 10',     check_paper_trade_count),
    ('Paper win rate >= 45%',       check_paper_win_rate),
    ('Risk limits configured',      check_risk_limits_configured),
    ('No active halt',             check_no_active_halt),
    ('Fee viability',              check_fee_viability),
    ('Manual confirmation',        check_manual_confirmation),
]


def main() -> None:
    print()
    print("=" * 60)
    print("  promote_perp_live.py — Binance Perp Paper-to-Live Checklist")
    print("=" * 60)
    print()

    results = []
    for i, (name, fn) in enumerate(CHECKS, start=1):
        # Skip manual confirmation if any earlier check failed
        if name == 'Manual confirmation' and any(not r['passed'] for r in results):
            print(f"  {i:>2}. {name:<35} -- SKIPPED (earlier check failed)")
            results.append({'passed': False, 'detail': 'Skipped — earlier check failed'})
            continue

        result = fn()
        status = PASS if result['passed'] else FAIL
        print(f"  {i:>2}. {name:<35} {status}  {result['detail']}")
        results.append(result)

        # Stop immediately on a hard failure (not manual conf — that waits for all)
        if not result['passed'] and name != 'Manual confirmation':
            print()
            print(f"  Stopped at check {i}: {name}")
            print(f"  Reason: {result['detail']}")
            print()
            print("  Fix the issue above and re-run this script.")
            print()
            sys.exit(1)

    all_passed = all(r['passed'] for r in results)
    print()

    if all_passed:
        print("=" * 60)
        print("  All checks passed. To go live:")
        print()
        print("  1. Set BINANCE_TESTNET=false in .env")
        print("  2. Verify PERP_PAPER=false in .env (if that flag exists)")
        print("  3. Restart the bot:")
        print("       pkill -f main.py && python3 main.py")
        print()
        print("  The bot will immediately begin using live Binance perp.")
        print("  Monitor logs/bot.log closely for the first 30 minutes.")
        print("=" * 60)
    else:
        # This branch is reached only if manual confirmation failed
        failed = [(i + 1, CHECKS[i][0], results[i]['detail'])
                  for i in range(len(results)) if not results[i]['passed']]
        print("=" * 60)
        print("  Promotion ABORTED. The following checks did not pass:")
        for num, name, detail in failed:
            print(f"    {num}. {name}: {detail}")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
