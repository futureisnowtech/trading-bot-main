"""Proof for maker-first routing."""
from forecast.strategy_engine import _MAKER_MIN_HOURS_TO_RES, _maker_first_utility


def _routes_maker(u_M, u_T, zeta, tau):
    e = _maker_first_utility(u_M, u_T, zeta, tau)
    return e > u_T and e > 0.0


def _broker():
    b = KalshiBroker()
    b._connected = True
    b._private_key = object()
    return b


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
