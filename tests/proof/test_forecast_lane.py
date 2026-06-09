"""
tests/proof/test_forecast_lane.py — Proof tests for the Kalshi forecast trading lane.

Coverage:
  1.  Schema: all 5 forecast tables created with correct columns + indexes
  2.  Upsert idempotency: upsert_market / upsert_contract are idempotent
  3.  Quote insertion and retrieval
  4.  Bar generation: all 5 required intervals (5m, 30m, 1h, 4h, 1d)
  5.  YES/NO pair join: omega_t and g_t computed correctly
  6.  Primitives: log_odds, entropy, overround, parity_gap, compute_q_hat
  7.  Weather-only lane: non-weather strategy path fails closed
  8.  Strategy determinism: weather override remains stable on same input
  11. Economics gate: correct veto for overround > MAX_OVERROUND
  12. Economics gate: correct veto for insufficient EV
  13. Economics gate: correct veto for concurrent cap
  14. Tiny-bankroll sizing: contracts_from_fraction respects all caps
  15. Novelty / non-economic markets fail-closed at discovery filter
  16. Dashboard forecast tab: render function importable + has correct name
  17. Dashboard app.py: single-page compatibility stub remains importable
  18. Active repo no longer carries the archived futures broker
  19. Validator: Kalshi-only section present in validate.py
  20. Promotion compatibility: init_forecast_db does not drop existing tables
"""

import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

# Ensure repo root is on path (conftest already inserts DASHBOARD_ROOT before ROOT;
# use conditional append to avoid displacing DASHBOARD_ROOT at index 0 and
# poisoning sys.modules['data'] during collection for other test files).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Isolated temporary SQLite DB with forecast tables initialised."""
    from forecast.db import init_forecast_db

    db = str(tmp_path / "test_forecast.db")
    init_forecast_db(db_path=db)
    return db


@pytest.fixture
def populated_db(tmp_db):
    """DB with one market, YES+NO contracts, and quotes inserted."""
    from forecast.db import insert_quote, upsert_contract, upsert_market

    now = datetime.now(timezone.utc).isoformat()
    mid = datetime.now(timezone.utc) + timedelta(days=7)
    expiry = mid.strftime("%Y%m%d")

    market_id = upsert_market(
        market_symbol="CPI",
        market_name="CPI >= 3.0% for March 2026",
        db_path=tmp_db,
    )
    yes_id = upsert_contract(
        market_id=market_id,
        local_symbol="CPI-2026M-C30",
        right="C",
        strike=3.0,
        last_trade_at=expiry,
        conid=12345,
        db_path=tmp_db,
    )
    no_id = upsert_contract(
        market_id=market_id,
        local_symbol="CPI-2026M-P30",
        right="P",
        strike=3.0,
        last_trade_at=expiry,
        conid=12346,
        db_path=tmp_db,
    )

    # Insert 10 quotes for each side, 60s apart
    base_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    for i in range(10):
        ts = (base_ts + timedelta(minutes=i)).isoformat()
        mid_yes = 0.55 + i * 0.005
        mid_no = 1.0 - mid_yes
        insert_quote(
            yes_id,
            ts,
            mid_yes - 0.01,
            mid_yes + 0.01,
            100,
            100,
            mid_yes,
            0.02,
            mid_yes,
            "YES",
            db_path=tmp_db,
        )
        insert_quote(
            no_id,
            ts,
            mid_no - 0.01,
            mid_no + 0.01,
            100,
            100,
            mid_no,
            0.02,
            mid_no,
            "NO",
            db_path=tmp_db,
        )

    return {
        "db": tmp_db,
        "market_id": market_id,
        "yes_id": yes_id,
        "no_id": no_id,
        "expiry": expiry,
        "strike": 3.0,
    }


# ── 1. Schema ──────────────────────────────────────────────────────────────────


def test_schema_all_tables_created(tmp_db):
    """All 5 forecast tables must exist after init_forecast_db()."""
    c = sqlite3.connect(tmp_db)
    tables = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    c.close()
    required = {
        "forecast_markets",
        "forecast_contracts",
        "forecast_quotes",
        "forecast_bars",
        "forecast_resolutions",
    }
    assert required.issubset(tables), f"Missing tables: {required - tables}"


def test_schema_indexes_created(tmp_db):
    """Required indexes must exist."""
    c = sqlite3.connect(tmp_db)
    indexes = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    c.close()
    assert "idx_forecast_quotes_cid_ts" in indexes
    assert "idx_forecast_bars_cid_int_ts" in indexes


# ── 2. Upsert idempotency ──────────────────────────────────────────────────────


def test_upsert_market_idempotent(tmp_db):
    """Calling upsert_market twice must not create duplicate rows."""
    from forecast.db import upsert_market

    id1 = upsert_market("NFP", "Nonfarm Payrolls", db_path=tmp_db)
    id2 = upsert_market("NFP", "Nonfarm Payrolls (updated)", db_path=tmp_db)
    assert id1 == id2, "Second upsert must return same id as first"

    c = sqlite3.connect(tmp_db)
    n = c.execute(
        "SELECT COUNT(*) FROM forecast_markets WHERE market_symbol='NFP'"
    ).fetchone()[0]
    c.close()
    assert n == 1, f"Expected 1 row, got {n}"


def test_upsert_contract_idempotent(tmp_db):
    """Calling upsert_contract twice must not create duplicate rows."""
    from forecast.db import upsert_contract, upsert_market

    mid = upsert_market("FOMC", "FOMC Rate Decision", db_path=tmp_db)
    expiry = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y%m%d")
    cid1 = upsert_contract(
        mid, "FOMC-C525", "C", 5.25, last_trade_at=expiry, db_path=tmp_db
    )
    cid2 = upsert_contract(
        mid, "FOMC-C525", "C", 5.25, last_trade_at=expiry, db_path=tmp_db
    )
    assert cid1 == cid2

    c = sqlite3.connect(tmp_db)
    n = c.execute(
        "SELECT COUNT(*) FROM forecast_contracts WHERE market_id=? AND right='C' AND strike=5.25",
        (mid,),
    ).fetchone()[0]
    c.close()
    assert n == 1


