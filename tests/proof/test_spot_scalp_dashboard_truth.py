from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_ssdt01_crypto_header_uses_live_position_readers(monkeypatch):
    import dashboard.data.crypto_dashboard as cd

    monkeypatch.setattr(
        cd,
        "_q1",
        lambda *a, **k: {"health": "HEALTHY", "active": 1, "autonomous_enabled": 1, "mode": "live"},
    )

    bal_mod = types.ModuleType("data.balance")
    bal_mod.get_coinbase_balance = lambda: {"balance": 1200.0}
    bal_mod.get_spot_balance_summary = lambda: {
        "usd_available": 700.0,
        "spot_equity": 1000.0,
    }
    pos_mod = types.ModuleType("data.positions")
    pos_mod.get_spot_positions_dashboard = lambda: [
        {"symbol": "BTC", "current_value": 200.0},
        {"symbol": "ETH", "current_value": 100.0},
    ]
    pos_mod.get_perp_positions = lambda: [
        {"symbol": "ETH", "qty": 1.0, "current_price": 250.0},
    ]
    db_mod = types.ModuleType("db")
    db_mod._runtime_paper_flag = lambda: 0

    monkeypatch.setitem(sys.modules, "data.balance", bal_mod)
    monkeypatch.setitem(sys.modules, "data.positions", pos_mod)
    monkeypatch.setitem(sys.modules, "db", db_mod)

    hdr = cd.get_crypto_header()
    assert hdr["open_count"] == 3
    assert hdr["spot_deployed_pct"] == 30.0
    assert hdr["perp_deployed_pct"] == 20.8


def test_ssdt02_opportunity_board_reads_canonical_spot_scalp_fields(monkeypatch):
    import dashboard.data.crypto_dashboard as cd

    monkeypatch.setattr(
        cd,
        "_q",
        lambda *a, **k: [
            {
                "symbol": "ETH",
                "underlying": "ETH",
                "exchange": "coinbase",
                "primary_setup": "impulse_continuation",
                "spot_regime": "TREND",
                "setup_family": "impulse_continuation",
                "tf_5m_state": "z=0.4",
                "tf_30m_state": "z=0.3",
                "tf_4h_state": "z=0.2",
                "tf_1d_state": "z=0.1",
                "structural_confirms": "kst,supertrend",
                "execution_route": "maker_first",
                "cooldown_until": "",
                "microstructure_veto": "",
                "expected_profit": 1.23,
                "score": 71.0,
                "status": "executable",
                "recommended_lane": "spot",
                "direction": "LONG",
                "auto_executable": 1,
                "manual_executable": 1,
                "trade_blocked_reason": "",
                "trade_size_block_reason": "",
                "trade_source_reason": "trusted_source",
                "stop_pct": 0.01,
                "ts": "2026-04-22T12:00:00",
                "decision": "entered",
            }
        ],
    )

    rows = cd.get_crypto_opportunity_board(hours=24)
    assert rows[0]["underlying"] == "ETH"
    assert rows[0]["expected_profit"] == 1.23
    assert rows[0]["spot_regime"] == "TREND"
    assert rows[0]["execution_route"] == "maker_first"
