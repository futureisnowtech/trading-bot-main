"""
config.py — Single source of truth. All values from .env.
Never hardcode anything that belongs here.
"""
import os
from datetime import time as dt_time
from dotenv import load_dotenv

load_dotenv()

# ════════════════════════════════════════════════════════════════════
# SYSTEM MODE
# ════════════════════════════════════════════════════════════════════
PAPER_TRADING: bool = os.getenv('PAPER_TRADING', 'true').lower() == 'true'
LIVE_TRADING: bool = not PAPER_TRADING

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv('ACCOUNT_SIZE', '500'))
MAX_DEPLOYED_PCT: float = 0.60
CASH_RESERVE_PCT: float = 0.40

# ════════════════════════════════════════════════════════════════════
# RISK — HARDCODED. NO AI CAN OVERRIDE THESE.
# ════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 0.02        # 2% of account per trade
MAX_DAILY_LOSS_PCT: float = 0.05            # 5% daily loss → halt all trading
MAX_POSITIONS_EQUITY: int = 2
MAX_POSITIONS_CRYPTO: int = 2
MAX_TRADES_PER_DAY_EQUITY: int = 3          # PDT cash account compliance
MAX_TRADES_PER_DAY_CRYPTO: int = 10
EQUITY_STOP_LOSS_PCT: float = 0.05
EQUITY_TAKE_PROFIT_PCT: float = 0.10
EQUITY_RSI_OVERSOLD: int = 35
EQUITY_RSI_OVERBOUGHT: int = 70
CRYPTO_STOP_LOSS_PCT: float = 0.03
CRYPTO_TAKE_PROFIT_PCT: float = 0.06
CRYPTO_RSI_OVERSOLD: int = 35
CRYPTO_RSI_OVERBOUGHT: int = 70
CRYPTO_MIN_ADX: float = 15.0
# 3-variant MACD params
CRYPTO_MACD1_FAST: int = 12
CRYPTO_MACD1_SLOW: int = 26
CRYPTO_MACD1_SIGNAL: int = 9
CRYPTO_MACD2_FAST: int = 5
CRYPTO_MACD2_SLOW: int = 13
CRYPTO_MACD2_SIGNAL: int = 3
CRYPTO_MACD3_FAST: int = 8
CRYPTO_MACD3_SLOW: int = 21
CRYPTO_MACD3_SIGNAL: int = 5
CRYPTO_MACD3_HISTOGRAM_THRESHOLD: float = 0.0
COINBASE_TAKER_FEE_PCT: float = 0.006
COINBASE_MAKER_FEE_PCT: float = 0.004
MAX_DAILY_FEE_DRAG_PCT: float = 0.015

# ════════════════════════════════════════════════════════════════════
# AI — ANTHROPIC
# ════════════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY: str = os.getenv('ANTHROPIC_API_KEY', '')
CLAUDE_MODEL: str = 'claude-sonnet-4-6'                      # Always latest
CLAUDE_MODEL_EXTENDED: str = 'claude-sonnet-4-6'             # For exit extended thinking
DEBATE_MAX_TOKENS: int = 300
EXIT_REVIEW_MAX_TOKENS: int = 800                             # More for extended thinking
QUICK_DEBATE_AGENTS: list = ['tudor_jones', 'simons', 'livermore']
FULL_DEBATE_MIN_AGREEMENT: float = 0.625                     # 5 of 8 agents

# Auto-tuning thresholds (AI switches debate depth based on account + win rate)
AUTO_TUNE_FULL_DEBATE_THRESHOLD: float = 1000.0   # Account > $1000 → always full debate
AUTO_TUNE_WIN_RATE_THRESHOLD: float = 0.55         # Win rate > 55% → upgrade debate depth

