from scripts.gate_audit import (
    _coerce_event_ts,
    parse_shadow_block_line,
    parse_veto_message,
    reason_family,
)


def test_parse_veto_message_extracts_ticker_and_reason():
    rec = parse_veto_message(
        1717540000.0,
        "[ForecastRunner] KXHIGHNY-04JUN26-T85 vetoed: market_truth_veto (Divergence=22.0% > 20%)",
    )

    assert rec is not None
    assert rec.ticker == "KXHIGHNY-04JUN26-T85"
    assert rec.reason == "market_truth_veto (Divergence=22.0% > 20%)"
    assert rec.family == "market_truth_veto"


def test_reason_family_normalizes_suffixes():
    assert reason_family("LOW_CONVICTION_ALPHA (Net_EV=0.0110 < 0.05)") == "LOW_CONVICTION_ALPHA"
    assert reason_family("stale_market_data") == "stale_market_data"


def test_parse_shadow_block_line_extracts_order_shape():
    attempt = parse_shadow_block_line(
        "[Kalshi] SHADOW MODE: Blocked POST /trade-api/v2/portfolio/orders "
        "body={'ticker': 'KXHIGHNY-04JUN26-T85', 'action': 'buy', 'side': 'yes', 'count': 4, 'type': 'limit'}"
    )

    assert attempt is not None
    assert attempt.ticker == "KXHIGHNY-04JUN26-T85"
    assert attempt.action == "BUY"
    assert attempt.side == "YES"
    assert attempt.count == 4
    assert attempt.order_type == "LIMIT"


def test_parse_shadow_block_line_ignores_noise():
    assert parse_shadow_block_line("ordinary log line") is None


def test_coerce_event_ts_accepts_isoformat_strings():
    ts = _coerce_event_ts("2026-06-04T22:30:00+00:00")

    assert ts is not None
    assert ts > 0
