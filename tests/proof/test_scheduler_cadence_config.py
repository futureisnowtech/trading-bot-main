from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_weather_refresh_cadence_stays_inside_the_tightest_freshness_gate():
    """The ensemble loop must refresh faster than the strictest staleness veto.

    Deriving the cadence from the wide daily window left hourly contracts stale
    for most of every cycle: the strategy engine vetoed them on entry and the
    release gate reported the weather provider as stale. Refresh cadence is set
    by the tightest gate, never the widest.
    """
    from config import (
        KALSHI_DATA_FRESHNESS_MINUTES_DAILY,
        KALSHI_DATA_FRESHNESS_MINUTES_HOURLY,
    )
    from data.kalshi_weather_monitor import (
        CACHE_EXPIRY_SEC,
        WEATHER_REFRESH_TARGET_SEC,
        WEATHER_STATE_TTL_SEC,
    )

    hourly_window_sec = KALSHI_DATA_FRESHNESS_MINUTES_HOURLY * 60
    assert WEATHER_REFRESH_TARGET_SEC < hourly_window_sec, (
        f"refresh cadence {WEATHER_REFRESH_TARGET_SEC}s does not fit inside the "
        f"{hourly_window_sec}s hourly freshness window"
    )
    # The per-coordinate fetch cache must not outlive the loop that drives it,
    # or the loop wakes up and gets a cached record back instead of a refresh.
    assert CACHE_EXPIRY_SEC <= WEATHER_REFRESH_TARGET_SEC
    # Cached state still has to remain usable across the widest (daily) window.
    assert WEATHER_STATE_TTL_SEC >= KALSHI_DATA_FRESHNESS_MINUTES_DAILY * 60


def test_weather_monitor_fallback_constants_match_the_derived_cadence():
    """A config import failure must not silently widen the cadence.

    The module-level fallbacks are what survive that failure, so they are pinned
    to the same values the config-derived path produces.
    """
    import config
    from data.kalshi_weather_monitor import (
        WEATHER_REFRESH_TARGET_SEC,
        WEATHER_STATE_TTL_SEC,
    )

    assert WEATHER_STATE_TTL_SEC == max(
        300, config.KALSHI_DATA_FRESHNESS_MINUTES_DAILY * 60
    )
    assert WEATHER_REFRESH_TARGET_SEC == max(
        300, config.KALSHI_DATA_FRESHNESS_MINUTES_HOURLY * 60 - 300
    )


def test_scc01_ml_retrain_check_waits_for_time_and_trade_threshold(monkeypatch):
    import config
    import scheduler.v10_runner as runner

    class _Learning:
        def __init__(self):
            self.called = False

        def maybe_trigger_retrains(self, **kwargs):
            self.called = True
            return ["BTC/LONG"]

    ll = _Learning()
    monkeypatch.setattr(config, "ML_RETRAIN_MIN_HOURS", 24, raising=False)
    monkeypatch.setattr(config, "ML_RETRAIN_MIN_NEW_CLEAN_TRADES", 20, raising=False)
    monkeypatch.setattr(runner, "_import_learning_loop", lambda: ll)
    monkeypatch.setattr(runner, "_learning_snapshot_count", lambda: 140)
    runner._last_ml_retrain_ts = time.time() - (25 * 3600)
    runner._last_ml_retrain_snapshot_count = 100

    runner.ml_retrain_check()
    assert ll.called is True


def test_scc02_rbi_waits_for_enough_new_learning_snapshots(monkeypatch):
    import config
    import scheduler.v10_runner as runner

    class _Learning:
        def __init__(self):
            self.called = False

        def run_nightly_rbi(self, symbol="BTCUSDT", **kwargs):
            self.called = True
            return {"promoted": 0, "passed": 0}

    ll = _Learning()
    monkeypatch.setattr(config, "RBI_MIN_DAYS", 7, raising=False)
    monkeypatch.setattr(config, "RBI_MIN_NEW_CLEAN_TRADES", 20, raising=False)
    monkeypatch.setattr(runner, "_import_learning_loop", lambda: ll)
    monkeypatch.setattr(runner, "_import_notification_engine", lambda: None)
    monkeypatch.setattr(runner, "_learning_snapshot_count", lambda: 115)
    runner._last_rbi_run_ts = time.time() - (8 * 86400)
    runner._last_rbi_snapshot_count = 100

    runner.rbi_nightly()
    assert ll.called is False
