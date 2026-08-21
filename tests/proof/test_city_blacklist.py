"""Proof: CITY_BLACKLIST actually blocks the cities it names.

The gate used to compare CITY_BLACKLIST against _get_city_hub(), which returns a
macro-region (WEST, MIDWEST). A city-code entry such as PHX therefore matched
nothing, and its ticker fallback looked for "KXHIGHPHX-" / "KXLOWPHX-" while live
daily tickers are "KXHIGHTPHX-..." — so the blacklist was silently inert.
"""

from __future__ import annotations

import pytest

from forecast import strategy_engine as se

LIVE_TICKERS = {
    "PHX": ["KXHIGHTPHX-26AUG22-T105", "KXLOWTPHX-26AUG22-T80", "KXHIGHPHX-26AUG22-B105"],
    "MSP": ["KXLOWTMIN-26AUG22-T63", "KXHIGHMSP-26AUG22-B85", "KXHIGHTMIN-26AUG22-T90"],
}


@pytest.fixture
def blacklist(monkeypatch):
    def _apply(codes):
        monkeypatch.setattr(se, "CITY_BLACKLIST", {c.upper() for c in codes})
    return _apply


@pytest.mark.parametrize("city", sorted(LIVE_TICKERS))
def test_blacklisted_city_blocks_every_ticker_form(blacklist, city):
    blacklist([city])
    for ticker in LIVE_TICKERS[city]:
        assert se._blacklisted_city_code(ticker) == city, (
            f"{ticker} slipped past a CITY_BLACKLIST={city} gate"
        )


@pytest.mark.parametrize("city", sorted(LIVE_TICKERS))
def test_non_blacklisted_cities_stay_tradeable(blacklist, city):
    blacklist([city])
    for other, tickers in LIVE_TICKERS.items():
        if other == city:
            continue
        for ticker in tickers:
            assert se._blacklisted_city_code(ticker) == ""
    for ticker in ("KXHIGHCHI-26AUG22-B90", "KXLOWTSEA-26AUG21-T60", "KXLOWTPHIL-26AUG22-T68"):
        if se._get_city_hub(ticker).upper() == se._get_city_hub(LIVE_TICKERS[city][0]).upper():
            continue  # same hub as the blacklisted city is a legitimate hub-level match
        assert se._blacklisted_city_code(ticker) == ""


def test_empty_blacklist_blocks_nothing(blacklist):
    blacklist([])
    for tickers in LIVE_TICKERS.values():
        for ticker in tickers:
            assert se._blacklisted_city_code(ticker) == ""


def test_hub_level_entry_still_supported(blacklist):
    blacklist(["MIDWEST"])
    assert se._blacklisted_city_code("KXHIGHCHI-26AUG22-B90") == "MIDWEST"
    assert se._blacklisted_city_code("KXLOWTPHIL-26AUG22-T68") == ""


def test_gate_reports_the_matched_city_not_the_hub(blacklist):
    blacklist(["PHX"])
    ok, _, _, reasons, *_ = se._strategy_weather_details(
        ticker="KXHIGHTPHX-26AUG22-T105",
        ask_yes=0.40,
        ask_no=0.60,
        hours_to_res=12.0,
    )
    assert ok is False
    assert "city_blacklisted_PHX" in reasons


def test_unknown_entries_are_reported(blacklist):
    blacklist(["PHX", "ZZZ"])
    assert se.unknown_city_blacklist_entries() == ["ZZZ"]
