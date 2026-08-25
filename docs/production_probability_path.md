# Production Probability Path

## Proven production eras

- **2026-07-24 through 2026-08-11:** NYC remained on commit `603a42a9da129d11e43c41e2a85228cb6b174d6f` (`VERSION.py` 19.10.12), even while later v19.11-v19.18 commits and documentation accumulated in Git. The deploy record attributes this to a dormant protected deployment path and failing/untriggered CI, not to those tagged releases running live.
- **2026-08-21 onward (verified 2026-08-24):** NYC runs commit `0ab0300a67edc19ca5a4f73e852f690376685ff4` (`VERSION.py` 19.18.0). The running container's tracked source files hash-match that checkout.

NYC has no commercial Open-Meteo key. Its current fallback requests deterministic GFS and ECMWF successfully, but its old `gfs_graphcast025` request returns null forecast arrays; therefore live `q_hat` is currently driven by GFS/ECMWF, with optional HRRR where the strategy supports it. ICON and the AI uncertainty feed are not live-active.

The important continuity is that both proven endpoints use the canonical `forecast.pricing_engine.calculate_pricing()` production branch. Later releases changed calibration, physics, execution, and risk details, so the entire July-to-present period must not be described as one unchanged algorithm.

## What the v19.20.0 source candidate creates

1. `data/kalshi_weather_monitor.py` fetches keyless deterministic GFS, ECMWF, and NCEP AIGFS forecasts, then attaches METAR and HRRR data where available. There is no commercial ensemble/key branch and no ICON input.
2. The top-level provider identity is always GFS. If GFS is absent, the bundle fails closed instead of copying ECMWF or AIGFS into the GFS slot. Partial real models remain missing rather than being converted into fake neutral probabilities.
3. Contract projection supplies model- and horizon-aware predictive-error sigma. `forecast/pricing_engine.py` uses that sigma in its kernel-smoothed CDF, so one-member deterministic forecasts retain honest uncertainty.
4. Daily-HIGH radiative/precipitation cooling and daily-LOW cloud/moisture/wind-mixing lift are bounded to 2.5°F and applied to the forecast variable before the CDF. Hourly temperature, rain, snow, and wind receive no unsafe cross-variable temperature shift. These coefficients are explicitly labeled `bounded_heuristic_v1`: their plumbing and dimensional behavior are proof-covered, but their forecast-skill benefit is not yet empirically established and must be judged from the new RBI evidence epoch.
5. Promoted RBI weights choose the GFS/ECMWF log-odds split. The actual contract-aligned AIGFS forecast has no voting weight: its disagreement with GFS/ECMWF widens the kernels and reduces order size. Near-resolution HRRR may splice into daily-HIGH.
6. Live-call isotonic refitting is retired. The governed raw blend is identity-clamped until a versioned, walk-forward-validated calibration artifact has its own promotion gate.
7. The convergence guardrail awards 1.5x only for unanimous GFS/ECMWF same-tail agreement. Gaps above 20 points pull `q_hat` toward 50% and reduce size; gaps above 70 points veto the entry.
8. The strategy compares guarded probability with live YES/NO quotes, ceiled fees, spread, freshness, and exposure. Convergence, divergence, predictive sigma, AIGFS uncertainty, and same-event penalties all reach the taker-only IOC size solver.
9. Fee-inclusive order cost is capped by the position rail and `KALSHI_KELLY_CAP`; aggregate family exposure is capped by `KALSHI_MAX_RISK_PER_EVENT_PCT`; covariance errors fail closed.

## RBI 2.0 learning period

The candidate starts a versioned evidence epoch named
`v19.20.0-deterministic-physics-path`. Predictions from earlier probability paths do
not count toward this epoch. RBI remains on the governed 60/40 GFS/ECMWF
baseline until current-epoch, officially settled evidence spans at least seven
days and contains at least 24 clean market samples. Passing that observation
gate only permits challenger training: the challenger must still beat the
effective champion on a chronological holdout without excessive segment
regression, and a human must explicitly promote it. Promotion rechecks the
epoch, duration, sample count, and validation result, so a stale artifact cannot
bypass the learning period.

## Repaired discrepancies from the old system

- The commercial ensemble/ICON candidate was removed, including its environment and deployment configuration. Persisted ICON data is cleared during projection so an old snapshot cannot restore it.
- The old AI path requested retired identifier `gfs_graphcast025`, which currently returns HTTP 200 with null forecast arrays, and its scaler compared model output with the contract strike. The candidate requests supported `ncep_aigfs025` from the GFS endpoint and passes the actual AIGFS forecast into `calculate_aigfs_lambda()`.
- `calculate_brier_weights()` used to reference an unimported `DB_PATH`; its broad exception handler hid the error and always returned 60/40. Promoted RBI weights now load on the production call path.
- `get_dynamic_param()` also referenced an unimported `sqlite3`; the swallowed exception made operator overrides inert. The import is restored and proof-covered behavior now follows one canonical runtime path.
- HRRR hydration wrote inside `intraday` while pricing read the top level, and one-member sigma was stored but ignored. Both values now reach pricing.
- Test names previously triggered an alternate stack-inspection math branch. That branch and its dead adaptive-weight helpers are removed, so proof and production execute the same pricing engine.
- The dead Cheat Code / Tiered Goldmine block was removed. It also contained unreachable threshold-probability code accidentally stranded after the helper's return; that deterministic probability path is restored.

## Sizing and risk posture

The former unconditional `convergence_multiplier = 1.5` and inert `divergence_size_multiplier = 1.0` have been replaced by the selected legacy guardrail described above. The guardrail is a hard veto only above a 70-point maximum physical-model gap; the 20-to-70-point region is deliberately a soft probability and size penalty.

Quarter Kelly is active. The candidate additionally enforces `KALSHI_KELLY_CAP=0.12` against fee-inclusive order cost and `KALSHI_MAX_RISK_PER_EVENT_PCT=0.08` against aggregate family exposure. Quantity/dollar position clamps, deployed-cap gate, concurrent/family counts, broker exposure, regional cap, and a fail-closed covariance budget remain additive protections.
