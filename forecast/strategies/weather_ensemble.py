import logging
from datetime import datetime, timezone
from data.external_apis.open_meteo_client import OpenMeteoClient
from forecast.primitives import compute_ev, fractional_kelly_fraction

logger = logging.getLogger(__name__)

def evaluate_weather_market(contract, yes_quote, no_quote):
    """
    Evaluates a weather contract using ensemble data.
    """
    local_symbol = contract.get("local_symbol", "").upper()

    # ADVERSARY FIX #4: Dynamic City Geocoding and Threshold Parsing
    CITY_COORDS = {
        "NYC": (40.7128, -74.0060), "CHI": (41.8781, -87.6298), "MIA": (25.7617, -80.1918),
        "LAX": (34.0522, -118.2437), "BOS": (42.3601, -71.0589), "HOU": (29.7604, -95.3698),
        "ATL": (33.7490, -84.3880), "DAL": (32.7767, -96.7970), "PHI": (39.9526, -75.1652),
        "DC": (38.9072, -77.0369), "AUS": (30.2672, -97.7431)
    }

    lat, lon, threshold = None, None, None
    for city, coords in CITY_COORDS.items():
        if city in local_symbol:
            lat, lon = coords
            break
            
    if "TEMP-" in local_symbol:
        try:
            # Example: TEMP-NYC-90-2026-05-20. We need the 90.
            parts = local_symbol.split("-")
            for part in parts:
                if part.isdigit() and len(part) <= 3: # Assuming threshold is 1-3 digits
                    threshold = float(part)
                    break
        except ValueError:
            logger.debug(f"Weather Strategy: Could not parse temperature threshold from {local_symbol}")

    if not lat or not lon or threshold is None:
        logger.debug(f"Weather Strategy: Could not auto-detect city/threshold for {local_symbol}")
        return None

    client = OpenMeteoClient()
    ensemble = client.get_temperature_ensemble(lat, lon)
    
    if ensemble is None:
        return None
        
    q_hat = client.calculate_probability(ensemble, datetime.now(), threshold, above=True)
    
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
