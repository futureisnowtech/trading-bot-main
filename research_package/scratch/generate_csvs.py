import sqlite3
import csv
import json
import os
from datetime import datetime

db_path = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/trades.db"
out_dir = "/Users/joshmacbookair2020/projects/algo_trading_final/research_package"
os.makedirs(out_dir, exist_ok=True)

def generate_trades():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades")
    db_trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Read CSV trades to ensure we merge them correctly if there are any that aren't in DB
    csv_trades = []
    csv_dir = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/csv"
    if os.path.exists(csv_dir):
        for f_name in os.listdir(csv_dir):
            if f_name.startswith("trades_") and f_name.endswith(".csv"):
                f_path = os.path.join(csv_dir, f_name)
                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for r in reader:
                            csv_trades.append(r)
                except Exception as e:
                    print(f"Error reading CSV {f_name}: {e}")

    # Deduplicate by order_id or timestamp + symbol
    all_trades = {}
    
    # Process DB trades first
    for t in db_trades:
        key = t.get("order_id") or f"{t['ts']}_{t['symbol']}"
        all_trades[key] = {
            "id": t.get("id"),
            "ts": t.get("ts"),
            "strategy": t.get("strategy"),
            "broker": t.get("broker"),
            "symbol": t.get("symbol"),
            "action": t.get("action"),
            "order_type": t.get("order_type"),
            "qty": t.get("qty"),
            "price": t.get("price"),
            "value_usd": t.get("value_usd"),
            "fee_usd": t.get("fee_usd"),
            "pnl_usd": t.get("pnl_usd"),
            "pnl_pct": t.get("pnl_pct", 0.0),
            "paper": t.get("paper"),
            "order_id": t.get("order_id"),
            "notes": t.get("notes"),
            "lane": t.get("lane"),
            "won": t.get("won"),
            "source": t.get("source"),
            "contract_side": t.get("contract_side"),
            "forecast_yes_prob": t.get("forecast_yes_prob"),
            "model_prob_gfs": t.get("model_prob_gfs"),
            "model_prob_ecmwf": t.get("model_prob_ecmwf"),
            "weather_mode": t.get("weather_mode"),
            "forecast_hours_to_resolution": t.get("forecast_hours_to_resolution")
        }

    # Merge CSV trades (preserving details from DB which has more columns)
    for t in csv_trades:
        key = t.get("order_id") or f"{t['ts']}_{t['symbol']}"
        if key not in all_trades:
            all_trades[key] = {
                "id": "",
                "ts": t.get("ts"),
                "strategy": t.get("strategy"),
                "broker": t.get("broker"),
                "symbol": t.get("symbol"),
                "action": t.get("action"),
                "order_type": t.get("order_type"),
                "qty": float(t.get("qty") or 0.0),
                "price": float(t.get("price") or 0.0),
                "value_usd": float(t.get("value_usd") or 0.0),
                "fee_usd": float(t.get("fee_usd") or 0.0),
                "pnl_usd": float(t.get("pnl_usd") or 0.0),
                "pnl_pct": float(t.get("pnl_pct") or 0.0) if t.get("pnl_pct") else 0.0,
                "paper": int(t.get("paper") or 0),
                "order_id": t.get("order_id"),
                "notes": t.get("notes"),
                "lane": t.get("lane") or "lane2",
                "won": t.get("won"),
                "source": t.get("source") or "live_v10",
                "contract_side": t.get("contract_side"),
                "forecast_yes_prob": t.get("forecast_yes_prob"),
                "model_prob_gfs": t.get("model_prob_gfs"),
                "model_prob_ecmwf": t.get("model_prob_ecmwf"),
                "weather_mode": t.get("weather_mode"),
                "forecast_hours_to_resolution": t.get("forecast_hours_to_resolution")
            }

    sorted_trades = sorted(all_trades.values(), key=lambda x: x["ts"] or "")

    # Output normalized_trades.csv
    fields = [
        "id", "ts", "strategy", "broker", "symbol", "action", "order_type", 
        "qty", "price", "value_usd", "fee_usd", "pnl_usd", "pnl_pct", "paper", 
        "order_id", "notes", "lane", "won", "source", "contract_side", 
        "forecast_yes_prob", "model_prob_gfs", "model_prob_ecmwf", 
        "weather_mode", "forecast_hours_to_resolution"
    ]
    with open(os.path.join(out_dir, "normalized_trades.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted_trades)
    
    print(f"Normalized {len(sorted_trades)} trades to normalized_trades.csv")

def generate_quotes():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT q.id as quote_id, q.ts, m.market_symbol, m.market_name, 
               c.local_symbol, c.strike, c.right, q.side, 
               q.bid, q.ask, q.mid, q.spread, q.bid_size, q.ask_size, q.implied_prob
        FROM forecast_quotes q
        JOIN forecast_contracts c ON q.contract_id = c.id
        JOIN forecast_markets m ON c.market_id = m.id
        ORDER BY q.ts ASC
    """
    cursor.execute(query)
    quotes = [dict(row) for row in cursor.fetchall()]
    conn.close()

    fields = [
        "quote_id", "ts", "market_symbol", "market_name", "local_symbol", 
        "strike", "right", "side", "bid", "ask", "mid", "spread", 
        "bid_size", "ask_size", "implied_prob"
    ]
    with open(os.path.join(out_dir, "normalized_weather_forecasts.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(quotes)

    print(f"Normalized {len(quotes)} quotes to normalized_weather_forecasts.csv")

def generate_actuals():
    watermarks_path = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/weather_watermarks.json"
    actuals = []
    if os.path.exists(watermarks_path):
        try:
            with open(watermarks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, val in data.items():
                    parts = key.split("|")
                    if len(parts) >= 3:
                        actuals.append({
                            "location": parts[0],
                            "date": parts[1],
                            "metric": parts[2],
                            "value": val
                        })
                    else:
                        actuals.append({
                            "location": key,
                            "date": "",
                            "metric": "",
                            "value": val
                        })
        except Exception as e:
            print(f"Error reading watermarks: {e}")

    fields = ["location", "date", "metric", "value"]
    with open(os.path.join(out_dir, "normalized_weather_actuals.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(actuals)

    print(f"Normalized {len(actuals)} watermarks to normalized_weather_actuals.csv")

if __name__ == "__main__":
    generate_trades()
    generate_quotes()
    generate_actuals()
