# Protected Deploy Automation

This repo now supports a **protected GitHub-driven NYC deploy path** without replacing the working local `./deploy.sh` flow.

## Goals

- keep GitHub as deploy authority
- keep version control truthful
- keep NYC as a pure deploy target
- avoid “push instantly breaks production”

## What Exists

- local guarded deploy: `./deploy.sh`
- local git hooks: `scripts/install_hooks.sh`
- CI proof workflow: `.github/workflows/ci.yml`
- protected deploy workflow: `.github/workflows/deploy-nyc.yml`

## Safety Model

The new workflow is intentionally conservative.

- It is **additive**, not a replacement for local deploys.
- It runs the proof suite, `validate.py`, and `repo_truth_gate.py --strict` before deploy.
- It deploys only the exact authored Git SHA checked out by GitHub Actions.
- It verifies `/root/bot/version.txt` after deploy.
- It waits for `algo-bot-live` to become `healthy`.

## Important Default

Automatic deploy after CI is **disabled by default**.

The workflow only auto-runs after CI success if the repository variable below is explicitly enabled:

- `NYC_AUTO_DEPLOY_ENABLED=true`

If that variable is not set to `true`, the workflow can still be run manually from the GitHub Actions UI.

## Required GitHub Setup

### 1. Create protected environment

Create a GitHub Actions environment named:

- `nyc-production`

Recommended protection:

- required reviewers: you

That approval gate is what keeps “automation” from becoming “oops, production changed immediately.”

### 2. Add environment secret

Add this secret to the **`nyc-production` environment**, not just repo secrets:

- `NYC_SSH_PRIVATE_KEY`

This should be the SSH private key that can reach:

- `root@64.225.20.38:2222`

### 3. Optional: enable auto-deploy after CI

If you want CI-success to automatically create an approval-gated production deploy, add this repository variable:

- `NYC_AUTO_DEPLOY_ENABLED=true`

If you leave it unset or set it to anything else, deploy remains manual-dispatch only.

## How To Use

### Safest path: manual protected deploy

From GitHub Actions:

1. Open `Protected NYC Deploy`
2. Click `Run workflow`
3. Confirm production deploy
4. Approve the `nyc-production` environment gate

This deploys `feature/v10-rebuild` head only.

### Auto path: CI success + approval

If `NYC_AUTO_DEPLOY_ENABLED=true`:

1. Push to `feature/v10-rebuild`
2. CI passes
3. `Protected NYC Deploy` starts automatically
4. GitHub waits for `nyc-production` approval
5. After approval, deploy runs

## What This Does Not Do Yet

- automatic rollback
- deploy arbitrary old SHAs from GitHub UI
- branch promotion beyond `feature/v10-rebuild`

Those can be added later, but keeping them out for now reduces risk.

## Current Operational Recommendation

- keep using local `./deploy.sh` as the fallback
- use the protected workflow once the environment and secret are configured
- only enable `NYC_AUTO_DEPLOY_ENABLED=true` after you trust the approval-gated path
