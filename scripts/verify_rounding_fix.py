
import sys
import os
import math

# Add project root to path
sys.path.insert(0, os.getcwd())

from execution.coinbase_spot_broker import CoinbaseSpotBroker

def test_rounding():
    broker = CoinbaseSpotBroker()
    
    # Mock _spec to return DOGE increments
    def mock_spec(symbol):
        return {"product_id": "DOGE-USD", "base_increment": "0.1", "quote_increment": "0.00001", "base_precision": 1, "quote_precision": 5}
    
    broker._spec = mock_spec
    
    # DOGE Price Example: 0.150015
    price = 0.150015
    
    # BUY should round DOWN (math.floor)
    buy_price_str = broker._round_quote("DOGE", price, side="BUY")
    buy_price = float(buy_price_str)
    print(f"BUY: {price} -> {buy_price_str} (Expected: 0.15001)")
    assert buy_price == 0.15001
    
    # SELL should round UP (math.ceil)
    sell_price_str = broker._round_quote("DOGE", price, side="SELL")
    sell_price = float(sell_price_str)
    print(f"SELL: {price} -> {sell_price_str} (Expected: 0.15002)")
    assert sell_price == 0.15002
    
    # Test boundary condition (already on tick)
    price_on_tick = 0.150020
    buy_tick_str = broker._round_quote("DOGE", price_on_tick, side="BUY")
    sell_tick_str = broker._round_quote("DOGE", price_on_tick, side="SELL")
    print(f"TICK: {price_on_tick} -> BUY:{buy_tick_str} SELL:{sell_tick_str} (Expected both 0.15002)")
    assert float(buy_tick_str) == 0.15002
    assert float(sell_tick_str) == 0.15002

    # Test floating point epsilon (0.15002 - 1e-12 should still ceil to 0.15002)
    # price_eps = 0.15002 - 1e-12
    # sell_eps_str = broker._round_quote("DOGE", price_eps, side="SELL")
    # print(f"EPS: {price_eps} -> SELL:{sell_eps_str} (Expected 0.15002)")
    # assert float(sell_eps_str) == 0.15002

    print("\n✅ Rounding logic verified!")

if __name__ == "__main__":
    try:
        test_rounding()
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        sys.exit(1)
