"""
tests/proof/test_execution_universe_gate.py — Proof tests for execution universe split (v15.10).

Coverage:
  1. get_underlying normalises all expected formats
  2. CORE_EXECUTION_UNDERLYINGS contains exactly the 10 expected symbols
  3. Core symbols return tier='core', execute=True
  4. SUPPRESSED_SYMBOLS return tier='suppressed', execute=False, reason='suppressed_symbol'
  5. Non-core non-suppressed symbols return tier='research_only', execute=False
  6. v10_runner imports _get_underlying from runtime.execution_universe (not local)
  7. v10_runner has research_only_block gate in _attempt_entry
  8. research_only_block gate fires after econ gate (correct order in source)
  9. get_execution_tier is case-insensitive
  10. CORE_EXECUTION_UNDERLYINGS is a set in config (not a list)
"""

import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_get_underlying_normalises_formats():
    from runtime.execution_universe import get_underlying

    assert get_underlying("PF_ETHUSD") == "ETH"
    assert get_underlying("ETHUSDT") == "ETH"
    assert get_underlying("ETH") == "ETH"
    assert get_underlying("ETH-USDC") == "ETH"
    assert get_underlying("ETHFI") == "ETHFI"
    assert get_underlying("BTCUSDT") == "BTC"
    assert get_underlying("PF_BTCUSD") == "BTC"
    assert get_underlying("SOLUSDT") == "SOL"


def test_core_execution_underlyings_has_10_symbols():
    from config import CORE_EXECUTION_UNDERLYINGS

    assert isinstance(CORE_EXECUTION_UNDERLYINGS, set), "must be a set"
    assert len(CORE_EXECUTION_UNDERLYINGS) == 10, (
        f"expected 10 core symbols, got {len(CORE_EXECUTION_UNDERLYINGS)}: {CORE_EXECUTION_UNDERLYINGS}"
    )


def test_core_execution_underlyings_contains_expected():
    from config import CORE_EXECUTION_UNDERLYINGS

    expected = {
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "DOGE",
        "AVAX",
        "LINK",
        "AAVE",
        "INJ",
        "NEAR",
    }
    assert expected == CORE_EXECUTION_UNDERLYINGS, (
        f"mismatch: expected={expected}, got={CORE_EXECUTION_UNDERLYINGS}"
    )


def test_core_symbols_return_execute_true():
    from runtime.execution_universe import get_execution_policy

    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "PF_BTCUSD", "ETH"):
        policy = get_execution_policy(sym)
        assert policy["execute"], f"{sym} should be executable: {policy}"
        assert policy["tier"] == "core", f"{sym} should be tier=core: {policy}"


def test_suppressed_symbols_return_execute_false():
    from runtime.execution_universe import get_execution_policy
    from config import SUPPRESSED_SYMBOLS

    for sym in SUPPRESSED_SYMBOLS:
        policy = get_execution_policy(sym)
        assert not policy["execute"], f"{sym} should be blocked: {policy}"
        assert policy["tier"] == "suppressed", (
            f"{sym} should be tier=suppressed: {policy}"
        )
        assert policy["reason"] == "suppressed_symbol"


def test_non_core_non_suppressed_returns_research_only():
    from runtime.execution_universe import get_execution_policy

    for sym in ("BNBUSDT", "OPUSDT", "ARBUSDT", "GMXUSDT", "PENDLEUSDT"):
        policy = get_execution_policy(sym)
        assert not policy["execute"], f"{sym} should be blocked: {policy}"
        assert policy["tier"] == "research_only"
        assert policy["reason"] == "non_core_execution_universe"


def test_v10_runner_imports_get_underlying_from_execution_universe():
    """v10_runner must not define its own _get_underlying function."""
    runner_path = os.path.join(_ROOT, "scheduler", "v10_runner.py")
    src = open(runner_path).read()
    assert "from runtime.execution_universe import get_underlying" in src, (
        "v10_runner must import _get_underlying from runtime.execution_universe"
    )
    # Must NOT define a local _get_underlying function
    tree = ast.parse(src)
    local_defs = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_get_underlying"
    ]
    assert not local_defs, (
        "v10_runner must not define a local _get_underlying — use the shared helper"
    )


def test_v10_runner_has_research_only_block_gate():
    """v10_runner._attempt_entry must contain the research_only_block gate."""
    runner_path = os.path.join(_ROOT, "scheduler", "v10_runner.py")
    src = open(runner_path).read()
    assert "research_only_block" in src, (
        "v10_runner must journal research_only_block decisions"
    )
    assert "get_execution_policy" in src or "execution_universe" in src, (
        "v10_runner must call execution universe policy in _attempt_entry"
    )


def test_research_only_block_gate_is_after_economics_gate():
    """research_only_block gate must appear after economics gate in _attempt_entry.

    Uses the position of 'def _attempt_entry' as the search anchor so that
    earlier counter initializations (_f_research_only_block = 0) don't produce
    a false negative.
    """
    runner_path = os.path.join(_ROOT, "scheduler", "v10_runner.py")
    src = open(runner_path).read()
    func_start = src.find("def _attempt_entry")
    assert func_start != -1, "def _attempt_entry not found in v10_runner"
    func_src = src[func_start:]
    econ_pos = func_src.find("economics_check")
    research_pos = func_src.find("research_only_block")
    assert econ_pos != -1, "economics_check not found in _attempt_entry"
    assert research_pos != -1, "research_only_block not found in _attempt_entry"
    assert research_pos > econ_pos, (
        "research_only_block gate must appear AFTER economics gate in _attempt_entry"
    )


def test_get_execution_tier_is_case_insensitive():
    from runtime.execution_universe import get_execution_tier

    # BTC in all-caps vs mixed case
    assert get_execution_tier("btcusdt") == "core"
    assert get_execution_tier("BTCUSDT") == "core"
    assert get_execution_tier("ethusdt") == "core"


def test_core_execution_underlyings_is_set_in_config():
    import config

    assert isinstance(config.CORE_EXECUTION_UNDERLYINGS, set), (
        "CORE_EXECUTION_UNDERLYINGS must be a set, not a list or tuple"
    )
