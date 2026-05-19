import requests
import pandas as pd
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

class OpenMeteoClient:
    """
    Client for Open-Meteo Ensemble API.
    Fetches 31-member GFS ensemble forecasts for temperature and precipitation.
    """
    BASE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

    def __init__(self):
        pass

    def get_temperature_ensemble(self, lat: float, lon: float, days: int = 3):
        """
        Fetches temperature ensemble (2m) for 31 members.
        Returns a DataFrame with members as columns.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "models": "gfs_seamless",
            "forecast_days": days,
            "timezone": "UTC"
        }
        
        try:
            resp = requests.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            
            # Open-Meteo ensemble returns members as temperature_2m_member00, member01, etc.
            df = pd.DataFrame({"time": times})
            for key, values in hourly.items():
                if key.startswith("temperature_2m_member"):
                    df[key.replace("temperature_2m_", "")] = values
            
            df["time"] = pd.to_datetime(df["time"])
            return df
        except Exception as e:
            logger.error(f"OpenMeteo Error: {e}")
            return None

    def calculate_probability(self, ensemble_df, target_time, threshold, above=True):
        """
        Calculates probability of temperature being above/below threshold at a specific time.
        """
        if ensemble_df is None or ensemble_df.empty:
            return None
            
        # Find closest time row
        target_time = pd.to_datetime(target_time).replace(tzinfo=None)
        row = ensemble_df.iloc[(ensemble_df['time'] - target_time).abs().argsort()[:1]]
        
        if row.empty:
            return None
            
        member_cols = [c for c in ensemble_df.columns if c.startswith("member")]
        member_values = row[member_cols].values[0]
        
        if above:
            count = sum(1 for v in member_values if v >= threshold)
        else:
            count = sum(1 for v in member_values if v <= threshold)
            
        return count / len(member_values)
