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
ACCOUNT_SIZE: float = float(os.getenv('ACCOUNT_SIZE', '5000'))
MAX_DEPLOYED_PCT: float = 0.90
CASH_RESERVE_PCT: float = 0.10

# ════════════════════════════════════════════════════════════════════
# RISK — HARDCODED. NO AI CAN OVERRIDE THESE.
# ════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 0.01        # 1% of account per trade (was 2%, cut 50%)
MAX_DAILY_LOSS_PCT: float = 0.04            # 4% daily loss → halt all trading (was 8%, cut 50%)
MAX_POSITIONS_EQUITY: int = 3               # was 5, cut 50% → 3 (PDT min)
MAX_POSITIONS_CRYPTO: int = 5               # was 10, cut 50%
MAX_TRADES_PER_DAY_EQUITY: int = 3          # PDT cash account compliance (regulatory, not account-size)
MAX_TRADES_PER_DAY_CRYPTO: int = 100       # Effectively unlimited — regime gate controls quality
CRYPTO_MIN_PROFIT_FEE_MULTIPLE: float = 1.0     # Take-profit must clear 1.0x round-trip fees
EQUITY_STOP_LOSS_PCT: float = 0.025        # was 0.05, cut 50%
EQUITY_TAKE_PROFIT_PCT: float = 0.075      # was 0.15, cut 50% — maintains 3:1 R:R
EQUITY_RSI_OVERSOLD: int = 35              # kept for exit signals only (not entry gate)
EQUITY_RSI_OVERBOUGHT: int = 70            # kept for exit signals only (not entry gate)
CRYPTO_STOP_LOSS_PCT: float = 0.015        # was 0.03, cut 50%
CRYPTO_TAKE_PROFIT_PCT: float = 0.045      # was 0.09, cut 50% — maintains 3:1 R:R
CRYPTO_RSI_OVERSOLD: int = 35              # kept for exit signals only (not entry gate)
CRYPTO_RSI_OVERBOUGHT: int = 70            # kept for exit signals only (not entry gate)
CRYPTO_MIN_ADX: float = 15.0
# 3-variant MACD params — must match backtested values (crypto_macd.py docstring)
# Workhorse: MACD(3/15/3) — trades every signal, high frequency
# Classic:   MACD(4/16/3) — line vs signal crossover, slightly lower frequency
# Sniper:    MACD(6/20/5) — strong momentum only, highest win rate (63.7%)
CRYPTO_MACD1_FAST: int = 3
CRYPTO_MACD1_SLOW: int = 15
CRYPTO_MACD1_SIGNAL: int = 3
CRYPTO_MACD2_FAST: int = 4
CRYPTO_MACD2_SLOW: int = 16
CRYPTO_MACD2_SIGNAL: int = 3
CRYPTO_MACD3_FAST: int = 6
CRYPTO_MACD3_SLOW: int = 20
CRYPTO_MACD3_SIGNAL: int = 5
CRYPTO_MACD3_HISTOGRAM_THRESHOLD: float = 0.0
COINBASE_TAKER_FEE_PCT: float = 0.006
COINBASE_MAKER_FEE_PCT: float = 0.004
MAX_DAILY_FEE_DRAG_PCT: float = 0.100  # 10% = $50 on $500 / $500 on $5000 — raised from 5%
MARKET_BREADTH_MIN_SPY_PCT: float = -2.0      # Block equity longs if SPY down more than this
BACKTEST_SLIPPAGE_PCT: float = 0.002           # 0.2% per side slippage added to commission
MAX_STRATEGY_LOSS_STREAK: int = 4             # Circuit breaker: pause strategy after N consecutive losses (was 8, cut 50%)
EQUITY_MAX_HOLD_HOURS: float = 6.0            # Close equity position if flat after this many hours
CRYPTO_MAX_HOLD_HOURS: float = 12.0           # Close crypto position if flat after this many hours
FLAT_POSITION_THRESHOLD_PCT: float = 0.015    # Position is "flat" if P&L within ±1.5%
CRYPTO_MIN_HOLD_MINUTES: int = 3              # Min hold before strategy SELL fires (prevents same-candle $0.00 exits)

# ── ATR-based exit multipliers (Dennis Turtle / deep research) ───────────────
ATR_STOP_MULTIPLIER: float = 2.0            # Stop = 2×ATR below entry
ATR_TARGET_MULTIPLIER: float = 4.0          # Target = 4×ATR above entry (2:1 R:R)
ATR_FEE_FLOOR_PCT: float = 0.004            # Min ATR/price to clear 2.4% round-trip (skip debate if below)

