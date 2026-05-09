"""
scripts/boot.py — launchd-safe boot wrapper for Python 3.14 on macOS.

Root cause: When Python is started as `python3 main.py`, macOS/launchd
places an OS-level exec() lock on main.py. Python 3.14's import system then
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

def _resolve_mode(argv: list[str]) -> tuple[str, bool]:
    """
    Resolve the boot mode before any project import can cache False.

    Priority:
      1. Explicit CLI flag:   scripts/boot.py --mode paper|live
      2. Explicit env var:    ALGO_BOOT_MODE=paper|live
      3. Safe default:        paper
    """
    mode = os.environ.get("ALGO_BOOT_MODE", "paper").strip().lower() or "paper"
    confirm_live = os.environ.get("ALGO_LIVE_CONFIRM", "").strip() == "I UNDERSTAND"

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1].strip().lower()
            i += 2
            continue
        if arg == "--confirm-live":
            confirm_live = True
        i += 1

    if mode not in {"paper", "live"}:
        print(f"boot.py: unsupported mode '{mode}'", file=sys.stderr)
        sys.exit(2)

    return mode, confirm_live


BOOT_MODE, LIVE_CONFIRMED = _resolve_mode(sys.argv)

# Force the target mode in the environment BEFORE any import can cache
# False from .env. config.py calls load_dotenv() + evaluates the flag
# at module import time, and main.py pre-warms config-related imports before
# parse_args() gets a chance to override anything.
os.environ["False"] = "false" if BOOT_MODE == "live" else "true"

if BOOT_MODE == "live":
    if not LIVE_CONFIRMED:
        print(
            "boot.py: refusing live launch without ALGO_LIVE_CONFIRM='I UNDERSTAND' "
            "or --confirm-live",
            file=sys.stderr,
        )
        sys.exit(2)
    os.environ["ALGO_LIVE_CONFIRM"] = "I UNDERSTAND"
    sys.argv = [main_path, "--mode", "live"]
else:
    os.environ.pop("ALGO_LIVE_CONFIRM", None)
    sys.argv = [main_path, "--mode", "paper"]
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
