"""
scripts/validate.py — Stable wrapper for the pre-flight validator.

Keeps the public entrypoint (`python3 scripts/validate.py`) deterministic by
delegating to the validator body file from a clean repo-root run-path.
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)

os.chdir(_REPO_ROOT)
_cmd = (
    f"{sys.executable} -B -c "
    f"\"import os, runpy; "
    f"os.chdir({_REPO_ROOT!r}); "
    f"runpy.run_path({os.path.join(_SCRIPT_DIR, 'validate_body.py')!r}, run_name='__main__')\""
)
raise SystemExit(os.system(_cmd) >> 8)
