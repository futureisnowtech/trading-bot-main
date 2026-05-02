import asyncio
import json
import time
import logging
import hmac
import hashlib
import jwt
import secrets
from typing import Dict, List, Optional
import websockets
from threading import Thread

import system_state

logger = logging.getLogger(__name__)

# ─── Volatility Circuit Breaker State ────────────────────────────────────────
_price_history: Dict[str, List[tuple]] = {}  # symbol -> [(ts, price), ...]
_halted_until: float = 0
_HALT_DURATION = 15 * 60  # 15 minutes
_VOLATILITY_THRESHOLD = 0.03  # 3%
_VOLATILITY_WINDOW = 60  # 60 seconds

# Global latest prices for the system
latest_prices: Dict[str, float] = {}

def is_volatility_halted() -> bool:
    return time.time() < _halted_until

def get_halt_time_remaining() -> float:
    return max(0, _halted_until - time.time())

def _check_circuit_breaker(symbol: str, price: float):
    global _halted_until
    now = time.time()
    
    if symbol not in _price_history:
        _price_history[symbol] = []
    
    history = _price_history[symbol]
    history.append((now, price))
    
    # Prune old data
    cutoff = now - _VOLATILITY_WINDOW
    while history and history[0][0] < cutoff:
        history.pop(0)
    
    if len(history) < 2:
        return

    # Check move
    min_price = min(p for ts, p in history)
    max_price = max(p for ts, p in history)
    move = (max_price - min_price) / min_price
    
    if move >= _VOLATILITY_THRESHOLD:
        if now > _halted_until:
            _halted_until = now + _HALT_DURATION
            logger.critical(
                f"🚨 VOLATILITY CIRCUIT BREAKER TRIGGERED: {symbol} moved {move:.1%} in <60s. "
                f"Pausing all entries for 15m."
            )
            try:
                from notifications.notification_engine import notify_risk, SEV_CRITICAL
                notify_risk(
                    title="VOLATILITY HALT",
                    detail=f"{symbol} move {move:.1%} triggered 15m pause.",
                    severity=SEV_CRITICAL
                )
            except Exception:
                pass

# ─── Coinbase WebSocket Implementation ───────────────────────────────────────

class CoinbaseWebsocketFeed:
    def __init__(self, key_name: str, private_key_pem: str, product_ids: List[str]):
        self.key_name = key_name
        self.private_key_pem = private_key_pem.replace("\\n", "\n").encode() if isinstance(private_key_pem, str) else private_key_pem
        self.product_ids = product_ids
        self.uri = "wss://advanced-trade-ws.coinbase.com"
        self._running = False

    def _generate_jwt(self, service="retail_rest_api_proxy"):
        """CDP JWT for WS authentication."""
        now = int(time.time())
        payload = {
            "sub": self.key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
        }
        headers = {
            "kid": self.key_name,
            "nonce": secrets.token_hex(16),
        }
        return jwt.encode(
            payload,
            self.private_key_pem,
            algorithm="ES256",
            headers=headers,
        )

    async def run(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(self.uri) as ws:
                    system_state.state.update_exchange(ws_connected=True)
                    # Subscribe
                    jwt_token = self._generate_jwt()
                    subscribe_msg = {
                        "type": "subscribe",
                        "product_ids": self.product_ids,
                        "channel": "ticker",
                        "jwt": jwt_token,
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"📡 Coinbase WS: Subscribed to {self.product_ids}")

                    async for message in ws:
                        data = json.loads(message)
                        if data.get("channel") == "ticker":
                            for event in data.get("events", []):
                                for ticker in event.get("tickers", []):
                                    symbol = ticker.get("product_id")
                                    price = float(ticker.get("price", 0))
                                    if symbol and price > 0:
                                        latest_prices[symbol] = price
                                        _check_circuit_breaker(symbol, price)
            except Exception as e:
                system_state.state.update_exchange(ws_connected=False)
                logger.error(f"❌ Coinbase WS Error: {e}")
                await asyncio.sleep(5) # Backoff

def start_coinbase_feed(key_name: str, private_key: str, products: List[str]):
    feed = CoinbaseWebsocketFeed(key_name, private_key, products)
    
    def _thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(feed.run())
    
    t = Thread(target=_thread_target, daemon=True, name="CoinbaseWS")
    t.start()
    return t
