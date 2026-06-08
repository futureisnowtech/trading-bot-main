from hypothesis import given, strategies as st
from forecast.weather_contracts import WeatherContractSemantics, member_satisfies_contract

@given(
    val=st.floats(min_value=-50.0, max_value=120.0),
)
def test_inclusive_boundary_fuzzing(val):
    """
    Property-based test verifying strict inclusivity at boundary strikes:
    1. A 'gt' (greater than) contract with threshold T must be satisfied by EXACTLY T.
    2. A 'lt' (less than) contract with threshold T must be satisfied by EXACTLY T.
    3. A 'between' contract with lower_bound L and upper_bound U must be satisfied by L but not by U.
    """
    # 1. Greater than (inclusive)
    semantics_gt = WeatherContractSemantics(
        ticker="TEST-GT",
        mode="TEMP",
        comparator="gt",
        source="fuzz",
        threshold=val,
        display_low=val,
    )
    assert member_satisfies_contract(val, semantics_gt) is True
    assert member_satisfies_contract(val + 0.001, semantics_gt) is True
    assert member_satisfies_contract(val - 0.001, semantics_gt) is False

    # 2. Less than (inclusive)
    semantics_lt = WeatherContractSemantics(
        ticker="TEST-LT",
        mode="TEMP",
        comparator="lt",
        source="fuzz",
        threshold=val,
        display_high=val,
    )
    assert member_satisfies_contract(val, semantics_lt) is True
    assert member_satisfies_contract(val - 0.001, semantics_lt) is True
    assert member_satisfies_contract(val + 0.001, semantics_lt) is False

    # 3. Between (L <= val < U)
    if val < 100.0:
        l = val
        u = val + 5.0
        semantics_bet = WeatherContractSemantics(
            ticker="TEST-BET",
            mode="TEMP",
            comparator="between",
            source="fuzz",
            lower_bound=l,
            upper_bound=u,
            display_low=l,
            display_high=u,
        )
        assert member_satisfies_contract(l, semantics_bet) is True
        assert member_satisfies_contract(l + 2.5, semantics_bet) is True
        assert member_satisfies_contract(u, semantics_bet) is False
        assert member_satisfies_contract(l - 0.001, semantics_bet) is False
