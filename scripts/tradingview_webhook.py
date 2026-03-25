"""
scripts/tradingview_webhook.py — TradingView Pine Script alert receiver.

Run this alongside the main bot:
    python3 scripts/tradingview_webhook.py

Then expose it via ngrok:
    ngrok http 8765

Paste the ngrok URL into TradingView alert → Webhook URL:
    https://xxxx.ngrok.io/webhook

Pine Script sends JSON; this server validates the secret, normalises the symbol,
and writes a row to system_events (source='tradingview').  job_runner adds
TV_SIGNAL_BOOST_CONVICTION pts to conviction when a matching signal is fresh.

Payload format expected from Pine Script:
    {
        "secret":  "{{strategy.order.comment}}",  <- set in .env as TV_WEBHOOK_SECRET
        "symbol":  "BTCUSDC",
        "action":  "buy",      <- "buy" | "sell" | "close"
        "price":   "{{close}}",
        "tf":      "1",        <- timeframe in minutes
        "signal":  "MACD_cross + RSI_oversold"
    }
"""

import json
import os
import sys
import sqlite3
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from urllib.parse import urlparse

# ── path ─────────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import DB_PATH, MARKET_TIMEZONE

# ── config ────────────────────────────────────────────────────────────────────
PORT:   int = int(os.getenv('TV_WEBHOOK_PORT', '8765'))
SECRET: str = os.getenv('TV_WEBHOOK_SECRET', '')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [tv_webhook] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('tv_webhook')

# ── symbol normaliser ─────────────────────────────────────────────────────────
# TradingView sends "BTCUSDC", "BTCUSD", "BTC-USD", "COINBASE:BTCUSD" etc.
# Our system uses Coinbase format: "BTC-USDC"
_TV_TO_CB = {
    'BTCUSDC': 'BTC-USDC',  'BTCUSD': 'BTC-USDC',   'BTCUSDT': 'BTC-USDC',
    'ETHUSDC': 'ETH-USDC',  'ETHUSD': 'ETH-USDC',   'ETHUSDT': 'ETH-USDC',
    'SOLUSDC': 'SOL-USDC',  'SOLUSD': 'SOL-USDC',   'SOLUSDT': 'SOL-USDC',
    'AVAXUSDC':'AVAX-USDC', 'AVAXUSD':'AVAX-USDC',  'AVAXUSDT':'AVAX-USDC',
    'XRPUSDC': 'XRP-USDC',  'XRPUSD': 'XRP-USDC',   'XRPUSDT': 'XRP-USDC',
    'DOGEUSDC':'DOGE-USDC', 'DOGEUSD':'DOGE-USDC',  'DOGEUSDT':'DOGE-USDC',
    'LINKUSDC':'LINK-USDC', 'LINKUSD':'LINK-USDC',  'LINKUSDT':'LINK-USDC',
    'ADAUSDC': 'ADA-USDC',  'ADAUSD': 'ADA-USDC',   'ADAUSDT': 'ADA-USDC',
}

def _normalise_symbol(raw: str) -> str:
    """Strip exchange prefix, uppercase, try lookup, else format as XX-USDC."""
    s = raw.upper().strip()
    if ':' in s:
        s = s.split(':')[-1]  # "COINBASE:BTCUSD" → "BTCUSD"
    s = s.replace('-', '').replace('/', '')  # "BTC-USD" → "BTCUSD"
    if s in _TV_TO_CB:
        return _TV_TO_CB[s]
    # Fallback: if ends with USDC/USDT/USD, insert dash before the quote currency
    for quote in ('USDC', 'USDT', 'USD'):
        if s.endswith(quote):
            base = s[:-len(quote)]
            return f'{base}-USDC'
    return s  # unknown, pass through


# ── DB writer ─────────────────────────────────────────────────────────────────
def _write_signal(symbol: str, action: str, price: float, tf: str, signal_desc: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    message = json.dumps({
        'symbol': symbol,
        'action': action,
        'price':  price,
        'tf_min': tf,
        'signal': signal_desc,
        'ts':     ts,
    })
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, ?, ?, ?)",
            (ts, 'INFO', 'tradingview', message),
        )
        conn.commit()
        conn.close()
        log.info(f"Saved TV signal: {symbol} {action.upper()} @ {price:.4f}  [{signal_desc}]")
    except Exception as e:
        log.error(f"DB write failed: {e}")


# ── HTTP handler ──────────────────────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress default access log — our log handler is cleaner
        pass

    def _send(self, code: int, body: str) -> None:
        payload = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        """Health check endpoint used by ngrok / UptimeRobot."""
        if urlparse(self.path).path == '/health':
            self._send(200, json.dumps({'status': 'ok', 'service': 'tv_webhook'}))
        else:
            self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        if urlparse(self.path).path != '/webhook':
            self._send(404, json.dumps({'error': 'not found'}))
            return

        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._send(400, json.dumps({'error': 'empty body'}))
            return

        try:
            body = self.rfile.read(length)
            data = json.loads(body)
        except Exception as e:
            log.warning(f"Bad JSON from {self.client_address[0]}: {e}")
            self._send(400, json.dumps({'error': 'invalid json'}))
            return

        # ── secret validation ───────────────────────────────────────────────
        if SECRET:
            incoming_secret = str(data.get('secret', ''))
            if incoming_secret != SECRET:
                log.warning(f"Bad secret from {self.client_address[0]} — rejected")
                self._send(403, json.dumps({'error': 'forbidden'}))
                return

        # ── parse fields ────────────────────────────────────────────────────
        raw_symbol = str(data.get('symbol', '')).strip()
        if not raw_symbol:
            self._send(400, json.dumps({'error': 'symbol required'}))
            return

        symbol = _normalise_symbol(raw_symbol)
        action = str(data.get('action', 'buy')).lower().strip()
        if action not in ('buy', 'sell', 'close', 'long', 'short'):
            self._send(400, json.dumps({'error': f'unknown action: {action}'}))
            return
        # Normalise buy/long → "buy", sell/short/close → "sell"
        if action in ('long',):
            action = 'buy'
        elif action in ('short', 'close'):
            action = 'sell'

        try:
            price = float(data.get('price', 0))
        except (TypeError, ValueError):
            price = 0.0

        tf         = str(data.get('tf', '1'))
        signal_desc = str(data.get('signal', ''))[:200]  # cap to 200 chars

        _write_signal(symbol, action, price, tf, signal_desc)
        self._send(200, json.dumps({'status': 'ok', 'symbol': symbol, 'action': action}))


# ── entrypoint ────────────────────────────────────────────────────────────────
def main():
    if not SECRET:
        log.warning("TV_WEBHOOK_SECRET not set in .env — all POST requests will be accepted without auth!")
    else:
        log.info(f"Secret validation enabled (TV_WEBHOOK_SECRET length={len(SECRET)})")

    server = HTTPServer(('0.0.0.0', PORT), WebhookHandler)
    log.info(f"TradingView webhook server listening on port {PORT}")
    log.info("Endpoints:")
    log.info(f"  POST http://localhost:{PORT}/webhook  ← Pine Script alert URL")
    log.info(f"  GET  http://localhost:{PORT}/health   ← health check")
    log.info("")
    log.info("Start ngrok to expose to TradingView:")
    log.info(f"  ngrok http {PORT}")
    log.info("Then paste the ngrok HTTPS URL into TradingView alert Webhook URL field.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Webhook server stopped.")
        server.server_close()


if __name__ == '__main__':
    main()
