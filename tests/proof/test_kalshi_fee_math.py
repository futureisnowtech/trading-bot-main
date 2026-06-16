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
