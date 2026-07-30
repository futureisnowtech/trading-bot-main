import os
import sys
import json
import sqlite3
import datetime

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.kalshi_broker import get_kalshi_broker
from forecast.db import reconcile_forecast_positions
from research_package.scratch.generate_csvs import generate_trades, generate_quotes, generate_actuals

from config import DB_PATH, POST_PAPER_START_DATE

def parse_iso_to_local_offset(iso_str):
    if not iso_str:
        return ""
    try:
        # Parse UTC time: 2026-06-23T14:45:12.737707Z
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # Convert to Eastern Time offset (-04:00)
        est_tz = datetime.timezone(datetime.timedelta(hours=-4))
        dt_est = dt.astimezone(est_tz)
        return dt_est.isoformat()
    except Exception:
        return iso_str

def ensure_market_and_contract(conn, ticker):
    cursor = conn.cursor()
    # Check if contract exists
    cursor.execute("SELECT id FROM forecast_contracts WHERE local_symbol=?", (ticker,))
    row = cursor.fetchone()
    if row:
        return row[0]
        
    print(f"Contract {ticker} not found in DB. Recreating metadata shims...")
    # Derive market symbol
    parts = ticker.split("-")
    market_symbol = parts[0] + "-" + parts[1] if len(parts) >= 2 else ticker
    
    # Check or insert market
    cursor.execute("SELECT id FROM forecast_markets WHERE market_symbol=?", (market_symbol,))
    m_row = cursor.fetchone()
    if m_row:
        market_id = m_row[0]
    else:
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO forecast_markets 
                (market_symbol, market_name, exchange, category_path, active, first_seen_at, last_seen_at)
            VALUES (?, ?, 'KALSHI', 'Climate and Weather', 0, ?, ?)
        """, (market_symbol, f"Reconstructed Market for {market_symbol}", now_str, now_str))
        market_id = cursor.lastrowid
        
    # Insert contract
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Try to parse strike
    strike = 0.0
    right = 'C'
    if len(parts) >= 3:
        strike_str = parts[2].replace("T", "").replace("B", "")
        try:
            strike = float(strike_str)
        except ValueError:
            pass
        if "B" in parts[2]:
            right = 'P'
            
    cursor.execute("""
        INSERT INTO forecast_contracts
            (market_id, conid, local_symbol, right, strike, currency, exchange, active, first_seen_at, last_seen_at, contract_name)
        VALUES (?, NULL, ?, ?, ?, 'USD', 'KALSHI', 0, ?, ?, ?)
    """, (market_id, ticker, right, strike, now_str, now_str, f"Reconstructed Contract {ticker}"))
    contract_id = cursor.lastrowid
    conn.commit()
    return contract_id

def reconstruct():
    broker = get_kalshi_broker()
    if not broker.connect():
        print("Error: Could not connect to Kalshi.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Pull Fills & Reconstruct trades table
    print("Downloading executed orders from Kalshi...")
    cursor = ""
    api_orders = []
    while True:
        params = {"limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = broker._request("GET", "/trade-api/v2/portfolio/orders", params=params)
        err = broker._extract_error_code(payload)
        if err:
            print(f"Error fetching orders: {err}")
            break
        batch = payload.get("orders") or []
        api_orders.extend(batch)
        cursor = payload.get("cursor", "").strip()
        if not cursor or not batch:
            break
    print(f"Fetched {len(api_orders)} total orders from Kalshi API.")

    inserted_trades = 0
    skipped_trades = 0
    
    for o in api_orders:
        if o.get("status") != "executed":
            continue
        
        qty = float(o.get("fill_count_fp") or 0.0)
        if qty <= 0:
            continue
            
        order_id = o["order_id"]
        # Check if already exists in trades table
        cursor_db = conn.cursor()
        cursor_db.execute("SELECT 1 FROM trades WHERE order_id=?", (order_id,))
        if cursor_db.fetchone():
            skipped_trades += 1
            continue

        ts_local = parse_iso_to_local_offset(o["created_time"])
        ticker = o["ticker"]
        action = str(o["action"]).upper()
        order_type = str(o["type"]).capitalize()
        side = str(o["side"]).upper()
        
        cost_dollars = float(o.get("taker_fill_cost_dollars") or o.get("maker_fill_cost_dollars") or 0.0)
        fees_dollars = float(o.get("taker_fees_dollars") or o.get("maker_fees_dollars") or 0.0)
        price = cost_dollars / qty if qty > 0 else float(o.get("yes_price_dollars") or o.get("no_price_dollars") or 0.0)
        
        # Insert trade
        cursor_db.execute("""
            INSERT INTO trades
            (ts, strategy, broker, symbol, action, order_type, qty, price, value_usd,
             fee_usd, pnl_usd, paper, order_id, notes, won, source, pnl_pct,
             contract_side, forecast_yes_prob, model_prob_gfs, model_prob_ecmwf,
             weather_mode, forecast_hours_to_resolution, lane)
            VALUES (?, 'reconstructed_kalshi', 'kalshi', ?, ?, ?, ?, ?, ?, ?, 0.0, 0, ?, 'reconstructed_from_kalshi_api', NULL, 'live_v10', 0.0, ?, NULL, NULL, NULL, NULL, NULL, 'lane2')
        """, (ts_local, ticker, action, order_type, qty, price, cost_dollars, fees_dollars, order_id, side))
        inserted_trades += 1

    conn.commit()
    print(f"Trades Update: Inserted {inserted_trades} new trades, skipped {skipped_trades} existing.")

    # 2. Pull Settlements & Reconstruct resolutions table
    print("Downloading settlements from Kalshi...")
    settlements = []
    try:
        settlements = broker.get_settlements(limit=1000)
    except Exception as e:
        print(f"Error fetching settlements: {e}")
        
    print(f"Fetched {len(settlements)} settlements from Kalshi.")
    inserted_resolutions = 0
    skipped_resolutions = 0
    
    for s in settlements:
        ticker = s["ticker"]
        contract_id = ensure_market_and_contract(conn, ticker)
        
        # Check if already exists in forecast_resolutions
        cursor_db = conn.cursor()
        cursor_db.execute("SELECT 1 FROM forecast_resolutions WHERE contract_id=?", (contract_id,))
        if cursor_db.fetchone():
            skipped_resolutions += 1
            continue
            
        resolved_side = str(s["market_result"]).upper()
        resolved_value = float(s.get("value") or 0.0) / 100.0
        resolved_at = s["settled_time"]
        
        cursor_db.execute("""
            INSERT INTO forecast_resolutions
                (contract_id, resolved_side, resolved_value, resolved_at, payout_at, notes, source)
            VALUES (?, ?, ?, ?, ?, 'reconstructed_from_kalshi_api', 'kalshi_api')
        """, (contract_id, resolved_side, resolved_value, resolved_at, resolved_at))
        inserted_resolutions += 1

    conn.commit()
    print(f"Resolutions Update: Inserted {inserted_resolutions} new resolutions, skipped {skipped_resolutions} existing.")

    # 3. Pull Positions & Reconcile forecast_positions
    print("Downloading open positions and reconciling...")
    try:
        broker.sync_positions()
        holdings = broker.get_positions()
        recon = reconcile_forecast_positions(holdings, db_path=DB_PATH)
        print(f"Positions Reconciled: {recon}")
    except Exception as e:
        print(f"Error reconciling positions: {e}")

    conn.close()

    # 4. Regenerate CSV flat-file exports
    print("Regenerating CSV exports inside /research_package/...")
    generate_trades()
    generate_quotes()
    generate_actuals()
    print("Done!")

if __name__ == "__main__":
    reconstruct()
