#!/usr/bin/env python3
"""
mcp_server/server.py — The King's MCP Server
Exposes the trading system as 15 callable tools for Claude Code.

Run: python3 mcp_server/server.py
Add to Claude Code settings: { "mcpServers": { "trading-bot": { "command": "python3", "args": ["/Users/joshmacbookair2020/Desktop/algo_trading_final/mcp_server/server.py"] } } }

Pattern: trading_skills/mcp_server/server.py (FastMCP @mcp.tool() decorators)
Tool catalog reference: Claude_Prophet/mcp-server.js (40-tool complete example)
"""
import os
import sys

os.environ["PYTHONUNBUFFERED"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(write_through=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(write_through=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("trading-bot")

# ── lazy imports so server starts even if some deps are missing ───────────────
def _get_risk_manager():
    from risk.risk_manager import get_risk_manager
    return get_risk_manager()

def _get_logger():
    from logging_db import trade_logger
    return trade_logger

def _get_config():
    import config
    return config


# ============================================================================
# POSITIONS & TRADES
# ============================================================================

@mcp.tool()
def get_positions() -> dict:
    """Get all currently open positions across all lanes (crypto, equity, futures, perp).

    Returns a dict with keys: crypto, equity, futures, perp.
    Each value is a dict of symbol → {qty, entry, stop, target, unrealized_pnl}.
    """
    rm = _get_risk_manager()
    return rm.get_all_positions()


@mcp.tool()
def get_open_trades(lane: str = "all") -> list:
    """Get open positions from the SQLite database.

    Args:
        lane: Filter by lane — 'crypto', 'equity', 'futures', 'perp', or 'all'
    """
    logger = _get_logger()
    positions = logger.get_open_positions()
    if lane == "all":
        return positions
    return [p for p in positions if p.get("strategy", "").startswith(lane.rstrip("s"))]


@mcp.tool()
def get_recent_trades(limit: int = 20) -> list:
    """Get the most recent closed trades with P&L.

    Args:
        limit: Number of trades to return (default 20, max 200)
    """
    from config import PAPER_TRADING
    logger = _get_logger()
    return logger.get_recent_trades(limit=min(limit, 200), paper=PAPER_TRADING)


@mcp.tool()
def close_position(symbol: str, strategy: str, reason: str = "manual_close") -> dict:
    """Close an open position immediately (paper mode only — live requires confirmation).

    Args:
        symbol: Trading symbol (e.g., BTC-USDC, AAPL)
        strategy: Strategy name (e.g., crypto_macd, equity_momentum)
        reason: Reason for closing (logged to trade history)

    Returns: {"success": bool, "message": str}
    """
    from config import PAPER_TRADING
    if not PAPER_TRADING:
        return {"success": False, "message": "Live mode: use the dashboard or confirm manually."}
    rm = _get_risk_manager()
    pos = rm.get_position(symbol, strategy)
    if not pos:
        return {"success": False, "message": f"No open position for {symbol} / {strategy}"}
    rm.close_position(symbol, strategy, exit_reason=reason)
    return {"success": True, "message": f"Closed {symbol} ({strategy}): {reason}"}


# ============================================================================
# SIGNALS & LEARNING
# ============================================================================

@mcp.tool()
def get_signal_stats(regime: str = "all", min_fires: int = 5) -> list:
    """Get Bayesian win-rate stats for all signals from the learning system.

    Args:
        regime: Filter by regime — 'trending', 'ranging', 'volatile', or 'all'
        min_fires: Minimum number of fires to include a signal (default 5)

    Returns: List of {signal, regime, fires, win_rate, bayesian_pts, source}
    """
    try:
        from learning.signal_performance import get_signal_leaderboard
        rows = get_signal_leaderboard()
        if regime != "all":
            rows = [r for r in rows if r.get("regime") == regime]
        return [r for r in rows if r.get("fires", 0) >= min_fires]
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_agent_accuracy() -> list:
    """Get historical vote accuracy for each AI debate agent.

    Returns: List of {agent_key, total_votes, correct_votes, accuracy_pct}
    Accuracy = % of times agent voted BUY and trade was profitable.
    """
    try:
        from learning.signal_performance import get_agent_accuracy_stats
        return get_agent_accuracy_stats()
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_ml_signal(symbol: str) -> dict:
    """Run the ML signal gate for a symbol and get P(win) probability.

    Args:
        symbol: Trading symbol (e.g., BTC-USDC)

    Returns: {"p_win": float, "label": "BUY"|"HOLD", "model_trained": bool}
    """
    try:
        from learning.ml_signal import get_ml_signal as _get_ml_signal
        from data.coinbase_feed import CoinbaseFeed
        feed = CoinbaseFeed()
        candles = feed.get_candles(symbol, limit=100)
        if candles is None or len(candles) < 50:
            return {"p_win": None, "label": "INSUFFICIENT_DATA", "model_trained": False}
        from data.indicators import calculate_indicators
        market_data = calculate_indicators(candles)
        p_win, label = _get_ml_signal(market_data)
        return {"p_win": round(p_win, 4), "label": label, "model_trained": True}
    except Exception as e:
        return {"p_win": None, "label": "ERROR", "error": str(e)}


# ============================================================================
# MARKET DATA
# ============================================================================

@mcp.tool()
def get_price_history(symbol: str, limit: int = 100, interval: str = "1m") -> list:
    """Get recent OHLCV candles for a symbol from the price archive or live feed.

    Args:
        symbol: Trading symbol (e.g., BTC-USDC, AAPL)
        limit: Number of candles (default 100, max 500)
        interval: Candle interval — '1m', '5m', '15m', '1h', '1d'

    Returns: List of {ts, open, high, low, close, volume}
    """
    try:
        from data.price_archive import PriceArchive
        archive = PriceArchive()
        candles = archive.get_candles(symbol, limit=min(limit, 500))
        if candles and len(candles) >= limit // 2:
            return [{"ts": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]} for c in candles]
        from data.coinbase_feed import CoinbaseFeed
        feed = CoinbaseFeed()
        raw = feed.get_candles(symbol, limit=min(limit, 500))
        if raw is None:
            return []
        return [{"ts": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]} for c in raw]
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_macro_context() -> dict:
    """Get current macro context: VIX regime, DXY change, SPY change, funding rates, session.

    Returns a dict with macro_score, vix_regime, session, no_trade_flags, conviction_hints.
    """
    try:
        from data.market_context import get_context_for_debate
        return get_context_for_debate()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def scan_crypto_pairs(pairs: str = "") -> list:
    """Get the latest signal scan results for crypto pairs from the scan feed.

    Args:
        pairs: Comma-separated symbols to filter (e.g., 'BTC-USDC,ETH-USDC').
               Leave empty for all recent scan activity.

    Returns: List of recent scan events with signal, conviction, and debate result.
    """
    from config import PAPER_TRADING
    logger = _get_logger()
    entries = logger.get_scan_feed(limit=50)
    if pairs:
        symbols = [p.strip().upper() for p in pairs.split(",")]
        entries = [e for e in entries if any(s in e.get("message", "") for s in symbols)]
    return entries


# ============================================================================
# DEBATES & DECISIONS
# ============================================================================

@mcp.tool()
def get_debate_result(symbol: str) -> dict:
    """Get the most recent AI debate result for a symbol.

    Args:
        symbol: Trading symbol (e.g., BTC-USDC)

    Returns: {symbol, signal, confidence, agent_votes, reasoning, ts}
    """
    logger = _get_logger()
    debates = logger.get_recent_debates(limit=50)
    for d in debates:
        if symbol.upper() in d.get("symbol", "").upper():
            return d
    return {"message": f"No recent debate found for {symbol}"}


@mcp.tool()
def run_backtest(symbol: str, strategy: str = "crypto", period: str = "1mo") -> dict:
    """Run a backtest for a symbol and strategy.

    Args:
        symbol: Trading symbol (e.g., BTC-USDC, AAPL)
        strategy: Strategy type — 'crypto', 'equity', 'mean_reversion'
        period: Lookback period — '1wk', '1mo', '3mo', '6mo', '1y'

    Returns: {win_rate, sharpe, max_drawdown, total_trades, passed_validation}
    """
    try:
        from backtesting.backtest_engine import BacktestEngine
        from config import ACCOUNT_SIZE
        engine = BacktestEngine(cash=ACCOUNT_SIZE)
        result = engine.run(symbol=symbol, strategy_key=strategy, period=period, interval="5m")
        return {
            "symbol": symbol,
            "strategy": strategy,
            "period": period,
            "win_rate": round(result.get("win_rate", 0), 4),
            "sharpe": round(result.get("sharpe", 0), 4),
            "max_drawdown": round(result.get("max_drawdown", 0), 4),
            "total_trades": result.get("total_trades", 0),
            "passed_validation": result.get("passed", False),
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ============================================================================
# SYSTEM STATUS
# ============================================================================

@mcp.tool()
def get_daily_summary() -> dict:
    """Get today's trading performance summary.

    Returns: {pnl_net, pnl_gross, fees, trades_today, wins, losses, win_rate, halted}
    """
    from config import PAPER_TRADING
    logger = _get_logger()
    today_stats = logger.get_today_stats(paper=PAPER_TRADING)
    all_stats = logger.get_all_time_stats(paper=PAPER_TRADING)
    rm = _get_risk_manager()
    fees = logger.get_todays_fees(paper=PAPER_TRADING)
    gross = logger.get_todays_pnl(paper=PAPER_TRADING)
    return {
        "pnl_net": round(gross - fees, 4),
        "pnl_gross": round(gross, 4),
        "fees_today": round(fees, 4),
        "trades_today": today_stats.get("total", 0),
        "wins_today": today_stats.get("wins", 0),
        "losses_today": today_stats.get("losses", 0),
        "win_rate_today": round(today_stats["wins"] / max(today_stats["total"], 1), 4),
        "all_time_pnl": round(all_stats.get("total_pnl", 0) - all_stats.get("total_fees", 0), 4),
        "all_time_win_rate": round(all_stats.get("win_rate", 0), 4),
        "halted": rm.is_halted,
        "paper_mode": PAPER_TRADING,
    }


@mcp.tool()
def get_readiness_score() -> dict:
    """Check paper→live readiness. Returns score and which criteria are passing/failing.

    Returns: {ready: bool, score: int/7, criteria: list of {name, passing, value}}
    """
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "scripts/check_readiness.py", "--json"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        import json
        return json.loads(result.stdout) if result.stdout else {"error": "No output from readiness check"}
    except Exception as e:
        return {"error": str(e), "hint": "Run: python3 scripts/check_readiness.py"}


@mcp.tool()
def get_notifications(limit: int = 20) -> list:
    """Get recent system notifications — trades, halts, signals, errors.

    Args:
        limit: Number of notifications to return (default 20)

    Returns: List of {ts, level, message} sorted newest first.
    """
    logger = _get_logger()
    return logger.get_recent_notifications(limit=min(limit, 100))


# ============================================================================
# SPRINT 3 TOOLS — Crypto engine + unified sizing introspection
# ============================================================================

@mcp.tool()
def get_engine_signal(symbol: str, btc_change_pct: float = None) -> dict:
    """Evaluate the 4-signal crypto engine for a symbol right now.

    Runs cascade → divergence → OBI → MACD hierarchy and returns
    the highest-priority signal that fired (or HOLD if none).

    Args:
        symbol:        Coinbase product ID (e.g. 'BTC-USDC', 'ETH-USDC').
        btc_change_pct: BTC's 5-min % change for divergence signal. If omitted,
                        divergence signal will be skipped.

    Returns dict with: action, signal_type, size_multiplier, confidence, reason,
                       fired_signals, blocked_reason (if HOLD/blocked).
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from data.coinbase_feed import get_candles
        from data.indicators import add_all_indicators
        from strategies.crypto.crypto_engine import evaluate as engine_evaluate, get_signal_tags
        from scheduler._helpers import _build_market_data, _crypto_strategy
        from data.macro_feed import get_macro_snapshot
        from config import CRYPTO_CANDLE_GRANULARITY

        df = get_candles(symbol, CRYPTO_CANDLE_GRANULARITY, 100)
        if df is None or len(df) < 30:
            return {'error': f'Insufficient candle data for {symbol}'}

        df_ind = add_all_indicators(df)
        price = float(df_ind.iloc[-1]['close'])
        market_data = _build_market_data(symbol, price, df_ind)

        # Enrich funding rate
        try:
            macro = get_macro_snapshot(symbols_of_interest=[symbol])
            fr = macro.get('funding_rates', {}).get(symbol, {})
            market_data['funding_rate_pct'] = fr.get('rate_pct')
        except Exception:
            pass

        # Inject MACD consensus flag
        macd_sig = _crypto_strategy.generate_signal(symbol, df_ind)
        market_data['macd_consensus'] = macd_sig.action == 'BUY'

        btc_pct = float(btc_change_pct) if btc_change_pct is not None else None
        signal = engine_evaluate(symbol, market_data, btc_change_pct=btc_pct)
        tags = get_signal_tags(signal)

        return {
            'symbol': symbol,
            'price': price,
            'action': signal.action,
            'signal_type': signal.signal_type,
            'size_multiplier': signal.size_multiplier,
            'confidence': round(signal.confidence, 3),
            'reason': signal.reason,
            'fired_signals': signal.fired_signals,
            'signal_tags': tags,
            'funding_rate_pct': market_data.get('funding_rate_pct'),
            'obi': market_data.get('obi'),
            'macd_consensus': market_data['macd_consensus'],
        }
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
def get_sizing_breakdown(symbol: str, strategy: str = 'crypto_ai',
                         base_size: float = None, confidence: float = 0.65) -> dict:
    """Show the full V×E×D×T×K×M sizing breakdown for a symbol.

    Useful for understanding why the bot sized a position the way it did,
    or what size it would use right now.

    Args:
        symbol:     Instrument symbol (e.g. 'BTC-USDC').
        strategy:   Strategy name (default 'crypto_ai'; use 'mes_pullback' for futures).
        base_size:  Base USD size (defaults to CRYPTO_POSITION_SIZE_USD from config).
        confidence: Debate confidence to use for Kelly calc [0,1] (default 0.65).

    Returns dict with: base_size, v, e, d, t, k, m, final_size, adaptive, trade_count,
                       and human-readable label for each factor.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from risk.unified_sizer import get_sizing_breakdown as _breakdown
        from config import CRYPTO_POSITION_SIZE_USD, PAPER_TRADING

        size = float(base_size) if base_size is not None else CRYPTO_POSITION_SIZE_USD
        result = _breakdown(
            strategy=strategy,
            symbol=symbol,
            base_size=size,
            confidence=float(confidence),
            paper=PAPER_TRADING,
        )
        return result
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
def get_edge_status(market: str = 'crypto') -> dict:
    """Get the current rolling edge score and auto-action status for a market lane.

    Shows the 20-trade composite score (WR 40% + PF 35% + Sharpe 25%),
    whether sizing is currently reduced due to consecutive low-edge windows,
    and the underlying win rate / profit factor / Sharpe components.

    Args:
        market: 'crypto' | 'mes' | 'polymarket' (default 'crypto')

    Returns dict with: edge_score, wr, pf, sharpe, size_factor, window_trades,
                       consecutive_low_windows, consecutive_high_windows, label.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from risk.edge_monitor import get_edge_score, get_edge_size_factor, check_edge_actions
        from config import PAPER_TRADING

        market = market.lower()
        edge_data = get_edge_score(market=market, paper=PAPER_TRADING)
        size_factor = get_edge_size_factor(market=market, paper=PAPER_TRADING)
        actions = check_edge_actions(market=market, paper=PAPER_TRADING)

        score = edge_data.get('edge_score', 0.0)
        if score >= 0.70:
            label = 'STRONG — Kelly max active'
        elif score >= 0.50:
            label = 'GOOD — normal sizing'
        elif score >= 0.30:
            label = 'WEAK — approaching auto-reduce'
        else:
            label = 'POOR — size may be reduced'

        return {
            'market': market,
            'edge_score': round(score, 3),
            'win_rate': round(edge_data.get('win_rate', 0.0), 3),
            'profit_factor': round(edge_data.get('profit_factor', 0.0), 3),
            'sharpe': round(edge_data.get('sharpe', 0.0), 3),
            'window_trades': edge_data.get('n_trades', 0),
            'size_factor': size_factor,
            'sizing_reduced': size_factor < 1.0,
            'label': label,
            'actions': actions,
        }
    except Exception as e:
        return {'error': str(e)}


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    mcp.run()
