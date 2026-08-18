"""Proof for the maker-path safety work.

Every case here maps to a defect found by live inspection on 2026-08-18, not to
a hypothetical. The headline one: cancel_order pointed at the deprecated v1
endpoint, so a post-only order that was placed could never be cancelled and
stayed resting on the exchange forever.
"""
from execution.kalshi_broker import KalshiBroker


def _broker() -> KalshiBroker:
    b = KalshiBroker()
    b._connected = True
    b._private_key = object()
    return b


def test_cancel_uses_the_v2_events_endpoint(monkeypatch):
    """v1 /portfolio/orders/{id} answers HTTP 410 deprecated_v1_order_endpoint.

    Verified live: the old path returned an error, cancel_order returned False,
    and the order was still 'resting' on the book afterwards.
    """
    b = _broker()
    seen = {}

    def fake_request(method, path, params=None, body=None):
        seen["method"], seen["path"] = method, path
        return {"order_id": "ORD-1", "reduced_by": "1.00"}

    monkeypatch.setattr(b, "_request", fake_request)
    assert b.cancel_order("ORD-1") is True
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/trade-api/v2/portfolio/events/orders/ORD-1"
    assert seen["path"] != "/trade-api/v2/portfolio/orders/ORD-1", "v1 path is deprecated"


def test_cancel_reports_failure_when_the_exchange_rejects(monkeypatch):
    """A rejected cancel must return False so callers can escalate."""
    b = _broker()
    monkeypatch.setattr(
        b, "_request",
        lambda *a, **k: {"error": {"code": "deprecated_v1_order_endpoint"}},
    )
    assert b.cancel_order("ORD-1") is False


def test_orphan_sweep_cancels_every_resting_order(monkeypatch):
    """A deploy inside the rest window must not leave live orders untracked."""
    b = _broker()
    monkeypatch.setattr(b, "list_resting_orders", lambda: [
        {"order_id": "A", "ticker": "KXHIGHNY-1"},
        {"order_id": "B", "ticker": "KXLOWLA-2"},
    ])
    cancelled = []
    monkeypatch.setattr(b, "cancel_order", lambda oid: cancelled.append(oid) or True)
    assert b.cancel_all_resting_orders(reason="test") == 2
    assert cancelled == ["A", "B"]


def test_orphan_sweep_counts_only_successful_cancels(monkeypatch):
    """A cancel that fails must not be reported as cleared."""
    b = _broker()
    monkeypatch.setattr(b, "list_resting_orders", lambda: [
        {"order_id": "A", "ticker": "T1"},
        {"order_id": "B", "ticker": "T2"},
    ])
    monkeypatch.setattr(b, "cancel_order", lambda oid: oid == "A")
    assert b.cancel_all_resting_orders(reason="test") == 1
