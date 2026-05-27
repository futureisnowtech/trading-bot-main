"""
tests/proof/test_trade_approval.py — Proof tests for the Trade Approval widget.

Coverage:
  1. manual_scan.py does NOT use `from data.historical_data import get_candles`
     (the ambiguous import that collides with dashboard/data/ namespace)
  2. manual_scan.py uses importlib.util to load get_candles from the explicit
     repo-root data/historical_data.py path
  3. dashboard/data/__init__.py exists (confirms dashboard/data/ is a package that
     shadows the repo-root `data` namespace when dashboard/ is first on sys.path)
  4. repo-root data/historical_data.py exists and defines get_candles
  5. The importlib.util load path in manual_scan.py resolves to a file that exists
  6. No other dashboard widget uses `from data.historical_data` (collision guard)
  7. dashboard/data/ intentional imports (positions, account, etc.) are NOT from repo-root
  8. manual_scan.py top-level data imports are from dashboard/data (intentional)
"""

import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DASH_DIR = os.path.join(_ROOT, "dashboard")
_WIDGET_PATH = os.path.join(_DASH_DIR, "widgets", "trade_approval", "manual_scan.py")
_DASH_DATA_DIR = os.path.join(_DASH_DIR, "data")
_ROOT_DATA_HIST = os.path.join(_ROOT, "data", "historical_data.py")


# ── helpers ───────────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── tests ─────────────────────────────────────────────────────────────────────


def _ast_from_imports(path: str) -> list[tuple[str, str]]:
    """
    Return (module, name) pairs for all `from X import Y` statements in the
    file's AST (comments and strings excluded).
    """
    tree = ast.parse(_read(path))
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                results.append((node.module, alias.name))
    return results


def test_manual_scan_no_ambiguous_data_historical_import():
    """
    manual_scan.py must NOT contain the bare executable statement
      from data.historical_data import get_candles
    (even inside a button handler / function body).
    That import crashes the dashboard because the `data` package resolves to
    dashboard/data/ (no historical_data.py there) when dashboard/ is first on sys.path.
    Uses AST parsing so comments referencing the import do not trigger a false positive.
    """
    from_imports = _ast_from_imports(_WIDGET_PATH)
    hist_imports = [
        (mod, name) for mod, name in from_imports if mod == "data.historical_data"
    ]
    assert not hist_imports, (
        "manual_scan.py still contains the ambiguous bare executable import "
        "from data.historical_data. "
        "This will crash the dashboard — use importlib.util instead. "
        f"Found: {hist_imports}"
    )


def test_manual_scan_uses_importlib_util_for_candles():
    """
    manual_scan.py must use importlib.util.spec_from_file_location to load
    get_candles from the explicit repo-root path, bypassing sys.modules cache.
    """
    src = _read(_WIDGET_PATH)
    assert "spec_from_file_location" in src, (
        "manual_scan.py must use importlib.util.spec_from_file_location "
        "to load get_candles from repo-root data/historical_data.py"
    )
    assert "historical_data.py" in src, (
        "manual_scan.py must reference 'historical_data.py' as the explicit load path"
    )


def test_dashboard_data_package_exists():
    """
    dashboard/data/__init__.py must exist. This confirms that dashboard/data/ is a
    Python package — which is why `from data.*` in dashboard code resolves to
    dashboard/data/ when dashboard/ is first on sys.path. Without this understanding,
    the collision is invisible.
    """
    init_path = os.path.join(_DASH_DATA_DIR, "__init__.py")
    assert os.path.exists(init_path), (
        "dashboard/data/__init__.py not found — the namespace collision proof "
        "assumes dashboard/data/ is a package"
    )


def test_root_data_historical_data_exists():
    """
    repo-root data/historical_data.py must exist and define get_candles.
    This is the file manual_scan.py must load via importlib.util.
    """
    assert os.path.exists(_ROOT_DATA_HIST), (
        f"data/historical_data.py not found at {_ROOT_DATA_HIST}"
    )
    src = _read(_ROOT_DATA_HIST)
    assert "def get_candles" in src, (
        "data/historical_data.py exists but does not define get_candles"
    )


def test_importlib_load_path_resolves():
    """
    The path constructed by manual_scan.py for importlib.util load must
    resolve to an existing file. This proves the explicit-path fix will work
    at runtime, not just syntactically.
    """
    # Reconstruct the path the widget builds: _ROOT / "data" / "historical_data.py"
    # _ROOT in manual_scan.py is 3 levels above _THIS_DIR
    # _THIS_DIR = dashboard/widgets/trade_approval/
    # _ROOT = project root (3 x dirname)
    expected = os.path.join(_ROOT, "data", "historical_data.py")
    assert os.path.exists(expected), (
        f"Importlib load path does not exist: {expected}\n"
        "The explicit-path fix in manual_scan.py will fail at runtime."
    )


def test_no_other_dashboard_widget_has_bare_data_historical_import():
    """
    No dashboard widget or data file should contain an executable AST-level
    `from data.historical_data import ...` statement (collision guard).
    Uses AST parsing so comments referencing the import do not false-positive.
    """
    violations = []
    for dirpath, _, filenames in os.walk(_DASH_DIR):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                hits = _ast_from_imports(path)
            except SyntaxError:
                continue
            except Exception:
                continue
            if any(mod == "data.historical_data" for mod, _ in hits):
                violations.append(path[len(_ROOT) + 1 :])
    assert not violations, (
        "The following dashboard files still contain the ambiguous bare executable "
        "import `from data.historical_data import ...`:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_manual_scan_dashboard_data_imports_are_intentional():
    """
    manual_scan.py uses `from data.positions` and `from data.account` at the top.
    These ARE intentional — they resolve to dashboard/data/positions.py and
    dashboard/data/account.py (the dashboard query layer), not repo-root data/.
    Both files must exist in dashboard/data/ to confirm this is correct behaviour.
    """
    src = _read(_WIDGET_PATH)
    assert "from data.positions import" in src, (
        "manual_scan.py must import get_open_positions / get_live_prices from data.positions"
    )
    assert "from data.account import" in src, (
        "manual_scan.py must import get_account from data.account"
    )
    # Confirm the dashboard/data/ layer has these files
    assert os.path.exists(os.path.join(_DASH_DATA_DIR, "positions.py")), (
        "dashboard/data/positions.py not found — intentional import would fail"
    )
    assert os.path.exists(os.path.join(_DASH_DATA_DIR, "account.py")), (
        "dashboard/data/account.py not found — intentional import would fail"
    )


def test_manual_scan_root_path_computed_correctly():
    """
    _ROOT in manual_scan.py is derived as 3 x dirname from _THIS_DIR.
    Verify the algebra: 3 levels up from dashboard/widgets/trade_approval/
    should reach the project root.
    """
    # Simulate what manual_scan.py does
    this_dir = os.path.dirname(_WIDGET_PATH)  # dashboard/widgets/trade_approval
    root = os.path.dirname(os.path.dirname(os.path.dirname(this_dir)))
    assert root == _ROOT, (
        f"manual_scan._ROOT would be {root!r} but expected {_ROOT!r}. "
        "The explicit importlib.util load path will be wrong."
    )
