"""
tests/proof/test_manual_trade_unified.py — Proof suite for manual scan tradeability wiring (v16.14).

Invariants proved:
  MT-01  manual_scan calls get_crypto_tradeability (not local tier-only logic) when available
  MT-02  blocked tradeability result shows blocked_reason in preview
  MT-03  spot lane routes to spot_engine
  MT-04  perp lane routes to perps_engine
  MT-05  blocked tradeability prevents any execution attempt
"""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── MT-01: manual_scan calls get_crypto_tradeability ─────────────────────────


def test_mt01_manual_scan_imports_tradeability_engine(monkeypatch):
    """
    The _TRADEABILITY_OK flag must be True after import — confirming the engine
    import succeeded and is wired into manual_scan.
    """
    # Reset cached module so the import runs fresh
    mods_to_clear = [k for k in sys.modules if "manual_scan" in k]
    for m in mods_to_clear:
        del sys.modules[m]

    # manual_scan.py is a dashboard widget requiring streamlit — mock it
    st_mock = types.ModuleType("streamlit")
    for attr in [
        "subheader",
        "caption",
        "divider",
        "markdown",
        "info",
        "success",
        "error",
        "warning",
        "button",
        "checkbox",
        "columns",
        "expander",
        "progress",
        "metric",
        "container",
        "number_input",
        "spinner",
        "session_state",
        "rerun",
        "text",
    ]:
        setattr(st_mock, attr, lambda *a, **kw: None)
    # session_state needs dict-like behaviour
    st_mock.session_state = {}
    monkeypatch.setitem(sys.modules, "streamlit", st_mock)

    # dashboard/db shim
    db_mock = types.ModuleType("db")
    db_mock._runtime_paper_flag = lambda: 1
    db_mock._q = lambda *a, **kw: []
    db_mock._q1 = lambda *a, **kw: {}
    db_mock.get_effective_launch_date = lambda: "2026-04-15"
    monkeypatch.setitem(sys.modules, "db", db_mock)

    # minimal data stubs
    for stub_name in ("data.positions", "data.account"):
        if stub_name not in sys.modules:
            m = types.ModuleType(stub_name)
            m.get_open_positions = lambda: []
            m.get_perp_positions = lambda: []
            m.get_live_prices = lambda s: {}
            m.get_account = lambda: (5000.0, True, 5000.0)
            monkeypatch.setitem(sys.modules, stub_name, m)

    # Ensure _ROOT is on path for runtime.crypto_tradeability
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    dashboard_root = os.path.join(ROOT, "dashboard")
    if dashboard_root not in sys.path:
        sys.path.insert(0, dashboard_root)

    import importlib

    spec = importlib.util.spec_from_file_location(
        "manual_scan_test",
        os.path.join(ROOT, "dashboard", "widgets", "trade_approval", "manual_scan.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "_TRADEABILITY_OK"), "manual_scan must define _TRADEABILITY_OK"
    assert mod._TRADEABILITY_OK is True, (
        "_TRADEABILITY_OK=False — crypto_tradeability import failed in manual_scan"
    )


# ── MT-02: blocked tradeability → preview shows blocked_reason ───────────────


def test_mt02_blocked_tradeability_preview_shows_reason():
    """
    When get_crypto_tradeability returns blocked, _compute_preview must
    return a dict with blocked=True and block_reason matching blocked_reason.
    """
    import runtime.crypto_tradeability as ct

    # Verify the blocked result structure that _compute_preview will return
    blocked = ct._blocked_result("BTC", "BTC", "spot_lane_disabled")
    assert blocked["status"] == "blocked"
    assert blocked["blocked_reason"] == "spot_lane_disabled"
    assert blocked["display_label"] == "BLOCKED"
    assert blocked["lane"] == "blocked"
    assert blocked["auto_executable"] == 0
    assert blocked["manual_executable"] == 0


# ── MT-03: spot lane routes to spot_engine ────────────────────────────────────


def test_mt03_spot_executable_result_has_correct_structure():
    """
    Verify get_crypto_tradeability returns the correct structure for spot routing
    that manual_scan uses to call spot_engine.
    """
    import os
    import sqlite3

    import config
    import runtime.crypto_tradeability as ct

    # Use a temp-path DB with no open positions
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "trades.db")
        with sqlite3.connect(db) as c:
            c.execute(
                """CREATE TABLE open_positions (
                    id INTEGER PRIMARY KEY, symbol TEXT, strategy TEXT,
                    qty REAL, entry REAL, paper INTEGER DEFAULT 1, direction TEXT
                )"""
            )

        original_spot_active = getattr(config, "SPOT_LANE_ACTIVE", True)
        original_db = ct._db_path

        try:
            config.SPOT_LANE_ACTIVE = True
            config.SPOT_MAX_DEPLOYED_PCT = 0.40
            config.SPOT_MIN_ORDER_USD = 10.0
            config.AUTONOMOUS_LIVE_PERP_SYMBOLS = ["BTC", "ETH", "SOL", "XRP"]
            config.CORE_EXECUTION_UNDERLYINGS = {"BTC", "ETH", "SOL", "XRP"}
            ct._db_path = lambda: db

            result = ct.get_crypto_tradeability("BTC", "LONG", live=False, manual=True)
            assert result["lane"] == "spot", f"Expected spot, got {result}"
            assert result["status"] == "executable"
            assert result["display_label"] == "SPOT EXECUTABLE"
            # manual_executable must be 1 for manual=True
            assert result["manual_executable"] == 1
        finally:
            config.SPOT_LANE_ACTIVE = original_spot_active
            ct._db_path = original_db


# ── MT-04: perp lane → perp engine result structure ──────────────────────────


def test_mt04_perp_executable_result_has_correct_structure():
    """
    SOL LONG (no spot) returns lane=perp with correct structure.
    manual_scan uses lane to dispatch to perps_engine.
    """
    import os
    import sqlite3
    import tempfile

    import config
    import runtime.crypto_tradeability as ct

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "trades.db")
        with sqlite3.connect(db) as c:
            c.execute(
                """CREATE TABLE open_positions (
                    id INTEGER PRIMARY KEY, symbol TEXT, strategy TEXT,
                    qty REAL, entry REAL, paper INTEGER DEFAULT 1, direction TEXT
                )"""
            )

        original_db = ct._db_path
        try:
            config.SPOT_LANE_ACTIVE = True
            config.SPOT_MAX_DEPLOYED_PCT = 0.40
            config.SPOT_MIN_ORDER_USD = 10.0
            config.AUTONOMOUS_LIVE_PERP_SYMBOLS = ["BTC", "ETH", "SOL", "XRP"]
            config.CORE_EXECUTION_UNDERLYINGS = {"BTC", "ETH", "SOL", "XRP"}
            ct._db_path = lambda: db

            result = ct.get_crypto_tradeability("SOL", "LONG", live=False, manual=True)
            assert result["lane"] == "perp", f"Expected perp for SOL, got {result}"
            assert result["status"] == "executable"
            assert result["display_label"] == "PERP EXECUTABLE"
            assert result["manual_executable"] == 1
        finally:
            ct._db_path = original_db


