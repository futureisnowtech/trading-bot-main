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
import spot_engine

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
                exit_reason TEXT,
                entry_fee_usd REAL DEFAULT 0.0,
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
        """Inject a qty_mismatch and assert get_spot_position_truth() auto-heals."""
        print("\n[TEST 2] Ghost Test (Auto-reconciliation)")
        
        # 1. Inject mismatched qty into DB for DOGE
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
             patch("runtime.spot_position_truth.DB_PATH", self.db_path), \
             patch("runtime.spot_position_truth._resolve_db_path", return_value=self.db_path):
            
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

    def test_inventory_aware_close_test(self):
        """Mock a DB qty > broker qty and verify close_spot uses broker qty."""
        print("\n[TEST 4] Inventory-Aware Close Test")
        
        # 1. Inject 11.0 XRP into DB
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO open_positions 
            (symbol, strategy, qty, paper, entry, entry_trade_id, ts_entry, direction) 
            VALUES ('XRP', 'spot_xrp', 11.0, 0, 1.0, 789, '2026-05-10T00:00:00', 'LONG')
        """)
        conn.commit()
        conn.close()
        
        # 2. Mock broker to say only 8.12 XRP available
        mock_bal = {"symbol_balances": {"XRP": 8.12}}
        mock_broker = MagicMock()
        mock_broker.get_spot_balance.return_value = mock_bal
        mock_broker.get_mark_price.return_value = 1.1
        
        # Mock _maker_first_sell to return successful order for 8.12
        with patch("spot_engine._get_db_path", return_value=self.db_path), \
             patch("spot_engine._load_spot_positions_from_db", return_value=[{"symbol": "XRP", "qty": 11.0, "entry": 1.0, "strategy": "spot_xrp", "entry_trade_id": 789}]), \
             patch("spot_engine._get_broker", return_value=mock_broker), \
             patch("spot_engine._maker_first_sell", return_value=({"order_id": "close_id", "filled_size": 8.12}, "maker_first", "none")):
            
            result = spot_engine.close_spot("XRP")
            
            # Verify result matches the broker qty
            self.assertIsNotNone(result, "close_spot returned None")
            self.assertEqual(result["qty"], 8.12)
            print("✅ Inventory-aware closure used broker qty (8.12) instead of DB qty (11.0).")

    def test_precision_rounding_test(self):
        """Verify strict floor rounding in CoinbaseSpotBroker."""
        print("\n[TEST 5] Precision Rounding Test (Authoritative floor)")
        
        from execution.coinbase_spot_broker import CoinbaseSpotBroker
        broker = CoinbaseSpotBroker()
        
        # Mock product spec with 1.0 increment (e.g. some obscure coin)
        with patch.object(broker, "_spec", return_value={"base_increment": 1.0, "base_precision": 0}):
            rounded = broker._round_base("XRP", 12.999)
            self.assertEqual(rounded, "12") # Floored to nearest increment
        
        # Mock XRP standard increment 0.000001
        with patch.object(broker, "_spec", return_value={"base_increment": 0.000001, "base_precision": 6}):
            rounded = broker._round_base("XRP", 12.01013157)
            self.assertEqual(rounded, "12.010131") # Floored to 6 decimals
        
        print("✅ Strict floor rounding verified for precision alignment.")

if __name__ == "__main__":
    unittest.main()
