# 2026-04-21 — Crypto-First Evolution Plan

## Decision

Evolve the system into a crypto-first operating model where:

- `CRYPTO` is the primary business lane and primary operator workflow
- spot is the main trade-frequency engine
- perps remain live, but are used as the selective tactical lane
- `STOCKS` remains a dormant-ready lane with its own dashboard tab and truthful readiness surfaces
- `FORECAST` remains a blocked-ready lane with its own dashboard tab and truthful readiness surfaces
- `FUTURES` remains an archived lane with its own dashboard tab and truthful reactivation surfaces

This plan keeps the current venue stack intact:

- Coinbase spot
- Coinbase nano perps
- IBKR stocks
- IBKR ForecastEx
- IBKR MES (archived)

The goal is not to chase novelty. The goal is to increase high-quality trade count from the cleanest currently executable venue while keeping the rest of the platform visible, recoverable, and promotion-ready.

## Why

### Trading reason

- The cleanest path to materially more opportunities on the current venue stack is crypto spot, not stocks, forecast, or MES.
- Crypto spot can rotate through more entries without relying on limited nano-perp sizing.
- Perps still matter, but the current account size and contract granularity make them better as a selective lane than as the throughput engine.
- Stocks, forecast, and MES are not dead. They are just not the best immediate engine for trade count or operational focus.

### Systems reason

- The repo already has a real crypto control plane and spot/perp routing substrate.
- The repo already has page wrappers and data readers for `STOCKS` and `MES`, but the dashboard shell no longer reflects them.
- The main operational mismatch is between `main.py`, `lane_runtime_state`, and the dashboard shell. The system still writes `stocks` lane runtime truth, but the dashboard no longer exposes it as a first-class lane.
- `runtime/lane_registry.py` omitting `stocks` is still worth fixing, but as memory/test hygiene rather than as a core operational repair.
- Forecast and archived futures are currently under-represented as first-class lanes even though they still exist operationally.

### Better success criterion

- The target is not simply “more trades.”
- The target is more trades from the lane with the cleanest execution, venue fit, and tax-operational fit on the current stack, while keeping the rest of the platform promotion-ready instead of abandoned.

## Current state in repo

- `dashboard/app.py` exposes 5 top-level pages:
  - `CONTROL TOWER`
  - `CRYPTO`
  - `FORECAST`
  - `PERFORMANCE LAB`
  - `ENGINEERING CONSOLE`
- `dashboard/widgets/pages/stocks_page.py` exists, but is not top-level.
- `dashboard/widgets/pages/mes_page.py` exists, but archived futures are currently buried in engineering surfaces.
- `main.py` updates runtime state for:
  - `crypto`
  - `forecast`
  - `mes_archived`
  - `stocks`
- `runtime/lane_registry.py` currently registers:
  - `crypto`
  - `forecast`
  - `mes_archived`
  and does **not** register `stocks`
- `config.py` currently supports:
  - `SPOT_LANE_ACTIVE`
  - `SPOT_SYMBOLS=BTC,ETH,SOL,XRP`
  - `AUTONOMOUS_LIVE_PERP_SYMBOLS=ETH`
  - `STOCKS_LANE_ACTIVE`
  - `FORECAST_LANE_ACTIVE`
  - `FUTURES_LANE_ACTIVE`

## Repo changes to make next

### 1. Reframe lane priority in config and runtime truth

#### Files

- `config.py`
- `runtime/lane_registry.py`
- `main.py`
- `dashboard/data/control_tower.py`
- `scripts/validate.py`

#### Changes

- Keep `crypto` as the only default-primary active lane.
- Keep `STOCKS_LANE_ACTIVE`, `FORECAST_LANE_ACTIVE`, and `FUTURES_LANE_ACTIVE`, but stop treating them as a single concept.
- Represent distinct lane roles:
  - `primary`
  - `tactical`
  - `dormant_ready`
  - `blocked_ready`
  - `archived`