def test_upsert_contract_persists_contract_name(tmp_db):
    from forecast.db import get_active_contracts, upsert_contract, upsert_market

    mid = upsert_market("KXHIGHLAX-26JUN05", "LA High", db_path=tmp_db)
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d")
    upsert_contract(
        market_id=mid,
        local_symbol="KXHIGHLAX-26JUN05-B69.5",
        right="C",
        strike=69.5,
        contract_name="Will the high temp in LA be 69-70° on Jun 5, 2026?",
        last_trade_at=expiry,
        db_path=tmp_db,
    )

    rows = get_active_contracts(db_path=tmp_db)
    row = next(r for r in rows if r["local_symbol"] == "KXHIGHLAX-26JUN05-B69.5")
    assert row["contract_name"] == "Will the high temp in LA be 69-70° on Jun 5, 2026?"


def test_run_discovery_deactivates_missing_markets_and_contracts(tmp_db):
    from forecast.db import get_active_contracts, upsert_contract, upsert_market
    from forecast.discovery import run_discovery

    future_dt = datetime.now(timezone.utc) + timedelta(days=2)
    future_symbol_day = future_dt.strftime("%y%b%d").upper()

    old_market_id = upsert_market("KXHIGHOLD", "Old Legacy Weather", db_path=tmp_db)
    upsert_contract(
        market_id=old_market_id,
        local_symbol=f"KXHIGHOLD-{future_symbol_day}-B75.5",
        right="C",
        strike=75.5,
        last_trade_at=future_dt.strftime("%Y%m%d"),
        db_path=tmp_db,
    )

    mock_broker = MagicMock()
    mock_broker.discover_markets.return_value = [
        {
            "underlier": "KXHIGHNEW",
            "event_title": "New Weather Market",
            "local_symbol": f"KXHIGHNEW-{future_symbol_day}-B80.5",
            "conid": None,
            "right": "C",
            "strike": 80.5,
            "last_trade_at": future_dt.strftime("%Y%m%d"),
            "exchange": "KALSHI",
            "currency": "USD",
            "contract_name": "Will the high temp hit 81F?",
            "long_name": "Will the high temp hit 81F?",
            "category": "Weather",
            "side": "YES",
        }
    ]

    result = run_discovery(broker=mock_broker, db_path=tmp_db)
    rows = get_active_contracts(db_path=tmp_db)

    assert result["deactivated_contracts"] >= 1
    assert result["deactivated_markets"] >= 1
    assert [row["local_symbol"] for row in rows] == [f"KXHIGHNEW-{future_symbol_day}-B80.5"]


def test_deactivate_expired_contracts_retires_past_due_rows(tmp_db):
    from forecast.db import (
        deactivate_expired_contracts,
        get_active_contracts,
        upsert_contract,
        upsert_market,
    )

    market_id = upsert_market("KXHIGHPAST", "Past Due Market", db_path=tmp_db)
    upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHPAST-26JUN04-B80.5",
        right="C",
        strike=80.5,
        last_trade_at=(datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y%m%d %H:%M:%S"),
        db_path=tmp_db,
    )

    updated = deactivate_expired_contracts(db_path=tmp_db)

    assert updated == 1
    assert get_active_contracts(db_path=tmp_db) == []


# ── 3. Quote insertion + retrieval ────────────────────────────────────────────


def test_quote_insert_and_retrieve(populated_db):
    """Quotes inserted in fixture must be retrievable in time order."""
    from forecast.db import get_recent_quotes

    quotes = get_recent_quotes(
        populated_db["yes_id"], limit=20, db_path=populated_db["db"]
    )
    assert len(quotes) == 10, f"Expected 10 quotes, got {len(quotes)}"
    # Should be oldest-first (get_recent_quotes reverses)
    assert quotes[0]["ts"] < quotes[-1]["ts"], "Quotes should be time-ordered ascending"
    for q in quotes:
        assert q["mid"] is not None
        assert 0.0 < q["mid"] < 1.0


# ── 4. Bar generation across all 5 intervals ──────────────────────────────────


def test_bars_all_intervals_generated(populated_db):
    """build_bars_now must produce bars for all 5 required intervals."""
    from forecast.db import get_bars
    from forecast.quote_harvester import build_bars_now

    results = build_bars_now(populated_db["yes_id"], db_path=populated_db["db"])

    for interval in ("5m", "30m", "1h", "4h", "1d"):
        assert interval in results, f"Missing interval {interval} in results"
        bars = get_bars(
            populated_db["yes_id"], interval, limit=10, db_path=populated_db["db"]
        )
        assert len(bars) >= 1, f"No bars for interval {interval}"


def test_bars_ohlc_from_midpoint(populated_db):
    """Bar OHLC must be derived from midpoint, not from last/trade prices."""
    from forecast.db import get_bars
    from forecast.quote_harvester import build_bars_now

    build_bars_now(populated_db["yes_id"], db_path=populated_db["db"])
    bars = get_bars(populated_db["yes_id"], "5m", limit=10, db_path=populated_db["db"])
    assert bars, "No 5m bars found"
    b = bars[0]
    assert b["derived_from_quotes"] == 1, (
        "Bars must be derived from quotes, not trade prints"
    )
    # OHLC must be within [0, 1] for event contracts
    for field in ("o", "h", "l", "c"):
        assert b[field] is not None
        assert 0.0 < b[field] < 1.0, f"Bar {field}={b[field]} out of range [0,1]"
    # H >= O, C, L
    assert b["h"] >= b["l"], "Bar high must be >= low"


# ── 5. YES/NO pair join: omega_t and g_t ──────────────────────────────────────


