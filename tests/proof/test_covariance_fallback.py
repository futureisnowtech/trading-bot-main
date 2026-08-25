from __future__ import annotations

from datetime import date, timedelta
import sqlite3
from unittest.mock import patch

import numpy as np
import pytest

from forecast.covariance_engine import (
    CovarianceDataUnavailable,
    MIN_AUTHORITATIVE_STATION_DAYS,
    NON_AUTHORITATIVE_COVARIANCE_MODE,
    assemble_covariance_matrix,
    check_and_shrink_candidate,
    get_station_history_status,
    get_station_correlation_matrix,
)


def _contract(ticker: str, name: str, strike: float) -> dict:
    return {
        "local_symbol": ticker,
        "contract_name": name,
        "strike": strike,
        "resolution_at": "2099-08-25T23:00:00Z",
    }


def _write_station_history(db_path, station_row_counts: dict[str, int]) -> None:
    rows = []
    start = date(2026, 1, 1)
    for station, count in station_row_counts.items():
        for offset in range(count):
            rows.append(
                (
                    station,
                    (start + timedelta(days=offset)).isoformat(),
                    40.0 + offset * 0.25 + len(station),
                )
            )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE noaa_daily_summaries "
            "(station TEXT NOT NULL, date TEXT NOT NULL, temp_max REAL)"
        )
        conn.executemany(
            "INSERT INTO noaa_daily_summaries (station, date, temp_max) "
            "VALUES (?, ?, ?)",
            rows,
        )


@pytest.mark.parametrize("raw_days", [60, 89])
def test_station_history_below_90_raw_days_is_not_authoritative(tmp_path, raw_days):
    db_path = tmp_path / f"{raw_days}_days.db"
    _write_station_history(db_path, {"KAAA": raw_days, "KBBB": raw_days})

    _matrix, authoritative = get_station_correlation_matrix(
        str(db_path), ["KAAA", "KBBB"]
    )

    assert MIN_AUTHORITATIVE_STATION_DAYS == 90
    assert authoritative is False


def test_sparse_station_cannot_gain_authority_from_imputation(tmp_path):
    db_path = tmp_path / "sparse_station.db"
    _write_station_history(db_path, {"KAAA": 90, "KBBB": 1})

    _matrix, authoritative = get_station_correlation_matrix(
        str(db_path), ["KAAA", "KBBB"]
    )

    assert authoritative is False


def test_each_station_with_90_raw_days_is_authoritative(tmp_path):
    db_path = tmp_path / "ninety_days.db"
    _write_station_history(db_path, {"KAAA": 90, "KBBB": 90})

    matrix, authoritative = get_station_correlation_matrix(
        str(db_path), ["KAAA", "KBBB"]
    )

    assert authoritative is True
    assert matrix[("KAAA", "KAAA")] == pytest.approx(1.0)


def test_station_history_status_reports_raw_coverage_without_imputation(tmp_path):
    db_path = tmp_path / "coverage.db"
    _write_station_history(db_path, {"KAAA": 90, "KBBB": 1})

    status = get_station_history_status(str(db_path), ["KAAA", "KBBB"])

    assert status["covered_station_count"] == 2
    assert status["minimum_raw_days"] == 1
    assert status["required_raw_days_per_station"] == 90
    assert status["authoritative"] is False


def test_non_authoritative_matrix_is_gross_comonotonic_even_for_disjoint_brackets():
    contracts = [
        _contract(
            "KXHIGHNY-99AUG25-L75",
            "Will the high temperature in New York be below 75F?",
            75.0,
        ),
        _contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
    ]
    pricing = {
        contracts[0]["local_symbol"]: {"q_hat": 0.40},
        contracts[1]["local_symbol"]: {"q_hat": 0.30},
    }

    sigma = assemble_covariance_matrix(
        contracts,
        pricing,
        {c["local_symbol"]: {} for c in contracts},
        {("KNYC", "KNYC"): -0.99},
        is_authoritative=False,
    )

    expected = np.sqrt(0.40 * 0.60) * np.sqrt(0.30 * 0.70)
    assert sigma[0, 1] == pytest.approx(expected)
    assert sigma[0, 1] > 0.0
    assert np.linalg.eigvalsh(sigma).min() > 0.0


