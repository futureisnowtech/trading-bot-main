import sqlite3
import collections

from config import DB_PATH, TRADE_DATA_START_DATE


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Map contracts to resolutions
    cursor.execute("""
        SELECT fc.local_symbol, fr.resolved_side, fr.resolved_value 
        FROM forecast_resolutions fr
        JOIN forecast_contracts fc ON fr.contract_id = fc.id
    """)
    resolutions = {}
    for row in cursor.fetchall():
        resolutions[row["local_symbol"]] = {
            "side": row["resolved_side"], # 'YES' or 'NO'
            "value": row["resolved_value"] # 1.0 or 0.0
        }
    print(f"Loaded {len(resolutions)} settlements from DB.")

    # 2. Get all trades
    cursor.execute(
        "SELECT * FROM trades WHERE ts >= ? ORDER BY symbol, ts ASC",
        (TRADE_DATA_START_DATE,),
    )
    trades = [dict(r) for r in cursor.fetchall()]
    print(f"Loaded {len(trades)} trades from DB.")

    # Group trades by symbol
    trades_by_symbol = collections.defaultdict(list)
    for t in trades:
        trades_by_symbol[t["symbol"]].append(t)

    total_realized_pnl = 0.0
    total_wins = 0
    total_losses = 0

    updates = []

    for symbol, sym_trades in trades_by_symbol.items():
        # We need to run FIFO matching for YES and NO sides separately
        # (Though usually we only trade one side per symbol)
        sides_queues = {
            "YES": collections.deque(),
            "NO": collections.deque()
        }

        resolution = resolutions.get(symbol)

        for t in sym_trades:
            action = t["action"]
            side = t["contract_side"] or "YES"
            qty = float(t["qty"])
            price = float(t["price"])
            fee = float(t["fee_usd"])
            trade_id = t["id"]

            if action == "BUY":
                # Add to queue
                sides_queues[side].append({
                    "id": trade_id,
                    "qty": qty,
                    "price": price,
                    "fee": fee
                })
            elif action == "SELL":
                # Match against BUY queue of same side
                pnl = 0.0
                remaining_sell_qty = qty
                matched_any = False
                
                while remaining_sell_qty > 0 and sides_queues[side]:
                    buy_lot = sides_queues[side][0]
                    match_qty = min(remaining_sell_qty, buy_lot["qty"])
                    
                    # Pro-rate the buy fee
                    pro_rated_buy_fee = buy_lot["fee"] * (match_qty / buy_lot["qty"])
                    buy_cost = match_qty * buy_lot["price"]
                    sell_revenue = match_qty * price
                    
                    # PnL for this match
                    pnl += (sell_revenue - buy_cost) - pro_rated_buy_fee
                    
                    # Subtract from buy lot
                    buy_lot["qty"] -= match_qty
                    buy_lot["fee"] -= pro_rated_buy_fee
                    if buy_lot["qty"] <= 0:
                        sides_queues[side].popleft()
                        
                    remaining_sell_qty -= match_qty
                    matched_any = True

                # Deduct the sell fee
                pnl -= fee
                won = 1 if pnl > 0 else 0
                
                updates.append((pnl, won, pnl / (qty * price) if (qty * price) > 0 else 0.0, trade_id))
                total_realized_pnl += pnl
                if won:
                    total_wins += 1
                else:
                    total_losses += 1

        # Check remaining BUY lots in queue (they went to settlement)
        for side, queue in sides_queues.items():
            while queue:
                buy_lot = queue.popleft()
                qty = buy_lot["qty"]
                price = buy_lot["price"]
                fee = buy_lot["fee"]
                trade_id = buy_lot["id"]

                pnl = 0.0
                won = 0
                if resolution:
                    # If settled, we know the outcome
                    # Settlement value is 1.0 if the side matches resolved_side, 0.0 otherwise
                    settlement_val = 1.0 if side == resolution["side"] else 0.0
                    pnl = (qty * settlement_val) - (qty * price) - fee
                    won = 1 if settlement_val == 1.0 else 0
                else:
                    # Still open or resolution missing
                    pnl = 0.0
                    won = 0

                updates.append((pnl, won, pnl / (qty * price) if (qty * price) > 0 else 0.0, trade_id))
                total_realized_pnl += pnl
                if won:
                    total_wins += 1
                else:
                    total_losses += 1

    # 3. Update trades in DB
    print("Writing PnL updates to database...")
    for pnl, won, pnl_pct, trade_id in updates:
        cursor.execute("""
            UPDATE trades 
            SET pnl_usd = ?, won = ?, pnl_pct = ?
            WHERE id = ?
        """, (pnl, won, pnl_pct, trade_id))

    conn.commit()
    conn.close()

    print("\n─── Reconstruction Complete ───")
    print(f"Total Trades Reconciled: {len(updates)}")
    print(f"Total Wins: {total_wins} ({(total_wins/len(updates))*100:.1f}%)")
    print(f"Total Losses: {total_losses} ({(total_losses/len(updates))*100:.1f}%)")
    print(f"Total Net PnL: ${total_realized_pnl:.2f}")

if __name__ == "__main__":
    main()