# ── Advanced math signal thresholds (deep-research-backed) ──────────────────
SQUEEZE_MIN_BARS: int = 20                  # BB-Keltner squeeze must be on ≥20 bars before it fires
RV_EXPANSION_THRESHOLD: float = 1.3        # RV ratio ≥ 1.3 = short vol > long vol = expansion regime
RV_COMPRESSION_THRESHOLD: float = 0.8      # RV ratio ≤ 0.8 = compressed vol = mean-reversion preferred
OBI_ACTIONABLE_THRESHOLD: float = 0.20     # OBI ≥ 0.20 = actionable buy pressure
OBI_STRONG_THRESHOLD: float = 0.35         # OBI ≥ 0.35 = strong buy pressure
OU_HALFLIFE_MIN_MINUTES: float = 3.0       # Min OU half-life to be tradeable (shorter = noise)
OU_HALFLIFE_MAX_MINUTES: float = 60.0      # Max OU half-life (longer = too slow for 1-min bars)
KALMAN_ENTRY_DEV_PCT: float = -1.0         # Enter when price ≥1% below Kalman estimate
AVWAP_ENTRY_DEV_PCT: float = -0.5          # Enter when price ≥0.5% below AVWAP (reclaim setup)
KYLE_LAMBDA_LOW_PCT: float = 30.0          # Kyle lambda ≤ 30th pct = liquid market, good fills

# ════════════════════════════════════════════════════════════════════
# AI — ANTHROPIC
# ════════════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY: str = os.getenv('ANTHROPIC_API_KEY', '')
CLAUDE_MODEL: str = 'claude-sonnet-4-6'                      # Always latest
CLAUDE_MODEL_EXTENDED: str = 'claude-sonnet-4-6'             # For exit extended thinking
DEBATE_MAX_TOKENS: int = 700                                  # Raised from 300 — agents need room to reason deeply
EXIT_REVIEW_MAX_TOKENS: int = 1500                            # Raised from 800 — exit reasoning is the most critical decision
MODERATOR_MAX_TOKENS: int = 900                               # CIO synthesis
QUICK_DEBATE_AGENTS: list = ['microstructure', 'fee_discipline', 'flow_tape']
# Full debate: 5 focused agents covering the 5 critical dimensions for 1-min crypto
# Dropped: session_breakout (session_active flag handles it), williams (pre-filter handles it),
#          quant_edge (Kelly/OU now in risk_manager + indicators)
FULL_DEBATE_AGENTS: list = ['microstructure', 'fee_discipline', 'flow_tape',
                             'regime_volatility', 'manipulation_risk']
FULL_DEBATE_MIN_AGREEMENT: float = 0.40                      # 2 of 5 agents — explicit count enforced in risk_synthesizer

# Auto-tuning thresholds (AI switches debate depth based on account + win rate)
AUTO_TUNE_FULL_DEBATE_THRESHOLD: float = float(os.getenv('AUTO_TUNE_FULL_DEBATE_THRESHOLD', '1000.0'))  # Account > $1000 → always full debate
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
UPTIME_PING_URL: str = os.getenv('UPTIME_PING_URL', '')  # UptimeRobot heartbeat URL

EQUITY_ENABLED:  bool = os.getenv('EQUITY_ENABLED',  'true').lower()  == 'true'
CRYPTO_ENABLED:  bool = os.getenv('CRYPTO_ENABLED',  'true').lower()  == 'true'
FUTURES_ENABLED: bool = os.getenv('FUTURES_ENABLED', 'false').lower() == 'true'
PERP_ENABLED:    bool = os.getenv('PERP_ENABLED',    'false').lower() == 'true'

# ── Bybit perpetual futures ──────────────────────────────────────────────────
BYBIT_API_KEY:    str   = os.getenv('BYBIT_API_KEY', '')
BYBIT_API_SECRET: str   = os.getenv('BYBIT_API_SECRET', '')
BYBIT_TESTNET:    bool  = os.getenv('BYBIT_TESTNET', 'true').lower() == 'true'
PERP_PAIRS:       list  = os.getenv('PERP_PAIRS', 'AVAXUSDT,SOLUSDT,ETHUSDT,BTCUSDT').split(',')
PERP_POSITION_SIZE_USD:  float = float(os.getenv('PERP_POSITION_SIZE_USD', '50'))   # was 100, cut 50%
PERP_MAX_LEVERAGE:       int   = int(os.getenv('PERP_MAX_LEVERAGE', '10'))            # was 20, cut 50%
PERP_MAX_POSITIONS:      int   = int(os.getenv('PERP_MAX_POSITIONS', '2'))            # was 3, cut 50%
PERP_STOP_PCT:           float = float(os.getenv('PERP_STOP_PCT', '0.008'))           # was 0.015, cut 50%
PERP_TAKE_PROFIT_PCT:    float = float(os.getenv('PERP_TAKE_PROFIT_PCT', '0.016'))   # was 0.03, cut 50%, maintains 2:1
BYBIT_TAKER_FEE_PCT:     float = 0.00055   # 0.055% taker (linear perp default)
BYBIT_MAKER_FEE_PCT:     float = 0.00020   # 0.020% maker

