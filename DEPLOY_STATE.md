# Deploy state — read before shipping anything to NYC

Last verified: 2026-08-12 19:45 UTC

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
| Images | `ghcr.io/futureisnowtech/trading-bot-main:latest`, `ghcr.io/futureisnowtech/trading-bot-main-dashboard:latest` |
| Live SHA / branch / deploy time | Read `/home/algo-runner/bot/version.txt` and `/home/algo-runner/bot/deploy_manifest.json` |
| Version | Read `app_version=` from the stamped provenance files above |
| Deploy method | Guarded deploy via `./deploy.sh` |
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
- runs the runtime and helper containers as the non-root deploy user
- verifies forecast-lane and cockpit readiness
- stamps deployed SHA provenance into the host and runtime logs
- fails if ownership drift appears anywhere under `/home/algo-runner/bot`
- runs the hosted release audit on the newly built runtime

Confirm a good deploy with `Live Execution cycle complete` in the logs, a
`bankroll=$…` that matches the real balance, and zero `Traceback` lines.

## Rollback

```bash
ssh -p 2222 algo-runner@157.245.15.40 'cd /home/algo-runner/bot && \
  docker tag algo-rollback-engine:20260811 ghcr.io/futureisnowtech/trading-bot-main:latest && \
  docker tag algo-rollback-dash:20260811 ghcr.io/futureisnowtech/trading-bot-main-dashboard:latest && \
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
- Runtime containers and deploy helper containers now run as UID/GID `1000`
  (`algo-runner`) and with `PYTHONDONTWRITEBYTECODE=1` so bind-mounted files
  stay owned by the deploy user instead of drifting to root.

## Deploy-script hazard: stdin-eating containers

The remote block in `deploy.sh` is piped into `bash -s`, so **the remote script
itself is that shell's stdin**. Any container started with stdin attached
(`docker exec -i`, `docker run -i` without its own redirect) consumes the rest
of the script as its own input. Bash then reaches EOF and exits **0**, so the
deploy reports success while every remaining step is silently skipped.

This bit a real deploy on 2026-08-12 (`55d198f`): the 600-second soak audit, the
post-deploy ownership re-audit, and the provenance echo never ran, and the
script still printed `Deployment complete.` and exited 0.

Two guards now exist and are pinned by `tests/proof/test_release_audit.py`:

- every `docker exec` in the remote block redirects `</dev/null`
- the remote block's last statement echoes `__REMOTE_DEPLOY_COMPLETE__`, which
  the local side greps for and fails the deploy if absent

The `docker run --rm -i ... << PYEOF` calls are safe because their heredoc
already supplies stdin. Keep it that way when adding steps.