def test_pair_join_omega_and_g(populated_db):
    """get_paired_quotes must compute omega_t and g_t from YES+NO quotes."""
    from forecast.quote_harvester import get_paired_quotes

    pair = get_paired_quotes(
        market_id=populated_db["market_id"],
        strike=populated_db["strike"],
        last_trade_at=populated_db["expiry"],
        db_path=populated_db["db"],
    )
    assert pair["yes_quote"] is not None
    assert pair["no_quote"] is not None
    assert pair["omega_t"] is not None, "omega_t must be computed"
    assert pair["g_t"] is not None, "g_t must be computed"
    # omega_t = ask_yes + ask_no - 1; with spread=0.02, ask = mid+0.01
    # so omega_t should be slightly positive (house has edge)
    assert isinstance(pair["omega_t"], float)
    assert isinstance(pair["g_t"], float)


# ── 6. Primitives: scalar math ────────────────────────────────────────────────


def test_primitives_log_odds_round_trip():
    """log_odds and logistic must be inverses."""
    from forecast.primitives import log_odds, logistic

    for p in [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99]:
        assert abs(logistic(log_odds(p)) - p) < 1e-9, f"Round-trip failed for p={p}"


def test_primitives_entropy_at_half():
    """H(0.5) = ln(2) ≈ 0.693 (maximum binary entropy)."""
    from forecast.primitives import entropy

    h = entropy(0.5)
    assert abs(h - math.log(2)) < 1e-9


def test_primitives_entropy_near_certainty():
    """H(p) → 0 as p → 0 or p → 1."""
    from forecast.primitives import entropy

    assert entropy(0.01) < 0.10
    assert entropy(0.99) < 0.10


def test_primitives_overround():
    """overround = ask_yes + ask_no - 1."""
    from forecast.primitives import overround

    assert abs(overround(0.55, 0.50) - 0.05) < 1e-9
    assert abs(overround(0.50, 0.50) - 0.00) < 1e-9


def test_primitives_parity_gap():
    """parity_gap = mid_yes + mid_no - 1."""
    from forecast.primitives import parity_gap

    assert abs(parity_gap(0.55, 0.47) - 0.02) < 1e-9
    assert abs(parity_gap(0.50, 0.50) - 0.00) < 1e-9


def test_primitives_q_hat_range():
    """compute_q_hat must always return a value in [CLIP_LO, CLIP_HI]."""
    from forecast.primitives import CLIP_HI, CLIP_LO, compute_q_hat

    for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
        q = compute_q_hat(p, sigma_t=0.1, ensemble_agreement=0.4, ml_bias=0.1)
        assert CLIP_LO <= q <= CLIP_HI, f"q_hat={q} out of range for p={p}"


# ── 7–8. Weather-only lane behavior ───────────────────────────────────────────


def _make_bars(probs: list[float]) -> list[dict]:
    """Build minimal bar dicts from a probability series."""
    bars = []
    for p in probs:
        bars.append({"c": p, "mid_mean": p, "o": p, "h": p, "l": p})
    return bars


def _make_quotes(
    mid_yes: float, mid_no: float, spread: float = 0.03
) -> tuple[dict, dict]:
    yes_q = {
        "bid": mid_yes - spread / 2,
        "ask": mid_yes + spread / 2,
        "mid": mid_yes,
        "spread": spread,
        "implied_prob": mid_yes,
    }
    no_q = {
        "bid": mid_no - spread / 2,
        "ask": mid_no + spread / 2,
        "mid": mid_no,
        "spread": spread,
        "implied_prob": mid_no,
    }
    return yes_q, no_q


def _make_contract(hours_out: float = 24.0, *, symbol: str = "CPI-C30") -> dict:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=hours_out)).strftime(
        "%Y%m%d"
    )
    return {
        "id": 1,
        "market_id": 1,
        "local_symbol": symbol,
        "right": "C",
        "strike": 3.0,
        "last_trade_at": expiry,
    }


def test_non_weather_contracts_fail_closed():
    from forecast.strategy_engine import evaluate_contract

    bars = _make_bars([0.40 + i * 0.02 for i in range(12)])
    yes_q, no_q = _make_quotes(0.60, 0.42)
    contract = _make_contract(36.0)

    result = evaluate_contract(
        contract, bars, bars, bars, bars, yes_q, no_q, bankroll=100.0
    )

    assert result is not None
    assert result.econ_approved is False
    assert result.veto_reason == "non_weather_contract_unsupported"


def test_weather_evaluation_is_stable_on_identical_inputs(monkeypatch):
    import forecast.strategy_engine as se

    weather = {
        "members_high": [80.0] * 31,
        "ecmwf": {"members_high": [80.0] * 31},
        "sigma_high": 0.8,
        "peak_tcdc": 5.0,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }
    monkeypatch.setattr(se, "get_weather_data", lambda ticker: weather)
    monkeypatch.setattr(se, "get_contract_weather_data", lambda ticker, **kwargs: weather)

    contract = _make_contract(36.0, symbol="KXHIGHNY-30JUN26-T75")
    bars = _make_bars([])
    yes_q, no_q = _make_quotes(0.58, 0.42)

    r1 = se.evaluate_contract(
        contract,
        bars,
        bars,
        bars,
        bars,
        yes_q,
        no_q,
        bankroll=100.0,
    )
    r2 = se.evaluate_contract(
        contract,
        bars,
        bars,
        bars,
        bars,
        yes_q,
        no_q,
        bankroll=100.0,
    )

    assert r1 is not None and r2 is not None
    assert r1.strategy_family == r2.strategy_family
    assert r1.side == r2.side
    assert abs(r1.q_hat - r2.q_hat) < 1e-9
    assert abs(r1.ev - r2.ev) < 1e-9


# ── 11–13. Economics gate ─────────────────────────────────────────────────────


