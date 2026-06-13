import math
from hypothesis import given, strategies as st
from forecast.strategy_engine import calculate_continuous_sizing

@given(
    price=st.floats(min_value=0.01, max_value=0.99),
    p=st.floats(min_value=0.01, max_value=0.99),
    bankroll=st.floats(min_value=10.0, max_value=5000.0),
)
def test_sizing_monotonicity_and_safety(price, p, bankroll):
    """
    Property-based test ensuring mathematical sanity and monotonicity:
    1. Sizing must never exceed KALSHI_MAX_QTY_PER_POSITION (2500).
    2. Sizing must be 0 if price is higher than probability (negative EV).
    3. Increasing probability must NEVER decrease size (all else equal).
    4. Sizing must decrease or stay equal if price increases (all else equal).
    """
    qty = calculate_continuous_sizing(price, p, bankroll)
    assert 0 <= qty <= 2500

    # Negative EV check
    fee = 0.07 * price * (1.0 - price)
    ev = p - price - fee
    if ev <= 0:
        assert qty == 0

    # Monotonicity check 1: Higher success probability should increase or maintain qty
    if p < 0.98 and qty > 0:
        qty_higher_p = calculate_continuous_sizing(price, p + 0.01, bankroll)
        assert qty_higher_p >= qty

    # Monotonicity check 2: Lower contract cost should increase or maintain qty
    if price > 0.02 and qty > 0:
        qty_higher_price = calculate_continuous_sizing(price - 0.01, p, bankroll)
        assert qty_higher_price >= qty
