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
import sys
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
main_path = os.path.join(PROJ, 'main.py')

sys.argv = [main_path, '--mode', 'paper']
os.chdir(PROJ)
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

with open(main_path, 'rb') as _f:
    _src = _f.read()

exec(
    compile(_src, main_path, 'exec'),
    {'__name__': '__main__', '__file__': main_path,
     '__doc__': None, '__package__': None, '__spec__': None,
     '__builtins__': __builtins__},
)