- Register `stocks` inside `runtime/lane_registry.py` so runtime lane memory matches `main.py`, while documenting that this is memory/test hygiene rather than the main operational source of truth.
- Add/standardize a lane-priority concept in dashboard-facing reads:
  - role
  - readiness
  - promotion condition

#### Why

- Lane truth should be coherent in config, runtime state, and dashboard state.
- A lane can be inactive strategically without being hidden operationally.
- The operator needs to see what is live, what is dormant, and what can be promoted next without guessing.

### 2. Separate visibility, runner startup, autonomy, and manual rights

#### Files

- `config.py`
- `main.py`
- `dashboard/data/control_tower.py`
- `dashboard/data/stocks.py`
- `dashboard/data/forecast.py`
- `dashboard/data/futures.py`

#### Changes

- Stop overloading a single `*_LANE_ACTIVE` flag to mean everything.
- Introduce and document distinct semantics for each lane:
  - visible in dashboard
  - runner started
  - autonomous trading enabled
  - manual trading allowed
- Apply those semantics explicitly:
  - `CRYPTO`: visible, runner started, autonomous enabled, manual allowed
  - `STOCKS`: visible, runner optional, autonomous disabled by default, manual disabled or tightly scoped
  - `FORECAST`: visible, runner optional, autonomous disabled until contract/readiness requirements are met
  - `FUTURES`: visible, runner off by default, autonomous disabled, archived-only presentation

#### Why

- “Dormant-ready” is not a real operating mode unless repo behavior matches the label.
- The operator needs to know whether a lane is merely visible, actually running, or allowed to place trades.
### 3. Promote dashboard IA from 5 pages to 7 lane-aligned pages

#### Files

- `dashboard/app.py`
- `dashboard/widgets/pages/control_tower.py`
- `dashboard/widgets/pages/crypto_page.py`
- `dashboard/widgets/pages/stocks_page.py`
- `dashboard/widgets/pages/forecast_page.py`
- `dashboard/widgets/pages/mes_page.py`
- `dashboard/widgets/pages/engineering_console.py`
- `dashboard/data/control_tower.py`
- `dashboard/widgets/stocks/stocks_dashboard.py`
- `dashboard/widgets/futures/mes_dashboard.py`
- `scripts/validate.py`
- `tests/proof/test_dashboard_architecture.py`
- `tests/proof/test_dashboard_harness.py`

#### Changes

- Move from the current 5-page shell to:
  - `CONTROL TOWER`
  - `CRYPTO`
  - `STOCKS`
  - `FORECAST`
  - `FUTURES`
  - `PERFORMANCE LAB`
  - `ENGINEERING CONSOLE`
- Keep `CONTROL TOWER` as the default landing page.
- Keep `CRYPTO` as the only page showing live primary workflow density.
- Give `STOCKS`, `FORECAST`, and `FUTURES` their own pages again, but render them with clear lane state badges:
  - `PRIMARY`
  - `TACTICAL`
  - `DORMANT READY`
  - `BLOCKED READY`
  - `ARCHIVED`
- Keep side-lane tabs thin and readiness-first rather than remounting legacy active-trading density unchanged.
- Update `CONTROL TOWER` so it remains the whole-system truth surface even after side-lane tabs return:
  - lane role
  - readiness
  - runner/autonomy status
  - promotion condition

#### Why

- The dashboard should mirror the strategic operating model, not hide side lanes inside engineering surfaces.
- A dormant-ready lane still needs a truthful operator surface.
- Reusing the existing page wrappers is sensible, but the underlying lane widgets need refactoring into thin readiness surfaces instead of being treated as fully active operator pages.

### 4. Make `CRYPTO` the explicit two-lane business page

#### Files

- `dashboard/data/crypto_dashboard.py`
- `dashboard/widgets/pages/crypto_page.py`
- `runtime/crypto_tradeability.py`
- `scheduler/v10_runner.py`
- `dashboard/data/control_tower.py`

