"""
scripts/tradingview_webhook.py — TradingView Pine Script alert receiver.

Run this alongside the main bot:
    python3 scripts/tradingview_webhook.py

Then expose it via ngrok:
    ngrok http 8765

Paste the ngrok URL into TradingView alert → Webhook URL:
    https://xxxx.ngrok.io/webhook

Pine Script sends JSON; this server validates the secret, normalises the symbol,
and writes a normalized TradingView HTF context row to SQLite.

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
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ── path ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    # Explicitly load the canonical repo .env so launchd always reads the
    # Projects checkout even if another clone exists elsewhere on disk.
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from config import (
    TV_SIGNAL_INDICATOR_NAME,
    TV_SIGNAL_PROFILE_NAME,
    TV_SIGNALS_ENABLED,
    TV_WEBHOOK_PORT,
    TV_WEBHOOK_SECRET,
)

# ── config ────────────────────────────────────────────────────────────────────
PORT: int = int(TV_WEBHOOK_PORT or 8765)
SECRET: str = str(TV_WEBHOOK_SECRET or "")

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


def _normalize_bias(data: dict) -> tuple[str, str, str]:
    action_raw = str(
        data.get("action")
        or data.get("direction")
        or data.get("bias")
        or data.get("signal_type")
        or "buy"
    ).strip().lower()
    if action_raw in {"buy", "long"}:
        return action_raw, "LONG", "LONG"
    if action_raw in {"sell", "short"}:
        return action_raw, "SHORT", "SHORT"
    if action_raw in {"close", "flat", "exit"}:
        return action_raw, "CLOSE", "CLOSE"
    return action_raw, "LONG", "LONG"


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
        if not TV_SIGNALS_ENABLED:
            self._send(503, json.dumps({'error': 'tv_signals_disabled'}))
            return
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
        secret_validated = False
        if SECRET:
            incoming_secret = str(data.get('secret', ''))
            if incoming_secret != SECRET:
                log.warning(f"Bad secret from {self.client_address[0]} — rejected")
                self._send(403, json.dumps({'error': 'forbidden'}))
                return
            secret_validated = True

        # ── parse fields ────────────────────────────────────────────────────
        raw_symbol = str(data.get('symbol', '')).strip()
        if not raw_symbol:
            self._send(400, json.dumps({'error': 'symbol required'}))
            return

        symbol = _normalise_symbol(raw_symbol)
        action_raw, direction, htf_bias = _normalize_bias(data)

        try:
            price = float(data.get('price', 0))
        except (TypeError, ValueError):
            price = 0.0

        tf         = str(data.get('tf', '1'))
        signal_desc = str(data.get('signal', ''))[:200]  # cap to 200 chars
        indicator_name = str(
            data.get("indicator")
            or data.get("indicator_name")
            or TV_SIGNAL_INDICATOR_NAME
        )[:120]
        profile_name = str(data.get("profile_name") or TV_SIGNAL_PROFILE_NAME)[:120]
        strength = str(data.get("strength") or "moderate")[:40]
        try:
            from logging_db.trade_logger import log_tv_signal

            log_tv_signal(
                symbol=symbol,
                action_raw=action_raw,
                direction=direction,
                htf_bias=htf_bias,
                price=price,
                tf_min=tf,
                indicator_name=indicator_name,
                profile_name=profile_name,
                strength=strength,
                signal_desc=signal_desc,
                secret_validated=secret_validated,
                raw_payload_json=json.dumps(data),
            )
            log.info(
                f"Saved TV HTF signal: {symbol} bias={htf_bias} tf={tf} "
                f"indicator={indicator_name} strength={strength}"
            )
        except Exception as e:
            log.error(f"DB write failed: {e}")
            self._send(500, json.dumps({'error': 'db_write_failed'}))
            return
        self._send(
            200,
            json.dumps(
                {
                    'status': 'ok',
                    'symbol': symbol,
                    'direction': direction,
                    'htf_bias': htf_bias,
                    'profile_name': profile_name,
                }
            ),
        )


# ── entrypoint ────────────────────────────────────────────────────────────────
def main():
    if not TV_SIGNALS_ENABLED:
        log.warning("TV_SIGNALS_ENABLED=false — webhook will reject all POST requests")
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
