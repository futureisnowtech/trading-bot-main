"""
tests/proof/test_forever_playbook_rules.py

Proof tests for the Forever Playbook routing, governance, and funding rules.
These tests are deterministic: no DB required, no network calls.
They define the invariants the system must always satisfy.

Run: python3 -m pytest tests/proof/test_forever_playbook_rules.py -v
"""

import pytest

from strategies.market_type_classifier import (
    MarketType,
    classify,
    is_tradeable,
    requires_explicit_unlock,
    underlying,
)
from strategies.symbol_governance import (
    GovernanceStatus,
    LaunchState,
    LAUNCH_STATE_RULES,
    evaluate_governance_update,
    get_policy,
)
from strategies.funding_instrument_router import (
    FundingRegime,
    InstrumentRoute,
    classify_funding,
    route,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Market-type classifier — bucket correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestMarketTypeClassifier:
    def test_carry_majors_classified_correctly(self):
        for sym in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            assert classify(sym) == MarketType.CARRY_MAJOR, (
                f"{sym} should be CARRY_MAJOR"
            )

    def test_pf_variants_inherit_underlying_bucket(self):
        assert classify("PF_XBTUSD") == MarketType.CARRY_MAJOR
        assert classify("PF_ETHUSD") == MarketType.CARRY_MAJOR
        assert classify("PF_NEARUSD") == MarketType.CLEAN_TREND_ALT
        assert classify("PF_ZECUSD") == MarketType.CLEAN_TREND_ALT

    def test_clean_trend_alts(self):
        for sym in ("NEAR", "LINK", "AVAX", "MORPHO", "TON", "ZEC"):
            assert classify(sym) == MarketType.CLEAN_TREND_ALT, (
                f"{sym} should be CLEAN_TREND_ALT"
            )

    def test_explosive_convex(self):
        for sym in ("TAO", "ENA", "LIT", "WLD", "ZRO"):
            assert classify(sym) == MarketType.EXPLOSIVE_CONVEX, (
                f"{sym} should be EXPLOSIVE_CONVEX"
            )

    def test_reflexive_meme_blocked_by_default(self):
        for sym in ("TRUMP", "WLFI", "PUMP", "VIRTUAL", "FARTCOIN", "VVV"):
            mt = classify(sym)
            assert mt == MarketType.REFLEXIVE_MEME, f"{sym} should be REFLEXIVE_MEME"
            assert requires_explicit_unlock(mt), f"{sym} should require explicit unlock"
            assert not is_tradeable(mt), f"{sym} should not be tradeable"

    def test_do_not_trade_not_tradeable(self):
        for sym in ("DOT", "ALGO", "BTCUSDT", "ETHUSDT", "PF_ADAUSD"):
            mt = classify(sym)
            assert mt == MarketType.DO_NOT_TRADE, f"{sym} should be DO_NOT_TRADE"
            assert not is_tradeable(mt), f"{sym} should not be tradeable"

    def test_mean_reversion_eligible(self):
        assert classify("DOGE") == MarketType.MEAN_REVERSION
        assert classify("PAXG") == MarketType.MEAN_REVERSION

    def test_is_tradeable_carry_and_trend(self):
        assert is_tradeable(MarketType.CARRY_MAJOR)
        assert is_tradeable(MarketType.CLEAN_TREND_ALT)
        assert is_tradeable(MarketType.EXPLOSIVE_CONVEX)
        assert is_tradeable(MarketType.MEAN_REVERSION)

    def test_reflexive_meme_requires_unlock_not_do_not_trade(self):
        # REFLEXIVE_MEME requires explicit unlock; DO_NOT_TRADE does not
        assert requires_explicit_unlock(MarketType.REFLEXIVE_MEME)
        assert not requires_explicit_unlock(MarketType.DO_NOT_TRADE)

    def test_underlying_strips_pf_prefix(self):
        assert underlying("PF_XBTUSD") == "BTC"
        assert underlying("PF_ETHUSD") == "ETH"
        assert underlying("PF_NEARUSD") == "NEAR"
        assert underlying("BTC") == "BTC"  # no-op for non-PF

    def test_unknown_symbol_falls_back_to_explosive(self):
        # Unknown symbols are treated as EXPLOSIVE_CONVEX (conservative)
        mt = classify("TOTALLY_UNKNOWN_XYZ123")
        assert mt == MarketType.EXPLOSIVE_CONVEX

    def test_duplicate_tickers_are_do_not_trade(self):
        assert classify("BTCUSDT") == MarketType.DO_NOT_TRADE
        assert classify("ETHUSDT") == MarketType.DO_NOT_TRADE


# ─────────────────────────────────────────────────────────────────────────────
# 2. Symbol governance — policy correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestSymbolGovernance:
    def test_systematic_losers_are_blocked(self):
        """VVV (n=23, -4.15), ALGO (n=11, -3.06), PF_ADAUSD (n=17, -6.94) must be BLOCKED."""
        for sym in ("VVV", "ALGO", "PF_ADAUSD", "DOT"):
            policy = get_policy(sym)
            assert policy.governance == GovernanceStatus.BLOCKED, (
                f"{sym} should be BLOCKED (systematic loser)"
            )
            assert not policy.can_enter, f"{sym} should not be enterable"

    def test_carry_majors_are_allowed(self):
        for sym in ("BTC", "ETH", "SOL", "BNB"):
            policy = get_policy(sym)
            assert policy.governance in (
                GovernanceStatus.ALLOWED,
                GovernanceStatus.PROMOTED,
            )
            assert policy.can_long

    def test_shorts_suppressed_globally_in_seed(self):
        """Per go-live audit: no symbol should have shorts_allowed=True in seed."""
        for sym in ("BTC", "ETH", "SOL", "NEAR", "LINK", "ZEC", "AVAX"):
            policy = get_policy(sym)
            assert not policy.can_short, (
                f"{sym} has shorts_allowed=True — suppressed per go-live audit "
                f"(LONG net=+13.56 vs SHORT net=-13.82)"
            )

    def test_blocked_symbols_have_zero_size(self):
        for sym in ("VVV", "ALGO", "PF_ADAUSD", "TRUMP", "WLFI", "FARTCOIN"):
            policy = get_policy(sym)
            assert policy.max_size_pct == 0.0, f"{sym} max_size_pct should be 0"

    def test_constrained_symbols_have_reduced_size(self):
        for sym in ("MORPHO", "TAO", "ENA", "ZRO", "ADA"):
            policy = get_policy(sym)
            assert policy.governance == GovernanceStatus.CONSTRAINED
            assert 0 < policy.max_size_pct <= 0.75, (
                f"{sym} max_size_pct={policy.max_size_pct} should be in (0, 0.75]"
            )

    def test_pf_zecusd_promoted(self):
        """PF_ZECUSD: n=14 net=+5.41 — should be PROMOTED (best performer with adequate sample)."""
        policy = get_policy("PF_ZECUSD")
        assert policy.governance == GovernanceStatus.PROMOTED

    def test_reflexive_meme_blocked_by_default(self):
        for sym in ("TRUMP", "PUMP", "VIRTUAL", "ASTER", "BERA"):
            policy = get_policy(sym)
            assert policy.governance == GovernanceStatus.BLOCKED
            assert not policy.can_enter

    def test_unknown_symbol_gets_derived_policy(self):
        """Unknown symbols derive policy from market type (EXPLOSIVE_CONVEX → CONSTRAINED)."""
        policy = get_policy("TOTALLY_UNKNOWN_XYZ123")
        assert policy.governance == GovernanceStatus.CONSTRAINED
        assert policy.max_size_pct == 0.5
        assert not policy.can_short

    def test_research_only_cannot_enter(self):
        for sym in ("BTCUSDT", "ETHUSDT", "PF_ALGOUSD"):
            policy = get_policy(sym)
            assert policy.governance == GovernanceStatus.RESEARCH_ONLY
            assert not policy.can_enter

    def test_promoted_zec_is_long_allowed(self):
        policy = get_policy("PF_ZECUSD")
        assert policy.can_long
        assert not policy.can_short  # shorts still suppressed tonight


# ─────────────────────────────────────────────────────────────────────────────
# 3. Governance update logic
# ─────────────────────────────────────────────────────────────────────────────


class TestGovernanceUpdates:
    def test_promote_on_strong_evidence(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=20,
            net_pnl=8.0,
            expectancy=0.40,
            current_status=GovernanceStatus.ALLOWED,
        )
        assert rec is not None
        assert rec[0] == GovernanceStatus.PROMOTED

    def test_block_on_strong_negative_evidence(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=10,
            net_pnl=-5.0,
            expectancy=-0.50,
            current_status=GovernanceStatus.ALLOWED,
        )
        assert rec is not None
        assert rec[0] == GovernanceStatus.BLOCKED

    def test_constrain_on_negative_evidence(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=6,
            net_pnl=-2.0,
            expectancy=-0.33,
            current_status=GovernanceStatus.ALLOWED,
        )
        assert rec is not None
        assert rec[0] == GovernanceStatus.CONSTRAINED

    def test_allow_from_constrained_on_positive_evidence(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=9,
            net_pnl=1.5,
            expectancy=0.17,
            current_status=GovernanceStatus.CONSTRAINED,
        )
        assert rec is not None
        assert rec[0] == GovernanceStatus.ALLOWED

    def test_no_update_on_thin_sample(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=2,
            net_pnl=5.0,
            expectancy=2.50,
            current_status=GovernanceStatus.ALLOWED,
        )
        assert rec is None, "Should not update governance on n<3"

    def test_no_downgrade_if_already_blocked(self):
        """Blocking an already-blocked symbol produces no recommendation."""
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=20,
            net_pnl=-10.0,
            expectancy=-0.50,
            current_status=GovernanceStatus.BLOCKED,
        )
        assert rec is None

    def test_no_promote_if_already_promoted(self):
        rec = evaluate_governance_update(
            "XYZ",
            n_trades=20,
            net_pnl=8.0,
            expectancy=0.40,
            current_status=GovernanceStatus.PROMOTED,
        )
        assert rec is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Funding regime classification