def test_economics_gate_veto_overround():
    """Gate must veto when overround exceeds MAX_OVERROUND."""
    from forecast.strategy_engine import MAX_OVERROUND, _economics_gate

    approved, reason, _, _ = _economics_gate(
        ask_yes=0.60,
        ask_no=0.60,  # Ω = 0.20 + 0.60 - 1 = 0.20 → but sum = 1.20 → Ω = 0.20
        q_hat=0.60,
        omega_t=MAX_OVERROUND + 0.01,  # just above threshold
        g_t=0.0,
        h_t=0.65,
        sigma_t=0.05,
        spread=0.02,
        hours_to_resolution=24.0,
    )
    assert not approved
    assert "overround" in reason.lower()


def test_economics_gate_veto_insufficient_ev():
    """Gate must veto when both sides have EV below threshold."""
    from forecast.strategy_engine import EV_THRESHOLD, _economics_gate

    # q_hat = 0.52, ask_yes = 0.55 → EV_yes = 0.52 - 0.55 = -0.03 (negative)
    # q_hat = 0.52, ask_no  = 0.50 → EV_no = 0.48 - 0.50 = -0.02 (negative)
    approved, reason, ev_yes, ev_no = _economics_gate(
        ask_yes=0.55,
        ask_no=0.50,
        q_hat=0.52,
        omega_t=0.05,
        g_t=0.00,
        h_t=0.65,
        sigma_t=0.05,
        spread=0.02,
        hours_to_resolution=24.0,
    )
    assert not approved
    assert "ev" in reason.lower() or "insufficient" in reason.lower()


def test_economics_gate_veto_concurrent_cap():
    """Gate must veto when concurrent position cap is reached."""
    from forecast.strategy_engine import MAX_CONCURRENT_POSITIONS, _economics_gate

    # Provide enough EV to pass that check
    approved, reason, _, _ = _economics_gate(
        ask_yes=0.40,
        ask_no=0.45,
        q_hat=0.65,
        omega_t=0.05,
        g_t=0.00,
        h_t=0.65,
        sigma_t=0.05,
        spread=0.02,
        hours_to_resolution=24.0,
        open_positions_count=MAX_CONCURRENT_POSITIONS,  # at cap
    )
    assert not approved
    assert "concurrent" in reason.lower() or "cap" in reason.lower()


# ── 14. Tiny-bankroll sizing ──────────────────────────────────────────────────


def test_sizing_respects_per_event_cap():
    """contracts_from_fraction must not exceed per_event_cap_pct × bankroll."""
    from forecast.primitives import contracts_from_fraction

    # fraction=0.10, bankroll=$100, per_event_cap=10% → max $10 at risk
    # ask=0.10 and fee=0.07 → total cash per contract = $0.17 → max 58 shares
    n = contracts_from_fraction(
        fraction=0.10,
        bankroll=100.0,
        p_cost=0.10,
        per_event_cap_pct=0.10,
        deployed_pct=0.0,
        max_deployed_pct=0.35,
        fee_per_contract=0.07,
    )
    assert n == 58, f"Expected 58 contracts, got {n}"


def test_sizing_respects_deployment_cap():
    """contracts_from_fraction must return 0 when deployment cap is hit."""
    from forecast.primitives import contracts_from_fraction

    # Already at 35% deployed → no room left
    n = contracts_from_fraction(
        fraction=0.10,
        bankroll=100.0,
        p_cost=0.10,
        per_event_cap_pct=0.10,
        deployed_pct=0.35,
        max_deployed_pct=0.35,
        fee_per_contract=0.07,
    )
    assert n == 0, f"Expected 0 contracts when deployed cap hit, got {n}"


def test_sizing_kelly_cap():
    """fractional_kelly_fraction must never exceed kelly_cap."""
    from forecast.primitives import fractional_kelly_fraction

    # Edge: q=0.95, p_cost=0.10 → raw Kelly = (0.95-0.10)/0.90 = 0.944
    f = fractional_kelly_fraction(q_side=0.95, p_cost=0.10, kelly_cap=0.10)
    assert f <= 0.10, f"Kelly fraction {f} exceeds cap 0.10"


def test_sizing_zero_when_no_edge():
    """fractional_kelly_fraction must return 0 when q <= p_cost."""
    from forecast.primitives import fractional_kelly_fraction

    f = fractional_kelly_fraction(q_side=0.40, p_cost=0.50)
    assert f == 0.0, f"Expected 0 when q < p_cost, got {f}"


# ── 15. Weather market filter fail-closed ─────────────────────────────────────


def test_discovery_rejects_non_weather_markets():
    """_is_weather_market must return False for non-weather subjects."""
    from execution.kalshi_broker import _is_weather_market

    assert not _is_weather_market("SUPERBOWL", "Super Bowl Winner 2026", "sports")
    assert not _is_weather_market("ELECTION", "US Presidential Election", "politics")
    assert not _is_weather_market("OSCARS", "Best Picture Oscar 2026", "entertainment")


def test_discovery_accepts_weather_markets():
    """_is_weather_market must return True for rain, temperature, and storm markets."""
    from execution.kalshi_broker import _is_weather_market

    assert _is_weather_market("KXRAINNY", "Will it rain in NYC?", "weather")
    assert _is_weather_market("KXHIGHCHI", "Chicago temperature above 80?", "")
    assert _is_weather_market("LANDFALL", "Will the storm make landfall?", "climate")


def test_discovery_rejects_contracts_beyond_live_120h_horizon():
    from forecast.discovery import _rank_contracts

    too_far = {
        "last_trade_at": (datetime.now(timezone.utc) + timedelta(hours=125)).strftime(
            "%Y%m%d %H:%M:%S"
        ),
        "contract_name": "Will NYC high exceed 80?",
        "market_name": "NYC high temperature",
        "underlier": "KXHIGHNY",
        "market_symbol": "KXHIGHNY",
    }

    ranked = _rank_contracts([too_far])
    assert ranked == [], "Discovery must not admit weather contracts beyond the live 120h horizon."


# ── 16–17. Dashboard alignment ────────────────────────────────────────────────


