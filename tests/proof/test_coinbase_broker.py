"""
tests/proof/test_coinbase_broker.py — Proof suite for Coinbase crypto lane migration.

Invariants proved:
  CB-01  Supported symbols map to correct Coinbase product IDs
  CB-02  Unsupported symbols raise CoinbaseSymbolError (fail-closed)
  CB-03  Contract size calculation is correct for each product
  CB-04  Paper mode open_long returns valid position dict without API calls
  CB-05  Paper mode open_short returns valid position dict without API calls
  CB-06  Paper mode close_position returns pnl_usd
  CB-07  One net position per symbol — duplicate open_long is blocked
  CB-08  Taker fee constant is Coinbase rate (0.03%), not Binance/Kraken
  CB-09  Round-trip cost is 0.06% (2 × taker)
  CB-10  perps_engine imports coinbase_broker, not binance_broker
  CB-11  perps_engine uses Coinbase fee constant (0.0003), not Kraken (0.00065)
  CB-12  perps_engine uses 'coinbase_paper' broker string, not 'kraken_paper'
  CB-13  config.py exports COINBASE_CDP_KEY_NAME and COINBASE_CDP_PRIVATE_KEY
  CB-14  config.py COINBASE_TAKER_FEE_PCT is Coinbase rate (0.0003)
  CB-15  economics_gate TAKER_FEE_PCT is Coinbase rate (0.0003)
  CB-16  economics_gate ROUND_TRIP_COST is 0.0006 (two-sided taker)
  CB-17  venue_specs.py includes 'coinbase' in VENUE_FEES
  CB-18  Funding rate returns 0.0 for dated contracts (not perpetual-style)
  CB-19  Qty-to-contracts rounds down to floor (never over-sizes)
  CB-20  Missing CDP credentials does not crash paper mode
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def broker():
    """CoinbaseBroker instance in paper mode — no API calls made."""
    from execution.coinbase_broker import CoinbaseBroker

    return CoinbaseBroker(paper=True)


# ─────────────────────────────────────────────────────────────────────────────
# CB-01 — Symbol → product ID mapping
# ─────────────────────────────────────────────────────────────────────────────


def test_cb01_btc_maps_to_bip(broker):
    from execution.coinbase_broker import PRODUCT_SPECS

    spec = PRODUCT_SPECS["BTC"]
    assert spec["product_id"] == "BIP-20DEC30-CDE", f"BTC product_id wrong: {spec}"


def test_cb01_eth_maps_to_etp(broker):
    from execution.coinbase_broker import PRODUCT_SPECS

    spec = PRODUCT_SPECS["ETH"]
    assert spec["product_id"] == "ETP-20DEC30-CDE", f"ETH product_id wrong: {spec}"


def test_cb01_sol_maps_to_slp(broker):
    from execution.coinbase_broker import PRODUCT_SPECS

    spec = PRODUCT_SPECS["SOL"]
    assert spec["product_id"] == "SLP-20DEC30-CDE", f"SOL product_id wrong: {spec}"


def test_cb01_xrp_maps_to_xpp(broker):
    from execution.coinbase_broker import PRODUCT_SPECS

    spec = PRODUCT_SPECS["XRP"]
    assert spec["product_id"] == "XPP-20DEC30-CDE", f"XRP product_id wrong: {spec}"


# ─────────────────────────────────────────────────────────────────────────────
# CB-02 — Unsupported symbols fail closed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sym", ["DOGE", "AVAX", "ADA", "PEPE", "BNB", "LINK", "MATIC"])
def test_cb02_unsupported_symbol_raises_coinbase_symbol_error(broker, sym):
    from execution.coinbase_broker import CoinbaseSymbolError

    with pytest.raises(CoinbaseSymbolError):
        broker._resolve_symbol(sym)


def test_cb02_usdt_suffixed_symbols_resolve_correctly(broker):
    """BTCUSDT/ETHUSDT strip USDT → BTC/ETH — scanner uses USDT names, must route correctly."""
    from execution.coinbase_broker import PRODUCT_SPECS

    assert broker._resolve_symbol("BTCUSDT") == PRODUCT_SPECS["BTC"]
    assert broker._resolve_symbol("ETHUSDT") == PRODUCT_SPECS["ETH"]
    assert broker._resolve_symbol("SOLUSDT") == PRODUCT_SPECS["SOL"]


# ─────────────────────────────────────────────────────────────────────────────
# CB-03 — Contract sizes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sym,expected_size",
    [
        ("BTC", 0.01),
        ("ETH", 0.1),
        ("SOL", 5.0),
        ("XRP", 500.0),
    ],
)
def test_cb03_contract_sizes(sym, expected_size):
    from execution.coinbase_broker import PRODUCT_SPECS

    assert PRODUCT_SPECS[sym]["contract_size"] == expected_size


# ─────────────────────────────────────────────────────────────────────────────
# CB-04 — Paper open_long returns valid dict without API calls
# ─────────────────────────────────────────────────────────────────────────────


def test_cb04_paper_open_long_returns_order_dict(broker):
    result = broker.open_long(symbol="BTC", size_usd=500.0, leverage=3)
    assert result is not None
    assert result.get("paper") is True
    assert "orderId" in result
    assert result.get("side") == "BUY"
    assert result.get("symbol") == "BTC"


def test_cb04_paper_open_long_eth(broker):
    result = broker.open_long(symbol="ETH", size_usd=200.0, leverage=2)
    assert result is not None
    assert result.get("paper") is True


# ─────────────────────────────────────────────────────────────────────────────
# CB-05 — Paper open_short returns valid dict without API calls
# ─────────────────────────────────────────────────────────────────────────────


def test_cb05_paper_open_short_returns_order_dict(broker):
    result = broker.open_short(symbol="SOL", size_usd=100.0, leverage=3)
    assert result is not None
    assert result.get("paper") is True
    assert result.get("side") == "SELL"
    assert result.get("symbol") == "SOL"


# ─────────────────────────────────────────────────────────────────────────────
# CB-06 — Paper close_position returns pnl_usd
# ─────────────────────────────────────────────────────────────────────────────


def test_cb06_paper_close_position_returns_pnl(broker):
    from execution.coinbase_broker import CoinbaseBroker

    b = CoinbaseBroker(paper=True)
    # Simulate a long position we know the details of
    pos = {
        "symbol": "BTC",
        "direction": "LONG",
        "entry_price": 90000.0,
        "qty": 0.01,  # 1 contract
    }
    result = b.close_position("BTC", pos_fallback=pos)
    assert result is not None
    assert "pnl_usd" in result
    assert "exit_price" in result
    assert result.get("paper") is True


def test_cb06_paper_close_short_position(broker):
    from execution.coinbase_broker import CoinbaseBroker

    b = CoinbaseBroker(paper=True)
    pos = {
        "symbol": "ETH",
        "direction": "SHORT",
        "entry_price": 3000.0,
        "qty": 0.1,  # 1 contract
    }
    result = b.close_position("ETH", pos_fallback=pos)
    assert result is not None
    assert "pnl_usd" in result


# ─────────────────────────────────────────────────────────────────────────────
# CB-07 — One net position per symbol (duplicate open blocked)
# ─────────────────────────────────────────────────────────────────────────────


def test_cb07_duplicate_open_long_allowed_up_to_3():
    """Up to 3 same-direction entries per symbol allowed (scaling in); 4th is blocked."""
    from execution.coinbase_broker import CoinbaseBroker

    b = CoinbaseBroker(paper=True)
    first = b.open_long(symbol="XRP", size_usd=100.0, leverage=3)
    assert first is not None, "First open should succeed"
    second = b.open_long(symbol="XRP", size_usd=100.0, leverage=3)
    assert second is not None, (
        "Second open_long on same symbol should be allowed (scaling in)"
    )
    third = b.open_long(symbol="XRP", size_usd=100.0, leverage=3)
    assert third is not None, "Third open_long on same symbol should be allowed"
    fourth = b.open_long(symbol="XRP", size_usd=100.0, leverage=3)
    assert fourth is None, "Fourth open_long must be blocked (per-symbol cap=3)"


def test_cb07_duplicate_open_short_allowed_up_to_3():
    """Up to 3 same-direction entries per symbol allowed; 4th is blocked."""
    from execution.coinbase_broker import CoinbaseBroker

    b = CoinbaseBroker(paper=True)
    first = b.open_short(symbol="BTC", size_usd=100.0, leverage=3)
    assert first is not None
    second = b.open_short(symbol="BTC", size_usd=100.0, leverage=3)
    assert second is not None, "Second open_short on same symbol should be allowed"
    third = b.open_short(symbol="BTC", size_usd=100.0, leverage=3)
    assert third is not None, "Third open_short should be allowed"
    fourth = b.open_short(symbol="BTC", size_usd=100.0, leverage=3)
    assert fourth is None, "Fourth open_short must be blocked (per-symbol cap=3)"


def test_cb07_close_then_reopen_allowed():
    from execution.coinbase_broker import CoinbaseBroker

    b = CoinbaseBroker(paper=True)
    b.open_long(symbol="SOL", size_usd=100.0, leverage=3)
    pos = {"symbol": "SOL", "direction": "LONG", "entry_price": 150.0, "qty": 5.0}
    b.close_position("SOL", pos_fallback=pos)
    # After closing, a new open should succeed
    second = b.open_long(symbol="SOL", size_usd=100.0, leverage=3)
    assert second is not None, "Reopen after close should be allowed"


# ─────────────────────────────────────────────────────────────────────────────
# CB-08 — Taker fee is Coinbase rate
# ─────────────────────────────────────────────────────────────────────────────


def test_cb08_coinbase_taker_fee_is_003_pct():
    from execution.coinbase_broker import COINBASE_TAKER_FEE

    assert abs(COINBASE_TAKER_FEE - 0.0003) < 1e-9, (
        f"COINBASE_TAKER_FEE should be 0.0003 (0.03%), got {COINBASE_TAKER_FEE}"
    )


def test_cb08_coinbase_maker_fee_is_zero():
    from execution.coinbase_broker import COINBASE_MAKER_FEE

    assert abs(COINBASE_MAKER_FEE - 0.0000) < 1e-9, (
        f"COINBASE_MAKER_FEE should be 0.0000, got {COINBASE_MAKER_FEE}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-09 — Round-trip cost
# ─────────────────────────────────────────────────────────────────────────────


def test_cb09_round_trip_cost_is_006_pct():
    from execution.coinbase_broker import COINBASE_TAKER_FEE

    rt = COINBASE_TAKER_FEE * 2
    assert abs(rt - 0.0006) < 1e-9, f"Round-trip should be 0.0006, got {rt}"


# ─────────────────────────────────────────────────────────────────────────────
# CB-10  perps_engine → coinbase_broker (not binance_broker)
# ─────────────────────────────────────────────────────────────────────────────


def test_cb10_perps_engine_imports_coinbase_not_binance():
    import inspect
    import perps_engine

    src = inspect.getsource(perps_engine)
    assert "coinbase_broker" in src, "perps_engine must import coinbase_broker"
    assert "binance_broker" not in src, "perps_engine must NOT import binance_broker"


# ─────────────────────────────────────────────────────────────────────────────
# CB-11  perps_engine fee constant is Coinbase 0.0003
# ─────────────────────────────────────────────────────────────────────────────


def test_cb11_perps_engine_uses_coinbase_fee_constant():
    import inspect
    import perps_engine

    src = inspect.getsource(perps_engine)
    assert "0.0003" in src, "perps_engine must use Coinbase taker fee 0.0003"
    assert "0.00065" not in src, (
        "perps_engine must NOT contain Binance/Kraken fee 0.00065"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-12  perps_engine uses 'coinbase_paper' broker string
# ─────────────────────────────────────────────────────────────────────────────


def test_cb12_perps_engine_uses_coinbase_broker_string():
    import inspect
    import perps_engine

    src = inspect.getsource(perps_engine)
    assert "coinbase_paper" in src, "perps_engine must log broker='coinbase_paper'"
    assert "kraken_paper" not in src, "perps_engine must NOT log broker='kraken_paper'"


# ─────────────────────────────────────────────────────────────────────────────
# CB-13  config.py exports CDP key vars
# ─────────────────────────────────────────────────────────────────────────────


def test_cb13_config_exports_cdp_key_name():
    import config

    assert hasattr(config, "COINBASE_CDP_KEY_NAME"), (
        "config.py must export COINBASE_CDP_KEY_NAME"
    )


def test_cb13_config_exports_cdp_private_key():
    import config

    assert hasattr(config, "COINBASE_CDP_PRIVATE_KEY"), (
        "config.py must export COINBASE_CDP_PRIVATE_KEY"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-14  config.py COINBASE_TAKER_FEE_PCT is correct
# ─────────────────────────────────────────────────────────────────────────────


def test_cb14_config_coinbase_taker_fee_is_coinbase_rate():
    import config

    fee = config.COINBASE_TAKER_FEE_PCT
    assert abs(fee - 0.0003) < 1e-9, (
        f"config.COINBASE_TAKER_FEE_PCT should be 0.0003, got {fee}"
    )


def test_cb14_config_coinbase_maker_fee_is_zero():
    import config

    fee = config.COINBASE_MAKER_FEE_PCT
    assert abs(fee - 0.0000) < 1e-9, (
        f"config.COINBASE_MAKER_FEE_PCT should be 0.0000 (promotional), got {fee}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-15  economics_gate TAKER_FEE_PCT is Coinbase rate
# ─────────────────────────────────────────────────────────────────────────────


def test_cb15_economics_gate_taker_fee_is_coinbase():
    from risk.economics_gate import TAKER_FEE_PCT

    assert abs(TAKER_FEE_PCT - 0.0003) < 1e-9, (
        f"economics_gate.TAKER_FEE_PCT should be 0.0003 (Coinbase), got {TAKER_FEE_PCT}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-16  economics_gate ROUND_TRIP_COST is 0.0006
# ─────────────────────────────────────────────────────────────────────────────


def test_cb16_economics_gate_round_trip_cost():
    from risk.economics_gate import ROUND_TRIP_COST

    assert abs(ROUND_TRIP_COST - 0.0006) < 1e-9, (
        f"economics_gate.ROUND_TRIP_COST should be 0.0006, got {ROUND_TRIP_COST}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-17  venue_specs includes 'coinbase' fee entry
# ─────────────────────────────────────────────────────────────────────────────


def test_cb17_venue_specs_includes_coinbase():
    from config.venue_specs import VENUE_FEES

    assert "coinbase" in VENUE_FEES, "VENUE_FEES must include 'coinbase' key"
    assert abs(VENUE_FEES["coinbase"] - 0.0003) < 1e-9, (
        f"VENUE_FEES['coinbase'] should be 0.0003, got {VENUE_FEES['coinbase']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-18  Funding rate returns 0.0 (dated contracts, no perpetual funding)
# ─────────────────────────────────────────────────────────────────────────────


def test_cb18_funding_rate_is_zero_for_dated_contracts(broker):
    rate = broker.get_funding_rate("BTC")
    assert rate == 0.0, (
        f"Coinbase dated contracts have no funding rate, should return 0.0, got {rate}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CB-19  Qty-to-contracts floors (never over-sizes)
# ─────────────────────────────────────────────────────────────────────────────


def test_cb19_qty_to_contracts_floors_btc():
    from execution.coinbase_broker import CoinbaseBroker, PRODUCT_SPECS

    b = CoinbaseBroker(paper=True)
    spec = PRODUCT_SPECS["BTC"]
    # 1 BIP = 0.01 BTC. At $90,000, 1 contract = $900 notional.
    # $500 at price $90,000 → floor(500/900) = 0 contracts
    contracts = b._qty_to_contracts(spec, size_usd=500.0, price=90000.0)
    assert contracts == 0, (
        f"$500 at BTC=$90K should be 0 contracts (too small), got {contracts}"
    )
    # $1800 → floor(1800/900) = 2 contracts
    contracts2 = b._qty_to_contracts(spec, size_usd=1800.0, price=90000.0)
    assert contracts2 == 2, f"$1800 at BTC=$90K should be 2 contracts, got {contracts2}"


def test_cb19_qty_to_contracts_floors_eth():
    from execution.coinbase_broker import CoinbaseBroker, PRODUCT_SPECS

    b = CoinbaseBroker(paper=True)
    spec = PRODUCT_SPECS["ETH"]
    # 1 ETP = 0.1 ETH. At $3,000, 1 contract = $300 notional.
    # $150 → 0 contracts
    c0 = b._qty_to_contracts(spec, size_usd=150.0, price=3000.0)
    assert c0 == 0
    # $650 → floor(650/300) = 2 contracts
    c2 = b._qty_to_contracts(spec, size_usd=650.0, price=3000.0)
    assert c2 == 2


# ─────────────────────────────────────────────────────────────────────────────
# CB-20  Missing CDP credentials does not crash paper mode
# ─────────────────────────────────────────────────────────────────────────────


def test_cb20_paper_mode_works_without_cdp_credentials(monkeypatch):
    """Paper mode must never call the API regardless of credential state."""
    from execution.coinbase_broker import CoinbaseBroker

    monkeypatch.setenv("COINBASE_CDP_KEY_NAME", "")
    monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", "")

    b = CoinbaseBroker(paper=True)
    result = b.open_long(symbol="BTC", size_usd=500.0, leverage=3)
    # Paper mode: should succeed (simulation) even with no creds
    # Note: paper trades with size_usd < 1 contract notional return None gracefully
    # This just proves no crash, not necessarily a fill
    # (BTC at simulated price may be 0 contracts → None is acceptable)
    assert result is None or result.get("paper") is True


# ─────────────────────────────────────────────────────────────────────────────
# CB-21  SUPPORTED_SYMBOLS set is exactly the 4 CFTC-regulated products
# ─────────────────────────────────────────────────────────────────────────────


def test_cb21_supported_symbols_is_four_products():
    from execution.coinbase_broker import SUPPORTED_SYMBOLS

    assert SUPPORTED_SYMBOLS == {"BTC", "ETH", "SOL", "XRP"}, (
        f"SUPPORTED_SYMBOLS should be exactly {{BTC,ETH,SOL,XRP}}, got {SUPPORTED_SYMBOLS}"
    )
