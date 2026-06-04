from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


def _bind_dashboard_db(monkeypatch, db_path: str) -> None:
    import dashboard.db as dash_db
    import db as db_shim

    monkeypatch.setattr(dash_db, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(db_shim, "DB_PATH", db_path, raising=False)


def _seed_operational_forecast(db_path: str) -> None:
    from forecast.db import init_forecast_db, insert_quote, upsert_bar, upsert_contract, upsert_market

    init_forecast_db(db_path=db_path)
    market_id = upsert_market(
        market_symbol="KXHIGHNY",
        market_name="NYC High Temperature",
        exchange="KALSHI",
        db_path=db_path,
    )
    contract_id = upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHNY-26JUN04-B80.5",
        right="C",
        strike=80.5,
        exchange="KALSHI",
        last_trade_at=(datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d"),
        db_path=db_path,
    )
    now = datetime.now(timezone.utc)
    insert_quote(
        contract_id,
        now.isoformat(),
        0.58,
        0.62,
        100.0,
        100.0,
        0.60,
        0.04,
        0.60,
        "YES",
        db_path=db_path,
    )
    upsert_bar(
        contract_id,
        "5m",
        (now - timedelta(minutes=5)).isoformat(),
        now.isoformat(),
        0.58,
        0.61,
        0.57,
        0.60,
        0.595,
        0.04,
        1.0,
        db_path=db_path,
    )


def test_forecast_control_snapshot_detects_runtime_data_contradiction(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from forecast.db import init_forecast_db, upsert_market
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_forecast_db(db_path=str(proof_runtime.db_path))
    upsert_market(
        market_symbol="KXRAINNY",
        market_name="NYC Rain",
        exchange="KALSHI",
        db_path=str(proof_runtime.db_path),
    )
    init_runtime_tables(db_path=str(proof_runtime.db_path))
    upsert_lane_state(
        "forecast",
        db_path=str(proof_runtime.db_path),
        active=1,
        enabled=1,
        readiness_state="NO_UNDERLIERS",
        last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )

    monkeypatch.delitem(sys.modules, "data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "dashboard.data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "data.trading_control", raising=False)
    from data.trading_control import get_forecast_control_snapshot

    snap = get_forecast_control_snapshot()
    assert snap["health"]["underliers_visible"] == 1
    assert snap["contradictions"], "Expected contradiction for visible markets + NO_UNDERLIERS"


def test_forecast_health_marks_stale_runtime_heartbeat_not_started(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from forecast.db import init_forecast_db
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_forecast_db(db_path=str(proof_runtime.db_path))
    init_runtime_tables(db_path=str(proof_runtime.db_path))
    upsert_lane_state(
        "forecast",
        db_path=str(proof_runtime.db_path),
        active=1,
        enabled=1,
        readiness_state="NO_UNDERLIERS",
        last_heartbeat_at="2026-04-01T00:00:00+00:00",
    )

    monkeypatch.delitem(sys.modules, "data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "dashboard.data.forecast", raising=False)
    from data.forecast import get_forecast_health, get_forecast_readiness

    health = get_forecast_health()
    readiness = get_forecast_readiness()
    assert health["lane_started"] is False
    assert readiness["lane_state"] == "LANE_NOT_STARTED"


def test_forecast_readiness_ready_when_quotes_and_bars_exist(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    _seed_operational_forecast(str(proof_runtime.db_path))
    init_runtime_tables(db_path=str(proof_runtime.db_path))
    upsert_lane_state(
        "forecast",
        db_path=str(proof_runtime.db_path),
        active=1,
        enabled=1,
        readiness_state="OPERATIONAL",
        last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )

    monkeypatch.delitem(sys.modules, "data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "dashboard.data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "data.trading_control", raising=False)
    from data.forecast import get_forecast_readiness
    from data.trading_control import get_forecast_control_snapshot

    readiness = get_forecast_readiness()
    snap = get_forecast_control_snapshot()
    assert readiness["status"] == "READY"
    assert readiness["lane_state"] == "OPERATIONAL"
    assert snap["contradictions"] == []


def test_forecast_readiness_blocks_when_markets_have_no_contracts(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from forecast.db import init_forecast_db, upsert_market
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_forecast_db(db_path=str(proof_runtime.db_path))
    upsert_market(
        market_symbol="KXRAINCHI",
        market_name="Chicago Rain",
        exchange="KALSHI",
        db_path=str(proof_runtime.db_path),
    )
    init_runtime_tables(db_path=str(proof_runtime.db_path))
    upsert_lane_state(
        "forecast",
        db_path=str(proof_runtime.db_path),
        active=1,
        enabled=1,
        readiness_state="NO_TRADABLE_CONTRACTS",
        last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )

    monkeypatch.delitem(sys.modules, "data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "dashboard.data.forecast", raising=False)
    from data.forecast import get_forecast_readiness

    readiness = get_forecast_readiness()
    assert readiness["status"] == "BLOCKED"
    assert readiness["contracts_unavailable_count"] == 1