def test_dashboard_forecast_widget_importable():
    """render_forecast_trading must be importable from the dashboard widgets."""
    from dashboard.widgets.forecast.forecast_dashboard import render_forecast_trading

    assert callable(render_forecast_trading)


def test_dashboard_app_tab_structure():
    """dashboard/app.py is single-page (v18.15+) — bot-reasoning-first, no tab structure."""
    app_path = os.path.join(_ROOT, "dashboard", "app.py")
    assert os.path.exists(app_path), "dashboard/app.py not found"
    src = open(app_path).read()
    assert "get_symbol_grid" in src, "single-page dashboard must call get_symbol_grid"
    assert "get_bot_pulse" in src, "single-page dashboard must call get_bot_pulse"
    assert "bot_state" in src, "single-page dashboard must import bot_state"


# ── 18. Active-repo purity ────────────────────────────────────────────────────


def test_mes_archival_lane_not_active():
    """FUTURES_LANE_ACTIVE must be absent or False in the active Kalshi repo."""
    import config

    active = getattr(config, "FUTURES_LANE_ACTIVE", False)
    assert not active, "FUTURES_LANE_ACTIVE should remain disabled in the active repo"


def test_legacy_futures_broker_removed_from_active_repo():
    """execution/ibkr_broker.py should not remain in the active Kalshi repo."""
    broker_path = os.path.join(_ROOT, "execution", "ibkr_broker.py")
    assert not os.path.exists(broker_path), "ibkr_broker.py should be archived out of the active repo"


# ── 19. Validator ─────────────────────────────────────────────────────────────


def test_validator_has_kalshi_section():
    """Validator implementation must describe the Kalshi-only runtime checks."""
    wrapper_path = os.path.join(_ROOT, "scripts", "validate.py")
    body_path = os.path.join(_ROOT, "scripts", "validate_body.py")
    assert os.path.exists(wrapper_path), "validate.py not found"
    assert os.path.exists(body_path), "validate_body.py not found"
    src = open(body_path).read()
    assert "Kalshi-only preflight validator" in src
    assert "--- Kalshi Lane ---" in src
    assert "KALSHI_API_KEY_ID" in src
    assert "FORECAST_AUTONOMOUS_ENABLED" in src
    assert "SHADOW_EXECUTION" in src


# ── 20. Promotion compatibility ───────────────────────────────────────────────


def test_init_forecast_db_does_not_drop_existing_tables(tmp_db):
    """Calling init_forecast_db twice must not lose data."""
    from forecast.db import init_forecast_db, upsert_market

    upsert_market("CPI", "CPI Test", db_path=tmp_db)

    # Second call (simulates restart)
    init_forecast_db(db_path=tmp_db)

    c = sqlite3.connect(tmp_db)
    n = c.execute(
        "SELECT COUNT(*) FROM forecast_markets WHERE market_symbol='CPI'"
    ).fetchone()[0]
    c.close()
    assert n == 1, "Re-initialising DB must not drop existing market rows"


def test_forecast_db_coexists_with_trades_table(tmp_path):
    """Forecast tables must coexist with the existing trades table without conflict."""
    from forecast.db import init_forecast_db

    db = str(tmp_path / "combined.db")
    # Create trades table first (simulating existing DB)
    c = sqlite3.connect(db)
    c.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, action TEXT, qty REAL, price REAL,
            pnl_usd REAL DEFAULT 0, paper INTEGER DEFAULT 1
        )"""
    )
    c.execute(
        "INSERT INTO trades (ts, symbol, action, qty, price) VALUES ('2026-01-01', 'BTC', 'BUY', 1, 50000)"
    )
    c.commit()
    c.close()

    # Now init forecast tables into the same DB
    init_forecast_db(db_path=db)

    c = sqlite3.connect(db)
    # trades row must still be there
    n_trades = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    # forecast tables must now also exist
    tables = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    c.close()

    assert n_trades == 1, "Existing trades row must not be lost"
    assert "forecast_markets" in tables
    assert "forecast_contracts" in tables


# ── 21-28. Forecast dashboard truth-layer tests (v15.9) ───────────────────────


def test_forecast_health_uses_lane_runtime_state_as_primary_truth(tmp_path):
    """
    get_forecast_health() must read lane_runtime_state.active as the primary
    source of lane_started truth when that table is available.
    It must NOT solely rely on system_events ForecastRunner counts.
    """
    import importlib

    # Build a DB with forecast tables + lane_runtime_state but ZERO system_events
    db = str(tmp_path / "health_test.db")
    from forecast.db import init_forecast_db

    init_forecast_db(db_path=db)

    c = sqlite3.connect(db)
    # Create lane_runtime_state table and mark forecast as active
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lane_runtime_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT UNIQUE,
            active INTEGER DEFAULT 0,
            last_heartbeat_at TEXT,
            readiness_state TEXT
        );
        INSERT INTO lane_runtime_state (lane_id, active, last_heartbeat_at, readiness_state)
        VALUES ('forecast', 1, datetime('now'), 'NO_TRADABLE_CONTRACTS_RIGHT_NOW');
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, level TEXT, source TEXT, message TEXT
        );
        -- NO ForecastRunner events inserted
    """)
    c.commit()
    c.close()

    # Patch DB_PATH so the module uses our tmp DB
    import dashboard.data.forecast as _fc_mod

    orig_path = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        result = _fc_mod.get_forecast_health()
    finally:
        _fc_mod.DB_PATH = orig_path

    assert result["lane_started"] is True, (
        "get_forecast_health() must return lane_started=True when lane_runtime_state "
        "shows active=1, even if there are zero ForecastRunner system_events. "
        "The primary truth source must be lane_runtime_state."
    )


