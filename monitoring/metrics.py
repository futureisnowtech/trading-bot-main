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
OBI_GAUGE = Gauge('algo_bot_obi_score', 'Real-time Order Book Imbalance score')
MICROPRICE_GAUGE = Gauge('algo_bot_microprice', 'Asset microprice (weighted mid)')
MID_PRICE_GAUGE = Gauge('algo_bot_mid_price', 'Asset mid price')

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

def start_metrics_server(port=8000):
    """Start the Prometheus metrics HTTP server."""
    try:
        start_http_server(port)
        logger.info(f"📊 Prometheus metrics server started on port {port}")
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
