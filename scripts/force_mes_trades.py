"""
scripts/force_mes_trades.py — Force 10 MES paper trades to verify functionality.

Runs 5 LONG round trips then 5 SHORT round trips.
Does NOT require FUTURES_ENABLED=true — directly imports IBKRBroker.
Reports connection status, price, trade results, and DB verification.
"""

import os
import sys
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, PAPER_TRADING, FUTURES_NUM_CONTRACTS

SEP = "=" * 60


def _get_trades_db(after_ts: str) -> list:
    """Fetch MES trades from DB written after after_ts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts, action, qty, price, pnl_usd, fee_usd, order_id, notes "
        "FROM trades WHERE symbol='MES' AND ts >= ? ORDER BY rowid ASC",
        (after_ts,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    print(SEP)
    print("  MES FUTURES FUNCTIONALITY AUDIT — 10 FORCED PAPER TRADES")
    print(SEP)
    print(f"  Paper mode : {PAPER_TRADING}")
    print(f"  DB path    : {DB_PATH}")
    print()

    # ── Import broker ─────────────────────────────────────────────────────────
    try:
        from execution.ibkr_broker import IBKRBroker

        broker = IBKRBroker()
        print("[1/4] IBKRBroker imported OK")
    except Exception as e:
        print(f"[FAIL] IBKRBroker import error: {e}")
        sys.exit(1)

    # ── Connect ───────────────────────────────────────────────────────────────
    print("[2/4] Connecting to TWS on port 7497 ...")
    connected = broker.connect()
    print(f"      Connected: {connected}")
    if not connected:
        print(
            "      ⚠  TWS not reachable — trades will be paper-logged (offline mode)."
        )
        print("      This still proves the logging pipeline works end-to-end.")
    print()

    # ── Price ─────────────────────────────────────────────────────────────────
    print("[3/4] Fetching MES price ...")
    price = broker.get_price("MES")
    print(f"      MES price : {price:.2f}")
    stop_long = round(price - 4.0, 2)
    target_long = round(price + 8.0, 2)
    stop_short = round(price + 4.0, 2)
    target_short = round(price - 8.0, 2)
    print(f"      LONG  SL={stop_long}  TP={target_long}")
    print(f"      SHORT SL={stop_short}  TP={target_short}")
    print()

    # ── Forced trades ─────────────────────────────────────────────────────────
    print("[4/4] Executing 10 forced paper round-trips ...")
    print()

    start_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    results = []
    total_pnl = 0.0
    total_fees = 0.0

    # 5 LONG round trips
    for i in range(5):
        print(f"  LONG #{i + 1}: entering ...")
        entry = broker.buy_mes(
            qty=1, stop_price=stop_long, target_price=target_long, reason="force_test"
        )
        if not entry:
            results.append({"trade": f"LONG#{i + 1}", "error": "buy_mes returned None"})
            continue
        time.sleep(1.5)  # let price move slightly

        print(f"  LONG #{i + 1}: exiting ...")
        ext = broker.sell_mes(
            qty=1, entry_price=entry["price"], reason="force_test_exit"
        )
        if not ext:
            results.append(
                {"trade": f"LONG#{i + 1}", "error": "sell_mes returned None"}
            )
            continue

        pnl = ext.get("pnl", 0.0)
        fee = 0.47 * 2  # entry + exit
        total_pnl += pnl
        total_fees += fee
        results.append(
            {
                "trade": f"LONG#{i + 1}",
                "entry_price": entry["price"],
                "exit_price": ext.get("exit_price", 0),
                "pnl": pnl,
                "order_id": entry.get("order_id", "?"),
            }
        )
        print(f"         P&L: ${pnl:+.2f}  order_id={entry.get('order_id')}")
        time.sleep(0.5)

    print()

    # 5 SHORT round trips
    for i in range(5):
        print(f"  SHORT #{i + 1}: entering ...")
        entry = broker.short_mes(
            qty=1, stop_price=stop_short, target_price=target_short, reason="force_test"
        )
        if not entry:
            results.append(
                {"trade": f"SHORT#{i + 1}", "error": "short_mes returned None"}
            )
            continue
        time.sleep(1.5)

        print(f"  SHORT #{i + 1}: exiting ...")
        ext = broker.cover_mes(
            qty=1, entry_price=entry["price"], reason="force_test_exit"
        )
        if not ext:
            results.append(
                {"trade": f"SHORT#{i + 1}", "error": "cover_mes returned None"}
            )
            continue

        pnl = ext.get("pnl", 0.0)
        fee = 0.47 * 2
        total_pnl += pnl
        total_fees += fee
        results.append(
            {
                "trade": f"SHORT#{i + 1}",
                "entry_price": entry["price"],
                "exit_price": ext.get("exit_price", 0),
                "pnl": pnl,
                "order_id": entry.get("order_id", "?"),
            }
        )
        print(f"         P&L: ${pnl:+.2f}  order_id={entry.get('order_id')}")
        time.sleep(0.5)

    print()

    # ── DB verification ───────────────────────────────────────────────────────
    db_trades = _get_trades_db(start_ts)

    print(SEP)
    print("  RESULTS SUMMARY")
    print(SEP)
    print(f"  Connection  : {'LIVE (TWS)' if connected else 'OFFLINE (paper-logged)'}")
    print(f"  MES price   : {price:.2f}")
    print(f"  Trades attempted : 10 round-trips (5L + 5S)")
    print(f"  DB rows written  : {len(db_trades)}  (expected ~20 for 10 round-trips)")
    print(f"  Total P&L   : ${total_pnl:+.2f}")
    print(f"  Total fees  : ${total_fees:.2f}")
    print()

    print("  Trade log:")
    for r in results:
        if "error" in r:
            print(f"    {r['trade']:10s}  ERROR: {r['error']}")
        else:
            print(
                f"    {r['trade']:10s}  entry={r['entry_price']:.2f}  "
                f"exit={r['exit_price']:.2f}  pnl=${r['pnl']:+.2f}  "
                f"id={r['order_id']}"
            )

    print()
    print("  DB rows (first 5):")
    for row in db_trades[:5]:
        print(
            f"    {row['ts']}  {row['action']:6s}  @{row['price']:.2f}  "
            f"pnl={row.get('pnl_usd') or 0:+.2f}  id={row['order_id']}"
        )
    if len(db_trades) > 5:
        print(f"    ... and {len(db_trades) - 5} more rows")

    print()

    errors = [r for r in results if "error" in r]
    if errors:
        print(f"  ⚠  {len(errors)} trade(s) had errors:")
        for e in errors:
            print(f"     {e['trade']}: {e['error']}")
    else:
        print("  ✓ All 10 round-trips completed without exceptions")

    if len(db_trades) >= 10:
        print("  ✓ DB verification: 10+ rows written to trades table")
    else:
        print(f"  ⚠ DB verification: only {len(db_trades)} rows found (expected ~20)")

    print()
    print(SEP)


if __name__ == "__main__":
    main()
