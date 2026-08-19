import json
import os
import pytest
from datetime import datetime, timezone
from forecast.weather_contracts import is_live_entry_weather_contract, weather_mode_for_ticker
from forecast.strategy_engine import min_contract_price_for_mode

def test_unregistered_tickers_blocked(proof_runtime):
    """Verify that unregistered non-weather or unregistered weather-like tickers are blocked."""
    # 1. Non-weather tickers
    assert is_live_entry_weather_contract("KXUFCFIGHT-26JUN14") is False
    assert is_live_entry_weather_contract("KXIMPEACHCABINET") is False
    assert is_live_entry_weather_contract("CONTROLH-2028") is False

    # 2. Weather-like ticker with unregistered city/station
    assert is_live_entry_weather_contract("KXRAINXYZ-26JUN22-1") is False

def test_lane_policy_enforcement(proof_runtime, monkeypatch):
    """Verify that lane_policy.json flags disable/enable correct contract modes."""
    # Write a custom policy to test
    policy_data = {
        "DAILY_HIGH": True,
        "DAILY_LOW": False,
        "HOURLY_TEMP": False,
        "RAIN": True,
        "SNOW": False,
        "WIND": False
    }
    
    # We write it to config/lane_policy.json (which monkeypatching file paths doesn't require since we edit real file or write to temp)
    # But wait! The code reads config/lane_policy.json relative to weather_contracts.py.
    # Let's write the custom policy to config/lane_policy.json and restore it after test.
    policy_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "lane_policy.json")
    
    # Backup original policy
    original_policy = None
    if os.path.exists(policy_path):
        with open(policy_path, "r") as f:
            original_policy = f.read()
            
    try:
        with open(policy_path, "w") as f:
            json.dump(policy_data, f)
            
        # DAILY_HIGH is True -> allowed
        assert is_live_entry_weather_contract("KXHIGHNY-20260708") is True
        
        # DAILY_LOW is False -> blocked
        assert is_live_entry_weather_contract("KXLOWNY-20260708") is False
        
        # HOURLY_TEMP is False -> blocked
        # Note: Hourly temp contains a timestamp and TEMP/HIGH/LOW
        assert is_live_entry_weather_contract("KXTEMPNYCH-24JAN0122-T75.99") is False
        
        # RAIN is True -> allowed
        assert is_live_entry_weather_contract("KXRAINNY-04JUN26-T1") is True
        
    finally:
        # Restore original policy
        if original_policy is not None:
            with open(policy_path, "w") as f:
                f.write(original_policy)

def test_snow_seasonal_schedule(proof_runtime, monkeypatch):
    """Verify that SNOW contracts are only allowed Nov 1 - Mar 31 if enabled."""
    policy_data = {
        "DAILY_HIGH": True,
        "DAILY_LOW": True,
        "SNOW": True,
    }
    policy_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "lane_policy.json")
    
    original_policy = None
    if os.path.exists(policy_path):
        with open(policy_path, "r") as f:
            original_policy = f.read()
            
    try:
        with open(policy_path, "w") as f:
            json.dump(policy_data, f)
            
        # Case A: Simulate July (Summer) -> Snow blocked
        # We monkeypatch datetime.now
        class MockDatetimeJuly:
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 15, tzinfo=timezone.utc)
        
        monkeypatch.setattr("forecast.weather_contracts.datetime", MockDatetimeJuly)
        assert is_live_entry_weather_contract("KXSNOWNY-20260715") is False
        
        # Case B: Simulate December (Winter) -> Snow allowed
        class MockDatetimeDec:
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 12, 15, tzinfo=timezone.utc)
                
        monkeypatch.setattr("forecast.weather_contracts.datetime", MockDatetimeDec)
        assert is_live_entry_weather_contract("KXSNOWNY-20261215") is True
        
    finally:
        if original_policy is not None:
            with open(policy_path, "w") as f:
                f.write(original_policy)

def test_hard_price_floor(proof_runtime):
    """Verify that strategy minimum contract price floor is $0.34."""
    # Min price floor should return KALSHI_MIN_ENTRY_PRICE (0.34) regardless of mode
    assert min_contract_price_for_mode("RAIN") == 0.34
    assert min_contract_price_for_mode("TEMP") == 0.34
    assert min_contract_price_for_mode("HIGH") == 0.34
