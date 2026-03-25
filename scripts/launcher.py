"""
scripts/launcher.py — launchd-safe bot launcher.

Python 3.14 on macOS deadlocks in launchd's process environment when importing
large modules (EDEADLK / Resource deadlock avoided). The fix is to spawn the
real bot in a new session (setsid) so it inherits no locks from launchd.

This script is intentionally tiny — only stdlib imports — so it never hits
the deadlock itself.
"""
import subprocess
import sys
import os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON  = sys.executable

proc = subprocess.Popen(
    [PYTHON, '-B', 'main.py', '--mode', 'paper'],
    cwd=PROJECT,
    start_new_session=True,   # setsid() — breaks launchd lock inheritance
    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
)
sys.exit(proc.wait())
