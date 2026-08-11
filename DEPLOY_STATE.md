# Deploy state — read before shipping anything to NYC

Last verified: 2026-08-11 23:30 UTC

## What is actually running

NYC production (droplet `157.245.15.40`) runs **Docker images built on the box
itself**, not a git checkout and not a registry pull. The source tree lives at
`/home/algo-runner/bot` with no `.git`, so the deployed commit is not
recoverable from the box alone — record it here on every deploy.

| | |
|---|---|
| Containers | `execution-engine`, `kalshi-cockpit` |
| Images | `algo-trading-bot:latest`, `algo-trading-bot-dashboard:latest` |
| Deployed commit | **`77cfa77`** (= `master` at deploy time) |
| Version | `VERSION = 19.17.0` |
| Deployed | 2026-08-11, manual build on droplet |
| Config/env | `/home/algo-runner/bot/.env` + `docker-compose.yml` |

Before this, production sat on `603a42a` (v19.10.12, 2026-07-12) for a month —
82 commits behind — because `deploy-nyc.yml` never fires (below). The entire
settled track record through 2026-08-11 belongs to `603a42a`, not to `master`.

If the deployed commit is ever unknown, fingerprint it:

```bash
ssh root@157.245.15.40 'docker exec execution-engine md5sum /app/config.py'
for c in $(git rev-list --all -- config.py); do
  [ "$(git show $c:config.py | md5 -q)" = "<hash>" ] && echo "$c" && break
done
```

## The deploy pipeline does not deploy

`deploy-nyc.yml` requires a green `Kalshi CI` run on `master` **and**
`vars.NYC_AUTO_DEPLOY_ENABLED == 'true'`. That variable is unset, so every
recorded run is `skipped`. CI itself also failed continuously until
2026-08-11. Deploys are therefore **manual**, by the procedure below.

## How to deploy

```bash
# 1. ship master (respects .gitignore, so .env / logs / *.pem are untouched)
git archive --format=tar master | ssh root@157.245.15.40 'tar -x -C /home/algo-runner/bot'

# 2. tag a rollback BEFORE building
ssh root@157.245.15.40 'docker tag algo-trading-bot:latest algo-rollback-engine:$(date +%Y%m%d)
                        docker tag algo-trading-bot-dashboard:latest algo-rollback-dash:$(date +%Y%m%d)'

# 3. build
ssh root@157.245.15.40 'cd /home/algo-runner/bot && docker compose build'

# 4. smoke test the built image against the live account BEFORE swapping
ssh root@157.245.15.40 'cd /home/algo-runner/bot && docker run --rm --env-file .env \
  -v /home/algo-runner/bot/logs:/app/logs \
  -v /home/algo-runner/bot/kalshi_private_key.pem:/run/secrets/kalshi_private_key.pem:ro \
  algo-trading-bot:latest python3 -c "
from runtime.live_account import resolve_live_bankroll; print(resolve_live_bankroll())"'

# 5. swap, then watch a full cycle
ssh root@157.245.15.40 'cd /home/algo-runner/bot && docker compose up -d'
```

**Step 4 is not optional.** It caught a bug that would otherwise have shipped
looking healthy: `resolve_live_bankroll()` returned the config floor instead of
the live balance, because `get_kalshi_broker()` hands back an unconnected
broker. A green test suite did not catch it; running the built image against
the real account did.

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

- Risk constants must move in **three places together**: the droplet `.env`
  (what executes), `config.py` defaults (what a fresh environment gets), and
  the `.env` block in `ci.yml` (what CI proves). The repo once claimed
  `0.60 / $40` while production ran `0.30 / $12`, so the cockpit overstated
  regional headroom and CI proved a posture nothing traded.
- To read the live values, ask the running container rather than trusting any
  document — including this one:
  ```bash
  ssh root@157.245.15.40 'docker exec execution-engine python3 -c "
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
