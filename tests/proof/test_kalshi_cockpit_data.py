from __future__ import annotations


def test_build_position_row_uses_side_specific_no_quotes():
    from dashboard.cockpit_data import build_position_row

    row = build_position_row(
        {
            "ticker": "KXHIGHDEN-26JUN06-T79",
            "side": "NO",
            "qty": 10,
            "entry_price": 0.41,
            "opened_at": "2026-06-05T00:00:00+00:00",
        },
        metadata={"contract_name": "Denver High", "resolution_at": "2026-06-06T04:59:00Z"},
        quote={"no_bid": 0.53, "no_ask": 0.57, "yes_bid": 0.43, "yes_ask": 0.47},
        entry_trade={"fee_usd": 0.7, "strategy": "weather_ensemble"},
    )

    assert row["bid"] == 0.53
    assert row["ask"] == 0.57
    assert row["mark"] == 0.55
    assert row["gross_mark_pnl"] == 1.4
    assert row["exit_pnl_est"] == -0.2


def test_summarize_hub_exposure_groups_city_hubs():
    from dashboard.cockpit_data import summarize_hub_exposure

    exposures = summarize_hub_exposure(
        [
            {"ticker": "KXHIGHLAX-26JUN05-B69.5", "qty": 20, "entry_price": 0.20, "hub": "WEST"},
            {"ticker": "KXHIGHSEA-26JUN05-B61.5", "qty": 10, "entry_price": 0.30, "hub": "WEST"},
            {"ticker": "KXHIGHMIA-26JUN05-B83.5", "qty": 5, "entry_price": 0.40, "hub": "FLORIDA"},
        ]
    )

    assert exposures[0] == {"hub": "WEST", "cost_basis_usd": 7.0}
    assert exposures[1] == {"hub": "FLORIDA", "cost_basis_usd": 2.0}


def test_build_realized_pnl_curve_accumulates_in_time_order():
    from dashboard.cockpit_data import build_realized_pnl_curve

    curve = build_realized_pnl_curve(
        [
            {"ts": "2026-06-05T03:00:00+00:00", "pnl_usd": 3.0},
            {"ts": "2026-06-05T02:00:00+00:00", "pnl_usd": -1.5},
            {"ts": "2026-06-05T01:00:00+00:00", "pnl_usd": 2.0},
        ]
    )

    assert [point["cumulative_pnl"] for point in curve] == [2.0, 0.5, 3.5]


def test_build_regime_manifest_surfaces_live_constants():
    from dashboard.cockpit_data import build_regime_manifest

    manifest = build_regime_manifest(balance_usd=200.0)

    assert manifest["version"]
    assert manifest["reasoning_model"]
    assert "60% GFS + 40% ECMWF" in manifest["ensemble_blend"]
    assert any("0.85" in line for line in manifest["exit_stack"])
    assert any("$40.00" in line for line in manifest["entry_gates"])
