"""
Smoke test: every dashboard module must import without error.
Catches UnboundLocalError, NameError, ImportError, and scoping bugs
before they blank out tabs at runtime.
"""

import sys
import types
import importlib
import pytest

# ---------------------------------------------------------------------------
# Stub out streamlit so widgets can be imported without a running server
# ---------------------------------------------------------------------------


def _make_st_stub():
    st = types.ModuleType("streamlit")

    # decorators that are no-ops
    def _noop_deco(*a, **kw):
        if len(a) == 1 and callable(a[0]):
            return a[0]
        return lambda f: f

    for name in (
        "fragment",
        "cache_data",
        "cache_resource",
        "experimental_memo",
        "experimental_singleton",
    ):
        setattr(st, name, _noop_deco)

    # common UI calls — just return harmless values
    _noop = lambda *a, **kw: None
    _false = lambda *a, **kw: False
    _col = types.SimpleNamespace(
        markdown=_noop,
        metric=_noop,
        caption=_noop,
        dataframe=_noop,
        info=_noop,
        error=_noop,
        success=_noop,
        warning=_noop,
        expander=lambda *a, **kw: _ctx(),
        columns=lambda n, **kw: [_col] * (n if isinstance(n, int) else len(n)),
        code=_noop,
        write=_noop,
        selectbox=_false,
        checkbox=_false,
        button=_false,
        text_input=lambda *a, **kw: "",
        number_input=lambda *a, **kw: 0,
        toggle=_false,
        empty=lambda: _col,
        image=_noop,
        plotly_chart=_noop,
        altair_chart=_noop,
        table=_noop,
        json=_noop,
        divider=_noop,
        header=_noop,
        subheader=_noop,
        title=_noop,
        spinner=lambda *a, **kw: _ctx(),
        container=lambda *a, **kw: _ctx(),
        tabs=lambda names: [_col] * len(names),
        rerun=_noop,
        stop=_noop,
    )

    class _ctx:
        def __enter__(self):
            return _col

        def __exit__(self, *a):
            pass

    for attr in dir(_col):
        if not attr.startswith("_"):
            setattr(st, attr, getattr(_col, attr))

    st.session_state = {}
    st.columns = lambda n, **kw: [_col] * (n if isinstance(n, int) else len(n))
    st.tabs = lambda names: [_col] * len(names)
    st.sidebar = _col
    return st


sys.modules.setdefault("streamlit", _make_st_stub())
sys.modules.setdefault("plotly", types.ModuleType("plotly"))
sys.modules.setdefault("plotly.graph_objects", types.ModuleType("plotly.graph_objects"))
sys.modules.setdefault("plotly.express", types.ModuleType("plotly.express"))
sys.modules.setdefault("altair", types.ModuleType("altair"))

# ---------------------------------------------------------------------------
# Add dashboard root to sys.path (same as app.py does)
# ---------------------------------------------------------------------------
import os

_DASH = os.path.join(os.path.dirname(__file__), "../../dashboard")
_DASH = os.path.abspath(_DASH)
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)

# ---------------------------------------------------------------------------
# The modules to smoke-test
# ---------------------------------------------------------------------------
DASHBOARD_MODULES = [
    # data layer
    "db",
    "formatters",
    "tooltips",
    "data.account",
    "data.balance",
    "data.execution",
    "data.forecast",
    "data.futures",
    "data.health",
    "data.integrity",
    "data.journal_health",
    "data.notifications",
    "data.performance",
    "data.positions",
    "data.scanner_data",
    # widgets
    "widgets.mission_control.status_hero",
    "widgets.mission_control.activity_log",
    "widgets.mission_control.alert_feed",
    "widgets.mission_control.decision_quality",
    "widgets.mission_control.edge_quality",
    "widgets.mission_control.equity_curve",
    "widgets.mission_control.execution_quality",
    "widgets.mission_control.failure_modes",
    "widgets.mission_control.open_positions",
    "widgets.mission_control.scanner_funnel",
    "widgets.mission_control.system_health",
    "widgets.crypto_performance.deep_analysis",
    "widgets.forecast.forecast_dashboard",
    "widgets.futures.mes_dashboard",
    "widgets.system_settings.dev_config",
    "widgets.trade_approval.manual_scan",
    "widgets.trade_approval.scan_breakdown",
    "data.scan_trace",
    # v17.0 data orchestrators
    "data.control_tower",
    "data.crypto_dashboard",
    "data.engineering_console",
    # v17.0 page widgets
    "widgets.pages.control_tower",
    "widgets.pages.crypto_page",
    "widgets.pages.stocks_page",
    "widgets.pages.forecast_page",
    "widgets.pages.mes_page",
    "widgets.pages.performance_lab",
    "widgets.pages.engineering_console",
]


@pytest.mark.parametrize("module", DASHBOARD_MODULES)
def test_dashboard_module_imports(module):
    """Each dashboard module must import cleanly — no NameError, UnboundLocalError, etc."""
    # Force a fresh import so edits are picked up between test runs
    full = module  # already relative to dashboard root on sys.path
    if full in sys.modules:
        del sys.modules[full]
    importlib.import_module(full)
