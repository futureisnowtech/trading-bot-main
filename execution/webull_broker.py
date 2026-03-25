"""
execution/webull_broker.py

NOTE: Webull's unofficial Python library is blocked by Webull's servers
(403 Illegal Client on all endpoints as of 2026). This file now proxies
to AlpacaBroker, which has a working official API.

To set up Alpaca (free, paper + live):
  1. alpaca.markets → sign up → Paper Trading → Generate API Keys
  2. Add to .env: ALPACA_API_KEY=PKxxx  ALPACA_SECRET_KEY=xxx
  3. Run: python3 scripts/test_brokers.py --broker webull
"""
# Re-export AlpacaBroker under the WebullBroker name so nothing else changes.
from execution.alpaca_broker import AlpacaBroker as WebullBroker, get_alpaca_broker as get_webull_broker  # noqa: F401

__all__ = ['WebullBroker', 'get_webull_broker']
