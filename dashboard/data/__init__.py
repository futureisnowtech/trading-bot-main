"""Lightweight dashboard data helpers kept for proof compatibility.

When proof tests put ``dashboard/`` ahead of the repo root on ``sys.path``,
``import data.*`` can resolve to this package first. Extend the package search
path so sibling modules from the real top-level ``data/`` package remain
importable through the same namespace.
"""

from __future__ import annotations

from pathlib import Path

_DASHBOARD_DATA_DIR = Path(__file__).resolve().parent
_ROOT_DATA_DIR = _DASHBOARD_DATA_DIR.parents[1] / "data"

if _ROOT_DATA_DIR.exists():
    root_data_path = str(_ROOT_DATA_DIR)
    if root_data_path not in __path__:
        __path__.append(root_data_path)
