"""
config.py — Single source of truth. All values from .env.
Never hardcode anything that belongs here.
"""

import os
from datetime import time as dt_time

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(dotenv_path: str | None = None) -> bool:
        """Minimal .env loader fallback for audit scripts on hosts without python-dotenv."""
        path = dotenv_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".env"
        )
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                os.environ.setdefault(key, value)
        return True


load_dotenv()

# v19.1.12: Canonical Repository Root
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = _ROOT_DIR

# ════════════════════════════════════════════════════════════════════
# SYSTEM MODE
# ════════════════════════════════════════════════════════════════════
# v18.32: Ripped out paper trading and scalper mode switches.
# All systems are strictly LIVE.
SHADOW_EXECUTION: bool = os.getenv("SHADOW_EXECUTION", "false").lower() == "true"

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward.
TRADE_SESSION_START: str = os.getenv("TRADE_SESSION_START", "2026-03-28")

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv("ACCOUNT_SIZE", "5000"))
MAX_DEPLOYED_PCT: float = 1.0
CASH_RESERVE_PCT: float = 0.0

# ════════════════════════════════════════════════════════════════════
# AI & INTELLIGENCE
# ════════════════════════════════════════════════════════════════════
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# AI Exit Settings
PM_LLM_TEMPERATURE: float = float(os.getenv("PM_LLM_TEMPERATURE", "0.3"))
PM_LLM_MAX_TOKENS: int = int(os.getenv("PM_LLM_MAX_TOKENS", "600"))
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL_EXTENDED: str = "gemini-3.5-pro"  # Frontier Reasoning

# ════════════════════════════════════════════════════════════════════
# KALSHI (Weather Prediction Engine)
# ════════════════════════════════════════════════════════════════════
KALSHI_API_KEY: str = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET: str = os.getenv("KALSHI_API_SECRET", "")
KALSHI_ENABLED: bool = os.getenv("KALSHI_ENABLED", "true").lower() == "true"
FORECAST_LANE_ACTIVE: bool = (
    os.getenv("FORECAST_LANE_ACTIVE", "true").lower() == "true"
)
FORECAST_DASHBOARD_VISIBLE: bool = (
    os.getenv("FORECAST_DASHBOARD_VISIBLE", "true").lower() == "true"
)
FORECAST_AUTONOMOUS_ENABLED: bool = (
    os.getenv("FORECAST_AUTONOMOUS_ENABLED", "false").lower() == "true"
)
FORECAST_MANUAL_ENABLED: bool = (
    os.getenv("FORECAST_MANUAL_ENABLED", "true").lower() == "true"
)

# Kalshi Risk & Capital Partitioning
KALSHI_MAX_DEPLOYED_PCT: float = 0.90
KALSHI_MAX_CONCURRENT_POSITIONS: int = 15
KALSHI_SAME_EVENT_FAMILY_CAP: int = int(os.getenv("KALSHI_SAME_EVENT_FAMILY_CAP", "2"))
KALSHI_MAX_QTY_PER_POSITION: int = 200
KALSHI_MAX_USD_PER_POSITION: float = 10.0
KALSHI_MIN_PRICE: float = 0.15
KALSHI_MAX_SIGMA: float = 3.0
KALSHI_MAX_SPREAD_RATIO: float = 0.20
KALSHI_DATA_FRESHNESS_MINUTES: int = 180
KALSHI_FEE_PER_CONTRACT: float = 0.07
KALSHI_MAX_FEE_DRAG_PCT: float = 0.30
KALSHI_FEE_BUFFER: float = 0.05
KALSHI_KELLY_CAP: float = 0.10
KALSHI_MAX_RISK_PER_EVENT_PCT: float = 0.015

# ════════════════════════════════════════════════════════════════════
# TELEGRAM (Mobile HUD)
# ════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_POLLING_HOSTNAME: str = os.getenv("TELEGRAM_POLLING_HOSTNAME", "kalshi-weather-bot")

# ════════════════════════════════════════════════════════════════════
# MONITORING & INCIDENT MANAGEMENT
# ════════════════════════════════════════════════════════════════════
GRAFANA_INCIDENT_ENABLED: bool = os.getenv("GRAFANA_INCIDENT_ENABLED", "false").lower() == "true"
GRAFANA_URL: str = os.getenv("GRAFANA_URL", "").strip()
GRAFANA_TOKEN: str = os.getenv("GRAFANA_TOKEN", "").strip()
GRAFANA_SERVICE_ACCOUNT_ID: str = os.getenv("GRAFANA_SERVICE_ACCOUNT_ID", "").strip()
UPTIME_PING_URL: str = os.getenv("UPTIME_PING_URL", "")

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
DB_USE_POSTGRES: bool = os.getenv("DB_USE_POSTGRES", "false").lower() == "true"
DB_PATH: str = os.path.join(_ROOT_DIR, "logs", "trades.db")
CSV_LOG_DIR: str = os.path.join(_ROOT_DIR, "logs", "csv")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
MARKET_TIMEZONE: str = "America/New_York"
