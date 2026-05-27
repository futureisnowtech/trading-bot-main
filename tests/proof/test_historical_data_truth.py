from __future__ import annotations

import os
import sys
import importlib.util

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_hist_module():
    path = os.path.join(ROOT, "data", "historical_data.py")
    spec = importlib.util.spec_from_file_location("proof_historical_data", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bare_btc_prefers_crypto_safe_alias_before_raw_symbol(monkeypatch):
    """
    get_candles('BTC') must try a crypto-safe alias (BTCUSDT -> BTC-USD)
    before falling through to the raw BTC ticker, which collides with stocks.
    """
    hd = _load_hist_module()

    calls: list[str] = []

    def _fake_hl(symbol, timeframe, limit):
        return None

    def _fake_fetch_and_store(symbol, timeframe, start_ms, limit, bar_ms, now_ms):
        calls.append(symbol)
        if symbol == "BTCUSDT":
            idx = pd.date_range("2026-04-19", periods=6, freq="1h", tz="UTC")
            return pd.DataFrame(
                {
                    "open": [73_000.0] * 6,
                    "high": [74_000.0] * 6,
                    "low": [72_500.0] * 6,
                    "close": [73_900.0] * 6,
                    "volume": [1_000_000.0] * 6,
                },
                index=idx,
            )
        return None

    monkeypatch.setattr(hd, "_fetch_hyperliquid", _fake_hl, raising=False)
    monkeypatch.setattr(hd, "_fetch_and_store", _fake_fetch_and_store, raising=False)
    monkeypatch.setattr(hd, "_load_from_db", lambda *a, **k: pd.DataFrame(), raising=False)
    monkeypatch.setattr(hd, "_save_to_db", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(hd, "_fetch_yfinance", lambda *a, **k: None, raising=False)

    df = hd.get_candles("BTC", "1h", 6)

    assert not df.empty
    assert calls[0] == "BTCUSDT", (
        "Bare BTC must route through a crypto-safe alias before trying the raw BTC ticker."
    )
    assert float(df["close"].iloc[-1]) == 73_900.0


def test_bare_btc_does_not_need_raw_ticker_when_alias_succeeds(monkeypatch):
    hd = _load_hist_module()

    calls: list[str] = []

    def _fake_hl(symbol, timeframe, limit):
        return None

    def _fake_fetch_and_store(symbol, timeframe, start_ms, limit, bar_ms, now_ms):
        calls.append(symbol)
        if symbol == "BTCUSDT":
            idx = pd.date_range("2026-04-19", periods=5, freq="1h", tz="UTC")
            return pd.DataFrame(
                {
                    "open": [73_000.0] * 5,
                    "high": [74_000.0] * 5,
                    "low": [72_500.0] * 5,
                    "close": [73_950.0] * 5,
                    "volume": [1_000_000.0] * 5,
                },
                index=idx,
            )
        return None

    monkeypatch.setattr(hd, "_fetch_hyperliquid", _fake_hl, raising=False)
    monkeypatch.setattr(hd, "_fetch_and_store", _fake_fetch_and_store, raising=False)
    monkeypatch.setattr(hd, "_load_from_db", lambda *a, **k: pd.DataFrame(), raising=False)
    monkeypatch.setattr(hd, "_save_to_db", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(hd, "_fetch_yfinance", lambda *a, **k: None, raising=False)

    hd.get_candles("BTC", "1h", 5)

    assert calls == ["BTCUSDT"], (
        "When the crypto-safe alias succeeds, get_candles('BTC') must not fall "
        "through to the raw BTC ticker."
    )
