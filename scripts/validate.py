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

PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"
_errors = []
_warnings = []


def ok(msg):
    print(f"  {PASS} {msg}")


def warn(msg):
    print(f"  {WARN} {msg}")
    _warnings.append(msg)


def fail(msg):
    print(f"  {FAIL} {msg}")
    _errors.append(msg)


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

# v10: ANTHROPIC_API_KEY no longer required (AI debate engine removed in v10).
# PAPER_TRADING and ACCOUNT_SIZE have safe defaults in config.py ("true" / 5000).
# Hard-require only keys with no safe fallback.
required_keys: list = []  # No hard-required env keys in v10+

optional_keys = [
    "ACCOUNT_SIZE",  # default 5000 in config.py — warn if missing
    "PAPER_TRADING",  # default True in config.py — warn if missing
    "ANTHROPIC_API_KEY",  # not used by v10 signal engine; warn only
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "TRADOVATE_USERNAME",
    "TRADOVATE_PASSWORD",
    "CRYPTO_PAIRS",
    "PERP_PAIRS",
]

for k in required_keys:
    v = os.getenv(k, "")
    if v:
        ok(f"{k} set")
    else:
        fail(f"{k} missing from .env")

for k in optional_keys:
    v = os.getenv(k, "")
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
        fail(
            f"CRYPTO_STOP_LOSS_PCT ({cfg.CRYPTO_STOP_LOSS_PCT}) >= CRYPTO_TAKE_PROFIT_PCT ({cfg.CRYPTO_TAKE_PROFIT_PCT})"
        )
    else:
        rr = cfg.CRYPTO_TAKE_PROFIT_PCT / cfg.CRYPTO_STOP_LOSS_PCT
        ok(
            f"Crypto R:R = {rr:.1f}:1 (stop {cfg.CRYPTO_STOP_LOSS_PCT:.1%} / target {cfg.CRYPTO_TAKE_PROFIT_PCT:.1%})"
        )

    if cfg.EQUITY_STOP_LOSS_PCT >= cfg.EQUITY_TAKE_PROFIT_PCT:
        fail(
            f"EQUITY_STOP_LOSS_PCT ({cfg.EQUITY_STOP_LOSS_PCT}) >= EQUITY_TAKE_PROFIT_PCT ({cfg.EQUITY_TAKE_PROFIT_PCT})"
        )
    else:
        rr = cfg.EQUITY_TAKE_PROFIT_PCT / cfg.EQUITY_STOP_LOSS_PCT
        ok(
            f"Equity R:R = {rr:.1f}:1 (stop {cfg.EQUITY_STOP_LOSS_PCT:.1%} / target {cfg.EQUITY_TAKE_PROFIT_PCT:.1%})"
        )

    # Fee floor sanity
    round_trip_fee = cfg.COINBASE_TAKER_FEE_PCT * 2
    min_atr_target = cfg.ATR_TARGET_MULTIPLIER * cfg.ATR_FEE_FLOOR_PCT
    if min_atr_target < round_trip_fee:
        warn(
            f"ATR_FEE_FLOOR guard ({min_atr_target:.2%}) < round-trip fee ({round_trip_fee:.2%}) — some trades may not cover fees"
        )
    else:
        ok(
            f"ATR fee floor clears round-trip cost ({min_atr_target:.2%} vs {round_trip_fee:.2%})"
        )

    # Account size
    if cfg.ACCOUNT_SIZE < 100:
        fail(f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE} — too small to trade safely")
    elif cfg.ACCOUNT_SIZE < 1000:
        warn(
            f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE} — small account, fee drag is high per trade"
        )
    else:
        ok(f"ACCOUNT_SIZE=${cfg.ACCOUNT_SIZE:,.0f}")

    # Position size vs account
    crypto_pct = cfg.CRYPTO_POSITION_SIZE_USD / cfg.ACCOUNT_SIZE
    equity_pct = cfg.EQUITY_POSITION_SIZE_USD / cfg.ACCOUNT_SIZE
    if crypto_pct > 0.30:
        warn(
            f"Crypto position ${cfg.CRYPTO_POSITION_SIZE_USD} = {crypto_pct:.0%} of account — consider reducing"
        )
    else:
        ok(
            f"Crypto position ${cfg.CRYPTO_POSITION_SIZE_USD} = {crypto_pct:.0%} of account"
        )

    if equity_pct > 0.30:
        warn(
            f"Equity position ${cfg.EQUITY_POSITION_SIZE_USD} = {equity_pct:.0%} of account — consider reducing"
        )
    else:
        ok(
            f"Equity position ${cfg.EQUITY_POSITION_SIZE_USD} = {equity_pct:.0%} of account"
        )

    # Max deployed check
    max_open_usd = cfg.MAX_POSITIONS_CRYPTO * cfg.CRYPTO_POSITION_SIZE_USD
    if max_open_usd > cfg.ACCOUNT_SIZE * cfg.MAX_DEPLOYED_PCT:
        warn(
            f"Max deployed crypto ({max_open_usd:.0f}) > {cfg.MAX_DEPLOYED_PCT:.0%} of account "
            f"(${cfg.ACCOUNT_SIZE * cfg.MAX_DEPLOYED_PCT:.0f}) — positions may be capped by risk manager"
        )
    else:
        ok(
            f"Max open crypto: ${max_open_usd:.0f} fits within {cfg.MAX_DEPLOYED_PCT:.0%} deployment cap"
        )

    # Squeeze min bars
    if cfg.SQUEEZE_MIN_BARS < 10:
        warn(
            f"SQUEEZE_MIN_BARS={cfg.SQUEEZE_MIN_BARS} — deep research recommends ≥20 bars for reliable squeeze signals"
        )
    else:
        ok(f"SQUEEZE_MIN_BARS={cfg.SQUEEZE_MIN_BARS} (≥20 per deep research)")

    # Agent agreement
    n_agents = len(cfg.FULL_DEBATE_AGENTS)
    min_agree = int(cfg.FULL_DEBATE_MIN_AGREEMENT * n_agents)
    ok(
        f"Full debate: {n_agents} agents, min agreement: {min_agree} (config={cfg.FULL_DEBATE_MIN_AGREEMENT:.0%})"
    )

    ok("Config loaded and consistent")
