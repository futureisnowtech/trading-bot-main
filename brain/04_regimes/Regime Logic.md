# Regime Logic

#active #regime

**Status as of: 2026-04-30**  
**Scope: active Coinbase spot truth-lane**

## Active Regime Language

The active spot lane uses exactly three regimes:

- `TREND`
- `NEUTRAL`
- `CHOP`

These are the only regime labels that should appear in live spot operator truth, setup gating, and tiny-live readiness discussion.

## Operational Meaning

### TREND

- best fit for directional continuation and clean expansion setups
- lower confirm burden than `NEUTRAL`
- uses the `precision` target profile
- currently live-eligible

### NEUTRAL

- allowed, but with stricter quality requirements
- requires more structural confirmation than `TREND`
- uses the `micro` target profile
- currently live-eligible

### CHOP

- blocked for tiny live
- not a “trade smaller” regime
- treated as a hard admission failure for the active spot lane

## Current Regime Contract

| Regime | Live Eligibility | Structural Confirms | Final Score Floor | Target Profile |
|---|---|---:|---:|---|
| `TREND` | allowed | 2 | 58 | `precision` |
| `NEUTRAL` | allowed | 3 | 60 | `micro` |
| `CHOP` | blocked | n/a | n/a | n/a |

## Internal Inputs

The runtime derives spot regime from internal multi-timeframe state, not old debate-era labels.

Important supporting context includes:
- `5m / 30m / 4h / 1d` frame state
- structural confirms
- path efficiency
- momentum impulse
- participation / structure components

## What No Longer Counts As Active Regime Truth

The following are historical and should not be treated as current operator truth for the live spot lane:

- old `TRENDING / RANGING / HIGH_VOL / UNKNOWN` operator language
- debate-era regime labels
- old ADX/CHOP conviction-point stacks used as direct live regime governance
- time-of-day regime heuristics masquerading as the primary regime model

## Strategic Use

The current regime model exists to:

- block weak trade environments early
- tighten standards in `NEUTRAL`
- refuse `CHOP`
- reduce thesis-decay and fee-burn clusters

It does **not** exist to justify broader activity.

