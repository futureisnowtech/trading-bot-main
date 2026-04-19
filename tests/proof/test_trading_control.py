from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_ROOT = os.path.join(ROOT, "dashboard")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if DASHBOARD_ROOT not in sys.path:
    sys.path.insert(0, DASHBOARD_ROOT)


def _bind_dashboard_db(monkeypatch, db_path: str) -> None:
    import dashboard.db as dash_db
    import db as db_shim

    monkeypatch.setattr(dash_db, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(db_shim, "DB_PATH", db_path, raising=False)


def test_trading_control_crypto_snapshot_classifies_strategy_system_bug(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from logging_db.trade_logger import log_scan_candidate, log_scan_funnel

    scan_id = "scanabc123"
    # Blank tradeability row to prove the control view detects missing truth.
    log_scan_candidate(
        scan_id=scan_id,
        symbol="ETH",
        exchange="kraken",
        base_asset="ETH",
        direction="LONG",
        primary_setup="momentum",
        scan_setups_json='["momentum"]',
        price=2500.0,
        volume_24h_usd=10_000_000.0,
        spread_pct=0.05,
        bid_depth_usd=10_000.0,
        ask_depth_usd=10_000.0,
        atr_15m=20.0,
        stop_pct=1.0,
        target_pct=3.0,
        scanner_expected_profit=0.8,
        regime="TRENDING",
        technical_score=70.0,
        ml_score=55.0,
        composite_score=62.0,
        entry_threshold=50.0,
        should_enter_signal=0,
        econ_approved=0,
        econ_tier="",
        econ_reject_reason="",
        edge_score=0.0,
        size_usd=0.0,
        leverage=3,
        entry_block_reason="composite < threshold",
        decision="below_threshold",
        paper=True,
        source="clean_paper_v10",
    )
    log_scan_candidate(
        scan_id=scan_id,
        symbol="SOL",
        exchange="hyperliquid",
        base_asset="SOL",
        direction="LONG",
        primary_setup="momentum",
        scan_setups_json='["momentum"]',
        price=150.0,
        volume_24h_usd=8_000_000.0,
        spread_pct=0.05,
        bid_depth_usd=8_000.0,
        ask_depth_usd=8_000.0,
        atr_15m=2.0,
        stop_pct=1.2,
        target_pct=3.6,
        scanner_expected_profit=0.9,
        regime="TRENDING",
        technical_score=72.0,
        ml_score=56.0,
        composite_score=64.0,
        entry_threshold=50.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier="B",
        econ_reject_reason="",
        edge_score=0.5,
        size_usd=0.0,
        leverage=3,
        entry_block_reason="perp_contract_min_exceeds_policy",
        decision="sizing_zero",
        paper=True,
        source="clean_paper_v10",
        recommended_lane="perp",
        tradeability_status="blocked",
        trade_blocked_reason="perp_contract_min_exceeds_policy",
        trade_size_block_reason="perp_contract_min_exceeds_policy",
        trade_source_reason="trusted_source",
        manual_executable=0,
        auto_executable=0,
    )
    log_scan_candidate(
        scan_id=scan_id,
        symbol="BTC",
        exchange="kraken",
        base_asset="BTC",
        direction="LONG",
        primary_setup="momentum",
        scan_setups_json='["momentum"]',
        price=90000.0,
        volume_24h_usd=50_000_000.0,
        spread_pct=0.04,
        bid_depth_usd=50_000.0,
        ask_depth_usd=50_000.0,
        atr_15m=500.0,
        stop_pct=1.5,
        target_pct=4.5,
        scanner_expected_profit=1.2,
        regime="TRENDING",
        technical_score=80.0,
        ml_score=60.0,
        composite_score=70.0,
        entry_threshold=50.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier="A",
        econ_reject_reason="",
        edge_score=0.8,
        size_usd=100.0,
        leverage=3,
        entry_block_reason="open_long/short returned None",
        decision="execution_failed",
        paper=True,
        source="clean_paper_v10",
        recommended_lane="spot",
        tradeability_status="executable",
        trade_blocked_reason="",
        trade_size_block_reason="none",
        trade_source_reason="trusted_source",
        manual_executable=1,
        auto_executable=1,
    )
    log_scan_candidate(
        scan_id=scan_id,
        symbol="BTC",
        exchange="kraken",
        base_asset="BTC",
        direction="LONG",
        primary_setup="momentum",
        scan_setups_json='["momentum"]',
        price=90000.0,
        volume_24h_usd=50_000_000.0,
        spread_pct=0.04,
        bid_depth_usd=50_000.0,
        ask_depth_usd=50_000.0,
        atr_15m=500.0,
        stop_pct=1.5,
        target_pct=4.5,
        scanner_expected_profit=1.2,
        regime="TRENDING",
        technical_score=80.0,
        ml_score=60.0,
        composite_score=70.0,
        entry_threshold=50.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier="A",
        econ_reject_reason="",
        edge_score=0.8,
        size_usd=100.0,
        leverage=3,
        entry_block_reason="",
        decision="entered",
        paper=True,
        source="clean_paper_v10",
        recommended_lane="spot",
        tradeability_status="executable",
        trade_blocked_reason="",
        trade_size_block_reason="none",
        trade_source_reason="trusted_source",
        manual_executable=1,
        auto_executable=1,
    )
    log_scan_funnel(
        scan_id=scan_id,
        scanner_candidates_total=4,
        below_threshold=1,
        sizing_zero=1,
        execution_failed=1,
        entered=1,
    )

    monkeypatch.delitem(sys.modules, "data.trading_control", raising=False)
    from data.trading_control import get_crypto_control_snapshot

    snap = get_crypto_control_snapshot(hours=24)
    assert snap["issue_breakdown"]["strategy"] >= 1
    assert snap["issue_breakdown"]["system"] >= 1
    assert snap["issue_breakdown"]["bug"] >= 1
    assert snap["blank_tradeability_count"] >= 1
    assert snap["funnel"]["entered"] >= 1


def test_scanner_data_prefers_db_truth_when_scan_funnels_exist(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from logging_db.trade_logger import log_scan_candidate, log_scan_funnel
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_runtime_tables(db_path=str(proof_runtime.db_path))
    upsert_lane_state(
        "crypto",
        db_path=str(proof_runtime.db_path),
        active=1,
        capital_deployed_usd=125.0,
        buying_power_usd=5000.0,
        last_heartbeat_at="2026-04-19T12:00:00+00:00",
    )
    scan_id = "dbscan123"
    log_scan_candidate(
        scan_id=scan_id,
        symbol="BTC",
        exchange="kraken",
        base_asset="BTC",
        direction="LONG",
        primary_setup="momentum",
        scan_setups_json='["momentum"]',
        price=90000.0,
        volume_24h_usd=50_000_000.0,
        spread_pct=0.04,
        bid_depth_usd=50_000.0,
        ask_depth_usd=50_000.0,
        atr_15m=500.0,
        stop_pct=1.5,
        target_pct=4.5,
        scanner_expected_profit=1.2,
        regime="TRENDING",
        technical_score=80.0,
        ml_score=60.0,
        composite_score=70.0,
        entry_threshold=50.0,
        should_enter_signal=1,
        econ_approved=1,
        econ_tier="A",
        econ_reject_reason="",
        edge_score=0.8,
        size_usd=100.0,
        leverage=3,
        entry_block_reason="",
        decision="entered",
        paper=True,
        source="clean_paper_v10",
        recommended_lane="spot",
        tradeability_status="executable",
        trade_blocked_reason="",
        trade_size_block_reason="none",
        trade_source_reason="trusted_source",
        manual_executable=1,
        auto_executable=1,
    )
    log_scan_funnel(
        scan_id=scan_id,
        scanner_candidates_total=5,
        below_threshold=2,
        econ_veto=1,
        execution_failed=1,
        entered=1,
    )

    monkeypatch.delitem(sys.modules, "data.scanner_data", raising=False)
    from data.scanner_data import get_scan_status

    status = get_scan_status()
    assert status["count"] == 5
    assert status["steps"], "DB-backed scanner funnel should produce stage rows"
    assert status["candidates"][0]["symbol"] == "BTC"


def test_trading_control_forecast_detects_runtime_data_contradiction(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_runtime_tables(db_path=str(proof_runtime.db_path))
    with sqlite3.connect(proof_runtime.db_path) as c:
        c.execute(
            "CREATE TABLE forecast_markets (id INTEGER PRIMARY KEY, market_symbol TEXT, market_name TEXT, category_path TEXT, active INTEGER DEFAULT 1)"
        )
        c.execute(
            "CREATE TABLE forecast_contracts (id INTEGER PRIMARY KEY, market_id INTEGER, active INTEGER DEFAULT 1)"
        )
        c.execute(
            "CREATE TABLE forecast_quotes (id INTEGER PRIMARY KEY, contract_id INTEGER, ts TEXT, mid REAL)"
        )
        c.execute(
            "CREATE TABLE forecast_bars (id INTEGER PRIMARY KEY, contract_id INTEGER, interval TEXT, ts_open TEXT, ts_close TEXT, c REAL)"
        )
        c.execute(
            "CREATE TABLE forecast_resolutions (id INTEGER PRIMARY KEY, contract_id INTEGER, resolution_ts TEXT)"
        )
        c.execute(
            "INSERT INTO forecast_markets (id, market_symbol, market_name, category_path, active) VALUES (1,'CPI','CPI release','economics',1)"
        )
    upsert_lane_state(
        "forecast",
        db_path=str(proof_runtime.db_path),
        active=1,
        enabled=1,
        readiness_state="NO_UNDERLIERS",
        last_heartbeat_at="2026-04-19T12:00:00+00:00",
    )

    monkeypatch.delitem(sys.modules, "data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "dashboard.data.forecast", raising=False)
    monkeypatch.delitem(sys.modules, "data.trading_control", raising=False)
    from data.trading_control import get_forecast_control_snapshot

    snap = get_forecast_control_snapshot()
    assert snap["health"]["underliers_visible"] == 1
    assert snap["contradictions"], "Expected contradiction for underliers + NO_UNDERLIERS"


def test_forecast_health_marks_stale_runtime_heartbeat_not_started(
    proof_runtime, monkeypatch
):
    _bind_dashboard_db(monkeypatch, str(proof_runtime.db_path))
    from runtime.runtime_state import init_runtime_tables, upsert_lane_state

    init_runtime_tables(db_path=str(proof_runtime.db_path))
    with sqlite3.connect(proof_runtime.db_path) as c:
        c.execute(
            "CREATE TABLE forecast_markets (id INTEGER PRIMARY KEY, market_symbol TEXT, market_name TEXT, category_path TEXT, active INTEGER DEFAULT 1)"
        )
        c.execute(
            "CREATE TABLE forecast_contracts (id INTEGER PRIMARY KEY, market_id INTEGER, active INTEGER DEFAULT 1)"
        )
        c.execute(
            "CREATE TABLE forecast_quotes (id INTEGER PRIMARY KEY, contract_id INTEGER, ts TEXT, mid REAL)"
        )
        c.execute(
            "CREATE TABLE forecast_bars (id INTEGER PRIMARY KEY, contract_id INTEGER, interval TEXT, ts_open TEXT, ts_close TEXT, c REAL)"
        )
        c.execute(
            "CREATE TABLE forecast_resolutions (id INTEGER PRIMARY KEY, contract_id INTEGER, resolution_ts TEXT)"
        )
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
