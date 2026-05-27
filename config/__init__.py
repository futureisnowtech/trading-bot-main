"""
config/__init__.py — Compatibility shim.

The config/ package was added to hold venue_specs.py and alpha_specs.py.
Because Python prefers a package (directory) over a module file when both
exist with the same name, this __init__.py re-exports every symbol from the
original config.py so that `from config import X` continues to work for all
existing callers without modification.

Sub-modules are available as:
    from config.venue_specs import KRAKEN_TAKER_FEE, MES_POINT_VALUE
    from config.alpha_specs import ENTRY_THRESHOLDS
"""

from __future__ import annotations

import importlib.util
import os

# Load the root-level config.py by file path so we bypass the package/module
# name conflict that would occur with a plain `import config`.
_config_py_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py"
)

_spec = importlib.util.spec_from_file_location("_config_root", _config_py_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Expose all public names from config.py in this package's namespace.
for _name in dir(_mod):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_mod, _name)

# Keep a clean namespace — remove loader artefacts
del _name, _mod, _spec, _config_py_path
