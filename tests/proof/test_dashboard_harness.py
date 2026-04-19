from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.proof.support import (
    insert_signal_stat,
    insert_system_event,
    insert_trade,
    insert_trade_attribution,
    write_log,
)


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"


def _stub_widget(label: str):
    def _render():
        import streamlit as st

        st.markdown(f"stub:{label}")

    return _render


def test_dashboard_data_summaries_read_live_schema(proof_runtime):
    _now = datetime.now()
    _ts = lambda h: (_now - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
    _ts_iso = lambda h: (_now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _scan_ts = _ts(1)

    insert_trade(proof_runtime.db_path, ts=_ts(2), pnl_usd=6.0, fee_usd=0.5, won=1)
    insert_trade_attribution(proof_runtime.db_path, created_at=_ts_iso(1))
    insert_system_event(
        proof_runtime.db_path,
        ts=_ts(1),
        source="health_check",
        message="Health 6/6 [HEALTHY]",
    )
    insert_system_event(
        proof_runtime.db_path,
        ts=_ts(1),
        source="heartbeat",
        message="heartbeat ok",
    )
    insert_system_event(
        proof_runtime.db_path,
        ts=_ts(2),
        source="main",
        message="Bot started — paper v13.4",
    )
    write_log(
        proof_runtime.log_path,
        f"{_scan_ts},000 root INFO [scanner] Step 1 (vol>2500K): 3 → 2",
        f"{_scan_ts},001 root INFO [scanner] Step 2 (setups): 2 → 2",
        f"{_scan_ts},002 root INFO [scanner] Complete: 2 candidates in 4.5s",
        f"{_scan_ts},003 root INFO → BTCUSDT LONG spike=1.9 adx=24.0 ev=$12.50 funding=0.1200%",
        f"{_scan_ts},004 root INFO [v10] scan: balance=$5000 deployed=$250",
    )

    from dashboard.data.execution import get_execution_stats
    from dashboard.data.health import get_health_status, get_restart_count_24h
    from dashboard.data.scanner_data import get_scan_status

    execution = get_execution_stats()
    health = get_health_status()
    scan = get_scan_status()

    assert execution["total"] == 1
    assert health["status"] == "HEALTHY"
    assert get_restart_count_24h() == 1
    assert scan["count"] == 2
    assert scan["candidates"][0]["symbol"] == "BTCUSDT"


def test_operator_panel_renders_all_tabs_with_widget_stubs(monkeypatch):
    # v17.0: 5-tab architecture — stub the page-level render functions
    widget_map = {
        "widgets.pages.control_tower": ("render_control_tower", "control-tower"),
        "widgets.pages.crypto_page": ("render_crypto_page", "crypto-page"),
        "widgets.pages.forecast_page": ("render_forecast_page", "forecast-page"),
        "widgets.pages.performance_lab": ("render_performance_lab", "performance-lab"),
        "widgets.pages.engineering_console": (
            "render_engineering_console",
            "engineering-console",
        ),
    }

    for module_path, (func_name, label) in widget_map.items():
        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, func_name, _stub_widget(label))

    at = AppTest.from_file(str(DASHBOARD_ROOT / "app.py"))
    at.run(timeout=15)

    assert not at.exception
    assert [tab.label for tab in at.tabs] == [
        "CONTROL TOWER",
        "CRYPTO",
        "FORECAST",
        "PERFORMANCE LAB",
        "ENGINEERING CONSOLE",
    ]
    rendered = [node.value for node in at.markdown]
    assert any("stub:control-tower" in value for value in rendered)
    assert any("stub:crypto-page" in value for value in rendered)
    assert any("stub:engineering-console" in value for value in rendered)


def test_decision_quality_widget_renders_created_at_backed_summary(proof_runtime):
    insert_trade(
        proof_runtime.db_path, ts="2026-04-10 09:35:00", pnl_usd=6.0, fee_usd=0.5, won=1
    )
    insert_trade_attribution(
        proof_runtime.db_path,
        created_at="2026-04-10T10:30:00+00:00",
        exit_type="target_hit",
        won=1,
    )
    insert_signal_stat(proof_runtime.db_path)

    script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(DASHBOARD_ROOT)!r})
from widgets.mission_control.decision_quality import render_decision_quality
render_decision_quality()
"""
    at = AppTest.from_string(script)
    at.run(timeout=15)

    assert not at.exception
    rendered = [node.value for node in at.markdown]
    assert any("Decision Quality" in value for value in rendered)
    assert any("Good outcome (won)" in value for value in rendered)
    assert any("Top signal" in value for value in rendered)
