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
    assert row["exit_pnl_est"] == 0.32
    assert row["weather_bucket"] == "Daily High"


def test_summarize_hub_exposure_groups_city_hubs():
    from dashboard.cockpit_data import summarize_hub_exposure

    exposures = summarize_hub_exposure(
        [
            {"ticker": "KXHIGHLAX-26JUN05-B69.5", "qty": 20, "entry_price": 0.20, "hub": "WEST"},
            {"ticker": "KXHIGHSEA-26JUN05-B61.5", "qty": 10, "entry_price": 0.30, "hub": "WEST"},
            {"ticker": "KXHIGHMIA-26JUN05-B83.5", "qty": 5, "entry_price": 0.40, "hub": "FLORIDA"},
        ]
    )

    assert exposures[0] == {"hub": "WEST", "exposure_usd": 7.38}
    assert exposures[1] == {"hub": "FLORIDA", "exposure_usd": 2.09}


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


def test_build_open_book_visual_rows_adds_book_heat_fields():
    from dashboard.cockpit_data import build_open_book_visual_rows

    rows = build_open_book_visual_rows(
        [
            {
                "ticker": "KXHIGHMIA-26JUN06-B87.5",
                "contract_name": "Miami High 87-88",
                "side": "NO",
                "qty": 10,
                "entry_price": 0.15,
                "gross_mark_pnl": -0.25,
                "exit_pnl_est": -0.59,
                "hub": "FLORIDA",
                "resolution_at": "2099-06-07T04:59:00Z",
            },
            {
                "ticker": "KXLOWTHOU-26JUN06-B76.5",
                "contract_name": "Houston Low 76-77",
                "side": "NO",
                "qty": 20,
                "entry_price": 0.16,
                "gross_mark_pnl": -0.75,
                "exit_pnl_est": -2.37,
                "hub": "GULF",
                "resolution_at": "2099-06-08T04:59:00Z",
            },
        ]
    )

    assert len(rows) == 2
    assert rows[0]["display_label"] == "NO • KXHIGHMIA-26JUN06-B87.5"
    assert rows[0]["exposure_usd"] > 0
    assert rows[0]["hours_to_resolution"] is not None
    assert rows[0]["resolve_bucket"] in {"0-12h", "12-24h", "24-48h", ">48h"}
    assert rows[0]["book_weight_pct"] > 0
    assert round(sum(row["book_weight_pct"] for row in rows), 2) == 100.0


def test_summarize_open_book_rolls_up_exposure_and_resolution():
    from dashboard.cockpit_data import summarize_open_book

    summary = summarize_open_book(
        [
            {
                "ticker": "KXHIGHMIA-26JUN06-B87.5",
                "contract_name": "Miami High 87-88",
                "side": "NO",
                "qty": 10,
                "entry_price": 0.15,
                "gross_mark_pnl": -0.25,
                "exit_pnl_est": -0.59,
                "hub": "FLORIDA",
                "resolution_at": "2099-06-07T04:59:00Z",
            },
            {
                "ticker": "KXLOWTHOU-26JUN06-B76.5",
                "contract_name": "Houston Low 76-77",
                "side": "NO",
                "qty": 20,
                "entry_price": 0.16,
                "gross_mark_pnl": -0.75,
                "exit_pnl_est": -2.37,
                "hub": "GULF",
                "resolution_at": "2099-06-08T04:59:00Z",
            },
        ]
    )

    assert summary["position_count"] == 2
    assert summary["contract_count"] == 30
    assert summary["total_exposure_usd"] > 0
    assert summary["total_mark_pnl_usd"] == -1.0
    assert summary["total_exit_pnl_est_usd"] == -2.96
    assert summary["largest_hub"] in {"FLORIDA", "GULF"}
    assert summary["nearest_resolution_label"] != "N/A"


def test_build_regime_manifest_surfaces_live_constants():
    from dashboard.cockpit_data import build_regime_manifest

    manifest = build_regime_manifest(balance_usd=200.0)

    assert manifest["version"]
    assert manifest["reasoning_model"]
    assert "60% GFS + 40% ECMWF" in manifest["ensemble_blend"]
    assert any("0.85" in line for line in manifest["exit_stack"])
    assert any("Same event family cap 5" in line for line in manifest["entry_gates"])
    assert any("$60.00" in line for line in manifest["entry_gates"])


