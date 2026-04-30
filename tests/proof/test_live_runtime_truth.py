from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel_path: str) -> str:
    with open(os.path.join(ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


def test_main_does_not_seed_crypto_lane_as_operational_before_runner_truth():
    src = _read("main.py")
    assert 'upsert_lane_state(\n        "crypto"' in src
    assert 'readiness_state="STARTING"' in src, (
        "main.py must seed the crypto lane as STARTING until the runner writes real "
        "connected/tradable/buying-power truth."
    )


def test_v10_runner_writes_crypto_lane_runtime_fields():
    src = _read("scheduler/v10_runner.py")
    assert "def _write_crypto_lane_runtime" in src
    for needle in (
        'upsert_lane_state(',
        '"crypto"',
        "connected=",
        "tradable=",
        "positions_open=",
        "capital_deployed_usd=",
        "buying_power_usd=",
        "readiness_state=",
    ):
        assert needle in src, f"Missing runtime truth field write: {needle}"


def test_go_live_waits_for_connected_crypto_lane_not_just_process_mode():
    src = _read("scripts/go_live.py")
    assert "def _load_crypto_lane" in src, (
        "go_live.py must read crypto lane runtime state during launch verification."
    )
    assert "connected, buying_power, readiness, blocked_reason = _load_crypto_lane()" in src
    assert "_spot_truth_ready()" in src, (
        "go_live.py must preflight the broker-canonical spot truth contract."
    )
    assert 'mode == "live" and connected and buying_power > 0 and readiness == "TINY_LIVE"' in src, (
        "go_live.py must not declare success on process_mode alone."
    )
