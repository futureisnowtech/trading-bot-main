import sqlite3

db = sqlite3.connect("logs/trades.db")
c = db.execute(
    "DELETE FROM trades WHERE broker='ibkr_stocks' AND symbol='UBER' AND action='BUY' AND order_id='27'"
)
db.commit()
print("removed:", c.rowcount, "row")
db.close()
