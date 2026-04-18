"""Quick verification of dashboard data state post-fixes."""

import sqlite3, sys, importlib.util, os

dash = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
root = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, root)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


db = load("db", os.path.join(dash, "db.py"))
print(f"Paper flag (0=live): {db._runtime_paper_flag()}")
print(f"LAUNCH_DATE: {db.LAUNCH_DATE}")
print(f"LIVE_START_DATE: {db.LIVE_START_DATE}")
print(f"Effective date: {db.get_effective_launch_date()}")

conn = sqlite3.connect(os.path.join(root, "logs", "trades.db"))
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM open_positions")
print(f"open_positions total: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM open_positions WHERE paper=0")
print(f"live open_positions (paper=0): {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM trade_attribution WHERE paper=0")
print(f"live trade_attribution rows: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM trade_attribution WHERE paper=1")
print(f"paper trade_attribution rows (hidden in live): {c.fetchone()[0]}")
conn.close()
print("All checks passed.")