#### Changes

- Keep spot as the primary long-only throughput lane.
- Keep perps as the tactical lane for:
  - shorts
  - exceptional high-conviction cases
  - names not approved for spot routing
- Make the business rules explicit in repo-facing terms:
  - spot-first for eligible longs
  - perp-only for shorts
  - no simultaneous spot/perp exposure on the same underlying
  - explicit spot budget and perp budget
  - manual exits always allowed
  - manual opens restricted by lane ownership and shared tradeability truth
- Surface the split directly in the page:
  - spot opportunity board
  - perp opportunity board
  - blocked cross-lane conflicts
  - by-underlying exposure state
- Make the page explicitly show when spot won lane ownership and when perp won lane ownership.

#### Why

- “Crypto” is not one thing operationally anymore.
- The operator needs to see whether capital is being deployed through spot or perps and why.
- This is where the business logic actually lives now, so the dashboard should stop flattening it into one generic table.

### 5. Keep side lanes promotion-ready, not abandoned

#### Files

- `dashboard/data/stocks.py`
- `dashboard/data/forecast.py`
- `dashboard/data/futures.py`
- `dashboard/widgets/pages/stocks_page.py`
- `dashboard/widgets/pages/forecast_page.py`
- `dashboard/widgets/pages/mes_page.py`
- `dashboard/widgets/stocks/stocks_dashboard.py`
- `dashboard/widgets/futures/mes_dashboard.py`

#### Changes

- Add a consistent page banner for side lanes:
  - lane state
  - readiness state
  - why not primary now
  - what must become true for promotion
- For `STOCKS`:
  - show that the lane is sidelined strategically, not deleted
  - keep live status and recent signals visible
  - make autonomy status explicit
- For `FORECAST`:
  - keep heartbeat/readiness visible and explicit
  - keep market availability and contract readiness honest
  - make “blocked-ready” visible as a first-class state
- For `FUTURES`:
  - keep MES archived status explicit
  - show reactivation requirements and recent history

#### Why

- Side lanes should stay one click away from reactivation, not buried.
- This preserves optionality without stealing focus from the primary crypto lane.

### 6. Align repo memory to the operating model

#### Files

- `AGENTS.md`
- `CHANGELOG.md`
- `brain/10_decisions/Decision Log.md`

#### Changes

- Update system memory so it no longer describes the dashboard as a 5-page shell if we promote the side-lane tabs.
- Document that the strategic priority is:
  - crypto first
  - spot as trade-frequency engine
  - perps tactical
  - stocks dormant-ready
  - forecast blocked-ready
  - futures archived
- Record the decision and reversal conditions in `Decision Log.md`.

#### Why

- Strategy drift and dashboard drift happen fastest when repo memory lags real intent.

## What this plan does not change

- No exchange migration
- No new broker integrations
- No futures/forecast deletion
- No broad signal-engine rewrite
- No promise that “more trades” means lowering standards blindly

## Proof requirements when implementing

- Dashboard shell test updated for 7 pages.
- Runtime lane registry test proves `stocks` is registered alongside `crypto`, `forecast`, and `mes_archived`.
- Control Tower proof shows primary vs dormant-ready lane badges.
- Control Tower proof shows lane role, autonomy state, and promotion condition for crypto, stocks, forecast, and futures.
- Crypto page proof shows separate spot/perp sections and cross-lane conflict visibility.
- Stocks / Forecast / Futures page proofs show explicit readiness or dormant-ready banners.
- Validator and repo-memory proof prove `AGENTS.md`, dashboard shell, `scripts/validate.py`, and tests agree.

## Reversal conditions

Reverse or soften this plan if any of the following become true:

- spot trade quality degrades materially as the symbol universe expands
- perps produce better capital efficiency than spot on the current account size
- forecast or stocks become clearly superior in live readiness and opportunity flow
- the operator surface becomes noisier instead of clearer after side-lane tabs are restored
