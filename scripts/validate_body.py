"""
scripts/validate_body.py — Kalshi-only preflight validator.

This validator is intentionally narrow: it checks the active weather-engine
runtime instead of historical crypto, futures, or dashboard lanes.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

os.chdir(_REPO_ROOT)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_errors: list[str] = []
_warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  [{PASS}] {msg}")


def warn(msg: str) -> None:
    print(f"  [{WARN}] {msg}")
    _warnings.append(msg)


def fail(msg: str) -> None:
    print(f"  [{FAIL}] {msg}")
    _errors.append(msg)


def info(msg: str) -> None:
    print(f"  [INFO] {msg}")


print("\n--- Environment ---")
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
    ok("dotenv loaded")
except ImportError:
    fail("python-dotenv not installed")

import config as cfg
from runtime.storage_guard import runtime_storage_status

required_env = [
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

for key in required_env:
    value = os.getenv(key, "").strip()
    if value:
        ok(f"{key} set")
    else:
        fail(f"{key} missing from .env")

private_key_path = cfg.get_kalshi_private_key_path().strip()
if private_key_path:
    if Path(private_key_path).exists():
        ok(f"Kalshi private key path exists ({private_key_path})")
    else:
        fail(f"KALSHI_PRIVATE_KEY_PATH does not exist: {private_key_path}")

if os.getenv("GOOGLE_API_KEY", "").strip():
    ok("GOOGLE_API_KEY set")
else:
    warn("GOOGLE_API_KEY not set — AI audit/oracle replies will be unavailable")


print("\n--- Kalshi Lane ---")
try:
    if float(cfg.ACCOUNT_SIZE) <= 0:
        fail(f"ACCOUNT_SIZE must be positive, got {cfg.ACCOUNT_SIZE}")
    else:
        ok(f"ACCOUNT_SIZE={cfg.ACCOUNT_SIZE}")

    if float(cfg.KALSHI_MAX_USD_PER_POSITION) <= 0:
        fail("KALSHI_MAX_USD_PER_POSITION must be positive")
    else:
        ok(f"KALSHI_MAX_USD_PER_POSITION={cfg.KALSHI_MAX_USD_PER_POSITION}")

    if int(cfg.KALSHI_SAME_EVENT_FAMILY_CAP) < 1:
        fail("KALSHI_SAME_EVENT_FAMILY_CAP must be at least 1")
    else:
        ok(f"KALSHI_SAME_EVENT_FAMILY_CAP={cfg.KALSHI_SAME_EVENT_FAMILY_CAP}")

    if float(cfg.KALSHI_HUB_EXPOSURE_PCT) <= 0:
        fail("KALSHI_HUB_EXPOSURE_PCT must be positive")
    else:
        ok(f"KALSHI_HUB_EXPOSURE_PCT={cfg.KALSHI_HUB_EXPOSURE_PCT:.2f}")

    if float(cfg.KALSHI_HUB_EXPOSURE_MIN_USD) < 0:
        fail("KALSHI_HUB_EXPOSURE_MIN_USD cannot be negative")
    else:
        ok(f"KALSHI_HUB_EXPOSURE_MIN_USD={cfg.KALSHI_HUB_EXPOSURE_MIN_USD:.2f}")

    if float(cfg.KALSHI_TAKER_FEE_RATE) < 0:
        fail("KALSHI_TAKER_FEE_RATE cannot be negative")
    else:
        ok(f"KALSHI_TAKER_FEE_RATE={cfg.KALSHI_TAKER_FEE_RATE:.4f}")

    if float(cfg.KALSHI_MAKER_FEE_RATE) < 0:
        fail("KALSHI_MAKER_FEE_RATE cannot be negative")
    else:
        ok(f"KALSHI_MAKER_FEE_RATE={cfg.KALSHI_MAKER_FEE_RATE:.4f}")

    if float(cfg.KALSHI_FEE_PER_CONTRACT) < 0:
        fail("KALSHI_FEE_PER_CONTRACT legacy fallback cannot be negative")
    else:
        ok(f"KALSHI_FEE_PER_CONTRACT_LEGACY={cfg.KALSHI_FEE_PER_CONTRACT}")

    if not cfg.FORECAST_LANE_ACTIVE:
        warn("FORECAST_LANE_ACTIVE=false — lane will remain disabled")
    else:
        ok("FORECAST_LANE_ACTIVE=true")

    if not cfg.FORECAST_AUTONOMOUS_ENABLED:
        warn("FORECAST_AUTONOMOUS_ENABLED=false — sniper_cron will exit cleanly")
    else:
        ok("FORECAST_AUTONOMOUS_ENABLED=true")

    if cfg.SHADOW_EXECUTION:
        warn("SHADOW_EXECUTION=true — live POST order placement is disabled")
    else:
        ok("SHADOW_EXECUTION=false")
except Exception as exc:
    fail(f"Config failed to load: {exc}")


print("\n--- Runtime Truth ---")
try:
    db_path = Path(cfg.DB_PATH)
    if not db_path.exists():
        warn(f"runtime DB not created yet: {db_path}")
    else:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table' AND name='lane_runtime_state'
                """
            ).fetchone()
            if row:
                ok("lane_runtime_state table reachable")
            else:
                warn("lane_runtime_state table missing — start the runtime once to initialize it")