def test_build_regime_manifest_surfaces_adaptive_blend_when_available():
    from dashboard.cockpit_data import build_regime_manifest

    manifest = build_regime_manifest(
        balance_usd=200.0,
        learning_status={
            "global_blend": {
                "segment": "GLOBAL",
                "sample_size": 14,
                "gfs_weight": 0.42,
                "ecmwf_weight": 0.58,
            }
        },
    )

    assert "GLOBAL: GFS 42% / ECMWF 58% from 14 resolved samples" in manifest["ensemble_blend"]


def test_build_regime_manifest_uses_runtime_build_version(monkeypatch):
    import dashboard.cockpit_data as cd

    monkeypatch.setattr(cd, "get_build_info", lambda: {"app_version": "19.10.1"})

    manifest = cd.build_regime_manifest(balance_usd=200.0)

    assert manifest["version"] == "19.10.1"


def test_metric_explainers_surface_new_hub_cap_formula():
    from dashboard.cockpit_data import build_metric_explainers

    explainers = build_metric_explainers(balance_usd=144.31)

    assert "43.29 dollars" in explainers["Regional Hub Cap"]
    assert "max($40" in explainers["Regional Hub Cap"]
    assert "30% of live cash" in explainers["Regional Hub Cap"]
    assert "7.0% x price x (1-price)" in explainers["Fee Model"]


def test_build_weather_type_boards_groups_open_book_by_lane():
    from dashboard.cockpit_data import build_weather_type_boards

    boards = build_weather_type_boards(
        [
            {
                "ticker": "KXHIGHMIA-26JUN06-B87.5",
                "contract_name": "Miami High 87-88",
                "side": "NO",
                "qty": 10,
                "entry_price": 0.15,
                "gross_mark_pnl": -0.25,
                "exit_pnl_est": -0.59,
                "hub": "FLORIDA",
                "resolution_at": "2099-06-07T04:59:00Z",
            },
            {
                "ticker": "KXRAINNYC-26JUN06-T0",
                "contract_name": "Will it rain in New York City on Saturday?",
                "side": "YES",
                "qty": 8,
                "entry_price": 0.42,
                "gross_mark_pnl": 0.10,
                "exit_pnl_est": -0.12,
                "hub": "NORTHEAST",
                "resolution_at": "2099-06-07T04:59:00Z",
            },
            {
                "ticker": "KXTEMPNYCH-26JUN0522-T75.99",
                "contract_name": "Will the temp in New York City be above 75.99° on Jun 5, 2026 at 10pm EDT?",
                "side": "YES",
                "qty": 5,
                "entry_price": 0.21,
                "gross_mark_pnl": 0.05,
                "exit_pnl_est": -0.08,
                "hub": "NORTHEAST",
                "resolution_at": "2099-06-06T02:00:00Z",
            },
        ]
    )

    by_bucket = {board["bucket"]: board for board in boards}
    assert by_bucket["Daily High"]["position_count"] == 1
    assert by_bucket["Rain"]["position_count"] == 1
    assert by_bucket["Hourly Temp"]["position_count"] == 1


def test_build_trade_edge_rows_handles_yes_and_no_side_buys():
    from dashboard.cockpit_data import build_trade_edge_rows

    rows = build_trade_edge_rows(
        [
            {
                "ts": "2026-06-05T03:00:00+00:00",
                "symbol": "KXHIGHLAX-26JUN05-B69.5",
                "action": "BUY",
                "price": 0.16,
                "contract_side": "YES",
                "forecast_yes_prob": 0.97,
                "strategy": "forecast_weather_ensemble",
            },
            {
                "ts": "2026-06-05T02:00:00+00:00",
                "symbol": "KXLOWTLV-26JUN05-T78",
                "action": "BUY",
                "price": 0.29,
                "contract_side": "NO",
                "forecast_yes_prob": 0.18,
                "strategy": "forecast_weather_ensemble",
            },
        ]
    )

    assert rows[0]["model_confidence_pct"] == 97.0
    assert rows[0]["edge_pct"] == 80.1
    assert rows[0]["edge_label"] == "Net Edge"
    assert rows[1]["model_confidence_pct"] == 82.0
    assert rows[1]["edge_pct"] == 51.6


