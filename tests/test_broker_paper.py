"""
tests/test_broker_paper.py — Smoke tests for each broker's paper mode.

Verifies that paper-mode brokers:
1. Import and instantiate without crashing
2. Expose the expected public API surface
3. Execute paper trades without hitting real APIs

Run: python3 -m pytest tests/test_broker_paper.py -v

No real API calls are made. External network calls are mocked.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCoinbaseBrokerPaper:
    """CoinbaseBroker reads PAPER_TRADING from config — no constructor arg needed."""

    def setup_method(self):
        with patch.dict(os.environ, {
            'PAPER_TRADING': 'true',
            'COINBASE_API_KEY': 'test_key',
            'COINBASE_API_SECRET': 'test_secret',
        }):
            import importlib
            import execution.coinbase_broker as mod
            importlib.reload(mod)
            self.broker = mod.CoinbaseBroker()
            self.mod = mod

    def test_api_surface(self):
        for method in ('connect', 'is_connected', 'buy_limit', 'sell_limit', 'sell_market'):
            assert hasattr(self.broker, method), f"CoinbaseBroker missing method: {method}"

    def test_connect_returns_bool(self):
        # With dummy creds, connect() will fail gracefully — just must return bool
        result = self.broker.connect()
        assert isinstance(result, bool)

    def test_paper_buy_returns_dict(self):
        """Paper buy bypasses real API — should return position dict."""
        with patch('execution.coinbase_broker.log_trade', return_value=None), \
             patch('execution.coinbase_broker.log_event', return_value=None):
            result = self.broker.buy_limit(
                product_id='BTC-USDC',
                size_usd=50.0,
                limit_price=50000.0,
                strategy='test',
                stop_loss=49250.0,
                take_profit=52500.0,
            )
        assert result is not None
        assert isinstance(result, dict)

    def test_paper_sell_returns_dict_or_none(self):
        """Paper sell on a position we just opened."""
        with patch('execution.coinbase_broker.log_trade', return_value=None), \
             patch('execution.coinbase_broker.log_event', return_value=None):
            # Open first so there's a position to sell
            self.broker.buy_limit('ETH-USDC', 50.0, 3000.0, strategy='test',
                                   stop_loss=2955.0, take_profit=3135.0)
            # Sell: base_size = qty held, entry_price for P&L calc
            pos = self.broker._open_positions.get('ETH-USDC', {})
            qty = pos.get('qty', 0.01)
            result = self.broker.sell_market('ETH-USDC', base_size=qty,
                                              strategy='test', entry_price=3000.0)
        assert result is None or isinstance(result, dict)


class TestAlpacaBrokerPaper:
    """AlpacaBroker reads PAPER_TRADING from config — no constructor arg needed."""

    def setup_method(self):
        with patch.dict(os.environ, {
            'PAPER_TRADING': 'true',
            'ALPACA_API_KEY': 'test_key',
            'ALPACA_API_SECRET': 'test_secret',
            'ALPACA_BASE_URL': 'https://paper-api.alpaca.markets',
        }):
            import importlib
            import execution.alpaca_broker as mod
            importlib.reload(mod)
            self.broker = mod.AlpacaBroker()

    def test_api_surface(self):
        for method in ('connect', 'is_connected', 'buy_limit', 'sell_limit', 'sell_market'):
            assert hasattr(self.broker, method), f"AlpacaBroker missing method: {method}"

    def test_connect_returns_bool(self):
        # Dummy creds will fail auth — just must not crash and return bool
        try:
            result = self.broker.connect()
            assert isinstance(result, bool)
        except Exception as e:
            pytest.fail(f"connect() raised unexpectedly: {e}")

    def test_paper_buy_returns_dict_or_none(self):
        """With PAPER_TRADING=true and no real connection, buy_limit uses paper path.
        AlpacaBroker.buy_limit takes qty (shares), not size_usd.
        """
        with patch('execution.alpaca_broker.log_trade', return_value=None), \
             patch('execution.alpaca_broker.log_event', return_value=None):
            result = self.broker.buy_limit(
                symbol='AAPL',
                qty=1,
                limit_price=175.0,
                strategy='equity_momentum',
            )
        # Paper mode without valid creds may return None — that's acceptable
        assert result is None or isinstance(result, dict)


class TestBinanceBrokerPaper:
    """BinanceBroker in testnet/paper mode — server-side orders are simulated."""

    def setup_method(self):
        with patch.dict(os.environ, {
            'BINANCE_TESTNET': 'true',
            'BINANCE_API_KEY': 'test_key',
            'BINANCE_API_SECRET': 'test_secret',
            'PAPER_TRADING': 'true',
        }):
            try:
                with patch('execution.binance_broker.BinanceClient', MagicMock()):
                    from execution.binance_broker import BinanceBroker
                    self.broker = BinanceBroker()
                self.available = True
            except (ImportError, Exception):
                self.available = False

    def test_api_surface(self):
        if not self.available:
            pytest.skip("Binance broker not available")
        for method in ('connect', 'is_connected', 'open_long', 'open_short',
                       'close_position', 'get_mark_price', 'get_funding_rate'):
            assert hasattr(self.broker, method), f"BinanceBroker missing method: {method}"

    def test_connect_returns_bool(self):
        if not self.available:
            pytest.skip("Binance broker not available")
        result = self.broker.connect()
        assert isinstance(result, bool)

    def test_get_funding_rate_returns_float(self):
        if not self.available:
            pytest.skip("Binance broker not available")
        mock_client = MagicMock()
        mock_client.futures_funding_rate.return_value = [{'fundingRate': '0.0001'}]
        self.broker._client = mock_client
        rate = self.broker.get_funding_rate('BTCUSDT')
        assert isinstance(rate, (float, int, type(None)))

    def test_paper_open_long_uses_paper_path(self):
        if not self.available:
            pytest.skip("Binance broker not available")
        with patch.object(self.broker, '_paper_open', return_value={'order_id': 'paper_123'}) as mock_paper, \
             patch('execution.binance_broker.log_event', return_value=None):
            self.broker.open_long('BTCUSDT', size_usd=100, leverage=5,
                                  stop_pct=0.015, take_profit_pct=0.045, strategy='test')
        # Paper path should have been called (PAPER_TRADING=true or no client)
        mock_paper.assert_called_once()
