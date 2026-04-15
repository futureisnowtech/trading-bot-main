# Current Active Logic

#active #strategy

**Status as of: 2026-04-15**
**System version: v15.2**
**Source: CLAUDE.md + runtime verified**

---

## CONFIRMED

### System Mode
- PAPER_TRADING=false (LIVE mode — confirmed by runtime)
- FORECAST_LANE_ACTIVE=true
- FUTURES_LANE_ACTIVE=false (MES archived/dormant)
- Entry: `python3 main.py --mode live`
- Dashboard: `streamlit run dashboard/app.py` port 8501

### Active Execution Venues
- **Crypto (LIVE)**: Coinbase US CFTC nano perp futures (`coinbase_broker.py`). CDP JWT/ES256 auth. Symbols: BTC→BIP-20DEC30-CDE, ETH→ETP-20DEC30-CDE, SOL→SLP-20DEC30-CDE, XRP→XPP-20DEC30-CDE. Taker 0.030%. ISOLATED margin.
- **ForecastEx (STARTED, enrollment pending)**: IBKR ForecastEx event contracts (SecType=OPT, Exchange=FORECASTX). Economic markets: CPI/CPIY/CPIC/DISSN/DISSA. Account U25028849. Broker connected. 0 tradable OPT contracts currently (no active event period / enrollment limitation).
- **MES futures**: DORMANT. Code preserved, FUTURES_LANE_ACTIVE=false.

### Scanner Sources
- Kraken Futures public REST + Binance USDM public REST + Hyperliquid — for intelligence/scanning only
- Live execution: Coinbase only

### Signal Engine
- Two-tower: Technical 0-100 + ML 0-100 (XGBoost 60% + LightGBM 40%, 57 features)
- Entry composite >= 58 (TRENDING) or regime-specific threshold
- No AI debate agents — replaced entirely in v10.0

### Risk
- Max risk/trade: 1%. Max daily loss: 4%. Max deployed: 90%. ISOLATED margin.
- Kill switch at balance < 75% of ACCOUNT_SIZE ($3,750 on $5K paper account)

### Key DB Tables (`logs/trades.db`)
- `trades`, `open_positions`, `system_events`, `scan_candidates`
- `forecast_markets`, `forecast_contracts`, `forecast_quotes`, `forecast_bars`, `forecast_resolutions`
- `system_runtime_state`, `lane_runtime_state`, `incidents`
- `trade_integrity`, `exit_evaluations`, `challenger_state`

### Dashboard Tabs (v15.2)
6 tabs: MISSION CONTROL, PERFORMANCE, TRADE APPROVAL, FORECAST TRADING, ARCHIVED FUTURES (MES), SYSTEM SETTINGS

---

## RETIRED (historical — do not treat as current)

- **Coinbase Advanced Trade** — replaced by Coinbase US CFTC nano perp futures (v14.1)
- **Bybit perps** — removed; never used in production
- **Tradovate MES** — replaced by IBKR MES, now dormant (FUTURES_LANE_ACTIVE=false)
- **5-agent AI debate panel** — removed v10.0; replaced by two-tower signal engine
- **MACD-only / equity lane** — removed v10.0
- **Conviction scoring (point stack)** — replaced by technical tower 0-100 scoring

---

## OPEN QUESTIONS

→ See [[01_current_system/Open Questions.md]]