def test_forecast_health_falls_back_to_system_events_when_no_runtime_table(tmp_path):
    """
    When lane_runtime_state table does not exist, get_forecast_health() must
    fall back to system_events ForecastRunner count.
    """
    db = str(tmp_path / "no_runtime.db")
    from forecast.db import init_forecast_db

    init_forecast_db(db_path=db)

    c = sqlite3.connect(db)
    # Add system_events with a recent ForecastRunner entry (no lane_runtime_state)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, level TEXT, source TEXT, message TEXT
        );
        INSERT INTO system_events (ts, level, source, message)
        VALUES (datetime('now'), 'INFO', 'ForecastRunner', 'test event');
    """)
    c.commit()
    c.close()

    import dashboard.data.forecast as _fc_mod

    orig_path = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        result = _fc_mod.get_forecast_health()
    finally:
        _fc_mod.DB_PATH = orig_path

    assert result["lane_started"] is True, (
        "get_forecast_health() must fall back to system_events when "
        "lane_runtime_state table does not exist."
    )


def test_forecast_health_system_events_uses_normalized_timestamp(tmp_path):
    """
    The system_events fallback must use datetime(replace(substr(ts,1,19),'T',' '))
    for timestamp comparison, not raw ts >= ? (which fails for ISO T-separator timestamps).
    """
    db = str(tmp_path / "ts_norm.db")
    from forecast.db import init_forecast_db

    init_forecast_db(db_path=db)

    c = sqlite3.connect(db)
    # Insert a ForecastRunner event with ISO T-separator timestamp from 30 min ago
    import datetime as _dtt

    ts_iso = (
        _dtt.datetime.now(_dtt.timezone.utc) - _dtt.timedelta(minutes=30)
    ).strftime("%Y-%m-%dT%H:%M:%S")  # ISO with T, no timezone suffix
    c.executescript(f"""
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, level TEXT, source TEXT, message TEXT
        );
        INSERT INTO system_events (ts, level, source, message)
        VALUES ('{ts_iso}', 'INFO', 'ForecastRunner', 'iso ts test');
    """)
    c.commit()
    c.close()

    import dashboard.data.forecast as _fc_mod

    orig_path = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        result = _fc_mod.get_forecast_health()
    finally:
        _fc_mod.DB_PATH = orig_path

    # The event is 30 min old — within the 2h window — must be found
    assert result["lane_started"] is True, (
        "get_forecast_health() fallback must find ISO T-separator timestamps within 2h "
        "using datetime(replace(substr(ts,1,19),'T',' ')) normalization. "
        "Raw `ts >= datetime('now','-2 hours')` fails for space-vs-T separator."
    )


def test_forecast_readiness_active_lane_with_no_contracts(tmp_path):
    """
    When lane_runtime_state shows active=1 and underliers exist but contracts=0,
    get_forecast_readiness() must return NO_TRADABLE_CONTRACTS_RIGHT_NOW —
    NOT LANE_NOT_STARTED.
    """
    db = str(tmp_path / "readiness_test.db")
    from forecast.db import init_forecast_db, upsert_market

    init_forecast_db(db_path=db)
    upsert_market("CPI", "CPI Test Market", db_path=db)  # underlier with no contracts

    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lane_runtime_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT UNIQUE,
            active INTEGER DEFAULT 0,
            last_heartbeat_at TEXT,
            readiness_state TEXT
        );
        INSERT INTO lane_runtime_state (lane_id, active, last_heartbeat_at, readiness_state)
        VALUES ('forecast', 1, datetime('now'), 'NO_TRADABLE_CONTRACTS_RIGHT_NOW');
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY, ts TEXT, level TEXT, source TEXT, message TEXT
        );
    """)
    c.commit()
    c.close()

    import dashboard.data.forecast as _fc_mod

    orig = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        r = _fc_mod.get_forecast_readiness()
    finally:
        _fc_mod.DB_PATH = orig

    assert r["lane_state"] == _fc_mod.NO_TRADABLE_CONTRACTS_RIGHT_NOW, (
        f"Expected NO_TRADABLE_CONTRACTS_RIGHT_NOW, got {r['lane_state']}. "
        "When runtime state shows active=1 and underliers exist but contracts=0, "
        "the readiness state must reflect that — not LANE_NOT_STARTED."
    )
    # Must not say the lane is not running
    lane_not_started_checks = [
        ch
        for ch in r.get("checks", [])
        if ch.get("status") != "PASS" and "not started" in ch.get("detail", "").lower()
    ]
    assert not lane_not_started_checks, (
        "Readiness checks must not report the lane as 'not started' when "
        "runtime state shows active=1. Got problematic checks: "
        + str(lane_not_started_checks)
    )


