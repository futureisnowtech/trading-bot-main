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

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward. Old trades are kept in DB for ML training
# but excluded from dashboard metrics and Kelly/sizing decisions.
# Reset this when making significant system changes so stale data doesn't
# pollute current performance signals.
TRADE_SESSION_START: str = os.getenv('TRADE_SESSION_START', '2026-03-28')

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv('ACCOUNT_SIZE', '5000'))
MAX_DEPLOYED_PCT: float = 0.90
CASH_RESERVE_PCT: float = 0.10

# ════════════════════════════════════════════════════════════════════
# RISK — HARDCODED. NO AI CAN OVERRIDE THESE.
# Paper mode uses looser limits to maximise learning velocity.
# Live mode uses tight limits to protect real capital.
# ════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 0.01        # 1% of account per trade

# Daily loss halt: paper = 20% (don't halt learning), live = 4%
MAX_DAILY_LOSS_PCT: float = 0.20 if PAPER_TRADING else 0.04

# Max open positions: paper = wide open for learning, live = conservative
MAX_POSITIONS_EQUITY: int = 10 if PAPER_TRADING else 3
MAX_POSITIONS_CRYPTO: int = 20 if PAPER_TRADING else 5  # all 20 pairs can run simultaneously

# Daily trade caps: paper = uncapped, live = PDT compliance
MAX_TRADES_PER_DAY_EQUITY: int = 999 if PAPER_TRADING else 3
MAX_TRADES_PER_DAY_CRYPTO: int = 999       # Effectively unlimited in both modes

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
MAX_DAILY_FEE_DRAG_PCT: float = 0.50 if PAPER_TRADING else 0.10  # paper: fees never halt learning; live: 10% cap
MARKET_BREADTH_MIN_SPY_PCT: float = -2.0      # Block equity longs if SPY down more than this
BACKTEST_SLIPPAGE_PCT: float = float(os.getenv('BACKTEST_SLIPPAGE_PCT', '0.001'))  # 0.1% per leg slippage added to commission
MAX_STRATEGY_LOSS_STREAK: int = 99 if PAPER_TRADING else 4  # paper: never pause on streak; live: 4-loss circuit breaker
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
CRYPTOPANIC_API_KEY: str = os.getenv('CRYPTOPANIC_API_KEY', '')    # Free tier at cryptopanic.com/developers/api/

# Reddit sentiment (PRAW — optional; graceful fallback if missing)
# Get free credentials: reddit.com/prefs/apps → create app (script type)
REDDIT_CLIENT_ID:     str = os.getenv('REDDIT_CLIENT_ID',     '')
REDDIT_CLIENT_SECRET: str = os.getenv('REDDIT_CLIENT_SECRET', '')
REDDIT_USER_AGENT:    str = os.getenv('REDDIT_USER_AGENT',    'AlgoTradingBot/1.0')
CLAUDE_MODEL: str = 'claude-sonnet-4-6'                      # Always latest
CLAUDE_MODEL_EXTENDED: str = 'claude-sonnet-4-6'             # For exit extended thinking
CLAUDE_DEBATE_MODEL: str = os.getenv('CLAUDE_DEBATE_MODEL', 'claude-haiku-4-5-20251001')  # Debate agents (cheap)
DEBATE_MAX_TOKENS: int = 700                                  # Raised from 300 — agents need room to reason deeply
EXIT_REVIEW_MAX_TOKENS: int = 1500                            # Raised from 800 — exit reasoning is the most critical decision
MODERATOR_MAX_TOKENS: int = 900                               # CIO synthesis
# 3-agent debate — same agents for quick and full (no distinction needed)
# funding_regime: macro + funding rate (crypto-native edge)
# momentum_structure: ADX + squeeze + WAE + WaveTrend + MACD
# risk_economics: fee math + ATR + volume + time-of-day gate
QUICK_DEBATE_AGENTS: list = ['funding_regime', 'momentum_structure', 'risk_economics']
FULL_DEBATE_AGENTS:  list = ['funding_regime', 'momentum_structure', 'risk_economics']
MES_DEBATE_AGENTS:   list = ['mes_momentum_risk', 'mes_quant', 'mes_market_structure']
FULL_DEBATE_MIN_AGREEMENT: float = 0.20 if PAPER_TRADING else 0.60   # paper: any 1/3 agent BUY = BUY

# ML signal gate — skip debate if P(win) below threshold
# Calibrated to seeded data baseline (~9% WR from math-only backtest).
# Once live trades accumulate (AI-filtered ~30-50% WR), raise this to 0.35+
# In .env: set ML_SIGNAL_MIN_PROB=0.35 after 50+ real trades
ML_SIGNAL_MIN_PROB: float = float(os.getenv('ML_SIGNAL_MIN_PROB', '0.08'))

