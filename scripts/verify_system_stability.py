#!/usr/bin/env python3
"""
scripts/verify_system_stability.py — Systemic validation of truth reconciliation and signature consistency.

Proves:
1. _get_broker() signature consistency in v10_runner.
2. STETH is correctly bypassed as external_manual.
3. LINK (now removed from manual list) correctly triggers a truth blocker if unclassified.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import sqlite3

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.spot_position_truth import get_spot_position_truth
import spot_engine

class SystemStabilityTest(unittest.TestCase):
    
    def setUp(self):
        self.db_path = "logs/test_stability.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE open_positions (
                symbol TEXT,
                strategy TEXT,
                qty REAL,
                entry REAL,
                paper INTEGER,
                direction TEXT,
                ts_entry TEXT,
                PRIMARY KEY (symbol, strategy, paper)
            )
        """)
        conn.execute("""
            CREATE TABLE spot_holding_classifications (
                symbol TEXT PRIMARY KEY,
                classification TEXT,
                note TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_broker_signature(self):
        """Verify _get_broker() takes 0 arguments and returns successfully."""
        print("\n[TEST 1] Broker Signature Consistency")
        broker = spot_engine._get_broker()
        # On local dev this might return None if credentials aren't set, but it shouldn't raise TypeError
        print("✅ _get_broker() called successfully without throwing TypeError.")

    def test_staked_eth_manual_bypass(self):
        """Verify STETH is correctly classified as external_manual and returns 0 blockers."""
        print("\n[TEST 2] Staked ETH Manual Bypass")
        
        # Mock broker holding STETH
        mock_holdings = [{"symbol": "STETH", "qty": 1.5, "current_price": 2000.0, "current_value": 3000.0}]
        
        with patch("runtime.spot_position_truth._get_live_broker_snapshot", return_value=(mock_holdings, 1000.0)), \
             patch("runtime.spot_position_truth._resolve_db_path", return_value=self.db_path):
            
            truth = get_spot_position_truth(db_path=self.db_path)
            
            steth_row = next((r for r in truth["all_live_holdings"] if r["symbol"] == "STETH"), None)
            self.assertIsNotNone(steth_row)
            self.assertEqual(steth_row["position_truth_status"], "external_manual")
            self.assertEqual(len(truth["blocking_issues"]), 0)
            print("✅ STETH correctly bypassed as external_manual.")

    def test_link_unclassified_block(self):
        """Verify LINK (no longer in manual list) triggers an unclassified block."""
        print("\n[TEST 3] LINK Unclassified Block (Safety Gate)")
        
        # Mock broker holding LINK
        mock_holdings = [{"symbol": "LINK", "qty": 100.0, "current_price": 10.0, "current_value": 1000.0}]
        
        with patch("runtime.spot_position_truth._get_live_broker_snapshot", return_value=(mock_holdings, 1000.0)), \
             patch("runtime.spot_position_truth._resolve_db_path", return_value=self.db_path):
            
            truth = get_spot_position_truth(db_path=self.db_path)
            
            link_row = next((r for r in truth["all_live_holdings"] if r["symbol"] == "LINK"), None)
            self.assertIsNotNone(link_row)
            # Since LINK is not in _DEFAULT_EXTERNAL_MANUAL and no DB row exists, it should be 'unclassified'
            self.assertEqual(link_row["position_truth_status"], "unclassified")
            self.assertGreater(len(truth["blocking_issues"]), 0)
            print("✅ LINK correctly blocked as unclassified.")

if __name__ == "__main__":
    unittest.main()
