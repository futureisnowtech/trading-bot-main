import sqlite3
import collections

DB_PATH = "/Users/joshmacbookair2020/projects/algo_trading_final/logs/trades.db"

CITY_TO_HUB = {
    # Northeast
    "BOS": "NORTHEAST", "NYC": "NORTHEAST", "NYCH": "NORTHEAST", "PHIL": "NORTHEAST", "PHL": "NORTHEAST", "DC": "NORTHEAST", "WAS": "NORTHEAST",
    # Midwest
    "CHI": "MIDWEST", "MSP": "MIDWEST", "MIN": "MIDWEST", "MKE": "MIDWEST", "OMA": "MIDWEST", "STL": "MIDWEST", "DET": "MIDWEST", "MCI": "MIDWEST", "OKC": "MIDWEST",
    # West
    "LAX": "WEST", "SFO": "WEST", "PHX": "WEST", "SEA": "WEST", "PDX": "WEST", "LV": "WEST",
    # South
    "ATL": "SOUTH", "CLT": "SOUTH", "RDU": "SOUTH", "BNA": "SOUTH", "CHS": "SOUTH",
    # Florida
    "MIA": "FLORIDA", "MCO": "FLORIDA",
    # Gulf
    "HOU": "GULF", "AUS": "GULF", "DAL": "GULF", "SAT": "GULF", "SATX": "GULF", "MSY": "GULF", "NOLA": "GULF",
    # Mountain
    "DEN": "MOUNTAIN", "SLC": "MOUNTAIN", "ABQ": "MOUNTAIN"
}

def get_asset_and_hub(symbol):
    # e.g., KXTEMPNYCH-26JUN2310-T70.99 or KXHIGHBOS-26JUN23-T74 or KXRAINAUSM-26JUN-1
    parts = symbol.split("-")
    prefix = parts[0]
    
    asset = "UNKNOWN"
    if "TEMP" in prefix:
        asset = "Hourly Temp"
    elif "HIGH" in prefix:
        asset = "Daily High Temp"
    elif "LOW" in prefix:
        asset = "Daily Low Temp"
    elif "RAIN" in prefix:
        asset = "Rain"
    elif "SNOW" in prefix:
        asset = "Snow"
    elif "WIND" in prefix:
        asset = "Wind"
    elif "GAME" in prefix:
        asset = "Legacy NBA"
        
    # Extract city prefix by stripping the longest matching weather prefix first
    clean_pfx = prefix
    for pfx in ("KXTEMP", "KXHIGHT", "KXLOWT", "KXHIGH", "KXLOW", "KXRAIN", "KXSNOW", "KXWIND"):
        if clean_pfx.startswith(pfx):
            clean_pfx = clean_pfx[len(pfx):]
            break
            
    city = clean_pfx
    hub = CITY_TO_HUB.get(city, "UNKNOWN")
    return asset, hub, city

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM trades")
    rows = cursor.fetchall()
    
    # Aggregations
    by_hub = collections.defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
    by_asset = collections.defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
    by_price = collections.defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
    
    total_count = 0
    total_wins = 0
    total_pnl = 0.0
    total_fees = 0.0
    
    for r in rows:
        symbol = r["symbol"]
        won = r["won"]
        pnl = float(r["pnl_usd"] or 0.0)
        fee = float(r["fee_usd"] or 0.0)
        price = float(r["price"] or 0.0)
        
        asset, hub, city = get_asset_and_hub(symbol)
        
        # Sizing
        total_count += 1
        if won == 1:
            total_wins += 1
        total_pnl += pnl
        total_fees += fee
        
        # Hub aggregation
        by_hub[hub]["count"] += 1
        if won == 1:
            by_hub[hub]["wins"] += 1
        by_hub[hub]["pnl"] += pnl
        by_hub[hub]["fees"] += fee
        
        # Asset aggregation
        by_asset[asset]["count"] += 1
        if won == 1:
            by_asset[asset]["wins"] += 1
        by_asset[asset]["pnl"] += pnl
        by_asset[asset]["fees"] += fee
        
        # Price bracket aggregation
        if price < 0.10:
            bracket = "< $0.10 (Penny)"
        elif price <= 0.30:
            bracket = "$0.10 - $0.30"
        elif price <= 0.70:
            bracket = "$0.30 - $0.70"
        else:
            bracket = "> $0.70 (Expensive)"
            
        by_price[bracket]["count"] += 1
        if won == 1:
            by_price[bracket]["wins"] += 1
        by_price[bracket]["pnl"] += pnl
        by_price[bracket]["fees"] += fee

    print("### 1. Performance by Regional Hub")
    print("| Regional Hub | Trades | Win Rate | Net PnL (USD) | Total Fees |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for hub, stats in sorted(by_hub.items(), key=lambda x: x[1]["pnl"], reverse=True):
        win_rate = (stats["wins"] / stats["count"]) * 100 if stats["count"] > 0 else 0.0
        print(f"| {hub} | {stats['count']} | {win_rate:.1f}% | ${stats['pnl']:.2f} | ${stats['fees']:.2f} |")
        
    print("\n### 2. Performance by Asset Type")
    print("| Asset Type | Trades | Win Rate | Net PnL (USD) | Total Fees |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for asset, stats in sorted(by_asset.items(), key=lambda x: x[1]["pnl"], reverse=True):
        win_rate = (stats["wins"] / stats["count"]) * 100 if stats["count"] > 0 else 0.0
        print(f"| {asset} | {stats['count']} | {win_rate:.1f}% | ${stats['pnl']:.2f} | ${stats['fees']:.2f} |")
        
    print("\n### 3. Performance by Price Bracket")
    print("| Price Bracket | Trades | Win Rate | Net PnL (USD) | Total Fees |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for bracket, stats in sorted(by_price.items(), key=lambda x: x[1]["pnl"], reverse=True):
        win_rate = (stats["wins"] / stats["count"]) * 100 if stats["count"] > 0 else 0.0
        print(f"| {bracket} | {stats['count']} | {win_rate:.1f}% | ${stats['pnl']:.2f} | ${stats['fees']:.2f} |")
        
    print(f"\n### 4. Portfolio Totals")
    print(f"- **Total Executed Trades**: {total_count}")
    print(f"- **Total Wins**: {total_wins} ({(total_wins/total_count)*100:.1f}%)")
    print(f"- **Total Net PnL**: ${total_pnl:.2f}")
    print(f"- **Total Transaction Fees Paid**: ${total_fees:.2f}")
    print(f"- **Fee-to-PnL Ratio**: {total_fees/total_pnl if total_pnl > 0 else 0.0:.2f}x (transaction fees vs net returns)")

if __name__ == "__main__":
    main()
conn = sqlite3.connect(DB_PATH)
conn.close()
