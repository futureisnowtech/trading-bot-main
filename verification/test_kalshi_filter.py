from execution.kalshi_broker import _is_economic_market

test_cases = [
    # Ticker, Title, Category, Expected
    ("KXPRESIDENT-2024", "Will the incumbent win the Presidential election?", "Politics", True),
    ("KXSENATE-OHIO", "Who will win the Ohio Senate race?", "Elections", True),
    ("KXCPIDATA", "Will CPI exceed 3.5% in June?", "Economics", True),
    ("KXTEMP-NYC", "Will temperature in NYC exceed 95 degrees?", "Climate and Weather", True),
    ("KXOSCARS-BESTPIC", "Who will win Best Picture?", "Social", False), # Blocked by global noise
    ("KXSPORT-NFL", "Will the Chiefs win?", "Sports", False), # Blocked by category whitelist
    ("KXBTC-PRICE", "Will Bitcoin reach 100k?", "Crypto", False), # Blocked by category whitelist
    ("KXCELEB-FOLLOW", "Will celebrity X reach 10m followers?", "Social", False), # Blocked by global noise
    ("KXINFLATION-STUBS", "Inflation markers", "Politics", False), # Wrong category match (Inflation isn't in Politics required keywords)
]

for ticker, title, category, expected in test_cases:
    result = _is_economic_market(ticker, title, category)
    status = "PASS" if result == expected else "FAIL"
    print(f"[{status}] {category}: {title} -> {result}")

