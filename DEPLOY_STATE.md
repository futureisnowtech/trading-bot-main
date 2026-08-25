# Deploy state — read before changing NYC

Last verified directly against NYC: 2026-08-25

## Deployed production truth

| Item | Verified state |
|---|---|
| Droplet | `algo-runner@157.245.15.40:2222` |
| Runtime root | `/home/algo-runner/bot` |
| Containers | `execution-engine`, `kalshi-cockpit` — both up when checked |
| Images | `ghcr.io/futureisnowtech/trading-bot-main:latest` and dashboard companion |
| Version | `19.18.0` |
| SHA / branch | `0ab0300a67edc19ca5a4f73e852f690376685ff4` / `master` |
| Deploy stamp | `2026-08-21T19:34:54Z` |
| Open-Meteo commercial key | absent |
| Weather provider actually available | deterministic GFS + ECMWF only; old AI identifier yields null data, and no ICON ensemble |
| City firewall | 27 cities blocked; only CHI, DEN, LAX, OKC, SAT enabled |

The stamped files are authoritative for deployed code identity:

- `/home/algo-runner/bot/version.txt`
- `/home/algo-runner/bot/deploy_manifest.json`
- their copies under `/home/algo-runner/bot/logs/`
- `BUILD_SHA` inside the images

The live database remains canonical for trades and recent runtime evidence:
`/home/algo-runner/bot/logs/trades.db`.

## Important live-versus-candidate discrepancy

NYC has **not** received the v19.20.0 deterministic-physics repair. Its deployed source
still requests retired Open-Meteo model identifier `gfs_graphcast025`, which
currently returns HTTP 200 with null forecast arrays, and the production
strategy branch still assigns the convergence sizing multiplier as an
unconditional `1.5`.

The local source candidate is `VERSION.py` 19.20.0 on branch
`codex/live-policy-truth-alignment`, based on `0ab0300`. The production stamp
above does not contain this candidate. The candidate:

- removes the commercial Open-Meteo ensemble/key path and ICON dependency;
- fetches deterministic GFS, ECMWF, and supported `ncep_aigfs025`, failing
  closed rather than relabeling a surviving non-GFS model;
- represents deterministic forecast uncertainty with model/horizon sigma and
  makes that sigma reach the probability kernel;
- applies bounded daily-HIGH radiative/precipitation cooling and daily-LOW
  moisture/cloud/wind-mixing lift in degrees Fahrenheit before the contract CDF;
- labels that physics method `bounded_heuristic_v1`: its end-to-end plumbing is
  proven, while its incremental forecast skill remains pending current-epoch
  outcomes rather than being claimed in advance;
- leaves hourly temperature, rain, snow, and wind probabilities free of unsafe
  cross-variable temperature shifts;
- restores the selected legacy convergence rule: 1.5x only for unanimous
  same-tail physical-model agreement, soft probability/size penalties above a
  20-point gap, and a hard veto above 70 points;
- routes supported `ncep_aigfs025` through the GFS endpoint and uses the actual
  AIGFS value as the uncertainty input;
- hardens partial/null hourly data without losing time alignment;
- loads promoted RBI GFS/ECMWF weights on the real pricing call path;
- makes convergence/divergence/AIGFS/sigma multipliers reach order quantity,
  enforces the fee-inclusive 12% Kelly cap and aggregate 8% event cap, and
  makes covariance failures veto;
- starts a new `v19.20.0-deterministic-physics-path` RBI evidence epoch and keeps the
  governed 60/40 baseline binding until at least seven days and 24 officially
  settled current-epoch samples exist, after which holdout validation and
  explicit human promotion are still required.

## Candidate proof status

The earlier isolated v19.19.0/ICON candidate proof is superseded and is not
evidence for this refactor. On 2026-08-25, this dirty v19.20.0 candidate passed:

- complete repository suite: 421 passed;
- clean touched-core Ruff, `compileall`, `scripts/validate.py`, strict boundary
  contract audit, and strict repo-truth gate;
- live Kalshi schema probe: structurally valid API with $58.73 balance;
- keyless provider probe: one contract-projected value each for GFS, ECMWF, and
  AIGFS; explicit GFS/ECMWF sigma; bounded pre-CDF physics; weights sum to one;
  model path `deterministic_gfs_ecmwf_aigfs_hrrr_physics`;
- non-trading provider → projection → pricing → strategy → sizing →
  execution-plan proof: `weather_physics`, fee-inclusive position fraction
  7.49%, 12 contracts, IOC plan `ready`, and a broker tripwire proving no order
  submission occurred;
- shadow single-pass runtime completion without authenticating to or reading the
  live account.

No container was restarted and no live order was submitted during these source
proofs. They prove source/runtime-path compatibility, not deployment; current
deployment truth must always be read from the stamped files listed above.

## Effective live risk posture

Verified values include:

- max deployed fraction: 0.90
- concurrent positions: 20
- same-event-family cap: 5
- max quantity per position: 15
- base position dollars: 10
- minimum entry price: 0.34
- Kelly fraction: 0.25
- declared Kelly cap: 0.12
- declared per-event risk: 0.08
- regional exposure: `max($20, 40% of live cash)`

Those values remain unenforced in deployed v19.18.0. The v19.20.0 source
candidate enforces both, but they must not be credited as live protection until
the exact committed candidate is deployed and re-verified.

## Deployment boundary

Do not deploy this candidate until all of the following are true:

1. the changes are reviewed, committed, and pushed;
2. the keyless deterministic provider probe proves GFS/ECMWF/AIGFS identity,
   completeness, freshness, and contract projection in the isolated runtime;
3. the user explicitly authorizes a production deployment;
4. `./deploy.sh` passes its clean-tree, origin-parity, hosted-soak, ownership,
   provenance, and release-audit guards.

`deploy.sh` exports the exact committed tree and syncs it to the droplet. When
GHCR credentials are supplied it pulls SHA-tagged images built by CI; otherwise
it builds from the exact tree on the droplet. It then tags/starts the lean stack
and stamps provenance. The protected GitHub workflow remains conditional on
its environment/variable gates; a green CI run alone is not proof of deploy.

## Read-only verification commands

```bash
ssh -p 2222 algo-runner@157.245.15.40 'sed -n "1,40p" /home/algo-runner/bot/version.txt'
ssh -p 2222 algo-runner@157.245.15.40 'sed -n "1,120p" /home/algo-runner/bot/deploy_manifest.json'
ssh -p 2222 algo-runner@157.245.15.40 'docker ps --format "{{.Names}} {{.Image}} {{.Status}}"'
```

## Deployment hazards that remain active

- The remote block in `deploy.sh` is piped into `bash -s`; a container command
  with unowned stdin can consume the rest of the deploy script. Preserve the
  `</dev/null>` / heredoc protections and final
  `__REMOTE_DEPLOY_COMPLETE__` sentinel.
- `logs/host_service_status.json` expires after 30 minutes. The installed
  five-minute `refresh_host_service_status.sh` cron must remain healthy or the
  release gate will eventually stop new entries.
- Risk values must stay aligned across `config.py`, `.env.example`, CI,
  protected deploy validation, and the droplet `.env`. Read the running
  container before trusting documentation.
