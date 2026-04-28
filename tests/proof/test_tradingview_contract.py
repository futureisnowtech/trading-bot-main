from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_tvc01_normalize_bias_contract():
    from scripts.tradingview_webhook import _normalize_bias

    assert _normalize_bias({"action": "buy"}) == ("buy", "LONG", "LONG")
    assert _normalize_bias({"direction": "short"}) == ("short", "SHORT", "SHORT")
    assert _normalize_bias({"bias": "close"}) == ("close", "CLOSE", "CLOSE")


def test_tvc02_tv_signal_round_trip_reads_from_dedicated_table(proof_runtime):
    from logging_db.trade_logger import get_recent_tv_signals, log_tv_signal

    log_tv_signal(
        symbol="BTC-USDC",
        action_raw="buy",
        direction="LONG",
        htf_bias="LONG",
        price=90000.0,
        tf_min="240",
        indicator_name="AlgoBot HTF Confluence Engine v2",
        profile_name="algobot_htf_v2",
        strength="strong",
        signal_desc="htf_long",
        secret_validated=True,
        raw_payload_json='{"symbol":"BTCUSDT"}',
    )

    rows = get_recent_tv_signals(max_age_seconds=60, symbol="BTC-USDC")
    assert len(rows) == 1
    assert rows[0]["profile_name"] == "algobot_htf_v2"
    assert rows[0]["htf_bias"] == "LONG"
    assert rows[0]["secret_validated"] is True
