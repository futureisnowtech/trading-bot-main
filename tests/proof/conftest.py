from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


@dataclass(frozen=True)
class ProofRuntime:
    db_path: Path
    csv_dir: Path
    log_path: Path


def _reset_risk_engine() -> None:
    import risk_engine

    risk_engine._state = risk_engine.RiskState()
    # Override hardcoded $10K defaults to match the $5K paper account
    # used in proof tests.  Without this, kill_switch fires on $5K balance
    # (below 75% of $10K = $7.5K) and blocks margin-utilization checks.
    risk_engine._state.account_balance = 5_000.0
    risk_engine._state.peak_balance = 5_000.0
    risk_engine._state.daily_start_balance = 5_000.0


def _reset_kill_switch() -> None:
    import kill_switch

    with kill_switch._lock:
        kill_switch._halted = False
        kill_switch._halt_reason = ""
        kill_switch._halt_ts = 0.0
        kill_switch._api_errors.clear()
        kill_switch._last_latency_ms = 0.0


@pytest.fixture
def proof_runtime(tmp_path, monkeypatch) -> ProofRuntime:
    logs_dir = tmp_path / "logs"
    csv_dir = logs_dir / "csv"
    db_path = logs_dir / "trades.db"
    log_path = logs_dir / "bot.log"
    csv_dir.mkdir(parents=True)
    log_path.write_text("", encoding="utf-8")

    import config
    import dashboard.db as dashboard_db
    import db as dashboard_db_shim
    import learning.signal_performance as signal_performance
    import logging_db.trade_logger as trade_logger
    import monitoring.health_check as health_check

    monkeypatch.setattr(config, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(config, "CSV_LOG_DIR", str(csv_dir), raising=False)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 5_000.0, raising=False)

    monkeypatch.setattr(trade_logger, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(trade_logger, "CSV_LOG_DIR", str(csv_dir), raising=False)
    monkeypatch.setattr(trade_logger, "_LOGGER_HANDLE", None, raising=False)

    monkeypatch.setattr(signal_performance, "DB_PATH", str(db_path), raising=False)

    monkeypatch.setattr(dashboard_db, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(dashboard_db, "LOG_PATH", str(log_path), raising=False)
    monkeypatch.setattr(dashboard_db_shim, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(dashboard_db_shim, "LOG_PATH", str(log_path), raising=False)

    monkeypatch.setattr(health_check, "DB_PATH", str(db_path), raising=False)

    trade_logger.init_db()
    signal_performance.init_learning_tables()
    _reset_risk_engine()
    _reset_kill_switch()

    runtime = ProofRuntime(db_path=db_path, csv_dir=csv_dir, log_path=log_path)
    yield runtime

    handle = getattr(trade_logger, "_LOGGER_HANDLE", None)
    if handle is not None and getattr(handle, "conn", None) is not None:
        try:
            handle.conn.close()
        except Exception:
            pass
    monkeypatch.setattr(trade_logger, "_LOGGER_HANDLE", None, raising=False)
