"""
tests/proof/test_forecast_lane.py — Proof tests for the ForecastEx trading lane.

Coverage:
  1.  Schema: all 5 forecast tables created with correct columns + indexes
  2.  Upsert idempotency: upsert_market / upsert_contract are idempotent
  3.  Quote insertion and retrieval
  4.  Bar generation: all 5 required intervals (5m, 30m, 1h, 4h, 1d)
  5.  YES/NO pair join: omega_t and g_t computed correctly
  6.  Primitives: log_odds, entropy, overround, parity_gap, compute_q_hat
  7.  Primitives: velocity, acceleration, log_odds_vol, z_score
  8.  Strategy determinism: continuation (stable output on same input)
  9.  Strategy determinism: mean_reversion (stable output on same input)
  10. Strategy determinism: late_repricing (stable output on same input)
  11. Economics gate: correct veto for overround > MAX_OVERROUND
  12. Economics gate: correct veto for insufficient EV
  13. Economics gate: correct veto for concurrent cap
  14. Tiny-bankroll sizing: contracts_from_fraction respects all caps
  15. Novelty / non-economic markets fail-closed at discovery filter
  16. Dashboard forecast tab: render function importable + has correct name
  17. Dashboard app.py: FORECAST TRADING tab and ARCHIVED FUTURES (MES) tab present
  18. MES archival: FUTURES_LANE_ACTIVE is absent or False
  19. Validator: ForecastEx section present in validate.py
  20. Promotion compatibility: init_forecast_db does not drop existing tables
"""

import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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
        q = compute_q_hat(p, v_1h=0.1, a_30m=0.05, sigma_t=0.1)
        assert CLIP_LO <= q <= CLIP_HI, f"q_hat={q} out of range for p={p}"


# ── 7. Primitives: series math ────────────────────────────────────────────────


def test_primitives_velocity_direction():
    """velocity must be positive when series is rising."""
    from forecast.primitives import log_odds, velocity

    rising = [log_odds(p) for p in [0.40, 0.45, 0.50, 0.55, 0.60]]
    assert velocity(rising) > 0, "velocity should be positive for rising series"

    falling = [log_odds(p) for p in [0.60, 0.55, 0.50, 0.45, 0.40]]
    assert velocity(falling) < 0, "velocity should be negative for falling series"


def test_primitives_acceleration_sign():
    """acceleration is positive when velocity is increasing."""
    from forecast.primitives import log_odds, acceleration

    # Accelerating upward: each step bigger
    accel = [log_odds(p) for p in [0.40, 0.42, 0.46, 0.52, 0.60]]
    assert acceleration(accel) > 0


def test_primitives_vol_nonnegative():
    """log_odds_vol must always be non-negative."""
    from forecast.primitives import log_odds, log_odds_vol

    xs = [log_odds(p) for p in [0.3, 0.5, 0.4, 0.6, 0.35, 0.55]]
    assert log_odds_vol(xs) >= 0


def test_primitives_z_score_zero_for_flat_series():
    """z_score should be near 0 for a constant series."""
    from forecast.primitives import log_odds, z_score

    flat = [log_odds(0.5)] * 25
    z = z_score(flat)
    assert abs(z) < 1e-6, f"z_score for flat series should be 0, got {z}"


# ── 8–10. Strategy determinism ────────────────────────────────────────────────


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


def _make_contract(hours_out: float = 24.0) -> dict:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=hours_out)).strftime(
        "%Y%m%d"
    )
    return {
        "id": 1,
        "market_id": 1,
        "local_symbol": "CPI-C30",
        "right": "C",
        "strike": 3.0,
        "last_trade_at": expiry,
    }


def test_strategy_continuation_deterministic():
    """continuation must return the same result on identical inputs."""
    from forecast.strategy_engine import evaluate_contract

    # Rising trend: probs increasing over last 12 bars on both 1h and 4h
    probs_up = [0.40 + i * 0.02 for i in range(12)]
    bars = _make_bars(probs_up)
    yes_q, no_q = _make_quotes(0.60, 0.42)
    contract = _make_contract(36.0)

    r1 = evaluate_contract(
        contract, bars, bars, bars, bars, yes_q, no_q, bankroll=100.0
    )
    r2 = evaluate_contract(
        contract, bars, bars, bars, bars, yes_q, no_q, bankroll=100.0
    )

    # Results must be identical (deterministic)
    if r1 is not None and r2 is not None:
        assert r1.strategy_family == r2.strategy_family
        assert r1.side == r2.side
        assert abs(r1.q_hat - r2.q_hat) < 1e-9
        assert abs(r1.ev - r2.ev) < 1e-9