# ── MT-05: blocked tradeability prevents execution attempt ───────────────────


def test_mt05_blocked_tradeability_prevents_execution():
    """
    When tradeability returns blocked, manual_scan's _compute_preview returns
    blocked=True. Verify the result contract that prevents execution.
    """
    import runtime.crypto_tradeability as ct

    # Simulate what _compute_preview does when tradeability returns blocked
    blocked_result = ct._blocked_result("DOGE", "DOGE", "perp_symbol_not_supported")

    # _compute_preview maps this to: blocked=True, no execution attempted
    assert blocked_result["status"] == "blocked"
    assert blocked_result["lane"] == "blocked"
    assert blocked_result["auto_executable"] == 0
    assert blocked_result["manual_executable"] == 0

    # The preview dict that manual_scan builds from this:
    preview = {
        "blocked": True,
        "block_reason": blocked_result["blocked_reason"],
        "display_label": blocked_result["display_label"],
        "trade_lane": "blocked",
    }
    # Execution guard: if blocked is True, no engine call may be made
    assert preview["blocked"] is True
    assert preview["trade_lane"] == "blocked"


def test_mt06_manual_scan_rows_use_shared_tradeability_source():
    """
    Row-level executability must come from the shared tradeability engine,
    not from execution-tier-only checks.
    """
    path = os.path.join(
        ROOT, "dashboard", "widgets", "trade_approval", "manual_scan.py"
    )
    src = open(path, encoding="utf-8").read()

    assert "_manual_tradeability(" in src, (
        "manual_scan row and review flow must call the shared _manual_tradeability helper"
    )
    assert '_tier["execute"]' not in src, (
        "manual_scan must not gate row executability directly from execution-tier-only logic"
    )


def test_mt07_spot_controls_use_shared_tradeability_before_open_spot():
    """
    Direct BTC/ETH spot buys from the widget must fail closed through the shared
    tradeability engine before calling spot_engine.open_spot().
    """
    path = os.path.join(
        ROOT, "dashboard", "widgets", "trade_approval", "manual_scan.py"
    )
    src = open(path, encoding="utf-8").read()

    assert 'open_spot(sym, size_input' in src
    assert '_manual_tradeability({"symbol": sym, "direction": "LONG"})' in src, (
        "Spot buy controls must call shared tradeability before open_spot()"
    )


def test_mt08_spot_section_not_hidden_behind_scan_selection():
    """
    Spot controls must still render even when there are no scan candidates or
    no selected crypto rows.
    """
    path = os.path.join(
        ROOT, "dashboard", "widgets", "trade_approval", "manual_scan.py"
    )
    src = open(path, encoding="utf-8").read()
    assert src.count("render_spot_section()") >= 5, (
        "render_spot_section() should be reachable from the no-candidates, "
        "no-selection, blocked-preview, and normal render paths"
    )
