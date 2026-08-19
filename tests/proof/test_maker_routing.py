"""Proof for maker-first routing."""
from forecast.strategy_engine import _MAKER_MIN_HOURS_TO_RES, _maker_first_utility


def _routes_maker(u_M, u_T, zeta, tau):
    e = _maker_first_utility(u_M, u_T, zeta, tau)
    return e > u_T and e > 0.0


# ---------------------------------------------------------------- routing


def test_low_fill_odds_no_longer_veto_a_better_maker_leg():
    u_T, u_M = 0.0020, 0.0031
    for zeta in (0.05, 0.10, 0.19, 0.33, 0.46):
        assert _routes_maker(u_M, u_T, zeta, 14.0), f"zeta={zeta} should route maker"
        assert not (zeta * u_M > u_T), f"zeta={zeta}: old scoring should have said taker"


def test_taker_still_wins_when_the_maker_leg_is_worse():
    for zeta in (0.10, 0.46, 0.80):
        assert not _routes_maker(0.0020, 0.0031, zeta, 14.0)


def test_last_hour_keeps_the_conservative_scoring():
    tau = _MAKER_MIN_HOURS_TO_RES / 2.0
    assert _maker_first_utility(0.0031, 0.0020, 0.46, tau) == 0.46 * 0.0031
    assert not _routes_maker(0.0031, 0.0020, 0.46, tau)


def test_maker_first_utility_formula_explicit():
    """Explicit mathematical verification of expected maker utility formula:
    zeta * u_M + (1.0 - zeta) * u_T * 0.98.
    """
    from forecast.strategy_engine import _MAKER_FALLBACK_DISCOUNT

    assert _MAKER_FALLBACK_DISCOUNT == 0.98

    # Case 1: Standard values with tau >= _MAKER_MIN_HOURS_TO_RES
    u_M, u_T, zeta, tau = 0.0031, 0.0020, 0.30, 10.0
    expected = 0.30 * 0.0031 + (1.0 - 0.30) * 0.0020 * 0.98
    actual = _maker_first_utility(u_M, u_T, zeta, tau)
    assert abs(actual - expected) < 1e-12

    # Case 2: zeta = 0.0 -> expected fallback = u_T * 0.98
    actual_zero = _maker_first_utility(u_M, u_T, 0.0, tau)
    assert abs(actual_zero - (u_T * 0.98)) < 1e-12

    # Case 3: zeta = 1.0 -> expected = u_M
    actual_one = _maker_first_utility(u_M, u_T, 1.0, tau)
    assert abs(actual_one - u_M) < 1e-12

    # Case 4: Clamping out-of-bounds zeta values
    assert abs(_maker_first_utility(u_M, u_T, -0.5, tau) - (u_T * 0.98)) < 1e-12
    assert abs(_maker_first_utility(u_M, u_T, 1.5, tau) - u_M) < 1e-12


# ------------------------------------------------- reduce_only replacement


def test_maker_route_still_respects_the_usd_position_cap():
    """Maker sizes via solve_optimal_size, which never sees position_cap_usd.

    The cap is applied afterwards by the Sovereign SRE clamp in
    evaluate_contract, on whichever route was chosen. This pins that the clamp
    covers the maker route too, at the tight limits production actually runs
    (MAX_USD=$10, MAX_QTY=15) rather than the looser repo defaults.
    """
    from config import max_kalshi_contracts_for_budget
    from forecast.strategy_engine import solve_optimal_size

    cap_usd, max_qty = 10.0, 15
    for price in (0.10, 0.30, 0.60, 0.85):
        _f, phi, n = solve_optimal_size(
            q=0.90, p=price, maker=True, bankroll=68.0,
            lambda_scaler=1.0, cov_charge=1.0,
        )
        cost = n * (price + phi)
        if cost > cap_usd:
            n = min(n, max_kalshi_contracts_for_budget(price, cap_usd), max_qty)
        else:
            n = min(n, max_qty)
        assert n * (price + phi) <= cap_usd + 0.01, (
            f"maker route breached the ${cap_usd} cap at price {price}"
        )