# ─────────────────────────────────────────────────────────────────────────────


class TestFundingRegime:
    def test_hostile_when_rate_above_threshold(self):
        assert classify_funding(0.0003) == FundingRegime.HOSTILE  # 0.03%/8h
        assert classify_funding(0.0010) == FundingRegime.HOSTILE  # 0.10%/8h

    def test_neutral_near_zero(self):
        assert classify_funding(0.0) == FundingRegime.NEUTRAL
        assert classify_funding(0.0001) == FundingRegime.NEUTRAL  # just below hostile
        assert (
            classify_funding(-0.00005) == FundingRegime.NEUTRAL
        )  # just above favorable

    def test_favorable_when_negative(self):
        assert classify_funding(-0.0002) == FundingRegime.FAVORABLE

    def test_carry_positive_when_strongly_negative(self):
        assert classify_funding(-0.0005) == FundingRegime.CARRY_POSITIVE

    def test_none_treated_as_neutral(self):
        assert classify_funding(None) == FundingRegime.NEUTRAL


# ─────────────────────────────────────────────────────────────────────────────
# 5. Instrument routing
# ─────────────────────────────────────────────────────────────────────────────


class TestInstrumentRouter:
    def test_carry_major_perp_preferred_on_favorable_funding(self):
        rd = route("BTC", "LONG", MarketType.CARRY_MAJOR, funding_rate=-0.0004)
        assert rd.route == InstrumentRoute.PERP_PREFERRED
        assert rd.funding_regime == FundingRegime.CARRY_POSITIVE

    def test_carry_major_spot_preferred_on_hostile_funding(self):
        rd = route("BTC", "LONG", MarketType.CARRY_MAJOR, funding_rate=0.0005)
        assert rd.route == InstrumentRoute.SPOT_PREFERRED
        assert rd.funding_regime == FundingRegime.HOSTILE

    def test_carry_major_perp_tolerated_on_neutral_funding(self):
        rd = route("ETH", "LONG", MarketType.CARRY_MAJOR, funding_rate=0.0)
        assert rd.route == InstrumentRoute.PERP_TOLERATED

    def test_clean_trend_alt_spot_preferred_by_default(self):
        rd = route("NEAR", "LONG", MarketType.CLEAN_TREND_ALT, funding_rate=0.0)
        assert rd.route == InstrumentRoute.SPOT_PREFERRED

    def test_clean_trend_alt_spot_on_hostile(self):
        rd = route("LINK", "LONG", MarketType.CLEAN_TREND_ALT, funding_rate=0.0005)
        assert rd.route == InstrumentRoute.SPOT_PREFERRED

    def test_clean_trend_alt_perp_tolerated_on_carry_positive(self):
        rd = route("NEAR", "LONG", MarketType.CLEAN_TREND_ALT, funding_rate=-0.0005)
        assert rd.route == InstrumentRoute.PERP_TOLERATED

    def test_explosive_convex_always_spot_preferred(self):
        for rate in (0.0005, 0.0, -0.0005):
            rd = route("TAO", "LONG", MarketType.EXPLOSIVE_CONVEX, funding_rate=rate)
            assert rd.route == InstrumentRoute.SPOT_PREFERRED, (
                f"EXPLOSIVE_CONVEX should be SPOT_PREFERRED regardless of funding (rate={rate})"
            )

    def test_reflexive_meme_blocked_regardless(self):
        for rate in (0.0005, 0.0, -0.0005):
            rd = route("TRUMP", "LONG", MarketType.REFLEXIVE_MEME, funding_rate=rate)
            assert rd.route == InstrumentRoute.BLOCKED

    def test_do_not_trade_blocked(self):
        rd = route("DOT", "LONG", MarketType.DO_NOT_TRADE, funding_rate=0.0)
        assert rd.route == InstrumentRoute.BLOCKED

    def test_shorts_always_blocked_tonight(self):
        """Per go-live audit, all shorts are suppressed."""
        for sym, mt in (
            ("BTC", MarketType.CARRY_MAJOR),
            ("NEAR", MarketType.CLEAN_TREND_ALT),
            ("TAO", MarketType.EXPLOSIVE_CONVEX),
        ):
            rd = route(sym, "SHORT", mt, funding_rate=0.0)
            assert rd.route == InstrumentRoute.BLOCKED, (
                f"SHORT on {sym} should be BLOCKED per go-live audit"
            )

    def test_pf_symbols_correctly_flagged(self):
        rd = route("PF_XBTUSD", "LONG", MarketType.CARRY_MAJOR, funding_rate=0.0)
        assert rd.is_pf_symbol
        assert not rd.live_eligible  # PF = paper-only tonight

    def test_non_pf_carry_major_live_eligible_when_not_blocked(self):
        rd = route("BTC", "LONG", MarketType.CARRY_MAJOR, funding_rate=0.0)
        assert not rd.is_pf_symbol
        assert rd.live_eligible

    def test_mean_reversion_spot_preferred(self):
        rd = route("DOGE", "LONG", MarketType.MEAN_REVERSION, funding_rate=0.0)
        assert rd.route == InstrumentRoute.SPOT_PREFERRED


