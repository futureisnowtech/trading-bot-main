from prometheus_client import start_http_server, Gauge, Histogram, Counter
import logging

logger = logging.getLogger(__name__)

# ─── Metrics Definitions ─────────────────────────────────────────────────────

# PnL & Performance
PNL_GAUGE = Gauge('algo_bot_pnl_usd', 'Real-time PnL in USD (Daily or Session)')
EQUITY_GAUGE = Gauge('algo_bot_equity_usd', 'Total account equity in USD')
TOTAL_EQUITY_GAUGE = Gauge('algo_bot_total_equity_usd', 'Total account equity in USD (v2)')
DRAWDOWN_GAUGE = Gauge('algo_bot_drawdown_pct', 'Current drawdown percentage')
BUYING_POWER_GAUGE = Gauge('algo_bot_buying_power_usd', 'Available buying power in USD')

# Strategy Vitals
OBI_GAUGE = Gauge('algo_bot_obi_score', 'Real-time Order Book Imbalance score (most-recent symbol)')
# v18.19: per-symbol OBI so Grafana can see each asset's order book balance.
OBI_SYMBOL_GAUGE = Gauge('algo_bot_obi_score_by_symbol', 'Order Book Imbalance per symbol', ['symbol'])
MICROPRICE_GAUGE = Gauge('algo_bot_microprice_usd', 'Asset microprice (weighted mid)')
MID_PRICE_GAUGE = Gauge('algo_bot_mid_price_usd', 'Asset mid price')

# v18.19: trade economics (session-resetting where noted).
PNL_NET_GAUGE = Gauge('algo_bot_pnl_net_usd', 'Realized PnL net of fees this session (resets midnight UTC)')
FEES_PAID_COUNTER = Counter('algo_bot_fees_paid_usd_total', 'Cumulative fees paid (monotonic)')
OPEN_TRADES_GAUGE = Gauge('algo_bot_open_trades', 'Currently open bot-managed positions')
OPEN_POS_PNL_GAUGE = Gauge('algo_bot_open_position_pnl_usd', 'Unrealized PnL per open position', ['asset'])
OPEN_POS_ENTRY_GAUGE = Gauge('algo_bot_open_position_entry_price', 'Entry price per open position', ['asset'])
TRADES_WON_COUNTER = Counter('algo_bot_trades_won_total', 'Profitable closes')
TRADES_LOST_COUNTER = Counter('algo_bot_trades_lost_total', 'Losing closes')
SESSION_TRADES_GAUGE = Gauge('algo_bot_session_trade_count', 'Trades executed today (resets midnight UTC)')

# v18.19: economics gate observability.
SPOT_EXIT_FEE_BLOCKED = Counter(
    'algo_bot_spot_exit_fee_blocked_total',
    'Discretionary exits blocked by economics gate',
    ['symbol', 'exit_type'],
)

# Execution & Latency
TRADES_COUNTER = Counter('algo_bot_total_trades_executed_total', 'Monotonically increasing trade count')
EXECUTION_LATENCY_HISTOGRAM = Histogram(
    'algo_bot_execution_latency_seconds',
    'Order execution latency in seconds',
    buckets=(.01, .025, .05, .1, .25, .5)
)

# Legacy / System
CPU_PERCENT_GAUGE = Gauge('algo_bot_cpu_percent', 'System CPU usage percentage')
RAM_PERCENT_GAUGE = Gauge('algo_bot_ram_percent', 'System RAM usage percentage')
LATENCY_GAUGE = Gauge('algo_bot_order_latency_ms', 'Last order fill latency in ms')
LATENCY_HISTOGRAM = Histogram(
    'algo_bot_order_latency_seconds', 
    'Order fill latency in seconds',
    buckets=(.005, .01, .015, .02, .05, .1, .5, 1.0, 5.0)
)

# System Health
KILL_SWITCH_GAUGE = Gauge('algo_bot_kill_switch_active', '1 if kill switch is active, 0 otherwise')
API_ERRORS_COUNTER = Counter('algo_bot_api_errors_total', 'Total API errors recorded')

# Strategy Metrics
STRATEGY_DRIFT_GAUGE = Gauge('algo_bot_strategy_drift', 'Difference between signal and execution price')

# v19.1.10: Sovereign Weather Alpha Metrics
WEATHER_ENSEMBLE_PROB_GAUGE = Gauge('algo_bot_weather_ensemble_prob', 'Ensemble probability per ticker', ['ticker'])
WEATHER_METAR_DIFF_GAUGE = Gauge('algo_bot_weather_metar_diff', 'METAR ground truth diff from threshold', ['ticker'])
WEATHER_HRRR_DIFF_GAUGE = Gauge('algo_bot_weather_hrrr_diff', 'HRRR intraday diff from threshold', ['ticker'])
WEATHER_SIGMA_GAUGE = Gauge('algo_bot_weather_sigma', 'Ensemble standard deviation (Sigma)', ['ticker'])

def start_metrics_server(port=8000):
    """Start the Prometheus metrics HTTP server."""
    try:
        start_http_server(port, addr='0.0.0.0')
        logger.info(f"📊 Prometheus metrics server started on 0.0.0.0:{port}")
    except Exception as e:
        logger.error(f"❌ Failed to start metrics server: {e}")

def update_performance(pnl: float, equity: float, drawdown: float):
    PNL_GAUGE.set(pnl)
    EQUITY_GAUGE.set(equity)
    DRAWDOWN_GAUGE.set(drawdown)

def update_latency(latency_ms: float):
    LATENCY_GAUGE.set(latency_ms)
    LATENCY_HISTOGRAM.observe(latency_ms / 1000.0)

def update_kill_switch(active: bool):
    KILL_SWITCH_GAUGE.set(1 if active else 0)

def increment_api_errors():
    API_ERRORS_COUNTER.inc()

def update_strategy_drift(drift: float):
    STRATEGY_DRIFT_GAUGE.set(drift)


# ─── v18.19 session-reset helpers ────────────────────────────────────────────


def reset_session_metrics():
    """Zero session-bucketed gauges at midnight UTC. Counters stay monotonic."""
    try:
        PNL_NET_GAUGE.set(0.0)
        SESSION_TRADES_GAUGE.set(0.0)
    except Exception as e:
        logger.warning(f"reset_session_metrics failed: {e}")


def drop_open_position_labels(symbol: str):
    """Remove per-asset gauge time series when a position closes."""
    sym = str(symbol or "").upper()
    if not sym:
        return
    try:
        OPEN_POS_PNL_GAUGE.remove(sym)
    except Exception:
        pass
    try:
        OPEN_POS_ENTRY_GAUGE.remove(sym)
    except Exception:
        pass
