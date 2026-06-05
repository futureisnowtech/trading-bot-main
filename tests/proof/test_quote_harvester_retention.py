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


def test_poll_and_build_fetches_one_quote_per_symbol_for_yes_no_pair(proof_runtime, monkeypatch):
    import sqlite3

    import forecast.db as fdb
    import forecast.quote_harvester as qh

    db = str(proof_runtime.db_path)
    fdb.init_forecast_db(db_path=db)
    market_id = fdb.upsert_market("KXHIGHLAX", "LA High", db_path=db)
    fdb.upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHLAX-26JUN05-B69.5",
        right="C",
        strike=69.5,
        last_trade_at="20260605",
        db_path=db,
    )
    fdb.upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHLAX-26JUN05-B69.5",
        right="P",
        strike=69.5,
        last_trade_at="20260605",
        db_path=db,
    )

    class BrokerStub:
        def __init__(self):
            self.calls: list[str] = []

        def is_connected(self):
            return True

        def get_quote(self, ticker):
            self.calls.append(ticker)
            return {
                "bid": 0.41,
                "ask": 0.43,
                "mid": 0.42,
                "spread": 0.02,
                "bid_size": 5,
                "ask_size": 5,
                "no_bid": 0.57,
                "no_ask": 0.59,
                "no_mid": 0.58,
                "no_spread": 0.02,
                "no_bid_size": 5,
                "no_ask_size": 5,
            }

    broker = BrokerStub()
    harvester = qh.QuoteHarvester(broker=broker, db_path=db)
    monkeypatch.setattr(qh, "_build_all_bars", lambda *_args, **_kwargs: None)

    harvester.run_once()

    with sqlite3.connect(db) as conn:
        saved_quotes = conn.execute("SELECT COUNT(*) FROM forecast_quotes").fetchone()[0]

    assert broker.calls == ["KXHIGHLAX-26JUN05-B69.5"]
    assert saved_quotes == 2