# ── Mean-reversion strategy (ranging / volatile regimes) ─────────────────────
MEAN_REVERSION_ENABLED:   bool  = os.getenv('MEAN_REVERSION_ENABLED', 'true').lower() == 'true'
MEAN_REVERSION_RSI_ENTRY: float = float(os.getenv('MEAN_REVERSION_RSI_ENTRY', '33'))
MEAN_REVERSION_ADX_MAX:   float = float(os.getenv('MEAN_REVERSION_ADX_MAX', '22'))
TRADOVATE_USERNAME: str = os.getenv('TRADOVATE_USERNAME', '')
TRADOVATE_PASSWORD: str = os.getenv('TRADOVATE_PASSWORD', '')
TRADOVATE_APP_ID: str = os.getenv('TRADOVATE_APP_ID', '')
TRADOVATE_APP_VERSION: str = os.getenv('TRADOVATE_APP_VERSION', '1.0')
TRADOVATE_DEVICE_ID: str = os.getenv('TRADOVATE_DEVICE_ID', 'algo_bot_001')
FUTURES_CONTRACT: str = 'MES'
FUTURES_NUM_CONTRACTS: int = int(os.getenv('FUTURES_NUM_CONTRACTS', '2'))  # was 3, cut 50% → 2 MES = ~$40 risk/trade
FUTURES_DAILY_GOAL_PTS: float = 8.0    # 8pts × 2 contracts × $5 = $80/day target (was $180)
FUTURES_DAILY_MAX_LOSS_PTS: float = 5.0   # was 10pts, cut 50% — 5pts × 2 contracts × $5 = $50 max daily loss
FUTURES_MAX_TRADES_DAY: int = 10

# ════════════════════════════════════════════════════════════════════
# MARKET & INSTRUMENTS
# ════════════════════════════════════════════════════════════════════
CRYPTO_PAIRS: list = os.getenv('CRYPTO_PAIRS', 'BTC-USDC,ETH-USDC,SOL-USDC,AVAX-USDC,XRP-USDC,DOGE-USDC,LINK-USDC,ADA-USDC').split(',')
CRYPTO_CANDLE_GRANULARITY: str = 'ONE_MINUTE'
EQUITY_MIN_PRICE: float = 1.00
EQUITY_MAX_PRICE: float = 200.00
EQUITY_MIN_VOLUME: int = 500_000
EQUITY_MIN_DOLLAR_VOLUME: float = 1_000_000
EQUITY_VOLUME_SPIKE_MULTIPLIER: float = 1.5
EQUITY_POSITION_SIZE_USD: float = float(os.getenv('EQUITY_POSITION_SIZE_USD', '75'))
CRYPTO_POSITION_SIZE_USD: float = float(os.getenv('CRYPTO_POSITION_SIZE_USD', '100'))

MARKET_TIMEZONE: str = 'America/New_York'
MARKET_OPEN: dt_time = dt_time(9, 30)
MARKET_CLOSE: dt_time = dt_time(16, 0)
NO_TRADE_UNTIL: dt_time = dt_time(10, 0)

# ════════════════════════════════════════════════════════════════════
# SCHEDULER INTERVALS
# ════════════════════════════════════════════════════════════════════
EQUITY_SCAN_INTERVAL_SECONDS: int = 60
CRYPTO_SCAN_INTERVAL_SECONDS: int = 60
FUTURES_SCAN_INTERVAL_SECONDS: int = 60
POSITION_MONITOR_INTERVAL_SECONDS: int = 30
WATCHDOG_INTERVAL_SECONDS: int = 900       # Alert if no scan in 15 min

# ════════════════════════════════════════════════════════════════════
# TRADINGVIEW WEBHOOK INTEGRATION
# ════════════════════════════════════════════════════════════════════
TV_WEBHOOK_PORT:            int   = int(os.getenv('TV_WEBHOOK_PORT', '8765'))
TV_WEBHOOK_SECRET:          str   = os.getenv('TV_WEBHOOK_SECRET', '')
TV_SIGNAL_BOOST_CONVICTION: int   = int(os.getenv('TV_SIGNAL_BOOST_CONVICTION', '20'))
TV_SIGNAL_MAX_AGE_SECONDS:  int   = int(os.getenv('TV_SIGNAL_MAX_AGE_SECONDS', '300'))  # ignore TV signals older than 5 min

# ════════════════════════════════════════════════════════════════════
# ALERTS — written to SQLite system_events, displayed on dashboard
# (No email. Notifications panel in the dashboard shows everything.)
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
DB_PATH: str = 'logs/trades.db'
LANCEDB_PATH: str = 'logs/memory'
CSV_LOG_DIR: str = 'logs/csv'
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
