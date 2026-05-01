#!/usr/bin/env python3
"""
scripts/nightly_recon.py — Lightweight nightly reconciliation job.
Compares DB open_positions to actual broker holdings.
Logs discrepancies to system_events for operator review.
"""

import os
import sys

# Ensure parent dir is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime.spot_position_truth import get_spot_position_truth
from logging_db.trade_logger import log_event
from config import LIVE_TRADING

def run_reconciliation():
    """Run reconciliation for live lane."""
    if not LIVE_TRADING:
        print("Reconciliation skipped: system is in PAPER_TRADING mode.")
        return

    print("Running nightly reconciliation...")
    try:
        truth = get_spot_position_truth(paper=False)
        
        if not truth.get("snapshot_ok"):
            log_event("CRITICAL", "nightly_recon", "Broker snapshot failed during nightly recon.")
            return

        issues = truth.get("issues", [])

        if not issues:
            log_event("INFO", "nightly_recon", "Reconciliation successful. 0 discrepancies found.")
            print("Done. No issues.")
            return

        for issue in issues:
            symbol = issue.get("symbol", "UNKNOWN")
            status = issue.get("position_truth_status", "unclassified")
            msg = f"Discrepancy found: {symbol} status={status}"
            level = "CRITICAL" if issue.get("truth_blocking") else "WARNING"
            log_event(level, "nightly_recon", msg)
            print(f"[{level}] {msg}")

        print(f"Reconciliation complete. {len(issues)} issues logged.")

    except Exception as e:
        log_event("ERROR", "nightly_recon", f"Nightly recon crashed: {str(e)}")
        print(f"Error: {e}")

if __name__ == "__main__":
    run_reconciliation()
