"""
scripts/boot.py — launchd-safe boot wrapper for Python 3.12/3.14.

Root cause: When Python is started as `python3 main.py`, macOS/launchd
places an OS-level exec() lock on main.py. Python's import system then
hits EDEADLK when it tries to open any .py file that shares an inode/lock
with main.py in the import chain.

Fix: Run THIS tiny file as the script argument. boot.py only imports stdlib
(already linked into the Python binary — no new file opens needed). It then
reads main.py as raw bytes via open() and exec()s it in a fresh namespace.
main.py is never the argv[1] script, so launchd never locks it.
"""

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
main_path = os.path.join(PROJ, "main.py")

# v18.17: System is strictly LIVE.
# Note: ALGO_BOOT_MODE, ALGO_LIVE_CONFIRM, and --confirm-live are supported for
# controlled launch compatibility and safety, but main.py always boots LIVE.
_mode = os.environ.get("ALGO_BOOT_MODE", "live").lower()
_confirm = os.environ.get("ALGO_LIVE_CONFIRM", "").strip() == "I UNDERSTAND"

# Check for CLI flags
for i, arg in enumerate(sys.argv):
    if arg == "--mode" and i + 1 < len(sys.argv):
        _mode = sys.argv[i + 1].lower()
    if arg == "--confirm-live":
        _confirm = True
        os.environ["ALGO_LIVE_CONFIRM"] = "I UNDERSTAND"

if _mode == "live" and not _confirm and not os.environ.get("GITHUB_ACTIONS"):
    print("boot.py: refusing live launch without ALGO_LIVE_CONFIRM='I UNDERSTAND' or --confirm-live", file=sys.stderr)
    sys.exit(2)

sys.argv = [main_path]

os.chdir(PROJ)
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

with open(main_path, "rb") as _f:
    _src = _f.read()

exec(
    compile(_src, main_path, "exec"),
    {
        "__name__": "__main__",
        "__file__": main_path,
        "__doc__": None,
        "__package__": None,
        "__spec__": None,
        "__builtins__": __builtins__,
    },
)
