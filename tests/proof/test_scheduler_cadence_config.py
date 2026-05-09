from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_scc01_ml_retrain_check_waits_for_time_and_trade_threshold(monkeypatch):
    import config
    import scheduler.v10_runner as runner

    class _Learning:
        def __init__(self):
            self.called = False

        def maybe_trigger_retrains(self):
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

        def run_nightly_rbi(self, symbol="BTCUSDT"):
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