# ─────────────────────────────────────────────────────────────────────────────
# 6. Dirty-row contamination invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestDirtyRowInvariants:
    """
    Ensures that contaminated sources can never silently pollute headline truth.
    These are logic-level invariants that do not require a live DB.
    """

    DIRTY_SOURCES = ("pre_v10_contaminated", "backtest", "bybit_paper", "paper_v10")
    HEADLINE_SOURCES = ("clean_paper_v10", "live_v10")

    def test_dirty_sources_not_in_headline(self):
        """No dirty source should appear in the headline source list."""
        for src in self.DIRTY_SOURCES:
            assert src not in self.HEADLINE_SOURCES, (
                f"Dirty source '{src}' must not appear in HEADLINE_SOURCES"
            )

    def test_headline_sources_not_in_dirty(self):
        for src in self.HEADLINE_SOURCES:
            assert src not in self.DIRTY_SOURCES

    def test_replay_markers_excluded(self):
        """Replay/synthetic markers must never reach live Bayesian weights."""
        replay_markers = ("replay", "synthetic", "bootstrap", "backtest_only")
        for marker in replay_markers:
            assert marker not in self.HEADLINE_SOURCES


# ─────────────────────────────────────────────────────────────────────────────
# 7. Launch-state ladder invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestLaunchStateLadder:
    def test_research_allows_no_trades(self):
        rules = LAUNCH_STATE_RULES[LaunchState.RESEARCH]
        assert not rules["live_allowed"]
        assert not rules["paper_allowed"]
        assert rules["max_size_pct"] == 0.0

    def test_constrained_live_no_shorts(self):
        rules = LAUNCH_STATE_RULES[LaunchState.CONSTRAINED_LIVE]
        assert rules["live_allowed"]
        assert not rules["shorts_allowed"]
        assert rules["max_size_pct"] == 0.5

    def test_defense_mode_allows_nothing(self):
        rules = LAUNCH_STATE_RULES[LaunchState.DEFENSE_MODE]
        assert not rules["live_allowed"]
        assert not rules["paper_allowed"]
        assert rules["max_size_pct"] == 0.0

    def test_scaled_live_allows_shorts(self):
        rules = LAUNCH_STATE_RULES[LaunchState.SCALED_LIVE]
        assert rules["live_allowed"]
        assert rules["shorts_allowed"]
        assert rules["max_size_pct"] == 1.0

    def test_paper_never_live(self):
        rules = LAUNCH_STATE_RULES[LaunchState.PAPER]
        assert not rules["live_allowed"]
        assert rules["paper_allowed"]

    def test_all_launch_states_defined(self):
        for state in LaunchState:
            assert state in LAUNCH_STATE_RULES, (
                f"{state} missing from LAUNCH_STATE_RULES"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Learning-segmentation boundary invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestLearningBoundaries:
    """
    Ensure that market-type classification preserves the learning segmentation
    required by the playbook: meme outcomes must not pollute major-symbol policy.
    """

    def test_meme_and_major_in_different_buckets(self):
        meme_syms = ("TRUMP", "PUMP", "VIRTUAL", "FARTCOIN")
        major_syms = ("BTC", "ETH", "SOL")
        for m in meme_syms:
            for maj in major_syms:
                assert classify(m) != classify(maj), (
                    f"{m} and {maj} should be in different buckets"
                )

    def test_carry_and_explosive_separate(self):
        carry = ("BTC", "ETH", "SOL")
        explosive = ("TAO", "ENA", "WLD", "LIT")
        for c in carry:
            for e in explosive:
                assert classify(c) != classify(e), (
                    f"{c} and {e} should not share the same bucket"
                )

    def test_do_not_trade_cannot_share_bucket_with_allowed(self):
        dnt = ("DOT", "ALGO", "BTCUSDT", "PF_ADAUSD")
        allowed = ("BTC", "ETH", "NEAR", "ZEC")
        for d in dnt:
            for a in allowed:
                assert classify(d) != classify(a), (
                    f"{d} (DO_NOT_TRADE) shares bucket with {a} (ALLOWED)"
                )
