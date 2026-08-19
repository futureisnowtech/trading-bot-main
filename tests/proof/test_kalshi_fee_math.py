def test_estimate_kalshi_fee_helpers_match_exchange_curve():
    from config import (
        estimate_kalshi_fee_per_contract,
        estimate_kalshi_order_fee_usd,
        kalshi_raw_fee_per_contract,
    )

    assert round(kalshi_raw_fee_per_contract(0.10), 4) == 0.0063
    assert round(kalshi_raw_fee_per_contract(0.20), 4) == 0.0112
    assert round(kalshi_raw_fee_per_contract(0.21), 4) == 0.0116
    assert estimate_kalshi_order_fee_usd(100, 0.20) == 1.12
    assert round(estimate_kalshi_fee_per_contract(0.20, qty=100), 4) == 0.0112


def test_max_kalshi_contracts_for_budget_uses_exact_fee_schedule():
    from config import estimate_kalshi_order_cost_usd, max_kalshi_contracts_for_budget

    qty = max_kalshi_contracts_for_budget(0.10, 10.0)

    assert qty == 94
    assert estimate_kalshi_order_cost_usd(qty, 0.10) == 10.0
    assert estimate_kalshi_order_cost_usd(qty + 1, 0.10) > 10.0


def test_position_exposure_uses_dynamic_fee_schedule():
    from config import get_kalshi_position_exposure_usd

    assert get_kalshi_position_exposure_usd(20, 0.20) == 4.23


def test_weather_net_edge_fee_hurdle_and_exit_weight():
    """Explicit verification of _weather_net_edge round-trip fee hurdle:
    - Rejects ask_price <= 0.0 by returning None.
    - Uses rounded=True for per-contract fee calculation.
    - Applies _EXIT_FEE_WEIGHT (0.48) to the exit fee.
    - Net edge formula: contract_prob - ask_price - (fee_in + 0.48 * fee_out).
    """
    from config import estimate_kalshi_fee_per_contract
    from forecast.strategy_engine import _EXIT_FEE_WEIGHT, _weather_net_edge

    # 1. Verify _EXIT_FEE_WEIGHT constant
    assert _EXIT_FEE_WEIGHT == 0.48

    # 2. Non-positive ask prices return None
    assert _weather_net_edge(0.70, 0.0) is None
    assert _weather_net_edge(0.70, -0.05) is None

    # 3. Explicit calculation check at ask_price = 0.20, contract_prob = 0.70
    ask_price = 0.20
    contract_prob = 0.70

    fee_rounded = estimate_kalshi_fee_per_contract(ask_price, rounded=True)
    fee_unrounded = estimate_kalshi_fee_per_contract(ask_price, rounded=False)

    # rounded=True ceilings fee to nearest cent (0.02 for price 0.20)
    assert fee_rounded == 0.02
    assert round(fee_unrounded, 4) == 0.0112

    # Expected net edge with rounded=True and _EXIT_FEE_WEIGHT (0.48)
    expected_round_trip_fee = fee_rounded + _EXIT_FEE_WEIGHT * fee_rounded  # 0.02 + 0.48*0.02 = 0.0296
    expected_net_edge = contract_prob - ask_price - expected_round_trip_fee  # 0.70 - 0.20 - 0.0296 = 0.4704

    actual_net_edge = _weather_net_edge(contract_prob, ask_price)
    assert actual_net_edge is not None
    assert abs(actual_net_edge - expected_net_edge) < 1e-12

    # 4. Assert unrounded fee calculation would be strictly higher (under-billing cost)
    unrounded_net_edge = contract_prob - ask_price - (fee_unrounded + _EXIT_FEE_WEIGHT * fee_unrounded)
    assert actual_net_edge < unrounded_net_edge, "rounded=True must provide a stricter hurdle than rounded=False"

    # 5. Assert entry-only fee calculation would be strictly higher (ignoring exit cost)
    entry_only_net_edge = contract_prob - ask_price - fee_rounded
    assert actual_net_edge < entry_only_net_edge, "_weather_net_edge must penalize exit fee"



def test_exit_fill_price_is_normalised_to_the_yes_leg():
    """Closing a NO position is submitted as side=yes, so the echo is a YES price.

    _realized_pnl treats the sides as complements, which only holds if both legs
    are YES-denominated. A no_price entry booked against a yes_price exit
    overstated the round trip by 2*entry-1 per contract.
    """
    from execution.kalshi_broker import KalshiBroker

    broker = object.__new__(KalshiBroker)  # no __init__: pure parsing under test

    # Authoritative field wins outright.
    assert broker._extract_average_fill_price( {"yes_price_dollars": "0.2000", "no_price_dollars": "0.8000"}
    ) == 0.20

    # A NO-side echo with no explicit yes field must be flipped to the YES leg.
    assert round(
        broker._extract_average_fill_price( {"outcome_side": "no", "price": "0.8000"}
        ),
        4,
    ) == 0.20

    # Cost-derived fallback is flipped the same way, and sums both fill buckets.
    assert round(
        broker._extract_average_fill_price(
            {
                "outcome_side": "no",
                "fill_count_fp": "5.00",
                "taker_fill_cost_dollars": "3.000000",
                "maker_fill_cost_dollars": "1.000000",
            },
        ),
        4,
    ) == 0.20

    # A YES-side order is left alone.
    assert round(
        broker._extract_average_fill_price( {"outcome_side": "yes", "price": "0.2000"}
        ),
        4,
    ) == 0.20
