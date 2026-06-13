import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Add root to sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from forecast.strategy_engine import _strategy_weather, _parse_weather_threshold
from forecast.weather_contracts import resolve_weather_contract, yes_probability_from_weather_data
from data.kalshi_weather_monitor import _parse_t_group

class TestWeatherSovereign(unittest.TestCase):
    
    def test_parse_t_group(self):
        """Verify high-precision METAR T-group parsing."""
        # T02500150 -> +25.0C temp, +15.0C dew
        metar = "KNYC 011551Z 24006KT 10SM CLR 25/15 A2992 RMK AO2 T02500150"
        temp_f = _parse_t_group(metar)
        # 25C * 9/5 + 32 = 77F
        self.assertEqual(temp_f, 77.0)
        
        # T10501050 -> -5.0C temp, -5.0C dew
        metar_neg = "KDEN 011551Z 24006KT 10SM CLR -05/-05 A2992 RMK AO2 T10501050"
        temp_f_neg = _parse_t_group(metar_neg)
        # -5C * 9/5 + 32 = 23F
        self.assertEqual(temp_f_neg, 23.0)

    @patch('forecast.strategy_engine.get_contract_weather_data')
    def test_ecmwf_convergence(self, mock_get_weather):
        """Verify GFS + ECMWF convergence (1.5x multiplier)."""
        ticker = "KXHIGHNY-26JUN01-T75"
        # 31 GFS members, 31 EC members for simplicity in mock
        # Success if >= 75
        mock_get_weather.return_value = {
            "members_high": [76] * 31, # 100% GFS prob inside >75
            "ecmwf": {
                "members_high": [76] * 31 # 100% EC prob inside >75
            },
            "peak_tcdc": 10.0
        }

        passes, side, conf, factors, is_taker = _strategy_weather(
            ticker,
            0.35,
            0.65,
            24.0,
            contract_name="Will the high temp in NY be 75° or higher on Jun 1, 2026?",
            strike=75.0,
        )
        
        self.assertTrue(passes)
        self.assertEqual(side, "YES")
        # confidence = 0.97 * 1.5 = 1.455
        self.assertAlmostEqual(conf, 1.455)
        self.assertIn("conv_mult=1.5x", factors)

    @patch('forecast.strategy_engine.get_contract_weather_data')
    def test_ecmwf_divergence_is_softened_not_vetoed(self, mock_get_weather):
        """Verify model divergence now sizes down instead of hard-vetoing."""
        ticker = "KXHIGHNY-26JUN01-T75"
        mock_get_weather.return_value = {
            "members_high": [76] * 31, # 100% GFS prob inside >75
            "ecmwf": {
                "members_high": [70] * 31 # 0% EC prob
            },
            "peak_tcdc": 10.0
        }

        passes, side, conf, factors, is_taker = _strategy_weather(
            ticker,
            0.35,
            0.65,
            24.0,
            contract_name="Will the high temp in NY be 75° or higher on Jun 1, 2026?",
            strike=75.0,
        )
        
        self.assertTrue(passes)
        self.assertEqual(side, "YES")
        self.assertTrue(any(str(f).startswith("div_gap=") for f in factors))

    def test_bracket_contract_semantics_count_only_inside_bin(self):
        semantics = resolve_weather_contract(
            "KXHIGHLAX-26JUN05-B69.5",
            contract_name="Will the high temp in LA be 69-70° on Jun 5, 2026?",
            strike=69.5,
        )
        self.assertIsNotNone(semantics)
        self.assertEqual(semantics.comparator, "between")
        self.assertAlmostEqual(semantics.lower_bound, 68.5)
        self.assertAlmostEqual(semantics.upper_bound, 70.5)

        prob = yes_probability_from_weather_data(
            "KXHIGHLAX-26JUN05-B69.5",
            {
                "members_high": [68.4, 68.9, 69.2, 70.4, 70.6],
            },
            contract_name="Will the high temp in LA be 69-70° on Jun 5, 2026?",
            strike=69.5,
        )
        self.assertAlmostEqual(prob, 3 / 5)

    def test_temperature_edge_contract_is_ambiguous_without_contract_title(self):
        semantics = resolve_weather_contract(
            "KXHIGHLAX-26JUN05-T76",
            contract_name="",
            strike=76.0,
        )
        self.assertIsNotNone(semantics)
        self.assertTrue(semantics.ambiguous)

    def test_title_semantics_override_t_suffix_for_lower_tail_weather_contracts(self):
        semantics = resolve_weather_contract(
            "KXHIGHMIA-26JUN06-T83",
            contract_name="Will the **high temp in Miami** be <83° on Jun 6, 2026?",
            strike=83.0,
        )
        self.assertIsNotNone(semantics)
        self.assertEqual(semantics.comparator, "lt")
        self.assertAlmostEqual(semantics.threshold, 82.5)

        prob = yes_probability_from_weather_data(
            "KXHIGHMIA-26JUN06-T83",
            {
                "members_high": [82.4, 82.5, 82.6, 83.0],
            },
            contract_name="Will the **high temp in Miami** be <83° on Jun 6, 2026?",
            strike=83.0,
        )
        self.assertAlmostEqual(prob, 0.5)

    def test_rain_thresholds_do_not_use_temperature_half_degree_offsets(self):
        semantics = resolve_weather_contract(
            "KXRAINNY-04JUN26-T1",
            contract_name="Will rainfall in NY be >1 inch on Jun 4, 2026?",
            strike=1.0,
        )
        self.assertIsNotNone(semantics)
        self.assertEqual(semantics.threshold, 1.0)

        prob = yes_probability_from_weather_data(
            "KXRAINNY-04JUN26-T1",
            {
                "members_precip": [0.80, 1.01, 1.50],
            },
            contract_name="Will rainfall in NY be >1 inch on Jun 4, 2026?",
            strike=1.0,
        )
        self.assertAlmostEqual(prob, 2 / 3)

if __name__ == "__main__":
    unittest.main()
