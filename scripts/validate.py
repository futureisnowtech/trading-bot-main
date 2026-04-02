"""
scripts/validate.py — Pre-flight system validator.
Run before starting the bot to catch config issues, missing keys, and broken imports.
Also run as a git pre-commit hook via scripts/install_hooks.sh.

Exit 0 = all checks pass. Exit 1 = critical failure (bot should not start).
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS  = "✅"
WARN  = "⚠️ "
FAIL  = "❌"
_errors   = []
_warnings = []


def ok(msg):   print(f"  {PASS} {msg}")
def warn(msg): print(f"  {WARN} {msg}"); _warnings.append(msg)
def fail(msg): print(f"  {FAIL} {msg}"); _errors.append(msg)


# ─────────────────────────────────────────────────────────────
# 1. ENVIRONMENT / .env
# ─────────────────────────────────────────────────────────────
print("\n─── Environment ────────────────────────────────────────")

try:
    from dotenv import load_dotenv
    load_dotenv()
    ok("dotenv loaded")
except ImportError:
    fail("python-dotenv not installed — run: pip install python-dotenv")

required_keys = ['ANTHROPIC_API_KEY', 'PAPER_TRADING', 'ACCOUNT_SIZE']
optional_keys = [
    'COINBASE_API_KEY', 'COINBASE_API_SECRET',
    'BINANCE_API_KEY', 'BINANCE_API_SECRET',
    'TRADOVATE_USERNAME', 'TRADOVATE_PASSWORD',
    'CRYPTO_PAIRS', 'PERP_PAIRS',
]

for k in required_keys:
    v = os.getenv(k, '')
    if v:
        ok(f"{k} set")
    else:
        fail(f"{k} missing from .env")

for k in optional_keys:
    v = os.getenv(k, '')
    if v:
        ok(f"{k} set")
    else:
        warn(f"{k} not set — related features will be disabled")


# ─────────────────────────────────────────────────────────────
# 2. CONFIG CONSISTENCY
# ─────────────────────────────────────────────────────────────
print("\n─── Config consistency ─────────────────────────────────")

try:
    import config as cfg

    # Stop < take profit
    if cfg.CRYPTO_STOP_LOSS_PCT >= cfg.CRYPTO_TAKE_PROFIT_PCT:
        fail(f"CRYPTO_STOP_LOSS_PCT ({cfg.CRYPTO_STOP_LOSS_PCT}) >= CRYPTO_TAKE_PROFIT_PCT ({cfg.CRYPTO_TAKE_PROFIT_PCT})")
    else:
        rr = cfg.CRYPTO_TAKE_PROFIT_PCT / cfg.CRYPTO_STOP_LOSS_PCT
        ok(f"Crypto R:R = {rr:.1f}:1 (stop {cfg.CRYPTO_STOP_LOSS_PCT:.1%} / target {cfg.CRYPTO_TAKE_PROFIT_PCT:.1%})")

    if cfg.EQUITY_STOP_LOSS_PCT >= cfg.EQUITY_TAKE_PROFIT_PCT:
        fail(f"EQUITY_STOP_LOSS_PCT ({cfg.EQUITY_STOP_LOSS_PCT}) >= EQUITY_TAKE_PROFIT_PCT ({cfg.EQUITY_TAKE_PROFIT_PCT})")
    else:
        rr = cfg.EQUITY_TAKE_PROFIT_PCT / cfg.EQUITY_STOP_LOSS_PCT
        ok(f"Equity R:R = {rr:.1f}:1 (stop {cfg.EQUITY_STOP_LOSS_PCT:.1%} / target {cfg.EQUITY_TAKE_PROFIT_PCT:.1%})")

    # Fee floor sanity
    round_trip_fee = cfg.COINBASE_TAKER_FEE_PCT * 2
    min_atr_target = cfg.ATR_TARGET_MULTIPLIER * cfg.ATR_FEE_FLOOR_PCT
    if min_atr_target < round_trip_fee:
        warn(f"ATR_FEE_FLOOR guard ({min_atr_target:.2%}) < round-trip fee ({round_trip_fee:.2%}) — some trades may not cover fees")
    else:
        ok(f"ATR fee floor clears round-trip cost ({min_atr_target:.2%} vs {round_trip_fee:.2%})")

    # Account size
    if cfg.ACCOUNT_SIZE < 100:
        fail(f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE} — too small to trade safely")
    elif cfg.ACCOUNT_SIZE < 1000:
        warn(f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE} — small account, fee drag is high per trade")
    else:
        ok(f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE:,.0f}")

    # Position size vs account
    crypto_pct = cfg.CRYPTO_POSITION_SIZE_USD / cfg.ACCOUNT_SIZE
    equity_pct = cfg.EQUITY_POSITION_SIZE_USD / cfg.ACCOUNT_SIZE
    if crypto_pct > 0.30:
        warn(f"Crypto position ${cfg.CRYPTO_POSITION_SIZE_USD} = {crypto_pct:.0%} of account — consider reducing")
    else:
        ok(f"Crypto position ${cfg.CRYPTO_POSITION_SIZE_USD} = {crypto_pct:.0%} of account")

    if equity_pct > 0.30:
        warn(f"Equity position ${cfg.EQUITY_POSITION_SIZE_USD} = {equity_pct:.0%} of account — consider reducing")
    else:
        ok(f"Equity position ${cfg.EQUITY_POSITION_SIZE_USD} = {equity_pct:.0%} of account")

    # Max deployed check
    max_open_usd = cfg.MAX_POSITIONS_CRYPTO * cfg.CRYPTO_POSITION_SIZE_USD
    if max_open_usd > cfg.ACCOUNT_SIZE * cfg.MAX_DEPLOYED_PCT:
        warn(f"Max deployed crypto ({max_open_usd:.0f}) > {cfg.MAX_DEPLOYED_PCT:.0%} of account "
             f"(${cfg.ACCOUNT_SIZE * cfg.MAX_DEPLOYED_PCT:.0f}) — positions may be capped by risk manager")
    else:
        ok(f"Max open crypto: ${max_open_usd:.0f} fits within {cfg.MAX_DEPLOYED_PCT:.0%} deployment cap")

    # Squeeze min bars
    if cfg.SQUEEZE_MIN_BARS < 10:
        warn(f"SQUEEZE_MIN_BARS={cfg.SQUEEZE_MIN_BARS} — deep research recommends ≥20 bars for reliable squeeze signals")
    else:
        ok(f"SQUEEZE_MIN_BARS={cfg.SQUEEZE_MIN_BARS} (≥20 per deep research)")

    # Agent agreement
    n_agents = len(cfg.FULL_DEBATE_AGENTS)
    min_agree = int(cfg.FULL_DEBATE_MIN_AGREEMENT * n_agents)
    ok(f"Full debate: {n_agents} agents, min agreement: {min_agree} (config={cfg.FULL_DEBATE_MIN_AGREEMENT:.0%})")

    ok("Config loaded and consistent")
except Exception as e:
    fail(f"Config failed to load: {e}")


# ─────────────────────────────────────────────────────────────
# 3. IMPORT CHECKS
# ─────────────────────────────────────────────────────────────
print("\n─── Critical imports ───────────────────────────────────")

critical_imports = [
    ('anthropic or urllib (API calls)', 'urllib.request', None),
    ('pandas', 'pandas', None),
    ('numpy', 'numpy', None),
    ('schedule', 'schedule', None),
    ('dotenv', 'dotenv', None),
    ('pytz', 'pytz', None),
    ('sqlite3', 'sqlite3', None),
    ('data.indicators', 'data.indicators', None),
    ('risk.risk_manager', 'risk.risk_manager', None),
    ('logging_db.trade_logger', 'logging_db.trade_logger', None),
]

optional_imports = [
    ('pandas_ta', 'pandas_ta', 'technical indicators will use fallbacks'),
    ('lancedb', 'lancedb', 'trade memory disabled — no LanceDB'),
    ('sentence_transformers', 'sentence_transformers', 'trade memory embeddings disabled'),
    ('yfinance', 'yfinance', 'market data fetches will fail'),
    ('streamlit', 'streamlit', 'dashboard will not start'),
]

for label, module, _ in critical_imports:
    try:
        importlib.import_module(module)
        ok(label)
    except ImportError as e:
        fail(f"{label}: {e}")
    except Exception as e:
        warn(f"{label} imported but has error: {e}")

for label, module, consequence in optional_imports:
    try:
        importlib.import_module(module)
        ok(f"{label} (optional)")
    except ImportError:
        warn(f"{label} not installed — {consequence}")


# ─────────────────────────────────────────────────────────────
# 4. DATABASE
# ─────────────────────────────────────────────────────────────
print("\n─── Database ───────────────────────────────────────────")

try:
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db')
    logs_dir = os.path.dirname(db_path)

    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir, exist_ok=True)
        warn(f"logs/ directory created — run setup.py first for full initialization")
    else:
        ok("logs/ directory exists")

    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA integrity_check")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        ok(f"trades.db reachable and WAL mode set")
    else:
        warn("trades.db not found — will be created on first run (run setup.py first)")
except Exception as e:
    fail(f"Database check failed: {e}")


# ─────────────────────────────────────────────────────────────
# 5. VERSION CONSISTENCY
# ─────────────────────────────────────────────────────────────
print("\n─── Version ────────────────────────────────────────────")

try:
    claude_md = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'CLAUDE.md')
    if os.path.exists(claude_md):
        with open(claude_md) as f:
            for line in f:
                if 'Current Version:' in line:
                    version = line.strip().split(':')[-1].strip()
                    ok(f"System version: {version}")
                    break
    else:
        warn("CLAUDE.md not found")
except Exception as e:
    warn(f"Version check skipped: {e}")


# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n─── Summary ────────────────────────────────────────────")
if _errors:
    print(f"\n  {FAIL} {len(_errors)} critical error(s):")
    for e in _errors:
        print(f"     • {e}")
    print(f"\n  {WARN} Fix these before starting the bot.\n")
    sys.exit(1)
elif _warnings:
    print(f"\n  {WARN} {len(_warnings)} warning(s) — bot can start but review these:")
    for w in _warnings:
        print(f"     • {w}")
    print(f"\n  {PASS} No critical errors. System is startable.\n")
    sys.exit(0)
else:
    print(f"\n  {PASS} All checks passed. System is ready.\n")
    sys.exit(0)


if __name__ == '__main__':
    pass
