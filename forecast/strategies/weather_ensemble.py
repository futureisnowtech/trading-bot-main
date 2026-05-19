import logging
from datetime import datetime, timezone
from data.external_apis.open_meteo_client import OpenMeteoClient
from forecast.primitives import compute_ev, fractional_kelly_fraction

logger = logging.getLogger(__name__)

def evaluate_weather_market(contract, yes_quote, no_quote):
    """
    Evaluates a weather contract using ensemble data.
    """
    local_symbol = contract.get("local_symbol", "")
    # Placeholder: Extract lat/lon/threshold/time from local_symbol or contract info
    # e.g., "KX_TEMP_NYC_90_2026-05-20"
    # This is a simplification for the plan.
    
    # NYC Coordinates
    lat, lon = 40.71, -74.00
    
    client = OpenMeteoClient()
    ensemble = client.get_temperature_ensemble(lat, lon)
    
    if ensemble is None:
        return None
        
    # Example: probability of > 85F
    q_hat = client.calculate_probability(ensemble, datetime.now(), 85.0, above=True)
    
    if q_hat is None:
        return None
        
    ask_yes = float(yes_quote.get("ask") or 1.0)
    ask_no = float(no_quote.get("ask") or 1.0)
    
    # Calculate EV
    ev_yes = compute_ev(q_hat, ask_yes)
    ev_no = compute_ev(1.0 - q_hat, ask_no)
    
    if ev_yes > 0.05:
        side = "YES"
        ev = ev_yes
        prob = q_hat
        price = ask_yes
    elif ev_no > 0.05:
        side = "NO"
        ev = ev_no
        prob = 1.0 - q_hat
        price = ask_no
    else:
        return None
        
    # Simple sizing
    fraction = fractional_kelly_fraction(prob, price, fraction=0.10)
    
    return {
        "strategy": "weather_ensemble",
        "side": side,
        "q_hat": q_hat,
        "ev": ev,
        "confidence": 0.8, # Ensembles are high confidence
        "position_fraction": fraction
    }
