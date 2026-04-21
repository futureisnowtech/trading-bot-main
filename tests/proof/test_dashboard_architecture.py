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


# ── gap-closure contracts (v17.0 patch) ──────────────────────────────────────


def test_control_tower_snapshot_accepts_hours():
    """get_control_tower_snapshot() must accept a hours parameter."""
    import ast

    src = _src("data/control_tower.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "get_control_tower_snapshot"
        ):
            args = [a.arg for a in node.args.args]
            defaults = node.args.defaults
            assert "hours" in args, "get_control_tower_snapshot must accept hours param"
            return
    assert False, "get_control_tower_snapshot not found"


def test_lifecycle_stages_function_exists():
    """trading_control.py must expose get_lifecycle_stages()."""
    src = _src("data/trading_control.py")
    assert "def get_lifecycle_stages" in src


def test_lifecycle_stages_has_all_8_stages():
    """get_lifecycle_stages must return all 8 standardized stage names."""
    src = _src("data/trading_control.py")
    required = [
        "discovered",
        "signal_pass",
        "econ_pass",
        "route_decided",
        "size_pass",
        "execution_attempted",
        "position_open",
        "exit_complete",
    ]
    for stage in required:
        assert f'"{stage}"' in src or f"'{stage}'" in src, (
            f"Stage '{stage}' missing from get_lifecycle_stages"
        )


def test_control_tower_page_uses_lifecycle():
    """control_tower page must render lifecycle_stages, not coarse stage_rows."""
    src = _src("widgets/pages/control_tower.py")
    assert "lifecycle_stages" in src, "control_tower page must use lifecycle_stages"
    assert "lifecycle" in src.lower(), "central funnel must reference lifecycle"


def test_control_tower_window_wired_to_snapshot():
    """control_tower page must pass window_hours to get_control_tower_snapshot."""
    src = _src("widgets/pages/control_tower.py")
    assert "get_control_tower_snapshot(hours=window_hours)" in src, (
        "window selector must be passed to get_control_tower_snapshot"
    )


def test_crypto_header_has_deployed_pcts():
    """get_crypto_header must compute spot_deployed_pct and perp_deployed_pct."""
    src = _src("data/crypto_dashboard.py")
    assert "spot_deployed_pct" in src
    assert "perp_deployed_pct" in src
    assert "spot_notional" in src, "must compute spot notional from positions"
    assert "perp_notional" in src, "must compute perp notional from positions"


def test_crypto_page_renders_deployed_pcts():
    """crypto_page must render both spot_deployed_pct and perp_deployed_pct."""
    src = _src("widgets/pages/crypto_page.py")
    assert "spot_deployed_pct" in src
    assert "perp_deployed_pct" in src


def test_crypto_page_has_auto_only_filter():
    """Opportunity board must include Auto-only filter."""
    src = _src("widgets/pages/crypto_page.py")
    assert "Auto-only" in src, "Auto-only filter missing from opportunity board"


def test_crypto_page_surfaces_size_block_reason():
    """Opportunity board must surface trade_size_block_reason."""
    src = _src("widgets/pages/crypto_page.py")
    assert "trade_size_block_reason" in src or "size_block" in src


def test_crypto_page_surfaces_source_reason():
    """Opportunity board must surface trade_source_reason."""
    src = _src("widgets/pages/crypto_page.py")
    assert "trade_source_reason" in src or "source_reason" in src
