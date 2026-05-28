"""
tests/proof/test_anti_hallucination_v19.py
v19.1.ARCH structural integrity audit.

Asserts that legacy paper-trading, obsolete brain documentation, 
and retired open_positions ledger logic cannot exist in the codebase.
"""

import os
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

def test_no_brain_directory():
    """Assert that the obsolete brain/ directory has been purged."""
    brain_dir = ROOT / "brain"
    assert not brain_dir.exists(), "The brain/ directory must not exist in v19.1.ARCH"

def test_no_paper_trading_in_config():
    """Assert that PAPER_TRADING is no longer a configuration constant."""
    import config
    assert not hasattr(config, "PAPER_TRADING"), "config.PAPER_TRADING must be excised in v19.1.ARCH"

def test_no_persist_position_in_logger():
    """Assert that the authoritative open_positions ledger write function is excised."""
    from logging_db import trade_logger
    assert not hasattr(trade_logger, "persist_position"), "trade_logger.persist_position must be excised in v19.1.ARCH"
    assert not hasattr(trade_logger, "delete_position"), "trade_logger.delete_position must be excised in v19.1.ARCH"

def test_no_obsolete_docs():
    """Assert that all outdated rebuild plans and masterplans are purged."""
    obsolete_files = [
        "brain_constitution.md",
        "brain_execution_os.md",
        "MATRIX_DECISION_UNIVERSE.md",
        "PROFIT_GOVERNANCE.md",
        "STOP_MATRIX.md",
        "RUNTIME_INVARIANTS.md",
        "docs/V5_REBUILD_PLAN.md",
        "docs/INTEGRATION_PLAN.md",
        "docs/PROJECT_AUDIT.md",
        "docs/SOVEREIGN_MASTERPLAN.md",
        "DEPLOYMENT_STATE_MACHINE.md",
        "SOP.html",
        "scripts/go_live.py",
        "scripts/check_readiness.py",
        "scripts/nightly_recon.py",
        "scripts/diagnose_drift.py",
        "scripts/coinbase_launch_validator.py",
        "scripts/ironclad_acceptance_test.py",
        "scripts/acceptance_test_spot_pipeline.py",
        "scripts/promote_perp_live.py",
        "scripts/migrate_v10.py",
        "scripts/migrate_clean_start.py",
        "scripts/funding_carry_audit.py",
        "scripts/purge_phantom_trades.py",
        "scripts/refresh_sop.py",
    ]
    for f in obsolete_files:
        p = ROOT / f
        assert not p.exists(), f"Obsolete document {f} must be purged"

def test_no_spot_position_truth_runtime():
    """Assert that the retired spot_position_truth service is purged."""
    p = ROOT / "runtime" / "spot_position_truth.py"
    assert not p.exists(), "runtime/spot_position_truth.py must be purged in v19.1.ARCH"
