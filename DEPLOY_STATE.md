# Deploy state — read before shipping anything to NYC

Last verified: 2026-08-11

## What is actually running

NYC production (droplet `157.245.15.40`) runs **Docker images from GHCR**, not a
git checkout. There is no repo on that box.

| | |
|---|---|
| Containers | `execution-engine`, `kalshi-cockpit` |
| Image | `ghcr.io/futureisnowtech/trading-bot-main:latest` |
| Deployed commit | **`603a42a`** — *"master SRE audit overhaul v20 and droplet rebuild"*, 2026-07-12 |
| Version | `VERSION = 19.10.12` |
| Config/env | `/home/algo-runner/bot/.env` + `docker-compose.yml` |

Identify the deployed commit by fingerprint, since the image carries no
`org.opencontainers.image.revision` label:

```bash
ssh root@157.245.15.40 'docker exec execution-engine md5sum /app/config.py'
# then match against history:
for c in $(git rev-list --all -- config.py); do
  [ "$(git show $c:config.py | md5 -q)" = "<hash>" ] && echo "$c" && break
done
```

## The gap

`master` is **82+ commits and 53 files ahead** of what trades, including 9 core
trading files. Everything below has never touched real money:

- the v19.11 → v19.18 release train (DWD ICON ensemble, diurnal derivative
  calculus, asymmetric Kelly sizing, cheatcode arbitrage scanner, goldmine city
  priority scanner)
- the JARVIS mutation bridge with hot-patch code execution
- the autonomous parameter optimizer with 72h auto-rollback
- maker-first entry, paper lane B, proactive sentinel

**The entire settled track record was produced by `603a42a`.** Any statement
about live performance describes that commit, not `master`.

## Why the gap exists

`deploy-nyc.yml` fires on a successful `Kalshi CI` run against `master` **and**
`vars.NYC_AUTO_DEPLOY_ENABLED == 'true'`. That variable is unset, so every
recorded run of the deploy workflow is `skipped`. CI itself also failed
continuously until 2026-08-11. The red gate is why the August regressions
(NO-side P&L sign, salvage tau-decay) never reached production — they were
introduced on 2026-08-01, nineteen days after the freeze.

## Shipping safely

A full `master` deploy replaces the entire proven strategy stack in one shot.
That is the highest-risk action available. Prefer a **minimal-delta build**:

```bash
git checkout -b deploy/<name> 603a42a
git cherry-pick <sha>          # only the commits you actually need
# build, push to GHCR, then on the droplet:
ssh root@157.245.15.40 'cd /home/algo-runner/bot && docker compose pull && docker compose up -d'
```

Candidate for the next minimal deploy: `feat/live-bankroll` (sizing reads the
Kalshi balance instead of `config.ACCOUNT_SIZE`). It changes position sizing,
so ship it when the book is light and watch the first cycle.

## Config divergences to know about

- `KALSHI_MAX_DEPLOYED_PCT` — the deployed image **hardcodes `0.90`** and
  ignores the environment, so the droplet's value is inert. `master` reads the
  env var, meaning the first deploy makes that setting live. Set the droplet
  `.env` to the value you actually want *before* deploying.
- Risk constants must be changed in the droplet `.env` **and** `config.py`
  defaults **and** the `ci.yml` pin together. The repo previously claimed
  `0.60 / $40` while production ran `0.30 / $12`, which made the cockpit
  overstate regional headroom and made CI prove a posture nothing traded.
- `.env` backups are written to `/home/algo-runner/bot/.env.bak.<timestamp>`
  before edits.
