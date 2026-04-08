"""
dashboard/tooltips.py — Plain-English metric definitions
Used via help= parameter on st.metric() calls throughout the dashboard.
Update this file when adding new metrics or sections.
"""

TIPS = {
    # ── Performance / Edge ────────────────────────────────────────────────────
    "profit_factor": (
        "Profit Factor (PF): Total money won divided by total money lost across all closed trades. "
        "1.0 = break-even. 1.35+ = confirmed edge. Below 1.0 = losing system. "
        "Example: PF 1.5 means for every $1 lost, we win $1.50."
    ),
    "win_rate": (
        "Win Rate: The percentage of closed trades that made money. "
        "60%+ is excellent. Even 45% can be profitable if winning trades are larger than losing ones. "
        "Watch this alongside Profit Factor — they tell the full story together."
    ),
    "ev_per_trade": (
        "Expected Value per Trade (EV): The average dollar profit or loss per closed trade after fees. "
        "Positive = the system has a real edge. Target: above $0.50. "
        "If this is negative, the strategy is losing money on average."
    ),
    "rr_realized": (
        "Realized Risk:Reward Ratio: Average winning trade size divided by average losing trade size. "
        "2.0× means wins are twice as large as losses on average. "
        "Higher is better — a 2:1 R:R with 40% win rate is still profitable."
    ),
    "max_drawdown": (
        "Maximum Drawdown: The largest drop from a peak balance to a trough balance. "
        "Example: account went from $10,500 to $10,200 → $300 drawdown = 2.9%. "
        "Tells you the worst pain experienced so far. Target: keep below 10%."
    ),
    "current_dd": (
        "Current Drawdown: How far below your all-time high balance you are right now. "
        "$0 means you are at or above your best-ever balance. "
        "If this is growing, the system may be in a losing streak."
    ),
    "profit_factor_7d": (
        "7-Day Rolling Profit Factor: Same as Profit Factor but only counting the last 7 days of trades. "
        "Catches regime decay much faster than the all-time number. "
        "If all-time PF is 1.5 but 7d PF is 0.8, the edge may be fading in current market conditions."
    ),
    "profit_factor_1d": (
        "24-Hour Profit Factor: Same as Profit Factor but only counting the last 24 hours. "
        "Useful for spotting intraday slippage or signal quality problems in real time."
    ),
    # ── System Health ─────────────────────────────────────────────────────────
    "heartbeat": (
        "Heartbeat: The trading bot writes an 'I am alive' timestamp every few minutes. "
        "This shows how long ago it last did so. "
        "Under 2 minutes = normal. Over 5 minutes = bot may be frozen or crashed."
    ),
    "last_scan": (
        "Last Scan Age: How long ago the scanner last checked all crypto markets for opportunities. "
        "Normal: under 2 minutes. Over 5 minutes = scanner may be stuck or overloaded. "
        "The scanner checks Kraken Futures + Binance + Hyperliquid simultaneously."
    ),
    "ml_gate": (
        "ML Gate Status: The machine learning model needs at least 200 clean trade snapshots "
        "before it activates. Until then it stays neutral (score = 50). "
        "Once active, it scores every trade candidate 0-100 using 57 market features."
    ),
    "error_rate": (
        "Error Rate (last 1 hour): Number of ERROR-level events logged in the last 60 minutes. "
        "0 is ideal. 1-5 = investigate when convenient. 5+ = something is broken, check logs."
    ),
    # ── Execution Quality ─────────────────────────────────────────────────────
    "entry_score": (
        "Entry Timing Score (0-10): Did we get a good entry price? "
        "Measured by how much the price moved against us immediately after entry (MAE). "
        "Score 6+ = good timing. Below 4 = entries are consistently poorly timed."
    ),
    "exit_score": (
        "Exit Efficiency Score (0-10): Did we capture most of the available profit? "
        "Measured as (actual P&L) ÷ (best unrealized profit we ever saw). "
        "Score 8 = we captured 80% of the peak move. Below 5 = leaving too much on the table."
    ),
    "mae": (
        "MAE — Maximum Adverse Excursion: The worst unrealized loss a trade experienced "
        "before it eventually closed. Lower = better entry timing. "
        "Example: 0.5% MAE means the trade went against us 0.5% at its worst before recovering."
    ),
    "mfe": (
        "MFE — Maximum Favorable Excursion: The best unrealized profit a trade ever saw "
        "before it finally closed. Comparing MFE to actual profit shows how much "
        "we left on the table. Example: MFE 2% but actual exit at 0.8% = gave back 60% of the move."
    ),
    "fee_trap": (
        "Fee Trap Rate: Percentage of trades where fees consumed more than 50% of the gross profit. "
        "These are trades that 'won' on paper but were nearly destroyed by transaction costs. "
        "Below 5% = healthy. Above 15% = trading too frequently on small moves."
    ),
    # ── Scanner / Signal ──────────────────────────────────────────────────────
    "scanner_funnel": (
        "Scanner Funnel: The system scans 200+ symbols and filters them down step by step. "
        "Each stage removes symbols that don't meet criteria (volume, volatility, signal score, etc.). "
        "The final number = actionable setup candidates sent to the entry decision engine."
    ),
    "veto_economics": (
        "Economics Veto: Before entering a trade, the system calculates whether expected profit "
        "exceeds fees plus funding costs. If not, the trade is blocked automatically. "
        "High veto rate = market conditions are unfavorable (tight moves, high fees relative to edge)."
    ),
    "signal_score": (
        "Composite Signal Score (0-100): Built from two towers: "
        "(1) Technical tower — 30+ rule-based conditions scored and normalized. "
        "(2) ML tower — XGBoost + LightGBM ensemble trained on 57 market features. "
        "Entry requires score ≥ 62 (trending) to ≥ 72 (high volatility) depending on regime."
    ),
    # ── Futures (MES) ─────────────────────────────────────────────────────────
    "or_high": (
        "Opening Range High: The highest price reached during the first 30 minutes of the trading day "
        "(9:30–10:00 ET). A sustained break ABOVE this level triggers a LONG trade. "
        "The range is locked in at 10:00 ET and does not change during the session."
    ),
    "or_low": (
        "Opening Range Low: The lowest price reached during the first 30 minutes of the trading day "
        "(9:30–10:00 ET). A sustained break BELOW this level triggers a SHORT trade. "
        "The range is locked in at 10:00 ET and does not change during the session."
    ),
    "or_range": (
        "Opening Range Size in points. Each full point on MES = $5 per contract. "
        "Example: range of 10 points = $50 of price distance. "
        "The range size determines stop distances and profit targets for both strategies."
    ),
    "mes_pnl": (
        "MES Daily P&L: Today's total profit or loss from S&P 500 E-mini (MES) futures trades. "
        "1 full point on 1 contract = $5.00. Tick size = 0.25 pts = $1.25. "
        "Executed via IBKR paper account — completely separate from crypto perps."
    ),
    "mes_profit_factor": (
        "MES All-Time Profit Factor: Same as crypto Profit Factor but calculated only for "
        "S&P 500 futures trades via IBKR. Filtered to clean trades from 2026-04-02 onward."
    ),
}