def test_strategy_mean_reversion_detects_overextension():
    """mean_reversion must trigger on a series with |z_t| >= MIN_ABS_Z_MEAN_REVERSION."""
    from forecast.strategy_engine import _strategy_mean_reversion
    from forecast.primitives import log_odds

    # Spike series: flat then sudden jump (creates high z_t)
    xs_flat = [log_odds(0.50)] * 18
    xs_spike = [log_odds(0.50 + i * 0.04) for i in range(4)]
    full_series_probs = [0.50] * 18 + [0.52, 0.56, 0.64, 0.76]

    bars = _make_bars(full_series_probs)
    features_mock = {
        "x_t": log_odds(0.76),
        "v_1h": 0.30,
        "v_4h": 0.15,
        "a_30m": -0.05,  # deceleration (rolling over)
        "sigma_t": 0.10,
        "h_t": 0.60,
        "omega_t": 0.05,
        "g_t": 0.01,
        "z_t": 2.1,  # overextended
        "latest_prob": 0.76,
        "velocity_30m": 0.1,
    }
    passes, side, conf, factors = _strategy_mean_reversion(
        features_mock, hours_to_res=24.0
    )
    assert passes, (
        f"mean_reversion should fire on overextended series, got factors={factors}"
    )
    assert side == "NO", f"Overextended toward YES → should fade to NO, got {side}"


def test_strategy_late_repricing_window():
    """late_repricing only fires within 2h–72h of resolution."""
    from forecast.strategy_engine import _strategy_late_repricing

    features = {
        "x_t": 0.2,
        "v_1h": 0.1,
        "v_4h": 0.25,
        "a_30m": 0.0,
        "sigma_t": 0.05,
        "h_t": 0.65,
        "omega_t": 0.04,
        "g_t": 0.01,
        "z_t": 0.3,
        "latest_prob": 0.58,
        "velocity_30m": 0.1,
    }

    # Inside window → should pass
    passes_in, _, _, _ = _strategy_late_repricing(features, hours_to_res=24.0)
    assert passes_in, "late_repricing should fire at 24h to resolution"

    # Outside window (>72h) → must not pass
    passes_out, _, _, _ = _strategy_late_repricing(features, hours_to_res=100.0)
    assert not passes_out, "late_repricing must NOT fire beyond 72h window"

    # Too close (<2h) → must not pass
    passes_close, _, _, _ = _strategy_late_repricing(features, hours_to_res=1.0)
    assert not passes_close, "late_repricing must NOT fire within 2h of resolution"


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
    # ask=0.10 → cost_per_contract = $10 → max 1 contract
    n = contracts_from_fraction(
        fraction=0.10,
        bankroll=100.0,
        p_cost=0.10,
        per_event_cap_pct=0.10,
        deployed_pct=0.0,
        max_deployed_pct=0.35,
    )
    assert n == 1, f"Expected 1 contract, got {n}"


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


# ── 15. Non-economic markets fail-closed ──────────────────────────────────────


def test_discovery_rejects_non_economic_markets():
    """_is_economic_market must return False for sports, politics, entertainment."""
    from execution.forecastex_broker import _is_economic_market

    assert not _is_economic_market("SUPERBOWL", "Super Bowl Winner 2026", "sports")
    assert not _is_economic_market("ELECTION", "US Presidential Election", "politics")
    assert not _is_economic_market("OSCARS", "Best Picture Oscar 2026", "entertainment")


def test_discovery_accepts_economic_markets():
    """_is_economic_market must return True for Fed, CPI, payroll, unemployment."""
    from execution.forecastex_broker import _is_economic_market

    assert _is_economic_market("CPI", "CPI YoY >= 3.0% for March", "economics")
    assert _is_economic_market(
        "FOMC", "Fed Funds Rate >= 5.25% after March meeting", ""
    )
    assert _is_economic_market(
        "NFP", "Nonfarm Payrolls >= 200K for February", "employment"
    )
    assert _is_economic_market("UNRATE", "Unemployment Rate >= 4.0%", "macro")


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


# ── 18. MES archival ──────────────────────────────────────────────────────────


def test_mes_archival_lane_not_active():
    """FUTURES_LANE_ACTIVE must be absent or False — MES is dormant."""
    import config

    active = getattr(config, "FUTURES_LANE_ACTIVE", False)
    assert not active, (
        "FUTURES_LANE_ACTIVE is True — set it to False to archive MES lane"
    )


def test_mes_broker_code_preserved():
    """execution/ibkr_broker.py must still exist (code preserved for reactivation)."""
    broker_path = os.path.join(_ROOT, "execution", "ibkr_broker.py")
    assert os.path.exists(broker_path), "ibkr_broker.py must not be deleted"


# ── 19. Validator ─────────────────────────────────────────────────────────────


def test_validator_has_forecastex_section():
    """Validator implementation must contain the ForecastEx lane section."""
    wrapper_path = os.path.join(_ROOT, "scripts", "validate.py")
    body_path = os.path.join(_ROOT, "scripts", "validate_body.py")
    assert os.path.exists(wrapper_path), "validate.py not found"
    assert os.path.exists(body_path), "validate_body.py not found"
    src = open(body_path).read()
    assert "ForecastEx lane" in src, "ForecastEx section missing from validate_body.py"
    assert "READY" in src, "READY status string missing from validate_body.py"
    assert "BLOCKED" in src, "BLOCKED status string missing from validate_body.py"
    assert "ACTION NEEDED" in src, (
        "ACTION NEEDED status string missing from validate_body.py"
    )


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