def test_forecast_readiness_zero_state_returns_useful_info(tmp_path):
    """
    In full zero-state (no contracts, no quotes, no bars, no trades),
    get_forecast_readiness() must still return useful non-empty checks
    and a lane_state that is not OPERATIONAL or LANE_NOT_STARTED
    (assuming the lane is alive per runtime state).
    """
    db = str(tmp_path / "zero_state.db")
    from forecast.db import init_forecast_db, upsert_market

    init_forecast_db(db_path=db)
    upsert_market("NFP", "NFP Jobs Test", db_path=db)

    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lane_runtime_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT UNIQUE,
            active INTEGER DEFAULT 0,
            last_heartbeat_at TEXT,
            readiness_state TEXT
        );
        INSERT INTO lane_runtime_state (lane_id, active, last_heartbeat_at, readiness_state)
        VALUES ('forecast', 1, datetime('now'), 'NO_TRADABLE_CONTRACTS_RIGHT_NOW');
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY, ts TEXT, level TEXT, source TEXT, message TEXT
        );
    """)
    c.commit()
    c.close()

    import dashboard.data.forecast as _fc_mod

    orig = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        r = _fc_mod.get_forecast_readiness()
    finally:
        _fc_mod.DB_PATH = orig

    assert r["lane_state"] != _fc_mod.OPERATIONAL, (
        "Zero-state must not report OPERATIONAL"
    )
    assert r["lane_state"] != _fc_mod.LANE_NOT_STARTED, (
        "Zero-state with active runtime must not report LANE_NOT_STARTED"
    )
    assert len(r.get("checks", [])) > 0, (
        "Readiness checks must be non-empty even in zero-state"
    )


def test_validate_forecast_lane_check_references_lane_runtime_state():
    """
    validator implementation Forecast lane active check must reference lane_runtime_state
    (not solely ForecastRunner system_events) as primary truth.
    """
    validate_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "validate_body.py",
    )
    with open(validate_path, encoding="utf-8") as f:
        src = f.read()

    assert "lane_runtime_state" in src, (
        "validate_body.py must reference lane_runtime_state in its forecast-lane check. "
        "Relying solely on ForecastRunner system_events is incorrect — events may be "
        "absent even when the lane is running (race at startup, fresh restart, etc.)."
    )


def test_forecast_dashboard_widget_uses_operational_funnel():
    """
    forecast_dashboard.py must render an 'Operational Funnel' section that
    shows pipeline stages from lane-alive through to trades.
    """
    widget_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "dashboard",
        "widgets",
        "forecast",
        "forecast_dashboard.py",
    )
    with open(widget_path, encoding="utf-8") as f:
        src = f.read()

    assert "Operational Funnel" in src or "funnel" in src.lower(), (
        "forecast_dashboard.py must include an operational funnel section "
        "showing the pipeline from lane-alive to trades"
    )
    # Must handle zero-state without crashing
    assert (
        "No forecast trades" in src
        or "no trades" in src.lower()
        or "total_trades" in src
    ), "forecast_dashboard.py must handle zero-state (no trades yet) gracefully"


def test_forecast_health_exposes_heartbeat_at(tmp_path):
    """
    get_forecast_health() must expose lane_heartbeat_at from lane_runtime_state
    so the dashboard can display a heartbeat age without an extra query.
    """
    db = str(tmp_path / "hb_test.db")
    from forecast.db import init_forecast_db

    init_forecast_db(db_path=db)
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lane_runtime_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lane_id TEXT UNIQUE,
            active INTEGER DEFAULT 0,
            last_heartbeat_at TEXT,
            readiness_state TEXT
        );
        INSERT INTO lane_runtime_state (lane_id, active, last_heartbeat_at, readiness_state)
        VALUES ('forecast', 1, '2026-04-16T03:00:00+00:00', 'NO_TRADABLE_CONTRACTS_RIGHT_NOW');
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY, ts TEXT, level TEXT, source TEXT, message TEXT
        );
    """)
    c.commit()
    c.close()

    import dashboard.data.forecast as _fc_mod

    orig = _fc_mod.DB_PATH
    _fc_mod.DB_PATH = db
    try:
        result = _fc_mod.get_forecast_health()
    finally:
        _fc_mod.DB_PATH = orig

    assert "lane_heartbeat_at" in result, (
        "get_forecast_health() must include lane_heartbeat_at in its return dict"
    )
    assert result["lane_heartbeat_at"] == "2026-04-16T03:00:00+00:00", (
        "lane_heartbeat_at must match the value from lane_runtime_state"
    )


def test_kalshi_hub_exposure_cap_helper_uses_new_floor_and_pct():
    from config import get_kalshi_hub_exposure_cap

    assert get_kalshi_hub_exposure_cap(100.0) == 40.0
    assert get_kalshi_hub_exposure_cap(144.31) == pytest.approx(43.293)
    assert get_kalshi_hub_exposure_cap(200.0) == 60.0


def test_strategy_engine_family_cap_allows_four_existing_positions(monkeypatch):
    from forecast.market_snapshot import MarketSnapshot
    import forecast.strategy_engine as se

    snapshot = MarketSnapshot(
        market_id=1,
        ticker="KXLOWTLV-99JAN01-B71.5",
        contract_name="Las Vegas Low",
        strike=71.5,
        last_trade_at="20990101",
        resolution_at="2099-01-01T00:00:00Z",
        yes_contract={"local_symbol": "KXLOWTLV-99JAN01-B71.5", "contract_name": "Las Vegas Low"},
        no_contract={"local_symbol": "KXLOWTLV-99JAN01-T72", "contract_name": "Las Vegas Low"},
        yes_quote={"ask": 0.42, "bid": 0.40, "mid": 0.41},
        no_quote={"ask": 0.58, "bid": 0.56, "mid": 0.57},
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
    )

    called = {"evaluate": False}

    def _stub_evaluate_contract(**kwargs):
        called["evaluate"] = True
        return se.StrategyResult(
            strategy_family="stub",
            side="NONE",
            q_hat=0.0,
            ev=0.0,
            ev_yes=0.0,
            ev_no=0.0,
            confidence=0.0,
            uncertainty_penalty=0.0,
            econ_approved=False,
            veto_reason="downstream_stub",
            position_fraction=0.0,
            position_contracts=0,
            top_factors=[],
            hours_to_resolution=24.0,
        )

    monkeypatch.setattr(se, "evaluate_contract", _stub_evaluate_contract)

    results = se.evaluate_market_snapshots(
        snapshots=[snapshot],
        bankroll=200.0,
        open_event_families={"KXLOWTLV": 4},
        open_positions=[],
    )

    assert called["evaluate"] is True
    assert results[0]["result"].veto_reason == "downstream_stub"


def test_strategy_engine_family_cap_blocks_fifth_existing_position(monkeypatch):
    from forecast.market_snapshot import MarketSnapshot
    import forecast.strategy_engine as se

    snapshot = MarketSnapshot(
        market_id=1,
        ticker="KXLOWTLV-99JAN01-B71.5",
        contract_name="Las Vegas Low",
        strike=71.5,
        last_trade_at="20990101",
        resolution_at="2099-01-01T00:00:00Z",
        yes_contract={"local_symbol": "KXLOWTLV-99JAN01-B71.5", "contract_name": "Las Vegas Low"},
        no_contract={"local_symbol": "KXLOWTLV-99JAN01-T72", "contract_name": "Las Vegas Low"},
        yes_quote={"ask": 0.42, "bid": 0.40, "mid": 0.41},
        no_quote={"ask": 0.58, "bid": 0.56, "mid": 0.57},
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
    )

    def _should_not_run(**kwargs):
        raise AssertionError("family-cap veto should happen before evaluate_contract")

    monkeypatch.setattr(se, "evaluate_contract", _should_not_run)

    results = se.evaluate_market_snapshots(
        snapshots=[snapshot],
        bankroll=200.0,
        open_event_families={"KXLOWTLV": 5},
        open_positions=[],
    )

    assert results[0]["result"].veto_reason == "same_event_family_cap_reached"


