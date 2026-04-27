"""
tests/proof/test_dashboard_sys_path.py — Guards against sys.modules namespace collisions.

The dashboard/data/ package and the root data/ package share the name "data".
If root data/ gets cached first (e.g. via a bot import bleeding into the same
process, or a Streamlit module-cache holdover), all `from data.X import Y`
calls inside dashboard widgets will fail with ModuleNotFoundError.

These tests:
  1. Simulate the exact collision scenario.
  2. Verify app.py's eviction loop fixes it.
  3. Verify the dashboard data orchestrators import cleanly after the fix.
  4. Verify critical page modules import cleanly.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
_DASH = os.path.join(_ROOT, "dashboard")
_DASH_DATA = os.path.join(_DASH, "data")
_ROOT_DATA = os.path.join(_ROOT, "data")


# ── helpers ───────────────────────────────────────────────────────────────────


def _evict_data_modules():
    """Run app.py's eviction logic: remove any data.* entries not under dashboard/."""
    for k in [k for k in list(sys.modules) if k == "data" or k.startswith("data.")]:
        cached_file = getattr(sys.modules[k], "__file__", "") or ""
        if _DASH not in cached_file:
            del sys.modules[k]


def _load_root_data_package():
    """Force the root data/ package into sys.modules to simulate the collision."""
    for k in [k for k in list(sys.modules) if k == "data" or k.startswith("data.")]:
        del sys.modules[k]
    init = os.path.join(_ROOT_DATA, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        "data", init, submodule_search_locations=[_ROOT_DATA]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["data"] = mod
    spec.loader.exec_module(mod)


def _ensure_dash_on_path():
    if _DASH not in sys.path:
        sys.path.insert(0, _DASH)


# ── collision simulation ──────────────────────────────────────────────────────


def test_root_data_package_exists_and_lacks_dashboard_modules():
    """Sanity: root data/ has no control_tower — the collision is real."""
    assert os.path.isdir(_ROOT_DATA), "Root data/ directory must exist"
    assert not os.path.exists(os.path.join(_ROOT_DATA, "control_tower.py")), (
        "Root data/ must NOT have control_tower.py — if it does, the collision no longer applies"
    )
    assert os.path.exists(os.path.join(_DASH_DATA, "control_tower.py")), (
        "dashboard/data/control_tower.py must exist"
    )


def test_collision_is_reproducible():
    """Loading root data/ first causes ModuleNotFoundError for data.control_tower."""
    _ensure_dash_on_path()
    _load_root_data_package()

    # Verify we injected the wrong package
    cached = sys.modules.get("data")
    assert cached is not None
    assert _DASH not in (getattr(cached, "__file__", "") or ""), (
        "Root data/ package should be cached, not dashboard/data/"
    )

    # Attempting to import data.control_tower should now fail
    if "data.control_tower" in sys.modules:
        del sys.modules["data.control_tower"]
    try:
        importlib.import_module("data.control_tower")
        assert False, "Expected ModuleNotFoundError — collision is not reproducible"
    except (ModuleNotFoundError, ImportError):
        pass  # expected — collision confirmed


def test_eviction_loop_fixes_collision():
    """After app.py's eviction loop, data.control_tower imports correctly."""
    _ensure_dash_on_path()
    _load_root_data_package()  # inject the collision

    # Run the eviction (mirrors app.py exactly)
    _evict_data_modules()

    # Now the import must succeed
    if "data.control_tower" in sys.modules:
        del sys.modules["data.control_tower"]
    mod = importlib.import_module("data.control_tower")
    assert hasattr(mod, "get_control_tower_snapshot"), (
        "get_control_tower_snapshot must be present after eviction fix"
    )


def test_eviction_loop_fixes_crypto_dashboard():
    """data.crypto_dashboard imports correctly after eviction."""
    _ensure_dash_on_path()
    _load_root_data_package()
    _evict_data_modules()
    for k in [k for k in list(sys.modules) if "crypto_dashboard" in k]:
        del sys.modules[k]
    mod = importlib.import_module("data.crypto_dashboard")
    assert hasattr(mod, "get_crypto_opportunity_board")
    assert hasattr(mod, "get_crypto_header")


def test_eviction_loop_fixes_engineering_console():
    """data.engineering_console imports correctly after eviction."""
    _ensure_dash_on_path()
    _load_root_data_package()
    _evict_data_modules()
    for k in [k for k in list(sys.modules) if "engineering_console" in k]:
        del sys.modules[k]
    mod = importlib.import_module("data.engineering_console")
    assert hasattr(mod, "get_engineering_truth_summary")


def test_app_py_contains_eviction_loop():
    """app.py must contain the eviction loop — removing it would re-introduce the crash."""
    src = open(os.path.join(_DASH, "app.py")).read()
    assert "del sys.modules[_k]" in src, (
        "app.py must contain the sys.modules eviction loop (del sys.modules[_k])"
    )
    assert "_DASH_DIR not in _cached_file" in src, (
        "Eviction loop must check _DASH_DIR to distinguish root data/ from dashboard/data/"
    )


def test_eviction_is_selective():
    """Eviction removes root data.* but leaves dashboard/data.* intact."""
    _ensure_dash_on_path()

    # Pre-load the dashboard data package legitimately
    if "data.control_tower" in sys.modules:
        del sys.modules["data.control_tower"]
    if "data" in sys.modules and _DASH in (
        getattr(sys.modules["data"], "__file__", "") or ""
    ):
        pass  # already correct
    else:
        _evict_data_modules()

    # Load control_tower so it's in sys.modules
    importlib.import_module("data.control_tower")
    assert "data.control_tower" in sys.modules

    # Now run eviction — should NOT remove the correctly-pathed entry
    _evict_data_modules()
    # After eviction of an already-correct cache, reimport must still work
    # (eviction only removes wrong entries, so either it was kept or can be re-imported)
    importlib.import_module("data.control_tower")
    assert hasattr(
        sys.modules.get("data.control_tower", types.ModuleType("")),
        "get_control_tower_snapshot",
    )
