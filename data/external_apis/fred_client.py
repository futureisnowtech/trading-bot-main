import requests
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class FredClient:
    """
    Client for St. Louis Fed (FRED) API.
    Fetches macroeconomic indicators for nowcasting.
    """
    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FRED_API_KEY")

    def get_series_latest(self, series_id: str):
        """
        Fetches the latest value for a given series ID (e.g., 'CPIAUCSNS' for CPI).
        """
        if not self.api_key:
            logger.error("FRED API Key missing.")
            return None

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        
        try:
            resp = requests.get(f"{self.BASE_URL}/series/observations", params=params)
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            if observations:
                return {
                    "date": observations[0]["date"],
                    "value": float(observations[0]["value"])
                }
            return None
        except Exception as e:
            logger.error(f"FRED Error fetching {series_id}: {e}")
            return None