# Funding rate signal thresholds (Coinglass per-8h %)
FUNDING_OVERHEATED_PCT:  float = 0.0005  # > this = longs overloaded (Binance decimal: 0.0001 = 0.01%/8h; 0.05% = overheated)
FUNDING_FAVORABLE_PCT:   float = 0.0001  # < this = low crowding, good long entry window

# Goku removed — was too expensive, now just a compat constant
GOKU_ENABLED: bool = False

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

# ── Binance USD-M perpetual futures (replaced Bybit, Sprint 1 overhaul) ──────
BINANCE_API_KEY:    str   = os.getenv('BINANCE_API_KEY', '')
BINANCE_API_SECRET: str   = os.getenv('BINANCE_API_SECRET', '')
BINANCE_TESTNET:    bool  = os.getenv('BINANCE_TESTNET', 'true').lower() == 'true'
PERP_PAIRS:       list  = os.getenv('PERP_PAIRS', 'AVAXUSDT,SOLUSDT,ETHUSDT,BTCUSDT').split(',')
PERP_POSITION_SIZE_USD:  float = float(os.getenv('PERP_POSITION_SIZE_USD', '50'))   # was 100, cut 50%
PERP_MAX_LEVERAGE:       int   = int(os.getenv('PERP_MAX_LEVERAGE', '10'))            # was 20, cut 50%
PERP_MAX_POSITIONS:      int   = int(os.getenv('PERP_MAX_POSITIONS', '2'))            # was 3, cut 50%
PERP_STOP_PCT:           float = float(os.getenv('PERP_STOP_PCT', '0.008'))           # was 0.015, cut 50%
PERP_TAKE_PROFIT_PCT:    float = float(os.getenv('PERP_TAKE_PROFIT_PCT', '0.016'))   # was 0.03, cut 50%, maintains 2:1
BINANCE_TAKER_FEE_PCT:   float = 0.00040   # 0.040% taker (USD-M futures standard tier)
BINANCE_MAKER_FEE_PCT:   float = 0.00020   # 0.020% maker
BINANCE_SPOT_MAKER_FEE_PCT: float = float(os.getenv('BINANCE_SPOT_MAKER_FEE_PCT', '0.001'))  # 0.10% spot maker (4x cheaper than Coinbase 0.40%)

# ── Mean-reversion strategy (ranging / volatile regimes) ─────────────────────
MEAN_REVERSION_ENABLED:   bool  = os.getenv('MEAN_REVERSION_ENABLED', 'true').lower() == 'true'
MEAN_REVERSION_RSI_ENTRY: float = float(os.getenv('MEAN_REVERSION_RSI_ENTRY', '33'))
MEAN_REVERSION_ADX_MAX:   float = float(os.getenv('MEAN_REVERSION_ADX_MAX', '22'))

# ── Fade-the-rally (SHORT overbought in ranging/volatile regimes) ─────────────
FADE_ENABLED: bool = os.getenv('FADE_ENABLED', 'true').lower() == 'true'

# ── Range scalper (ultra-flat ADX < 15 + tight BBs — buy range support) ──────
RANGE_SCALPER_ENABLED: bool = os.getenv('RANGE_SCALPER_ENABLED', 'true').lower() == 'true'
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
CRYPTO_CANDLE_GRANULARITY: str = 'FIVE_MINUTE'   # v5.0 Sprint 2: 5-min bars (was ONE_MINUTE)
EQUITY_MIN_PRICE: float = 1.00
EQUITY_MAX_PRICE: float = 200.00
EQUITY_MIN_VOLUME: int = 500_000
EQUITY_MIN_DOLLAR_VOLUME: float = 1_000_000
EQUITY_VOLUME_SPIKE_MULTIPLIER: float = 1.5
EQUITY_POSITION_SIZE_USD: float = float(os.getenv('EQUITY_POSITION_SIZE_USD', '75'))
CRYPTO_POSITION_SIZE_USD: float = float(os.getenv('CRYPTO_POSITION_SIZE_USD', '500'))

MARKET_TIMEZONE: str = 'America/New_York'
MARKET_OPEN: dt_time = dt_time(9, 30)
MARKET_CLOSE: dt_time = dt_time(16, 0)
NO_TRADE_UNTIL: dt_time = dt_time(10, 0)

# ════════════════════════════════════════════════════════════════════
# SCHEDULER INTERVALS
# ════════════════════════════════════════════════════════════════════
EQUITY_SCAN_INTERVAL_SECONDS: int = 60
CRYPTO_SCAN_INTERVAL_SECONDS: int = 15
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
# ALERTS
# SQLite system_events: always active, displayed on dashboard.
# Telegram: optional — fill TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID for phone alerts.
# ════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID:   str = os.getenv('TELEGRAM_CHAT_ID',   '')

