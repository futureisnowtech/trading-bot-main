# Deploy state — read before changing NYC

Last verified directly against NYC: 2026-08-25

## Deployed production truth

| Item | Verified state |
|---|---|
| Droplet | `algo-runner@157.245.15.40:2222` |
| Runtime root | `/home/algo-runner/bot` |
| Containers | `execution-engine`, `kalshi-cockpit` — both up when checked |
| Images | `ghcr.io/futureisnowtech/trading-bot-main:latest` and dashboard companion |
| Version | `19.20.2` |
| SHA / branch | `master`; read the authoritative runtime stamp for the exact SHA |
| Verified rollout | v19.20.2 live-integration repair; the protected rollout's exact SHA and time are authoritative only in the runtime stamps listed below |
| Open-Meteo commercial key | absent |
| Weather provider actually available | keyless deterministic GFS + ECMWF + NCEP AIGFS disagreement; optional HRRR and METAR; no commercial ensemble and no ICON |
| City firewall | 27 cities blocked; only CHI, DEN, LAX, OKC, SAT enabled |

The stamped files are authoritative for deployed code identity:

- `/home/algo-runner/bot/version.txt`
- `/home/algo-runner/bot/deploy_manifest.json`
- their copies under `/home/algo-runner/bot/logs/`
- `BUILD_SHA` inside the images

The live database remains canonical for trades and recent runtime evidence:
`/home/algo-runner/bot/logs/trades.db`.

## Deployed v19.20.2 system truth

NYC received the v19.20 deterministic-physics repair on 2026-08-25. The exact
deployed SHA is deliberately not duplicated as a supposedly self-updating value
inside this tracked file: the four runtime stamps above, container `BUILD_SHA`,
and `origin/master` must agree. The deployed version family:

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
  governed 60/40 baseline binding until at least seven days and 24 independent,
  officially settled current-epoch events exist, after which holdout validation and
  explicit human promotion are still required.
- records canonical YES-basis probability plus RBI-required model/provider
  fields for every market that reaches production pricing, including later
  vetoes, and alerts through shared operator truth/Telegram if valid traces stop
  advancing;
- replays the production divergence probability shrink during challenger
  scoring rather than validating only the pre-guard blend;
- uses empirical covariance only after 90 raw local-calendar archive days exist
  for every station coordinate; a daily job maintains this keyless historical
  input independently of the commercial ensemble API;
  before then, a gross comonotonic bound grants no YES/NO, cross-city, or
  disjoint-bracket netting;
- refreshes the live broker book before every qualified candidate's sequential
  risk admission, keeps covariance/controller sizing fee-inclusive, and
  post-validates discrete covariance quantities against the hard variance rail;
- blocks fresh entries during deployment until the new SHA earns its own hosted
  audit, and rechecks the release/firewall/snapshot gates after final quote
  refresh immediately before any broker POST.

## Production proof status

The earlier isolated v19.19.0/ICON candidate proof is superseded and is not
evidence for this refactor. On 2026-08-25, v19.20.2 passed:

- complete repository suite: 471 passed;
- clean touched-core Ruff, `compileall`, `scripts/validate.py`, strict boundary
  contract audit, and strict repo-truth gate;
- live Kalshi REST audit: $58.15 cash, $58.16 equity, one open broker
  position, 297 settled contracts, and independent P&L cross-check agreement;
- keyless provider probe: one contract-projected value each for GFS, ECMWF, and
  AIGFS; explicit GFS/ECMWF sigma; bounded pre-CDF physics; weights sum to one;
  model path `deterministic_gfs_ecmwf_aigfs_hrrr_physics`;
- non-trading provider → projection → pricing → strategy → sizing →
  execution-plan proof: `weather_physics`, fee-inclusive position fraction
  7.49%, 12 contracts, IOC plan `ready`, and a broker tripwire proving no order
  submission occurred;
- shadow single-pass runtime completion without authenticating to or reading the
  live account;
- protected GitHub CI and protected NYC deployment from the exact authored SHA;
- an independent in-container hosted audit after deployment: `READY_FOR_LIVE`,
  zero blockers, fresh deterministic weather, broker/runtime balance parity,
  healthy cockpit, and new entries allowed. Re-run this hosted audit after every
  subsequent SHA; the runtime artifact, not this narrative, is authoritative.

No live order was submitted by the proof commands. Current deployment identity
must always be read from the stamped files listed above.

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

Those values are enforced by the v19.20 production strategy and execution path.
Jarvis and Telegram obtain the same values from `runtime.operator_truth` rather
than maintaining a second policy description.

## Deployment boundary

Every follow-up deployment must satisfy all of the following:

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
