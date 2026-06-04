"""
tests/proof/test_lane_gating.py — Kalshi lane gating and readiness proofs.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


def _insert_error(db_path: Path, source: str, message: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'ERROR', ?, ?)",
            (ts, source, message),
        )


def _set_recent_forecast_heartbeat(db_path: str) -> None:
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_runtime_tables(db_path=db_path)
    upsert_lane_state(
        "forecast",
        db_path=db_path,
        enabled=1,
        active=1,
        readiness_state="OPERATIONAL",
        last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )


def test_health_check_degraded_without_credentials(proof_runtime, monkeypatch):
    import monitoring.health_check as hc

    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(hc, "DB_PATH", str(proof_runtime.db_path), raising=False)

    result = hc.run_health_check(force=True)
    checks = {check["name"]: check for check in result["checks"]}

    assert result["healthy"] is False
    assert result["status"] == "DEGRADED"
    assert checks["sqlite"]["ok"] is True
    assert checks["kalshi_credentials"]["ok"] is False
    assert checks["telegram"]["ok"] is False


def test_health_check_healthy_with_credentials_and_low_error_rate(
    proof_runtime, monkeypatch
):
    import monitoring.health_check as hc

    key_path = proof_runtime.db_path.parent / "kalshi_test_key.pem"
    key_path.write_text("test-key", encoding="utf-8")

    monkeypatch.setenv("KALSHI_API_KEY_ID", "demo-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(hc, "DB_PATH", str(proof_runtime.db_path), raising=False)

    result = hc.run_health_check(force=True)
    checks = {check["name"]: check for check in result["checks"]}

    assert result["healthy"] is True
    assert checks["recent_errors"]["detail"] == "0 recent errors"


def test_error_rate_turns_degraded_when_recent_errors_spike(proof_runtime, monkeypatch):
    import monitoring.health_check as hc

    monkeypatch.setattr(hc, "DB_PATH", str(proof_runtime.db_path), raising=False)
    for i in range(12):
        _insert_error(
            proof_runtime.db_path,
            source="ForecastRunner",
            message=f"quote harvest timeout {i}",
        )

    result = hc._check_error_rate()
    assert result["ok"] is False
    assert result["detail"] == "12 recent errors"


def test_forecast_readiness_not_started_without_heartbeat(proof_runtime, monkeypatch):
    import dashboard.db as dash_db
    import db as db_shim
    from dashboard.data.forecast import get_forecast_readiness
    from forecast.db import init_forecast_db

    init_forecast_db(db_path=str(proof_runtime.db_path))
    monkeypatch.setattr(dash_db, "DB_PATH", str(proof_runtime.db_path), raising=False)
    monkeypatch.setattr(db_shim, "DB_PATH", str(proof_runtime.db_path), raising=False)

    readiness = get_forecast_readiness()
    assert readiness["lane_state"] == "LANE_NOT_STARTED"
    assert readiness["status"] == "ACTION_NEEDED"


def test_stub_markets_block_readiness_until_contracts_arrive(
    proof_runtime, monkeypatch
):
    import dashboard.db as dash_db
    import db as db_shim
    from dashboard.data.forecast import get_forecast_readiness
    from forecast.discovery import run_discovery

    monkeypatch.setattr(dash_db, "DB_PATH", str(proof_runtime.db_path), raising=False)
    monkeypatch.setattr(db_shim, "DB_PATH", str(proof_runtime.db_path), raising=False)

    mock_broker = MagicMock()
    mock_broker.discover_markets.return_value = [
        {
            "underlier": "KXRAINNY",
            "und_conid": 123456,
            "long_name": "NYC Rain",
            "category": "weather",
            "stub_only": True,
            "opt_unavailable": True,
            "local_symbol": "KXRAINNY",
            "conid": None,
            "right": None,
            "strike": None,
            "last_trade_at": None,
            "exchange": "KALSHI",
            "currency": "USD",
        }
    ]

    result = run_discovery(broker=mock_broker, db_path=str(proof_runtime.db_path))
    _set_recent_forecast_heartbeat(str(proof_runtime.db_path))
    readiness = get_forecast_readiness()

    assert result["stubs_persisted"] == 1
    assert readiness["status"] == "BLOCKED"
    assert readiness["contracts_unavailable_count"] == 1