def test_load_session_win_rate_uses_broker_settlement_truth(monkeypatch):
    import dashboard.cockpit_data as cd

    monkeypatch.setattr(
        cd,
        "load_weather_settlement_truth",
        lambda: {
            "total": 12,
            "wins": 8,
            "losses": 4,
            "win_rate": 8 / 12,
            "total_won_usd": 21.5,
            "total_lost_usd": -9.25,
            "total_pnl_usd": 12.25,
            "source": "broker_settlements",
            "stale": False,
        },
    )

    payload = cd.load_session_win_rate()

    assert payload["total"] == 12
    assert payload["wins"] == 8
    assert payload["losses"] == 4
    assert payload["total_pnl_usd"] == 12.25
    assert payload["source"] == "broker_settlements"
    assert payload["stale"] is False


def test_build_ai_insights_translates_runtime_into_plain_english():
    from dashboard.cockpit_data import build_ai_insights

    insights = build_ai_insights(
        truth={"broker_connected": True, "position_drift": {"has_drift": False}},
        release_status={"current_release_verdict": "READY_FOR_LIVE", "entries_allowed": True},
        lane={"readiness_state": "OPERATIONAL"},
        market_counts={"active_markets": 168},
        recent_events=[
            {
                "source": "PositionReconciler",
                "message": "Reconciliation complete: connected=True Kalshi holdings=1 adopted=0 refreshed=1 closed=0",
            },
            {
                "source": "Discovery",
                "message": "[Discovery] found=382 persisted=240 stubs=0 active_in_db=336 errors=0",
            },
        ],
        recent_trades=[],
        recent_vetoes={"count": 2, "top_reasons": [{"reason": "missing_quotes", "count": 2}]},
        learning_status={
            "global_blend": {
                "sample_size": 14,
                "gfs_weight": 0.42,
                "ecmwf_weight": 0.58,
            }
        },
    )

    titles = [row["title"] for row in insights]
    bodies = " ".join(row["body"] for row in insights)

    assert "Engine Live" in titles
    assert "Release Gate Open" in titles
    assert "Learner Active" in titles
    assert "Ledger Reconciled" in titles
    assert "Universe Refreshed" in titles
    assert "holding" in bodies.lower() or "veto" in bodies.lower()


def test_load_deploy_metadata_prefers_runtime_dir(tmp_path, monkeypatch):
    import json
    import runtime.build_info as bi

    runtime_dir = tmp_path / "runtime"
    root_dir = tmp_path / "root"
    runtime_dir.mkdir()
    root_dir.mkdir()

    (runtime_dir / "deploy_manifest.json").write_text(
        json.dumps(
            {
                "app_version": "19.10.1",
                "sha": "runtime-sha",
                "cockpit_url": "http://example",
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "version.txt").write_text(
        "app_version=19.10.1\nsha=runtime-sha\nbranch=master\ndeployed_at_utc=2026-06-05T00:00:00Z\n",
        encoding="utf-8",
    )
    (root_dir / "deploy_manifest.json").write_text(
        json.dumps({"app_version": "19.9.9", "sha": "root-sha"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(bi, "_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(bi, "_ROOT", root_dir)

    payload = bi.load_deploy_metadata()
    build = bi.get_build_info()

    assert payload["app_version"] == "19.10.1"
    assert payload["sha"] == "runtime-sha"
    assert payload["branch"] == "master"
    assert build["version"] == "19.10.1"
    assert build["short_sha"] == "runtime"


def test_get_build_info_ignores_stale_local_metadata_when_git_sha_differs(monkeypatch):
    import runtime.build_info as bi

    monkeypatch.setattr(
        bi,
        "load_deploy_metadata",
        lambda: {
            "app_version": "19.9.9",
            "sha": "olddeploysha",
            "branch": "master",
            "deployed_at_utc": "2026-06-01T00:00:00Z",
            "cockpit_url": "http://stale.example",
        },
    )

    def fake_git_value(*args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "newgitsha1234567"
        if args == ("branch", "--show-current"):
            return "master"
        return ""

    monkeypatch.setattr(bi, "_read_git_value", fake_git_value)

    build = bi.get_build_info()

    assert build["version"] != "19.9.9"
    assert build["sha"] == "newgitsha1234567"
    assert build["short_sha"] == "newgits"
    assert build["deployed_at_utc"] == ""
    assert build["cockpit_url"] == ""
    assert build["metadata_stale"] is True
