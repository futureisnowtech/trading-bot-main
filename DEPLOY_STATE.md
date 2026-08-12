# Deploy state — read before shipping anything to NYC

Last verified: 2026-08-11 23:30 UTC

## What is actually running

NYC production (droplet `157.245.15.40`) runs **Docker images built on the box
itself**, not a git checkout and not a registry pull. The source tree lives at
`/home/algo-runner/bot` with no `.git`, so git itself is not the runtime source
of truth. Deploy provenance is stamped by `deploy.sh` into:

- `/home/algo-runner/bot/version.txt`
- `/home/algo-runner/bot/deploy_manifest.json`
- `/home/algo-runner/bot/logs/version.txt`
- `/home/algo-runner/bot/logs/deploy_manifest.json`
- `BUILD_SHA` embedded into both Docker images at build time

| | |
|---|---|
| Containers | `execution-engine`, `kalshi-cockpit` |
| Images | `algo-trading-bot:latest`, `algo-trading-bot-dashboard:latest` |
| Deployed commit | **`77cfa77`** (= `master` at deploy time) |
| Version | `VERSION = 19.17.0` |
| Deployed | 2026-08-11, manual build on droplet |
| Config/env | `/home/algo-runner/bot/.env` + `docker-compose.yml` |

Before this, production sat on `603a42a` (v19.10.12, 2026-07-12) for a month —
82 commits behind — because `deploy-nyc.yml` never fired. The entire settled
track record through 2026-08-11 belongs to `603a42a`, not to `master`.

It is valid for `master` to be ahead of production by docs-only commits. Do not
block startup just because the deployed SHA is behind `master`; the real bug is
silent drift, not controlled lag.

If the deployed commit is ever unclear, trust the stamped provenance first:

```bash
ssh -p 2222 algo-runner@157.245.15.40 'sed -n "1,20p" /home/algo-runner/bot/version.txt'
ssh -p 2222 algo-runner@157.245.15.40 'sed -n "1,80p" /home/algo-runner/bot/deploy_manifest.json'
```

## The deploy pipeline does not deploy

`deploy-nyc.yml` requires a green `Kalshi CI` run on `master` **and**
`vars.NYC_AUTO_DEPLOY_ENABLED == 'true'`. That variable is unset, so every
recorded run is `skipped`. CI itself also failed continuously until
2026-08-11. Deploys are therefore **manual**, by the procedure below.

## How to deploy

```bash
# From the repo root on a clean, pushed master:
./deploy.sh
```

`deploy.sh` is the blessed path because it:

- refuses dirty or unpushed work
- ships the exact committed tree
- builds both images on the droplet itself
- verifies forecast-lane and cockpit readiness
- stamps deployed SHA provenance into the host and runtime logs
- runs the hosted release audit on the newly built runtime

Confirm a good deploy with `Live Execution cycle complete` in the logs, a
`bankroll=$…` that matches the real balance, and zero `Traceback` lines.

## Rollback

```bash
ssh root@157.245.15.40 'cd /home/algo-runner/bot && \
  docker tag algo-rollback-engine:20260811 algo-trading-bot:latest && \
  docker tag algo-rollback-dash:20260811 algo-trading-bot-dashboard:latest && \
  docker compose up -d'
```

## Config rules

- Risk constants must move in **four places together**: the droplet `.env`
  (what executes), `config.py` defaults (what a fresh environment gets), the
  `.env` block in `ci.yml`, and the `.env` block in `deploy-nyc.yml`. Protected
  deploy must prove the same posture that CI proves.
- To read the live values, ask the running container rather than trusting any
  document — including this one:
  ```bash
  ssh -p 2222 algo-runner@157.245.15.40 'docker exec execution-engine python3 -c "
  import config
  for k in dir(config):
      if k.startswith((\"KALSHI_\", \"SALVAGE_\", \"ACCOUNT_\")):
          print(k, getattr(config, k))"'
  ```
- The max-deployed-capital ceiling is now honoured from the environment. The
  old image hardcoded it and ignored the env var entirely; the droplet `.env`
  was aligned to the hardcoded figure before deploy specifically so that
  activation changed nothing.
- `ACCOUNT_SIZE` no longer drives live sizing. The bankroll is read from the
  broker each cycle via `runtime.live_account.resolve_live_bankroll()`;
  `ACCOUNT_SIZE` survives only as the last-resort floor.
- `.env` backups are written to `/home/algo-runner/bot/.env.bak.<timestamp>`.
