#!/usr/bin/env python3
"""
scripts/ironclad_acceptance_test.py — Systemic validation of price sanity, truth reconciliation, and semantic vetoes.

Mocks external dependencies to verify architectural fixes in a controlled environment.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sqlite3
from datetime import datetime, timezone

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler.v10_runner import _attempt_entry
from runtime.spot_position_truth import get_spot_position_truth
from runtime.spot_strategy import spot_quality_block_reason

class IroncladAcceptanceTest(unittest.TestCase):
    
    def setUp(self):
        self.db_path = "logs/test_ironclad.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE open_positions (
                symbol TEXT,
                strategy TEXT,
                qty REAL,
                entry REAL,
                stop REAL,
                target REAL,
                high_since_entry REAL,
                low_since_entry REAL,
                ts_entry TEXT,
                paper INTEGER,
                direction TEXT,
                entry_trade_id INTEGER,
                entry_feature_snapshot_id INTEGER,
                base_asset TEXT,
                setup_family TEXT,
                execution_route TEXT,
                trailing_active INTEGER DEFAULT 0,
                scale_33_done INTEGER DEFAULT 0,
                scale_66_done INTEGER DEFAULT 0,
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

    @patch("scheduler.v10_runner.logger")
    def test_drift_test(self, mock_logger):
        """Inject a 6% price drift and assert the fallback logic executes."""
        print("\n[TEST 1] Drift Test (REST Fallback)")
        
        mock_candidate = {
            "symbol": "SOL",
            "_live_price": 106.0,
            "_df": pd.DataFrame({
                "close": [100.0] * 60,
                "high": [101.0] * 60,
                "low": [99.0] * 60,
                "volume": [1000.0] * 60
            })
        }
        
        # Mocking the complex signature of _attempt_entry
        mock_se = MagicMock()
        mock_se.score.return_value = {"composite_score": 50.0}
        
        with patch("scheduler.v10_runner._journal_scan_candidate"), \
             patch("scheduler.v10_runner._tradeability_hint", return_value={}), \
             patch("logging_db.trade_logger.update_scan_candidate_result"), \
             patch("spot_engine.open_spot") as mock_open:
            
            _attempt_entry(
                candidate=mock_candidate,
                symbol="SOL",
                direction="LONG",
                balance=1000.0,
                deployed_usd=0.0,
                perps=None,
                se=mock_se,
                pm=MagicMock(),
                get_candles=MagicMock(),
                build_features=MagicMock(return_value={"rsi": 50.0}),
                classify_from_features=MagicMock(return_value="NORMAL"),
                ne=MagicMock(),
                get_size_multiplier=MagicMock(),
                scan_id="test_scan"
            )
            
            # Verify logger.info was called with "FORCING REST FALLBACK"
            found_log = any("FORCING REST FALLBACK" in str(call) for call in mock_logger.info.call_args_list)
            self.assertTrue(found_log, "Heartbeat sync log not found")
            print("✅ Drift caught and Heartbeat Sync logged.")

    def test_ghost_test(self):
        """Inject a qty_mismatch for DOGE and assert get_spot_position_truth() auto-heals."""
        print("\n[TEST 2] Ghost Test (Auto-reconciliation)")
        
        # 1. Inject mismatched qty into DB for DOGE (NOT in manual list)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO open_positions 
            (symbol, strategy, qty, paper, entry_trade_id, entry_feature_snapshot_id, base_asset, setup_family, execution_route) 
            VALUES ('DOGE', 'spot_doge', 10.0, 0, 123, 456, 'DOGE', 'wae', 'maker_first')
        """)
        conn.commit()
        conn.close()
        
        # 2. Mock broker snapshot with 11.0 qty
        mock_holdings = [{"symbol": "DOGE", "qty": 11.0, "current_price": 1.0, "current_value": 11.0}]
        
        with patch("runtime.spot_position_truth._get_live_broker_snapshot", return_value=(mock_holdings, 1000.0)), \
             patch("runtime.spot_position_truth.DB_PATH", self.db_path):
            
            truth = get_spot_position_truth(db_path=self.db_path)
            
            # Assert 0 blocking issues for DOGE
            blockers = [b for b in truth["blocking_issues"] if b.get("symbol") == "DOGE"]
            self.assertEqual(len(blockers), 0, f"Blocking issues found for DOGE: {blockers}")
            
            # Verify DB was updated
            conn = sqlite3.connect(self.db_path)
            row = conn.execute("SELECT qty FROM open_positions WHERE symbol='DOGE'").fetchone()
            conn.close()
            self.assertEqual(row[0], 11.0, f"DB qty not updated. Expected 11.0, got {row[0]}")
            print("✅ Quantity mismatch auto-healed and returned clean truth.")

    def test_semantic_veto_test(self):
        """Mock a CHOP regime and assert status is STRATEGY_VETO."""
        print("\n[TEST 3] Semantic Veto Test (VETO vs BLOCKER)")
        
        mock_state = {
            "regime": "CHOP",
            "frames": {"5m": {"atr_pct": 0.01, "v": 1000000.0}, "30m": {}}
        }
        
        import system_state
        # Ensure BTC entry exists in stochastic
        system_state.state.update_stochastic("BTC", {"status": "ACTIVE"})
        
        reason, floor = spot_quality_block_reason("BTC", mock_state)
        
        self.assertEqual(reason, "spot_regime_not_allowed:CHOP")
        
        stoch = system_state.state.get_state()["strategy"]["stochastic"].get("BTC", {})
        self.assertEqual(stoch.get("status"), "STRATEGY_VETO")
        
        print("✅ Strategy veto correctly classified.")

if __name__ == "__main__":
    unittest.main()