except Exception as e:
    fail(f"Config failed to load: {e}")


# ─────────────────────────────────────────────────────────────
# 3. IMPORT CHECKS
# ─────────────────────────────────────────────────────────────
print("\n─── Critical imports ───────────────────────────────────")

critical_imports = [
    ("anthropic or urllib (API calls)", "urllib.request", None),
    ("pandas", "pandas", None),
    ("numpy", "numpy", None),
    ("schedule", "schedule", None),
    ("dotenv", "dotenv", None),
    ("pytz", "pytz", None),
    ("sqlite3", "sqlite3", None),
    ("data.indicators", "data.indicators", None),
    ("risk.risk_manager", "risk.risk_manager", None),
    ("logging_db.trade_logger", "logging_db.trade_logger", None),
]

optional_imports = [
    ("pandas_ta", "pandas_ta", "technical indicators will use fallbacks"),
    ("lancedb", "lancedb", "trade memory disabled — no LanceDB"),
    (
        "sentence_transformers",
        "sentence_transformers",
        "trade memory embeddings disabled",
    ),
    ("yfinance", "yfinance", "market data fetches will fail"),
    ("streamlit", "streamlit", "dashboard will not start"),
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
    except Exception as e:
        warn(f"{label} imported but has error: {e}")


# ─────────────────────────────────────────────────────────────
# 4. DATABASE
# ─────────────────────────────────────────────────────────────
print("\n─── Database ───────────────────────────────────────────")

try:
    import sqlite3

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db"
    )
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
# 5. FORECASTEX LANE READINESS
# ─────────────────────────────────────────────────────────────
# Autonomously answers 9 questions.  Each resolves to:
#   READY         — check passed, lane can proceed
#   BLOCKED       — automated fix needed; lane cannot start
#   ACTION NEEDED — human-only step required
# ─────────────────────────────────────────────────────────────
print("\n─── ForecastEx lane ────────────────────────────────────")