except Exception as exc:
    warn(f"runtime truth check skipped: {exc}")


print("\n--- Imports ---")
critical_imports = [
    "numpy",
    "requests",
    "pytz",
    "psutil",
    "schedule",
    "cryptography",
    "logging_db.trade_logger",
    "data.kalshi_weather_monitor",
    "execution.kalshi_broker",
    "forecast.runner",
    "forecast.strategy_engine",
    "notifications.telegram_bot",
]

optional_imports = [
    ("google.genai", "Gemini-based SRE Oracle disabled"),
    ("prometheus_client", "Prometheus metrics export disabled"),
]

for module in critical_imports:
    try:
        importlib.import_module(module)
        ok(f"import {module}")
    except Exception as exc:
        fail(f"import {module} failed: {exc}")

for module, message in optional_imports:
    try:
        importlib.import_module(module)
        ok(f"import {module}")
    except Exception:
        warn(f"{module} unavailable — {message}")


print("\n--- Filesystem ---")
logs_dir = Path(cfg.DB_PATH).parent
try:
    logs_dir.mkdir(parents=True, exist_ok=True)
    ok(f"runtime dir ready: {logs_dir}")
except Exception as exc:
    fail(f"Cannot create runtime dir: {exc}")

try:
    storage = runtime_storage_status()
    if storage["ok"]:
        ok(
            f"disk headroom healthy: {storage['free_mb']:.0f}MB free "
            f"(threshold={storage['threshold_mb']:.0f}MB)"
        )
    else:
        warn(
            f"low disk headroom: {storage['free_mb']:.0f}MB free at {storage['path']} "
            f"(threshold={storage['threshold_mb']:.0f}MB)"
        )
except Exception as exc:
    warn(f"disk headroom check skipped: {exc}")

verify_script = _REPO_ROOT / "scripts" / "verify_kalshi_connection.py"
if verify_script.exists():
    ok("Kalshi diagnostic script present")
else:
    fail("scripts/verify_kalshi_connection.py missing")

release_audit_script = _REPO_ROOT / "scripts" / "release_audit.py"
if release_audit_script.exists():
    ok("Release audit script present")
else:
    fail("scripts/release_audit.py missing")


print("\n--- Summary ---")
if _warnings:
    print(f"  warnings: {len(_warnings)}")
if _errors:
    print(f"  errors: {len(_errors)}")
    raise SystemExit(1)

print("  Kalshi-only validator passed.")
