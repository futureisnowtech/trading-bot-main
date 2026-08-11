"""Proof: the bankroll denominator comes from Kalshi, and never reads zero.

The live loop used to size against a hand-set config.ACCOUNT_SIZE, which sat
at $100 while the real account held $76.14. These proofs pin the replacement:
the broker is the source, a bad reading never propagates into sizing, and the
fallback chain degrades in a safe order.
"""

from __future__ import annotations

import sqlite3

import pytest

from runtime.live_account import resolve_live_bankroll


class _Broker:
    def __init__(self, balance):
        self._balance = balance

    def get_account_balance(self):
        if isinstance(self._balance, Exception):
            raise self._balance
        return self._balance


def _seed_state(db_path, value):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_runtime_state ("
            "id INTEGER PRIMARY KEY, account_size_live REAL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO system_runtime_state (id, account_size_live) VALUES (1, ?)",
            (value,),
        )


def test_live_broker_balance_is_the_source(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_state(db, 500.0)

    # The broker wins over any cached or configured value.
    assert resolve_live_bankroll(db_path=db, broker=_Broker(76.14)) == pytest.approx(76.14)


def test_healthy_reading_is_cached_for_later_failures(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_state(db, 0.0)

    assert resolve_live_bankroll(db_path=db, broker=_Broker(88.20)) == pytest.approx(88.20)
    # A subsequent outage falls back to what the broker last reported, not to
    # a stale hand-set constant.
    assert resolve_live_bankroll(
        db_path=db, broker=_Broker(RuntimeError("api down"))
    ) == pytest.approx(88.20)


@pytest.mark.parametrize("bad", [0, 0.0, -5.0, None, RuntimeError("boom")])
def test_bad_readings_never_propagate_into_sizing(tmp_path, bad):
    """Zero or negative bankroll is the dangerous case: it must never surface."""
    db = str(tmp_path / "t.db")
    _seed_state(db, 64.00)

    assert resolve_live_bankroll(db_path=db, broker=_Broker(bad)) == pytest.approx(64.00)


def test_falls_back_to_config_floor_when_nothing_else_is_available(tmp_path):
    from config import ACCOUNT_SIZE

    db = str(tmp_path / "empty.db")
    resolved = resolve_live_bankroll(db_path=db, broker=_Broker(RuntimeError("no broker")))

    assert resolved == pytest.approx(float(ACCOUNT_SIZE))


class _UnconnectedBroker:
    """Mirrors get_kalshi_broker(), which returns an instance that has not
    authenticated yet. Reading balance without connecting returns 0."""

    def __init__(self, balance):
        self._balance = balance
        self._connected = False
        self.connect_calls = 0

    def is_connected(self):
        return self._connected

    def connect(self, *, sync_positions=True, quiet=False):
        self.connect_calls += 1
        self._connected = True
        return True

    def get_account_balance(self):
        return self._balance if self._connected else 0.0


def test_unconnected_broker_is_connected_before_reading_balance(tmp_path):
    """Regression: the balance read silently returned 0 and fell to the floor."""
    db = str(tmp_path / "t.db")
    broker = _UnconnectedBroker(76.14)

    resolved = resolve_live_bankroll(db_path=db, broker=broker)

    assert broker.connect_calls == 1
    assert resolved == pytest.approx(76.14)


def test_already_connected_broker_is_not_reconnected(tmp_path):
    db = str(tmp_path / "t.db")
    broker = _UnconnectedBroker(76.14)
    broker.connect()
    broker.connect_calls = 0

    assert resolve_live_bankroll(db_path=db, broker=broker) == pytest.approx(76.14)
    assert broker.connect_calls == 0


def test_resolved_bankroll_is_always_positive(tmp_path):
    """Whatever happens, sizing gets a usable denominator."""
    db = str(tmp_path / "empty.db")
    for broker in (_Broker(0), _Broker(None), _Broker(RuntimeError("x")), _Broker(-1)):
        assert resolve_live_bankroll(db_path=db, broker=broker) > 0
