"""
One-shot: remove stale ADA/LINK/ETH spot positions from open_positions.
User is managing those at Coinbase directly.
"""

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "logs" / "trades.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

before = conn.execute(
    "SELECT symbol, strategy FROM open_positions WHERE strategy LIKE 'spot_%' AND paper=0"
).fetchall()
print("Spot positions before:", [dict(r) for r in before])

conn.execute(
    "DELETE FROM open_positions WHERE symbol IN ('ADA','LINK','ETH') "
    "AND strategy LIKE 'spot_%' AND paper=0"
)
conn.commit()

after = conn.execute(
    "SELECT symbol, strategy FROM open_positions WHERE strategy LIKE 'spot_%' AND paper=0"
).fetchall()
print("Spot positions after:", [dict(r) for r in after])
conn.close()
print("Done.")
