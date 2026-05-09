#!/usr/bin/env python3
"""
scripts/reconcile_spot_truth.py — diagnostic utility for the Coinbase spot lane.

Prints a truth table comparing broker holdings vs DB state and offers repairs.
"""

import sys
import os
import sqlite3
import logging

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.spot_position_truth import get_spot_position_truth, _resolve_db_path
from logging_db.trade_logger import delete_position

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def print_truth_table(truth: dict, paper: bool):
    mode = "PAPER" if paper else "LIVE"
    logger.info(f"\n--- {mode} SPOT TRUTH TABLE ---")
    logger.info(f"Snapshot OK: {truth.get('snapshot_ok')}")
    logger.info(f"Cash Available: ${truth.get('broker_cash_usd') or 0.0:.2f}")
    logger.info(f"Deployment Notional: ${truth.get('deployment_notional') or 0.0:.2f}")
    logger.info(f"Positions Open: {truth.get('positions_open') or 0}")

    all_holdings = truth.get("all_live_holdings", [])
    issues = truth.get("issues", [])

    if not all_holdings and not issues:
        logger.info("No holdings or issues found.")
        return

    logger.info("\n%-10s | %-15s | %-12s | %-20s | %-10s" % ("SYMBOL", "QTY", "VALUE", "STATUS", "BOT?"))
    logger.info("-" * 80)
    
    for row in all_holdings:
        logger.info("%-10s | %-15.8f | %-12.2f | %-20s | %-10s" % (
            row.get("symbol"),
            row.get("qty", 0),
            row.get("current_value", 0),
            row.get("position_truth_status"),
            row.get("is_bot_managed")
        ))
    
    for row in issues:
        if any(h["symbol"] == row["symbol"] for h in all_holdings):
            continue
        logger.info("%-10s | %-15.8f | %-12.2f | %-20s | %-10s" % (
            row.get("symbol"),
            row.get("qty", 0),
            row.get("current_value", 0),
            row.get("position_truth_status"),
            row.get("is_bot_managed")
        ))

def repair_issues(truth: dict, paper: bool):
    issues = truth.get("issues", [])
    if not issues:
        logger.info("No repairable issues found.")
        return

    for row in issues:
        status = row.get("position_truth_status")
        symbol = row.get("symbol")
        
        if status in ("db_only_stale", "metadata_missing"):
            logger.info(f"Repairing {symbol} ({status})...")
            try:
                strat = row.get("strategy") or f"spot_{symbol.lower()}"
                delete_position(symbol, strategy=strat)
                logger.info(f"Successfully deleted stale/broken position for {symbol}")
            except Exception as e:
                logger.error(f"Failed to repair {symbol}: {e}")

def main():
    repair = "--repair" in sys.argv
    
    # Check Live
    live_truth = get_spot_position_truth()
    print_truth_table(live_truth)
    
    # Check Paper
    paper_truth = get_spot_position_truth()
    print_truth_table(paper_truth)

    if repair:
        logger.info("\n--- REPAIR MODE ACTIVE ---")
        repair_issues(live_truth)
        repair_issues(paper_truth)
        logger.info("\nRepairs complete. Re-running check...")
        live_truth = get_spot_position_truth()
        print_truth_table(live_truth)
        paper_truth = get_spot_position_truth()
        print_truth_table(paper_truth)

if __name__ == "__main__":
    main()
