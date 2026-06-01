import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Add root to sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from forecast.strategy_engine import _strategy_weather, _parse_weather_threshold
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

    @patch('forecast.strategy_engine.get_weather_data')
    def test_ecmwf_convergence(self, mock_get_weather):
        """Verify GFS + ECMWF convergence (1.5x multiplier)."""
        ticker = "KXHIGHNY-26JUN01-B75.5"
        # 31 GFS members, 31 EC members for simplicity in mock
        # Success if >= 75.5
        mock_get_weather.return_value = {
            "members_high": [76] * 31, # 100% GFS prob
            "ecmwf": {
                "members_high": [77] * 31 # 100% EC prob
            },
            "peak_tcdc": 10.0
        }
        
        passes, side, conf, factors, is_taker = _strategy_weather(ticker, 0.50, 0.50, 24.0)
        
        self.assertTrue(passes)
        self.assertEqual(side, "YES")
        # ensemble_prob is capped at 0.97
        # confidence = 0.97 * 1.5 = 1.455
        self.assertAlmostEqual(conf, 1.455)
        self.assertIn("conv_mult=1.5x", factors)

    @patch('forecast.strategy_engine.get_weather_data')
    def test_ecmwf_divergence_veto(self, mock_get_weather):
        """Verify GFS + ECMWF divergence veto (gap > 40%)."""
        ticker = "KXHIGHNY-26JUN01-B75.5"
        mock_get_weather.return_value = {
            "members_high": [80] * 31, # 100% GFS prob
            "ecmwf": {
                "members_high": [70] * 31 # 0% EC prob
            },
            "peak_tcdc": 10.0
        }
        
        passes, side, conf, factors, is_taker = _strategy_weather(ticker, 0.50, 0.50, 24.0)
        
        self.assertFalse(passes)
        self.assertIn("model_divergence_veto", factors)

if __name__ == "__main__":
    unittest.main()
