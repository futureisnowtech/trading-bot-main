from __future__ import annotations

from types import SimpleNamespace


def test_market_scan_findings_blocks_infra_cluster():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 10,
            "infrastructure_rejections": [{"reason": "missing_quotes", "count": 4}],
            "systematic_thin_liquidity": False,
        },
        active_markets=10,
        strict_runtime=True,
    )

    assert blockers == ["quote_ingestion_failure (4/10 infrastructure vetoes)"]
    assert warnings == []


def test_market_scan_findings_warns_on_systematic_thin_liquidity():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 8,
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": True,
        },
        active_markets=8,
        strict_runtime=True,
    )

    assert blockers == []
    assert warnings == ["systematic_thin_liquidity"]


def test_render_markdown_report_contains_verdict_and_counts():
    import scripts.release_audit as ra

    markdown = ra._render_markdown_report(
        {
            "mode": "remote_hosted",
            "verdict": "READY_FOR_LIVE",
            "entries_allowed": True,
            "audited_sha": "abc123",
            "as_of": "2026-06-05T20:00:00+00:00",
            "blockers": [],
            "warnings": ["systematic_thin_liquidity"],
            "details": {
                "provider_status": {"provider_mode": "deterministic_multi_model"},
                "live_truth": {"broker_connected": True, "active_markets": 12},
                "release_status": {"heartbeat_fresh": True},
                "market_scan": {
                    "markets_scanned": 12,
                    "approved_candidates": 2,
                    "execution_ready": 1,
                    "thin_liquidity_count": 1,
                },
            },
        }
    )

    assert "# Release Audit" in markdown
    assert "`READY_FOR_LIVE`" in markdown
    assert "Thin Liquidity Count" in markdown


def test_strategy_cycle_blocks_new_entries_when_release_gate_closed(monkeypatch):
    import config
    import forecast.db as fdb
    import forecast.runner as fr
    import runtime.operator_truth as ot

    events: list[tuple[str, str, str]] = []

    class BrokerStub:
        def is_connected(self):
            return True

        def sync_positions(self):
            return None

        def get_account_balance(self):
            return 164.0

        def get_positions(self):
            return []

    monkeypatch.setattr(config, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FORECAST_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(fr, "_get_broker", lambda: BrokerStub(), raising=False)
    monkeypatch.setattr(
        fdb,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 7,
                "local_symbol": "KXLOWNY-26JUN06-T70",
                "contract_name": "NY Low",
                "right": "C",
                "strike": 70.0,
                "last_trade_at": "20260606",
                "resolution_at": "2026-06-06T04:59:00Z",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        ot,
        "get_release_status",
        lambda: {
            "entries_allowed": False,
            "current_release_verdict": "BLOCKED",
            "top_infrastructure_blockers": ["release_audit_missing"],
        },
        raising=False,
    )
    monkeypatch.setattr(
        fr,
        "log_event",
        lambda level, source, message: events.append((level, source, message)),
        raising=False,
    )

    result = fr.run_strategy_cycle(bankroll=164.0)

    assert result == []
    assert any("entry_gate_blocked" in message for _level, _source, message in events)


def test_scan_live_market_surface_stays_read_only_for_weather_truth(monkeypatch):
    import scripts.release_audit as ra

    monkeypatch.setattr(
        ra,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 9,
                "local_symbol": "KXHIGHNY-26JUN05-B89.5",
                "contract_name": "NY High",
                "right": "C",
                "strike": 89.5,
                "last_trade_at": "2026-06-05T23:59:59Z",
                "resolution_at": "2026-06-05T23:59:59Z",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "build_market_snapshots",
        lambda *args, **kwargs: [
            SimpleNamespace(
                ticker="KXHIGHNY-26JUN05-B89.5",
                yes_quote={},
                no_quote={},
            )
        ],
        raising=False,
    )

    def _evaluate_market_snapshots(**kwargs):
        return []

    monkeypatch.setattr(ra, "evaluate_market_snapshots", _evaluate_market_snapshots, raising=False)

    payload = ra._scan_live_market_surface(
        bankroll=164.0,
        open_positions=[],
        scan_limit=4,
    )

    assert payload["weather_warmup"]["mode"] == "read_only_shared_truth"
    assert payload["weather_warmup"]["attempted"] is False
