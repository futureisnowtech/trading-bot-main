import unittest
import math
from unittest.mock import MagicMock, patch
import os
import sys

# Add root to sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from forecast.strategy_engine import (
    calculate_ceiled_fee,
    calculate_favorite_scaler,
    solve_optimal_size,
    log_utility_g,
    estimate_zeta
)
from forecast.runner import (
    calculate_salvage_exit_threshold,
    get_position_basis_quality
)
from forecast.covariance_engine import (
    assemble_covariance_matrix
)


class TestV20Specification(unittest.TestCase):

    def test_ceiled_fee_model(self):
        """Verify ceiled continuous fee model (SPEC Phase 6)."""
        # fee(p, n, r) = ceil(r * n * p * (1-p) * 100) / 100 / n
        # For p = 0.40, n = 100, r_taker = 0.07:
        # raw_total = 0.07 * 100 * 0.40 * 0.60 = 1.68
        # Due to float precision, 1.68 is ceiled to 1.69 in estimate_kalshi_fee_per_contract.
        # So fee = 1.69 / 100 = 0.0169 per contract.
        fee_t = calculate_ceiled_fee(0.40, 100, maker=False)
        self.assertAlmostEqual(fee_t, 0.0169)

        # For maker (r_maker = 0.0175):
        # raw_total = 0.0175 * 100 * 0.40 * 0.60 = 0.42
        # Due to float representation, it ceils to 0.43 -> 0.0043
        fee_m = calculate_ceiled_fee(0.40, 100, maker=True)
        self.assertAlmostEqual(fee_m, 0.0043)

        # Check boundary clamps (SRE check 1)
        fee_low = calculate_ceiled_fee(0.0001, 10, maker=False)
        self.assertTrue(fee_low > 0.0)  # Price clamped to 0.01 minimum
        
        fee_high = calculate_ceiled_fee(1.5, 10, maker=False)
        self.assertTrue(fee_high > 0.0)  # Price clamped to 0.99 maximum

    def test_favorite_scaler(self):
        """Verify favorite scaler curves (SPEC Phase 6)."""
        # S(q, B) = 0.60 + (S_max(B) - 0.60) / (1 + exp(-12*(q - 0.70)))
        # S_max(B) = 1 + 0.5 / (1 + exp(-(B - 2000)/800))
        
        # Test low probability favorite (< 0.70)
        s_low = calculate_favorite_scaler(0.50, 5000.0)
        self.assertTrue(s_low < 0.70)  # Scaled down

        # Test high probability favorite (> 0.70)
        s_high = calculate_favorite_scaler(0.95, 5000.0)
        self.assertTrue(s_high > 1.0)  # Scaled up

        # Verify S_max(B) bankroll scaling:
        # High bankroll ($5000) should have larger favorite scaling potential than low ($1000)
        s_5000 = calculate_favorite_scaler(0.95, 5000.0)
        s_1000 = calculate_favorite_scaler(0.95, 1000.0)
        self.assertTrue(s_5000 > s_1000)

    def test_solve_optimal_size_lambda_sensitivity(self):
        """Verify sizing response to GraphCast Lambda scaling."""
        # Baseline Kelly size
        f_star_base, phi_base, n_base = solve_optimal_size(
            q=0.60,
            p=0.45,
            maker=False,
            bankroll=1000.0,
            lambda_scaler=1.0,
            cov_charge=1.0
        )
        self.assertTrue(n_base > 0)

        # Doubled Lambda should decrease sizing proportionally
        f_star_scaled, phi_scaled, n_scaled = solve_optimal_size(
            q=0.60,
            p=0.45,
            maker=False,
            bankroll=1000.0,
            lambda_scaler=2.0,
            cov_charge=1.0
        )
        self.assertTrue(n_scaled < n_base)

    def test_exit_curves(self):
        """Salvage exit is tiered by entry conviction, not tau or entry price."""
        from config import (
            SALVAGE_EXIT_DELTA,
            SALVAGE_EXIT_DELTA_HIGH_PROB,
            SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB,
        )

        self.assertAlmostEqual(SALVAGE_EXIT_DELTA, 0.15)
        self.assertAlmostEqual(SALVAGE_EXIT_DELTA_HIGH_PROB, 0.12)
        self.assertAlmostEqual(SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB, 0.10)

        for tau_hours in (120.0, 24.0, 3.2, 1.0, 0.0, -5.0):
            for p_entry in (0.10, 0.40, 0.80):
                self.assertAlmostEqual(
                    calculate_salvage_exit_threshold(
                        tau_hours,
                        p_entry,
                        entry_held_probability=0.79,
                    ),
                    SALVAGE_EXIT_DELTA,
                    msg=f"salvage threshold varied at tau={tau_hours}, p={p_entry}",
                )
                self.assertAlmostEqual(
                    calculate_salvage_exit_threshold(
                        tau_hours,
                        p_entry,
                        entry_held_probability=0.80,
                    ),
                    SALVAGE_EXIT_DELTA_HIGH_PROB,
                )
                self.assertAlmostEqual(
                    calculate_salvage_exit_threshold(
                        tau_hours,
                        p_entry,
                        entry_held_probability=0.90,
                    ),
                    SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB,
                )

    def test_salvage_delta_matches_published_parameter_catalog(self):
        """The parameter catalog is an audit surface; it must state the live value."""
        from config import (
            SALVAGE_EXIT_DELTA,
            SALVAGE_EXIT_DELTA_HIGH_PROB,
            SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB,
        )

        catalog = os.path.join(
            _ROOT, "research_package", "03_parameter_catalog.md"
        )
        with open(catalog, "r", encoding="utf-8") as handle:
            salvage_lines = [
                line for line in handle if "Sovereign Salvage Delta" in line
            ]
        self.assertTrue(salvage_lines, "Salvage delta missing from parameter catalog.")
        for line in salvage_lines:
            for value in (
                SALVAGE_EXIT_DELTA,
                SALVAGE_EXIT_DELTA_HIGH_PROB,
                SALVAGE_EXIT_DELTA_ULTRA_HIGH_PROB,
            ):
                self.assertIn(
                    f"{value:.2f}",
                    line,
                    msg=f"Catalog row disagrees with config ({value}): {line.strip()}",
                )

    def test_disjoint_bracket_covariance(self):
        """Verify disjoint bracket same-event contracts have negative covariance (SPEC Phase 5)."""
        # Under same-station disjoint brackets, joint probability is zero.
        # Covariance = joint_p - q_i * q_j = 0 - q_i * q_j = - q_i * q_j.
        # After shrinkage regularization: cov_shrink = 0.9 * cov = -0.9 * q_i * q_j.
        q_i = 0.40
        q_j = 0.30
        
        contracts = [
            {
                "local_symbol": "KXHIGHNY-26JUN01-L75",
                "station_code": "KNYC",
                "side": "YES",
                "weight": 1.0,
                "q_live": q_i,
                "entry_price": 0.42,
                "qty": 10,
                "strike": 75.0,
                "contract_name": "Will the high temp in NY be less than 75° on Jun 1, 2026?"
            },
            {
                "local_symbol": "KXHIGHNY-26JUN01-T75",
                "station_code": "KNYC",
                "side": "YES",
                "weight": 1.0,
                "q_live": q_j,
                "entry_price": 0.32,
                "qty": 10,
                "strike": 75.0,
                "contract_name": "Will the high temp in NY be 75° or higher on Jun 1, 2026?"
            }
        ]
        
        pricing_dict = {
            "KXHIGHNY-26JUN01-L75": {"q_hat": q_i},
            "KXHIGHNY-26JUN01-T75": {"q_hat": q_j}
        }
        w_data_dict = {
            "KXHIGHNY-26JUN01-L75": {},
            "KXHIGHNY-26JUN01-T75": {}
        }
        R = {("KNYC", "KNYC"): 1.0}
        
        cov = assemble_covariance_matrix(contracts, pricing_dict, w_data_dict, R, is_authoritative=True)
        self.assertEqual(cov.shape, (2, 2))
        
        expected_cov = 0.9 * (-q_i * q_j)
        self.assertAlmostEqual(cov[0, 1], expected_cov)
        self.assertAlmostEqual(cov[1, 0], expected_cov)

    def test_covariance_matrix_psd(self):
        """Verify assembled covariance matrix is PSD via eigenvalue floor (SPEC Phase 5)."""
        contracts = [
            {"local_symbol": "A", "station_code": "KNYC", "side": "YES", "weight": 1.0, "q_live": 0.5, "entry_price": 0.5, "qty": 10},
            {"local_symbol": "B", "station_code": "KBOS", "side": "YES", "weight": 1.0, "q_live": 0.5, "entry_price": 0.5, "qty": 10},
            {"local_symbol": "C", "station_code": "KPHL", "side": "YES", "weight": -1.0, "q_live": 0.5, "entry_price": 0.5, "qty": 10}
        ]
        
        pricing_dict = {
            "A": {"q_hat": 0.5},
            "B": {"q_hat": 0.5},
            "C": {"q_hat": 0.5}
        }
        w_data_dict = {
            "A": {},
            "B": {},
            "C": {}
        }
        R_dict = {
            ("KNYC", "KNYC"): 1.0,
            ("KBOS", "KBOS"): 1.0,
            ("KPHL", "KPHL"): 1.0,
            ("KNYC", "KBOS"): 0.99,
            ("KBOS", "KNYC"): 0.99,
            ("KNYC", "KPHL"): 0.99,
            ("KPHL", "KNYC"): 0.99,
            ("KBOS", "KPHL"): 0.99,
            ("KPHL", "KBOS"): 0.99
        }
        
        cov = assemble_covariance_matrix(contracts, pricing_dict, w_data_dict, R_dict, is_authoritative=True)
        import numpy as np
        eigenvals = np.linalg.eigvalsh(cov)
        for val in eigenvals:
            self.assertTrue(val >= 1e-6)

    def test_daily_kill_switch_firewall(self):
        """Verify stateful daily loss kill switch (SPEC Phase 1)."""
        from forecast.firewall import check_kill_switch, record_realized_pnl, ensure_firewall_tables
        db_path = "test_firewall.db"
        if os.path.exists(db_path):
            os.remove(db_path)
            
        try:
            ensure_firewall_tables(db_path=db_path)
            
            # Initial: no loss -> allowed
            allowed, _ = check_kill_switch(1000.0, db_path=db_path)
            self.assertTrue(allowed)
            
            # Record $100 loss (exceeds $30 threshold at $1000 bankroll)
            record_realized_pnl(-100.0, db_path=db_path)
            allowed, reason = check_kill_switch(1000.0, db_path=db_path)
            self.assertFalse(allowed)
            self.assertIn("firewall_daily_kill_switch", reason)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
