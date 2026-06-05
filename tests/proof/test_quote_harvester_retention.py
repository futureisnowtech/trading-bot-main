def test_run_once_prunes_in_lean_mode(monkeypatch):
    import forecast.db as fdb
    import forecast.quote_harvester as qh

    harvester = qh.QuoteHarvester(broker=None)
    harvester._last_prune = 0.0

    calls = {"poll": 0, "quotes": 0, "bars": 0}

    monkeypatch.setattr(harvester, "_poll_and_build", lambda: calls.__setitem__("poll", calls["poll"] + 1))
    monkeypatch.setattr(qh, "PRUNE_INTERVAL_MIN", 1)
    monkeypatch.setattr(qh.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(fdb, "prune_old_quotes", lambda db_path=None: calls.__setitem__("quotes", calls["quotes"] + 1) or 7)
    monkeypatch.setattr(fdb, "prune_old_bars", lambda db_path=None: calls.__setitem__("bars", calls["bars"] + 1) or 3)

    harvester.run_once()

    assert calls == {"poll": 1, "quotes": 1, "bars": 1}


def test_run_once_skips_prune_before_interval(monkeypatch):
    import forecast.db as fdb
    import forecast.quote_harvester as qh

    harvester = qh.QuoteHarvester(broker=None)
    harvester._last_prune = 9_990.0

    calls = {"poll": 0, "quotes": 0, "bars": 0}

    monkeypatch.setattr(harvester, "_poll_and_build", lambda: calls.__setitem__("poll", calls["poll"] + 1))
    monkeypatch.setattr(qh, "PRUNE_INTERVAL_MIN", 1)
    monkeypatch.setattr(qh.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(fdb, "prune_old_quotes", lambda db_path=None: calls.__setitem__("quotes", calls["quotes"] + 1) or 0)
    monkeypatch.setattr(fdb, "prune_old_bars", lambda db_path=None: calls.__setitem__("bars", calls["bars"] + 1) or 0)

    harvester.run_once()

    assert calls == {"poll": 1, "quotes": 0, "bars": 0}