# ════════════════════════════════════════════════════════════════════
# LANE 3 — PREDICTION MARKETS (Polymarket + Kalshi)
# ════════════════════════════════════════════════════════════════════
LANE3_ENABLED:      bool  = os.getenv('LANE3_ENABLED',      'false').lower() == 'true'
POLYMARKET_ENABLED: bool  = os.getenv('POLYMARKET_ENABLED', 'false').lower() == 'true'
KALSHI_ENABLED:     bool  = os.getenv('KALSHI_ENABLED',     'false').lower() == 'true'
POLYMARKET_PAPER:   bool  = os.getenv('POLYMARKET_PAPER',   'true').lower()  == 'true'
KALSHI_PAPER:       bool  = os.getenv('KALSHI_PAPER',        'true').lower()  == 'true'

# Polymarket (Polygon CLOB — requires crypto wallet for live trading)
POLYMARKET_PRIVATE_KEY:    str = os.getenv('POLYMARKET_PRIVATE_KEY',    '')
POLYMARKET_API_KEY:        str = os.getenv('POLYMARKET_API_KEY',        '')
POLYMARKET_API_SECRET:     str = os.getenv('POLYMARKET_API_SECRET',     '')
POLYMARKET_API_PASSPHRASE: str = os.getenv('POLYMARKET_API_PASSPHRASE', '')
POLYMARKET_CHAIN_ID:       int = int(os.getenv('POLYMARKET_CHAIN_ID',   '137'))  # Polygon mainnet

# Kalshi (CFTC-regulated, USD-direct — demo.kalshi.co for paper)
KALSHI_API_KEY:    str = os.getenv('KALSHI_API_KEY',    '')
KALSHI_API_SECRET: str = os.getenv('KALSHI_API_SECRET', '')

# Market selection filters
PM_MIN_VOLUME_USD:  float = float(os.getenv('PM_MIN_VOLUME_USD',  '10000'))  # min $10k/day volume
PM_MAX_POSITION_USD: float = float(os.getenv('PM_MAX_POSITION_USD', '25'))   # max $25 per trade
PM_MIN_EDGE_PCT:     float = float(os.getenv('PM_MIN_EDGE_PCT',    '0.03'))  # need ≥3% edge vs market
PM_MAX_POSITIONS:    int   = int(os.getenv('PM_MAX_POSITIONS',     '5'))     # max 5 open pred. market positions
PM_MIN_DAYS:         float = float(os.getenv('PM_MIN_DAYS',        '1.0'))   # min days to expiry
PM_MAX_DAYS:         float = float(os.getenv('PM_MAX_DAYS',        '90.0'))  # max days to expiry (avoid illiquid far-dated)
PM_STOP_LOSS_FRACTION:   float = 0.50   # exit if price drops to 50% of entry (e.g. $0.60 → exit at $0.30)
PM_TAKE_PROFIT_FRACTION: float = 0.60   # exit when 60% of potential gain captured
LANE3_SCAN_INTERVAL_SECONDS: int = int(os.getenv('LANE3_SCAN_INTERVAL_SECONDS', '900'))  # 15 min

# Multi-LLM ensemble weights (must sum to 1.0)
# Weights are adapted by pm_calibrator.py based on per-model Brier scores
ENSEMBLE_CLAUDE_WEIGHT: float = float(os.getenv('ENSEMBLE_CLAUDE_WEIGHT', '1.0'))   # start Claude-only
ENSEMBLE_GPT_WEIGHT:    float = float(os.getenv('ENSEMBLE_GPT_WEIGHT',    '0.0'))   # add when OPENAI_API_KEY set
ENSEMBLE_GEMINI_WEIGHT: float = float(os.getenv('ENSEMBLE_GEMINI_WEIGHT', '0.0'))   # add when GOOGLE_API_KEY set
PM_ENSEMBLE_MIN_MODELS: int   = int(os.getenv('PM_ENSEMBLE_MIN_MODELS',   '1'))     # min models needed for forecast
PM_LLM_TEMPERATURE:     float = float(os.getenv('PM_LLM_TEMPERATURE',     '0.3'))   # lower = more deterministic
PM_LLM_MAX_TOKENS:      int   = int(os.getenv('PM_LLM_MAX_TOKENS',        '600'))

# Optional additional LLM providers (add keys to enable)
OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
GOOGLE_API_KEY: str = os.getenv('GOOGLE_API_KEY', '')

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
DB_PATH: str = 'logs/trades.db'
LANCEDB_PATH: str = 'logs/memory'
CSV_LOG_DIR: str = 'logs/csv'
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