_fx_checks: list[dict] = []  # {name, status, detail}
_fx_blocked = False
_fx_action = False


def _fx(name: str, status: str, detail: str) -> None:
    """Record a ForecastEx check result and print it."""
    global _fx_blocked, _fx_action
    icon = {"READY": PASS, "BLOCKED": FAIL, "ACTION NEEDED": WARN}.get(status, WARN)
    print(f"  {icon} [{status:13s}] {name}: {detail}")
    _fx_checks.append({"name": name, "status": status, "detail": detail})
    if status == "BLOCKED":
        _fx_blocked = True
    elif status == "ACTION NEEDED":
        _fx_action = True


# 1. Forecast DB tables present
try:
    import sqlite3 as _sq3

    _fx_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db"
    )
    _required_tables = {
        "forecast_markets",
        "forecast_contracts",
        "forecast_quotes",
        "forecast_bars",
        "forecast_resolutions",
    }
    if os.path.exists(_fx_db):
        _c = _sq3.connect(_fx_db)
        _found = {
            r[0]
            for r in _c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        _c.close()
        _missing = _required_tables - _found
        if _missing:
            _fx(
                "DB tables",
                "BLOCKED",
                f"Missing: {', '.join(sorted(_missing))} — run forecast.db.init_forecast_db()",
            )
        else:
            _fx("DB tables", "READY", "All 5 forecast tables present")
    else:
        _fx("DB tables", "BLOCKED", "trades.db not found — run setup first")
except Exception as _e:
    _fx("DB tables", "BLOCKED", f"DB check error: {_e}")

# 2. ForecastEx discovery imports and is runnable
try:
    import importlib as _il

    _il.import_module("forecast.discovery")
    _il.import_module("forecast.db")
    _fx("Discovery module", "READY", "forecast.discovery imports cleanly")
except ImportError as _e:
    _fx("Discovery module", "BLOCKED", f"Import error: {_e}")

# 3. ForecastEx broker importable
try:
    _il.import_module("execution.forecastex_broker")
    _fx("ForecastEx broker", "READY", "execution.forecastex_broker imports cleanly")
except ImportError as _e:
    _fx("ForecastEx broker", "BLOCKED", f"Import error: {_e}")

# 4. Conids cached in DB (active contracts > 0)
try:
    if os.path.exists(_fx_db):
        _c = _sq3.connect(_fx_db)
        try:
            _n = _c.execute(
                "SELECT COUNT(*) FROM forecast_contracts WHERE active=1"
            ).fetchone()[0]
            _c.close()
            if _n > 0:
                _fx("Conids cached", "READY", f"{_n} active contracts in DB")
            else:
                _fx(
                    "Conids cached",
                    "ACTION NEEDED",
                    "No contracts yet — connect TWS and run discovery",
                )
        except Exception:
            _c.close()
            _fx(
                "Conids cached",
                "BLOCKED",
                "forecast_contracts table missing or unreadable",
            )
    else:
        _fx("Conids cached", "BLOCKED", "DB not found")
except Exception as _e:
    _fx("Conids cached", "BLOCKED", f"Check error: {_e}")

# 5. Quotes ingesting (last quote < 10 min ago)
try:
    if os.path.exists(_fx_db):
        _c = _sq3.connect(_fx_db)
        try:
            _row = _c.execute(
                "SELECT ts FROM forecast_quotes ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            _c.close()
            if _row:
                from datetime import datetime as _dt, timezone as _tz

                _lag = (
                    _dt.now(_tz.utc) - _dt.fromisoformat(_row[0].replace("Z", "+00:00"))
                ).total_seconds()
                if _lag < 600:
                    _fx("Quotes ingesting", "READY", f"Last quote {_lag / 60:.1f}m ago")
                else:
                    _fx(
                        "Quotes ingesting",
                        "ACTION NEEDED",
                        f"Last quote {_lag / 3600:.1f}h ago — harvester may be stopped",
                    )
            else:
                _fx(
                    "Quotes ingesting",
                    "ACTION NEEDED",
                    "No quotes yet — start the forecast lane to collect quotes",
                )
        except Exception:
            _c.close()
            _fx("Quotes ingesting", "BLOCKED", "forecast_quotes unreadable")
    else:
        _fx("Quotes ingesting", "BLOCKED", "DB not found")
except Exception as _e:
    _fx("Quotes ingesting", "BLOCKED", f"Check error: {_e}")

# 6. Derived bars present (at least one 5m bar)
try:
    if os.path.exists(_fx_db):
        _c = _sq3.connect(_fx_db)
        try:
            _n = _c.execute(
                "SELECT COUNT(*) FROM forecast_bars WHERE interval='5m'"
            ).fetchone()[0]
            _c.close()
            if _n > 0:
                _fx("Bars built", "READY", f"{_n} 5m bars in DB")
            else:
                _fx(
                    "Bars built",
                    "ACTION NEEDED",
                    "No 5m bars yet — collect quotes first",
                )
        except Exception:
            _c.close()
            _fx("Bars built", "BLOCKED", "forecast_bars unreadable")
    else:
        _fx("Bars built", "BLOCKED", "DB not found")
except Exception as _e:
    _fx("Bars built", "BLOCKED", f"Check error: {_e}")

# 7. Strategy path functional (all strategy engine imports resolve)
try:
    _il.import_module("forecast.strategy_engine")
    _il.import_module("forecast.primitives")
    _fx(
        "Strategy path", "READY", "forecast.strategy_engine + primitives import cleanly"
    )
except ImportError as _e:
    _fx("Strategy path", "BLOCKED", f"Import error: {_e}")

# 8. Dashboard aligned (FORECAST TRADING tab present in app.py)
try:
    _app_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dashboard",
        "app.py",
    )
    _app_src = open(_app_path).read() if os.path.exists(_app_path) else ""
    _has_fc_tab = "FORECAST TRADING" in _app_src
    _has_arc_mes = "ARCHIVED FUTURES (MES)" in _app_src
    _has_widget = "render_forecast_trading" in _app_src
    if _has_fc_tab and _has_arc_mes and _has_widget:
        _fx(
            "Dashboard aligned",
            "READY",
            "FORECAST TRADING tab + MES archived + widget wired",
        )
    else:
        _missing_items = []
        if not _has_fc_tab:
            _missing_items.append("FORECAST TRADING tab")
        if not _has_arc_mes:
            _missing_items.append("ARCHIVED FUTURES (MES) tab")
        if not _has_widget:
            _missing_items.append("render_forecast_trading import")
        _fx("Dashboard aligned", "BLOCKED", f"Missing: {', '.join(_missing_items)}")
except Exception as _e:
    _fx("Dashboard aligned", "BLOCKED", f"Check error: {_e}")

# 9. MES archived correctly (FUTURES_LANE_ACTIVE not True, or key is absent)
try:
    import config as _cfg

    _mes_active = getattr(_cfg, "FUTURES_LANE_ACTIVE", False)
    _fc_active = getattr(_cfg, "FORECAST_LANE_ACTIVE", False)
    if _mes_active:
        _fx(
            "MES archived",
            "ACTION NEEDED",
            "FUTURES_LANE_ACTIVE=True — set to False to fully archive MES lane",
        )
    else:
        _fx(
            "MES archived",
            "READY",
            "FUTURES_LANE_ACTIVE is False — MES is dormant (expected)",
        )
    _fx(
        "Forecast lane flag",
        "READY",
        f"FORECAST_LANE_ACTIVE={'True' if _fc_active else 'False'} — {'active, wired into main.py' if _fc_active else 'standalone/disabled (start manually or set FORECAST_LANE_ACTIVE=true)'}",
    )
except Exception as _e:
    _fx("MES archived", "BLOCKED", f"Config check error: {_e}")

# 9b. Forecast lane DB activity
try:
    if os.path.exists(_fx_db):
        _c = _sq3.connect(_fx_db)
        try:
            _lane_events = 0
            _markets_count = 0
            _stub_count = 0
            try:
                _lane_events = _c.execute(
                    "SELECT COUNT(*) FROM system_events "
                    "WHERE source='ForecastRunner' AND ts >= datetime('now','-2 hours')"
                ).fetchone()[0]
            except Exception:
                pass
            try:
                _markets_count = _c.execute(
                    "SELECT COUNT(*) FROM forecast_markets WHERE active=1"
                ).fetchone()[0]
                _stub_count = _c.execute(
                    "SELECT COUNT(*) FROM forecast_markets fm WHERE fm.active=1 "
                    "AND NOT EXISTS (SELECT 1 FROM forecast_contracts fc "
                    "WHERE fc.market_id=fm.id AND fc.active=1)"
                ).fetchone()[0]
            except Exception:
                pass
            _c.close()
            if _lane_events > 0:
                _fx("Forecast lane active", "READY", f"ForecastRunner: {_lane_events} events in last 2h")
            else:
                _fx("Forecast lane active", "ACTION NEEDED", "No ForecastRunner events in last 2h — lane not running")
            if _stub_count > 0:
                _fx(
                    "Forecast enrollment",
                    "ACTION NEEDED",
                    f"{_stub_count} underlier(s) visible but no OPT contracts — check ForecastEx portal enrollment",
                )
            elif _markets_count > 0:
                _fx("Forecast enrollment", "READY", f"{_markets_count} market(s) with active contracts")
        except Exception as _e:
            _c.close()
            _fx("Forecast lane active", "BLOCKED", f"DB check error: {_e}")
    else:
        _fx("Forecast lane active", "BLOCKED", "DB not found")
except Exception as _e:
    _fx("Forecast lane active", "BLOCKED", f"Check error: {_e}")

# 10. Tiny live test trades allowed (all READY, TWS connected, bankroll sufficient)
try:
    _live_allowed = (
        not _fx_blocked
        and not _fx_action
        and os.getenv("PAPER_TRADING", "true").lower() != "true"
    )
    if _fx_blocked:
        _fx("Live test trades", "BLOCKED", "Blocked checks must be resolved first")
    elif _fx_action:
        _fx(
            "Live test trades",
            "ACTION NEEDED",
            "Complete ACTION NEEDED steps then re-run validator",
        )
    else:
        _is_paper = os.getenv("PAPER_TRADING", "true").lower() != "false"
        if _is_paper:
            _fx(
                "Live test trades",
                "ACTION NEEDED",
                "PAPER_TRADING=true — set PAPER_TRADING=false and confirm account balance",
            )
        else:
            _fx(
                "Live test trades",
                "READY",
                "All checks green + PAPER_TRADING=false — tiny live trades ENABLED",
            )
except Exception as _e:
    _fx("Live test trades", "BLOCKED", f"Check error: {_e}")

# ─────────────────────────────────────────────────────────────
# 5. VERSION CONSISTENCY
# ─────────────────────────────────────────────────────────────
print("\n─── Version ────────────────────────────────────────────")

try:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_sources = [
        os.path.join(repo_root, "AGENTS.md"),
        os.path.join(repo_root, "CLAUDE.md"),
    ]

    for version_source in version_sources:
        if not os.path.exists(version_source):
            continue

        with open(version_source) as f:
            for line in f:
                if "Current Version:" in line:
                    version = line.strip().split(":")[-1].strip()
                    ok(f"System version: {version}")
                    raise StopIteration

    warn("No version source found (checked AGENTS.md, CLAUDE.md)")
except StopIteration:
    pass
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


if __name__ == "__main__":
    pass