# ════════════════════════════════════════════════════════════════════
# BROKERS
# ════════════════════════════════════════════════════════════════════
WEBULL_USERNAME: str = os.getenv('WEBULL_USERNAME', '')
WEBULL_PASSWORD: str = os.getenv('WEBULL_PASSWORD', '')
WEBULL_TRADE_PIN: str = os.getenv('WEBULL_TRADE_PIN', '')
WEBULL_MFA: str = os.getenv('WEBULL_MFA', '')
WEBULL_DEVICE_ID: str = os.getenv('WEBULL_DEVICE_ID', 'algo_bot_001')

COINBASE_API_KEY: str = os.getenv('COINBASE_API_KEY', '')
COINBASE_API_SECRET: str = os.getenv('COINBASE_API_SECRET', '')

FUTURES_ENABLED: bool = os.getenv('FUTURES_ENABLED', 'false').lower() == 'true'
TRADOVATE_USERNAME: str = os.getenv('TRADOVATE_USERNAME', '')
TRADOVATE_PASSWORD: str = os.getenv('TRADOVATE_PASSWORD', '')
TRADOVATE_APP_ID: str = os.getenv('TRADOVATE_APP_ID', '')
TRADOVATE_APP_VERSION: str = os.getenv('TRADOVATE_APP_VERSION', '1.0')
TRADOVATE_DEVICE_ID: str = os.getenv('TRADOVATE_DEVICE_ID', 'algo_bot_001')
FUTURES_CONTRACT: str = 'MES'
FUTURES_DAILY_GOAL_PTS: float = 6.0
FUTURES_DAILY_MAX_LOSS_PTS: float = 5.0
FUTURES_MAX_TRADES_DAY: int = 4

# ════════════════════════════════════════════════════════════════════
# MARKET & INSTRUMENTS
# ════════════════════════════════════════════════════════════════════
CRYPTO_PAIRS: list = os.getenv('CRYPTO_PAIRS', 'BTC-USDC,ETH-USDC').split(',')
CRYPTO_CANDLE_GRANULARITY: str = 'FIVE_MINUTE'
EQUITY_MIN_PRICE: float = 1.00
EQUITY_MAX_PRICE: float = 200.00
EQUITY_MIN_VOLUME: int = 500_000
EQUITY_MIN_DOLLAR_VOLUME: float = 1_000_000
EQUITY_VOLUME_SPIKE_MULTIPLIER: float = 1.5
EQUITY_POSITION_SIZE_USD: float = float(os.getenv('EQUITY_POSITION_SIZE_USD', '75'))
CRYPTO_POSITION_SIZE_USD: float = float(os.getenv('CRYPTO_POSITION_SIZE_USD', '50'))

MARKET_TIMEZONE: str = 'America/New_York'
MARKET_OPEN: dt_time = dt_time(9, 30)
MARKET_CLOSE: dt_time = dt_time(16, 0)
NO_TRADE_UNTIL: dt_time = dt_time(10, 0)

# ════════════════════════════════════════════════════════════════════
# SCHEDULER INTERVALS
# ════════════════════════════════════════════════════════════════════
EQUITY_SCAN_INTERVAL_SECONDS: int = 60
CRYPTO_SCAN_INTERVAL_SECONDS: int = 300
FUTURES_SCAN_INTERVAL_SECONDS: int = 60
POSITION_MONITOR_INTERVAL_SECONDS: int = 30
WATCHDOG_INTERVAL_SECONDS: int = 900       # Alert if no scan in 15 min

# ════════════════════════════════════════════════════════════════════
# ALERTS & TELEGRAM
# ════════════════════════════════════════════════════════════════════
EMAIL_FROM: str = os.getenv('EMAIL_FROM', 'futureisnowtech@gmail.com')
EMAIL_TO: str = os.getenv('EMAIL_TO', 'futureisnowtech@gmail.com')
EMAIL_APP_PASSWORD: str = os.getenv('EMAIL_APP_PASSWORD', '')

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
DB_PATH: str = 'logs/trades.db'
LANCEDB_PATH: str = 'logs/memory'
CSV_LOG_DIR: str = 'logs/csv'
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