def test_authoritative_disjoint_brackets_retain_exact_negative_covariance():
    contracts = [
        _contract(
            "KXHIGHNY-99AUG25-L75",
            "Will the high temperature in New York be below 75F?",
            75.0,
        ),
        _contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
    ]
    pricing = {
        contracts[0]["local_symbol"]: {"q_hat": 0.40},
        contracts[1]["local_symbol"]: {"q_hat": 0.30},
    }

    sigma = assemble_covariance_matrix(
        contracts,
        pricing,
        {c["local_symbol"]: {} for c in contracts},
        {("KNYC", "KNYC"): 1.0},
        is_authoritative=True,
    )

    assert sigma[0, 1] == pytest.approx(0.9 * (-0.40 * 0.30))


@pytest.mark.parametrize("candidate_side", ["YES", "NO"])
def test_non_authoritative_admission_is_sign_safe_and_trades_small_bankroll(candidate_side):
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )

    with (
        patch(
            "forecast.covariance_engine.get_station_correlation_matrix",
            return_value=({}, False),
        ),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch(
            "data.kalshi_weather_monitor.get_weather_data",
            return_value={"members_high": [79.0, 80.0, 81.0]},
        ),
        patch(
            "forecast.pricing_engine.calculate_pricing",
            return_value={"q_hat": 0.50},
        ),
    ):
        qty, charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side=candidate_side,
            candidate_price=0.50,
            candidate_qty=15,
            open_positions=[],
            bankroll=58.0,
            db_path=None,
        )

    assert qty == 9
    assert charge == 1.0
    assert debug["covariance_mode"] == NON_AUTHORITATIVE_COVARIANCE_MODE
    assert debug["station_correlation_authoritative"] is False
    assert debug["non_netting"] is True
    assert debug["fallback_pairwise_correlation"] == 1.0


def test_non_authoritative_cross_station_yes_no_positions_receive_no_netting_credit():
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    open_template = {
        **_contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
        "entry_price": 0.50,
        "market_exposure_usd": 1.0,
        "qty": 2,
    }

    outcomes = {}
    with (
        patch(
            "forecast.covariance_engine.get_station_correlation_matrix",
            return_value=({}, False),
        ),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch(
            "data.kalshi_weather_monitor.get_weather_data",
            return_value={"members_high": [79.0, 80.0, 81.0]},
        ),
        patch(
            "forecast.pricing_engine.calculate_pricing",
            return_value={"q_hat": 0.50},
        ),
    ):
        for held_side in ("YES", "NO"):
            for candidate_side in ("YES", "NO"):
                qty, charge, debug = check_and_shrink_candidate(
                    candidate_contract=candidate,
                    candidate_side=candidate_side,
                    candidate_price=0.50,
                    candidate_qty=15,
                    open_positions=[{**open_template, "side": held_side}],
                    bankroll=58.0,
                    db_path=None,
                )
                outcomes[(held_side, candidate_side)] = (qty, charge)
                assert debug["non_netting"] is True

    # Neither flipping the held side nor flipping the candidate side can create
    # covariance capacity without authoritative station evidence.
    assert len(set(outcomes.values())) == 1
    assert next(iter(outcomes.values()))[0] < 9


def test_authoritative_copula_exception_fails_closed():
    contracts = [
        _contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
        _contract(
            "KXHIGHDEN-99AUG25-T80",
            "Will the high temperature in Denver be 80F or above?",
            80.0,
        ),
    ]
    pricing = {c["local_symbol"]: {"q_hat": 0.50} for c in contracts}

    with patch(
        "forecast.covariance_engine.multivariate_normal.cdf",
        side_effect=RuntimeError("numeric failure"),
    ):
        with pytest.raises(
            CovarianceDataUnavailable,
            match="copula_covariance_unavailable",
        ):
            assemble_covariance_matrix(
                contracts,
                pricing,
                {c["local_symbol"]: {} for c in contracts},
                {("KNYC", "KDEN"): 0.30},
                is_authoritative=True,
            )


def test_soft_marginal_charge_cannot_erase_single_hard_admitted_contract():
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, False)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.50}),
        patch("forecast.covariance_engine.calculate_shrinkage_limit", return_value=1.1),
        patch("forecast.covariance_engine.calculate_marginal_risk_charge", return_value=0.9955),
    ):
        qty, charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side="YES",
            candidate_price=0.77,
            candidate_qty=1,
            open_positions=[],
            bankroll=58.15,
        )

    assert qty == 1
    assert charge == pytest.approx(0.9955)
    assert debug["reason"] == "approved"


