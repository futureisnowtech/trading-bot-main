from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_live_account_size_prefers_runtime_balance_over_config(proof_runtime, monkeypatch):
    import config
    import runtime.live_account as la
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 5000.0, raising=False)
    monkeypatch.setattr(la, "_db_path", lambda: db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(
        db_path=db,
        process_mode="live",
        account_size_live=1966.0,
    )

    assert la.get_live_account_size() == 1966.0
    assert la.get_live_account_size() != float(config.ACCOUNT_SIZE)


def test_live_account_size_falls_back_to_config_in_paper_mode(proof_runtime, monkeypatch):
    import config
    import runtime.live_account as la
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 5000.0, raising=False)
    monkeypatch.setattr(la, "_db_path", lambda: db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(
        db_path=db,
        process_mode="paper",
        account_size_live=1966.0,
    )

    assert la.get_live_account_size() == 5000.0


def test_runtime_tables_include_account_size_live(proof_runtime):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    rs.init_runtime_tables(db_path=db)

    with sqlite3.connect(db) as conn:
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(system_runtime_state)").fetchall()
        }

    assert "account_size_live" in cols
