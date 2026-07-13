import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from forecast.firewall import (
    ensure_firewall_tables,
    check_entry_firewall,
    record_exit_lockout,
    record_round_trip,
    record_realized_pnl,
    check_reentry_lockout,
    check_oscillation_breaker,
    check_quote_coherence,
    check_kill_switch,
    is_ticker_halted,
    is_entries_allowed_today,
    reset_daily_flag,
)

def test_firewall_table_initialization(proof_runtime):
    """Verify that firewall tables are created in the test DB."""
    db = str(proof_runtime.db_path)
    ensure_firewall_tables(db)
    
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "firewall_state" in tables
        assert "firewall_round_trips" in tables
        assert "firewall_day_pnl" in tables

def test_reentry_lockout_enforcement(proof_runtime):
    """Verify lockout is active after loss-realizing exits until settlement."""
    db = str(proof_runtime.db_path)
    ensure_firewall_tables(db)
    ticker = "KXRAINNYC-26JUN22-1"
    
    # 1. Non-loss exit (take_profit) does not trigger lockout
    record_exit_lockout(ticker, "2026-07-08T12:00:00Z", "take_profit", db_path=db)
    allowed, reason = check_reentry_lockout(ticker, db_path=db)
    assert allowed is True
    assert reason == ""

    # 2. Loss-realizing exit triggers lockout until settlement
    settlement = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    record_exit_lockout(ticker, settlement, "salvage_exit", db_path=db)
    
    allowed, reason = check_reentry_lockout(ticker, db_path=db)
    assert allowed is False
    assert "firewall_reentry_lockout" in reason

    # 3. Lockout expires after settlement time passes
    past_settlement = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    record_exit_lockout(ticker, past_settlement, "salvage_exit", db_path=db)
    allowed, reason = check_reentry_lockout(ticker, db_path=db)
    assert allowed is True

def test_oscillation_breaker_doom_loop_replay(proof_runtime):
    """Verify oscillation breaker halts ticker at cycle 2 and caps losses."""
    db = str(proof_runtime.db_path)
    ensure_firewall_tables(db)
    ticker = "KXRAINNYC-26JUN22-1"

    # Cycle 1: BUY->SELL round trip with loss
    record_round_trip(ticker, db_path=db)
    record_realized_pnl(-30.0, db_path=db)
    
    allowed, reason = check_oscillation_breaker(ticker, db_path=db)
    assert allowed is True
    
    # Cycle 2: BUY->SELL round trip with loss
    record_round_trip(ticker, db_path=db)
    record_realized_pnl(-35.0, db_path=db)
    
    # Oscillation breaker should now trigger and halt ticker
    allowed, reason = check_oscillation_breaker(ticker, db_path=db)
    assert allowed is False
    assert "firewall_oscillation_breaker" in reason
    
    # Ticker should be explicitly halted
    halted, halt_reason = is_ticker_halted(ticker, db_path=db)
    assert halted is True
    assert "firewall_oscillation_breaker" in halt_reason

def test_quote_coherence_invariant(proof_runtime):
    """Verify that quote coherence checks trigger halt on wide divergence."""
    db = str(proof_runtime.db_path)
    ensure_firewall_tables(db)
    ticker = "KXRAINNYC-26JUN22-1"

    # Coherent quotes (within 12 cents)
    allowed, reason = check_quote_coherence(ticker, entry_ask=0.50, exit_bid=0.45, max_spread_dollars=0.12, db_path=db)
    assert allowed is True
    
    # Incoherent quotes (15 cents difference > 12 cents limit)
    allowed, reason = check_quote_coherence(ticker, entry_ask=0.50, exit_bid=0.35, max_spread_dollars=0.12, db_path=db)
    assert allowed is False
    assert "quote_coherence_violation" in reason
    
    # Ticker must be explicitly halted
    halted, halt_reason = is_ticker_halted(ticker, db_path=db)
    assert halted is True
    assert "quote_coherence_violation" in halt_reason

def test_daily_kill_switch_and_self_healing(proof_runtime):
    """Verify daily loss kill switch blocks entries and resets on day rollover."""
    db = str(proof_runtime.db_path)
    ensure_firewall_tables(db)
    bankroll = 1000.0
    
    # 1. Realized loss within limits (3% of bankroll = $30)
    record_realized_pnl(-10.0, db_path=db)
    allowed, reason = check_kill_switch(bankroll, db_path=db)
    assert allowed is True
    
    # 2. Realized loss exceeds 3% threshold ($35 > $30)
    record_realized_pnl(-25.0, db_path=db) # total realized PnL = -35.0
    
    # Trigger kill switch
    allowed, reason = check_kill_switch(bankroll, db_path=db)
    assert allowed is False
    assert "firewall_daily_kill_switch" in reason
    
    # Verify entries are now blocked globally
    # (check_entry_firewall sets entries_allowed = 0 globally on kill switch trigger)
    allowed, reason = check_entry_firewall("ANY_TICKER", bankroll, db_path=db)
    assert allowed is False
    assert "firewall_daily_kill_switch" in reason
    
    # 3. Test self-healing on UTC day rollover
    # Manually backdate the global block timestamp in firewall_state
    # and the day_utc in firewall_day_pnl to simulate day change
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    yesterday_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE firewall_state SET updated_at = ? WHERE ticker = '__global__'",
            (yesterday,)
        )
        conn.execute(
            "UPDATE firewall_day_pnl SET day_utc = ? WHERE day_utc = ?",
            (yesterday_date, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        )
        conn.commit()
        
    # check_entry_firewall should self-heal and allow entry
    allowed, reason = check_entry_firewall("ANY_TICKER", bankroll, db_path=db)
    assert allowed is True
    assert reason == ""