def test_authoritative_soft_charge_never_enlarges_or_breaks_hard_variance_root():
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    open_position = {
        **_contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
        "qty": 10,
        "side": "YES",
        "entry_price": 0.50,
        "market_exposure_usd": 5.0,
    }
    sigma = np.array([[0.25, -0.125], [-0.125, 0.25]], dtype=float)
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, True)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.50}),
        patch("forecast.covariance_engine.assemble_covariance_matrix", return_value=sigma),
    ):
        qty, charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side="YES",
            candidate_price=0.50,
            candidate_qty=15,
            open_positions=[open_position],
            bankroll=62.75,
        )

    assert debug["raw_charge_factor"] > 1.0
    assert charge == 1.0
    assert qty == 10
    assert debug["projected_variance"] <= debug["limit"] + 1e-9


def test_absolute_backstop_counts_existing_position_fee_before_candidate():
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    open_position = {
        **_contract(
            "KXHIGHNY-99AUG25-T75",
            "Will the high temperature in New York be 75F or above?",
            75.0,
        ),
        "qty": 1,
        "side": "YES",
        "entry_price": 0.90,
        "market_exposure_usd": 0.90,
    }
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, False)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.001}),
    ):
        qty, _charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side="YES",
            candidate_price=0.89,
            candidate_qty=1,
            open_positions=[open_position],
            bankroll=2.0,
        )

    # Raw position exposure leaves $0.90 under the 90% rail and would admit
    # this $0.89 + $0.01-fee order.  Counting the existing position's estimated
    # fee leaves only $0.89, so the new fee-inclusive order is correctly vetoed.
    assert qty == 0
    assert debug["reason"] == "shrunk_to_zero"
    assert debug["max_by_backstop"] == 0


@pytest.mark.parametrize("raw_charge", [float("nan"), float("inf"), "bad"])
def test_invalid_marginal_risk_charge_fails_closed(raw_charge):
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, False)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.50}),
        patch("forecast.covariance_engine.calculate_marginal_risk_charge", return_value=raw_charge),
    ):
        with pytest.raises(CovarianceDataUnavailable, match="invalid_marginal_risk_charge"):
            check_and_shrink_candidate(
                candidate_contract=candidate,
                candidate_side="YES",
                candidate_price=0.50,
                candidate_qty=1,
                open_positions=[],
                bankroll=58.15,
            )


def test_zero_soft_charge_has_truthful_veto_reason():
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, False)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.50}),
        patch("forecast.covariance_engine.calculate_marginal_risk_charge", return_value=0.0),
    ):
        qty, charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side="NO",
            candidate_price=0.50,
            candidate_qty=1,
            open_positions=[],
            bankroll=58.15,
        )

    assert qty == 0
    assert charge == 0.0
    assert debug["reason"] == "marginal_risk_charge_veto"


@pytest.mark.parametrize(
    ("candidate_variance", "expected_qty", "expected_reason"),
    [(0.25, 1, "approved"), (1.0, 0, "post_validation_variance_veto")],
)
def test_discrete_post_validation_shrinks_or_vetoes_false_analytical_capacity(
    candidate_variance,
    expected_qty,
    expected_reason,
):
    candidate = _contract(
        "KXHIGHDEN-99AUG25-T80",
        "Will the high temperature in Denver be 80F or above?",
        80.0,
    )
    with (
        patch("forecast.covariance_engine.get_station_correlation_matrix", return_value=({}, True)),
        patch("forecast.db.get_contract_metadata", return_value=None),
        patch("data.kalshi_weather_monitor.get_weather_data", return_value={"members_high": [79.0, 80.0, 81.0]}),
        patch("forecast.pricing_engine.calculate_pricing", return_value={"q_hat": 0.50}),
        patch(
            "forecast.covariance_engine.assemble_covariance_matrix",
            return_value=np.array([[candidate_variance]], dtype=float),
        ),
        patch("forecast.covariance_engine.calculate_shrinkage_limit", return_value=5.0),
        patch("forecast.covariance_engine.calculate_marginal_risk_charge", return_value=1.0),
    ):
        qty, _charge, debug = check_and_shrink_candidate(
            candidate_contract=candidate,
            candidate_side="YES",
            candidate_price=0.50,
            candidate_qty=5,
            open_positions=[],
            bankroll=10.0,
        )

    assert qty == expected_qty
    assert debug["reason"] == expected_reason
    if qty:
        assert debug["projected_variance"] <= debug["limit"] + 1e-9
