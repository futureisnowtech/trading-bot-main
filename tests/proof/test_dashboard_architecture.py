"""
tests/proof/test_dashboard_architecture.py — Proof tests for v17.0 5-tab dashboard architecture.

These tests are pure source-analysis (AST + file reads) — no imports, no streamlit stub.
Import-level sanity for the new page modules lives in test_dashboard_imports.py.

Invariants:
  - Exactly 5 top-level tabs: CONTROL TOWER, CRYPTO, FORECAST, PERFORMANCE LAB, ENGINEERING CONSOLE
  - Old top-level tabs removed (MISSION CONTROL, TRADE APPROVAL, SYSTEM SETTINGS, ARCHIVED FUTURES)
  - All 5 page modules exist with the expected render functions
  - Data orchestrators exist and expose expected public functions
  - Manual trade console is inside CRYPTO page, not a standalone tab
  - Archived futures is inside ENGINEERING CONSOLE, not a standalone tab
  - Forecast page checks heartbeat freshness
"""

import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DASH = os.path.join(_ROOT, "dashboard")


# ── helpers ──────────────────────────────────────────────────────────────────


def _app_src() -> str:
    return open(os.path.join(_DASH, "app.py")).read()


def _app_tab_names() -> list:
    tree = ast.parse(_app_src())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "tabs"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            return [ast.literal_eval(e) for e in node.args[0].elts]
    return []


def _src(rel_path: str) -> str:
    return open(os.path.join(_DASH, rel_path)).read()


# ── tab structure ─────────────────────────────────────────────────────────────


def test_exactly_5_tabs():
    names = _app_tab_names()
    assert len(names) == 5, f"Expected 5 tabs, got {len(names)}: {names}"


def test_tab_names_are_correct():
    expected = [
        "CONTROL TOWER",
        "CRYPTO",
        "FORECAST",
        "PERFORMANCE LAB",
        "ENGINEERING CONSOLE",
    ]
    assert _app_tab_names() == expected, (
        f"Tab names wrong.\nExpected: {expected}\nGot: {_app_tab_names()}"
    )


def test_old_tabs_not_present():
    src = _app_src()
    for name in (
        "MISSION CONTROL",
        "TRADE APPROVAL",
        "SYSTEM SETTINGS",
        "ARCHIVED FUTURES",
    ):
        assert name not in src, f"Old tab '{name}' still present in app.py"


# ── page module existence + key symbols ──────────────────────────────────────


def test_control_tower_page_exists():
    assert "def render_control_tower" in _src("widgets/pages/control_tower.py")


def test_crypto_page_exists():
    src = _src("widgets/pages/crypto_page.py")
    assert "def render_crypto_page" in src
    assert "render_manual_scan" in src, (
        "Manual trade console must be inside CRYPTO page"
    )


def test_forecast_page_exists():
    assert "def render_forecast_page" in _src("widgets/pages/forecast_page.py")


def test_performance_lab_exists():
    assert "def render_performance_lab" in _src("widgets/pages/performance_lab.py")


def test_engineering_console_exists():
    src = _src("widgets/pages/engineering_console.py")
    assert "def render_engineering_console" in src
    assert "mes_dashboard" in src or "render_futures" in src, (
        "Archived MES widget must be inside engineering_console.py"
    )


# ── data orchestrators ────────────────────────────────────────────────────────


def test_control_tower_data_module():
    assert "def get_control_tower_snapshot" in _src("data/control_tower.py")


def test_crypto_dashboard_data_module():
    src = _src("data/crypto_dashboard.py")
    assert "def get_crypto_opportunity_board" in src
    assert "def get_crypto_header" in src


def test_engineering_console_data_module():
    assert "def get_engineering_truth_summary" in _src("data/engineering_console.py")


# ── placement rules ───────────────────────────────────────────────────────────


def test_archived_futures_not_top_level():
    assert "ARCHIVED FUTURES" not in _app_src()


def test_archived_futures_in_engineering_console():
    src = _src("widgets/pages/engineering_console.py")
    assert "mes_dashboard" in src or "render_futures" in src


def test_trade_approval_not_top_level():
    assert "TRADE APPROVAL" not in _app_src(), (
        "Trade approval must live inside CRYPTO, not as a standalone top-level tab"
    )


def test_forecast_heartbeat_check_in_page():
    src = _src("widgets/pages/forecast_page.py")
    assert "heartbeat" in src.lower() or "stale" in src.lower(), (
        "forecast_page.py must check heartbeat staleness"
    )
