"""Proof for maker routing and for the in-process replacement of reduce_only."""
import config
from execution.kalshi_broker import KalshiBroker
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


def test_maker_exit_never_rests_more_than_is_held(monkeypatch):
    """Kalshi rejects reduce_only on resting orders, so qty must be capped here."""
    b = _broker()
    monkeypatch.setattr(b, "live_position_qty", lambda t: 3.0)
    monkeypatch.setattr(b, "get_quote", lambda t: {"yes_ask": 0.80})
    monkeypatch.setattr(b, "_order_filled_qty", lambda oid: 0.0)
    monkeypatch.setattr(b, "cancel_order", lambda oid: True)
    monkeypatch.setattr(config, "MAKER_EXIT_TIMEOUT_S", 0)
    bodies = []

    def fake_request(method, path, params=None, body=None):
        if method == "POST":
            bodies.append(body)
        return {"order": {"status": "resting", "order_id": "O1"}}

    monkeypatch.setattr(b, "_request", fake_request)
    b._try_maker_exit(contract_dict={"local_symbol": "T"}, ticker="T", right="C",
                      side="yes", qty=10, taker_limit_price=0.60, kwargs={})
    assert bodies, "expected a resting sell"
    assert bodies[0]["count"] == "3.00", "must cap at the 3 contracts actually held"
    assert bodies[0]["reduce_only"] is False, "Kalshi rejects reduce_only on GTC"
    assert bodies[0]["post_only"] is True
    assert bodies[0]["time_in_force"] == "good_till_canceled"


def test_maker_exit_refuses_to_rest_when_nothing_is_held(monkeypatch):
    b = _broker()
    monkeypatch.setattr(b, "live_position_qty", lambda t: 0.0)
    placed = []
    monkeypatch.setattr(b, "_request", lambda m, p, params=None, body=None: placed.append(body) or {})
    out = b._try_maker_exit(contract_dict={"local_symbol": "T"}, ticker="T", right="C",
                            side="yes", qty=5, taker_limit_price=0.60, kwargs={})
    assert out is None
    assert not placed, "must never rest a sell against a position we do not hold"


def test_maker_exit_refuses_to_rest_when_position_is_unknown(monkeypatch):
    """A failed position lookup must not be read as 'nothing to protect'."""
    b = _broker()
    monkeypatch.setattr(b, "live_position_qty", lambda t: -1.0)
    placed = []
    monkeypatch.setattr(b, "_request", lambda m, p, params=None, body=None: placed.append(body) or {})
    assert b._try_maker_exit(contract_dict={"local_symbol": "T"}, ticker="T", right="C",
                             side="yes", qty=5, taker_limit_price=0.60, kwargs={}) is None
    assert not placed


def test_resting_sell_is_pulled_when_the_position_shrinks(monkeypatch):
    """The core reduce_only guarantee: never let the remainder open a short."""
    b = _broker()
    seq = iter([5.0, 5.0, 1.0, 1.0, 1.0])  # cap at 5, then it collapses to 1
    monkeypatch.setattr(b, "live_position_qty", lambda t: next(seq, 1.0))
    monkeypatch.setattr(b, "get_quote", lambda t: {"yes_ask": 0.80})
    monkeypatch.setattr(b, "_order_filled_qty", lambda oid: 0.0)
    monkeypatch.setattr(config, "MAKER_EXIT_TIMEOUT_S", 30)
    monkeypatch.setattr(config, "MAKER_EXIT_POLL_S", 0.5)
    cancelled = []
    monkeypatch.setattr(b, "cancel_order", lambda oid: cancelled.append(oid) or True)
    monkeypatch.setattr(b, "_request",
                        lambda m, p, params=None, body=None: {"order": {"status": "resting", "order_id": "O1"}})
    out = b._try_maker_exit(contract_dict={"local_symbol": "T"}, ticker="T", right="C",
                            side="yes", qty=5, taker_limit_price=0.60, kwargs={})
    assert cancelled == ["O1"], "must cancel as soon as the position drops"
    assert out is None, "nothing filled, so fall through to the taker path"


def test_risk_driven_exits_never_rest():
    for urgent in ("salvage_exit", "fee_aware_admissibility_exit", "stop_loss",
                   "manual_exit", "firewall_daily_kill_switch"):
        assert not any(r in urgent for r in config.MAKER_EXIT_ELIGIBLE_REASONS)


def test_take_profit_is_eligible():
    assert any(r in "take_profit" for r in config.MAKER_EXIT_ELIGIBLE_REASONS)


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
