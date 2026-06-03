from prometheus_client import start_http_server, Gauge, Histogram, Counter
import logging

logger = logging.getLogger(__name__)

# ─── Metrics Definitions ─────────────────────────────────────────────────────

# PnL & Performance
PNL_GAUGE = Gauge('algo_bot_pnl_usd', 'Real-time PnL in USD (Daily or Session)')
EQUITY_GAUGE = Gauge('algo_bot_equity_usd', 'Total account equity in USD')
TOTAL_EQUITY_GAUGE = Gauge('algo_bot_total_equity_usd', 'Total account equity in USD (v2)')
BUYING_POWER_GAUGE = Gauge('algo_bot_buying_power_usd', 'Available buying power in USD')

# SRE FIX: Pure Weather Metrics Exporter
KALSHI_WEATHER_EDGE_RATIO_GAUGE = Gauge('kalshi_weather_edge_ratio', 'Edge ratio for active weather markets', ['ticker'])
WEATHER_ENSEMBLE_PROB_GAUGE = Gauge('algo_bot_weather_ensemble_prob', 'Ensemble probability per ticker', ['ticker'])
WEATHER_METAR_DIFF_GAUGE = Gauge('algo_bot_weather_metar_diff', 'METAR ground truth diff from threshold', ['ticker'])
WEATHER_HRRR_DIFF_GAUGE = Gauge('algo_bot_weather_hrrr_diff', 'HRRR intraday diff from threshold', ['ticker'])
WEATHER_SIGMA_GAUGE = Gauge('algo_bot_weather_sigma', 'Ensemble standard deviation (Sigma)', ['ticker'])

# v18.19: trade economics
OPEN_TRADES_GAUGE = Gauge('algo_bot_open_trades', 'Currently open bot-managed positions')
TRADES_WON_COUNTER = Counter('algo_bot_trades_won_total', 'Profitable closes')
TRADES_LOST_COUNTER = Counter('algo_bot_trades_lost_total', 'Losing closes')
SESSION_TRADES_GAUGE = Gauge('algo_bot_session_trade_count', 'Trades executed today (resets midnight UTC)')

# System & Latency
CPU_PERCENT_GAUGE = Gauge('algo_bot_cpu_percent', 'System CPU usage percentage')
RAM_PERCENT_GAUGE = Gauge('algo_bot_ram_percent', 'System RAM usage percentage')
API_ERRORS_COUNTER = Counter('algo_bot_api_errors_total', 'Total API errors recorded')
KILL_SWITCH_GAUGE = Gauge('algo_bot_kill_switch_active', '1 if kill switch is active, 0 otherwise')

def start_metrics_server(port=8000):
    """Start the Prometheus metrics HTTP server."""
    try:
        start_http_server(port, addr='0.0.0.0')
        logger.info(f"📊 Prometheus metrics server started on 0.0.0.0:{port}")
    except Exception as e:
        logger.error(f"❌ Failed to start metrics server: {e}")

def update_kill_switch(active: bool):
    KILL_SWITCH_GAUGE.set(1 if active else 0)

def increment_api_errors():
    KALSHI_API_ERRORS_TOTAL.inc()
    API_ERRORS_COUNTER.inc()