def test_strategy_engine_does_not_preconsume_family_capacity_during_ranking(monkeypatch):
    from forecast.market_snapshot import MarketSnapshot
    import forecast.strategy_engine as se

    original_cap = se.KALSHI_SAME_EVENT_FAMILY_CAP
    monkeypatch.setattr(se, "KALSHI_SAME_EVENT_FAMILY_CAP", 1, raising=False)

    snapshots = [
        MarketSnapshot(
            market_id=1,
            ticker="KXLOWTLV-99JAN01-B71.5",
            contract_name="Las Vegas Low A",
            strike=71.5,
            last_trade_at="20990101",
            resolution_at="2099-01-01T00:00:00Z",
            yes_contract={"local_symbol": "KXLOWTLV-99JAN01-B71.5", "contract_name": "Las Vegas Low A"},
            no_contract={"local_symbol": "KXLOWTLV-99JAN01-T72", "contract_name": "Las Vegas Low A"},
            yes_quote={"ask": 0.42, "bid": 0.40, "mid": 0.41},
            no_quote={"ask": 0.58, "bid": 0.56, "mid": 0.57},
            bars_5m=[],
            bars_30m=[],
            bars_1h=[],
            bars_4h=[],
        ),
        MarketSnapshot(
            market_id=2,
            ticker="KXLOWTLV-99JAN01-B73.5",
            contract_name="Las Vegas Low B",
            strike=73.5,
            last_trade_at="20990101",
            resolution_at="2099-01-01T00:00:00Z",
            yes_contract={"local_symbol": "KXLOWTLV-99JAN01-B73.5", "contract_name": "Las Vegas Low B"},
            no_contract={"local_symbol": "KXLOWTLV-99JAN01-T74", "contract_name": "Las Vegas Low B"},
            yes_quote={"ask": 0.42, "bid": 0.40, "mid": 0.41},
            no_quote={"ask": 0.58, "bid": 0.56, "mid": 0.57},
            bars_5m=[],
            bars_30m=[],
            bars_1h=[],
            bars_4h=[],
        ),
    ]

    call_count = {"value": 0}

    def _stub_evaluate_contract(**kwargs):
        call_count["value"] += 1
        return se.StrategyResult(
            strategy_family="stub",
            side="YES",
            q_hat=0.75,
            ev=0.10 if call_count["value"] == 1 else 0.20,
            ev_yes=0.10 if call_count["value"] == 1 else 0.20,
            ev_no=-1.0,
            confidence=0.90 if call_count["value"] == 1 else 0.95,
            uncertainty_penalty=0.0,
            econ_approved=True,
            veto_reason="",
            position_fraction=0.02,
            position_contracts=1,
            top_factors=[],
            hours_to_resolution=24.0,
            ask_yes=0.42,
            ask_no=0.58,
        )

    monkeypatch.setattr(se, "evaluate_contract", _stub_evaluate_contract)

    results = se.evaluate_market_snapshots(
        snapshots=snapshots,
        bankroll=200.0,
        open_event_families={},
        open_positions=[],
    )

    monkeypatch.setattr(se, "KALSHI_SAME_EVENT_FAMILY_CAP", original_cap, raising=False)

    assert len(results) == 2
    assert results[0]["result"].econ_approved is True
    assert results[1]["result"].econ_approved is True
    assert results[0]["rank_score"] > results[1]["rank_score"]


def test_strategy_engine_hub_cap_uses_thirty_percent_with_forty_dollar_floor(monkeypatch):
    from forecast.market_snapshot import MarketSnapshot
    import forecast.strategy_engine as se

    snapshot = MarketSnapshot(
        market_id=2,
        ticker="KXHIGHSEA-99JAN01-B61.5",
        contract_name="Seattle High",
        strike=61.5,
        last_trade_at="20990101",
        resolution_at="2099-01-01T00:00:00Z",
        yes_contract={"local_symbol": "KXHIGHSEA-99JAN01-B61.5", "contract_name": "Seattle High"},
        no_contract={"local_symbol": "KXHIGHSEA-99JAN01-T62", "contract_name": "Seattle High"},
        yes_quote={"ask": 0.42, "bid": 0.40, "mid": 0.41},
        no_quote={"ask": 0.58, "bid": 0.56, "mid": 0.57},
        bars_5m=[],
        bars_30m=[],
        bars_1h=[],
        bars_4h=[],
    )

    def _mock_eval(*args, **kwargs):
        from forecast.strategy_engine import StrategyResult
        return StrategyResult(
            strategy_family="weather_ensemble",
            side="YES",
            q_hat=0.80,
            ev=0.20,
            ev_yes=0.20,
            ev_no=-0.10,
            confidence=0.80,
            uncertainty_penalty=0.0,
            econ_approved=True,
            veto_reason="",
            position_fraction=0.10,
            position_contracts=100,
            top_factors=[],
            hours_to_resolution=12.0,
        )

    monkeypatch.setattr(se, "evaluate_contract", _mock_eval)

    results = se.evaluate_market_snapshots(
        snapshots=[snapshot],
        bankroll=100.0,
        open_event_families={},
        open_positions=[
            {
                "local_symbol": "KXHIGHLAX-99JAN01-B69.5",
                "qty": 100,
                "entry_price": 0.50,
                "side": "YES",
            }
        ],
    )

    assert results[0]["result"].veto_reason == "hub_exposure_cap_reached (114.0/40.0)"
