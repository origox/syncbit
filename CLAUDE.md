# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SyncBit is a long-running Python service that syncs Fitbit health data to Victoria Metrics through the **Google Health API v4**. The legacy Fitbit Web API was shut down on 2026-11-02 and returns 410; `HEALTH_PROVIDER=google` is the default and the only working provider. The `fitbit_*` modules are kept behind the same interface but are dead code paths.

Design, data flow, endpoint table, limits and error handling: `docs/ARCHITECTURE.md`. Read it before changing the collector, scheduler or auth.

## Development Commands

```bash
devbox shell
devbox run install-dev                 # prod + dev deps
python main.py --authorize [--manual]  # OAuth; --manual = paste the code, no callback server
python main.py --log-level DEBUG
devbox run test                        # pytest
black src tests main.py scripts && ruff check src tests main.py scripts
python scripts/probe_google_health.py  # dump raw API responses (writes probe_google_health.json, gitignored: real health data)
```

Local secrets come from `.envrc` (direnv + `op.exe read op://kubernetes/...`) or `.env`; real env vars win over `.env` (`load_dotenv(override=False)`). The shell used by tools does not load direnv, so pass `DATA_DIR=./data` and credentials explicitly when running against the live API.

## Architecture Map

- `src/collector.py` (`HealthDataCollector` protocol, `RateLimitError`) and `src/health_types.py` are the provider-neutral contract. The writer, scheduler and sync state depend only on these, never on a provider's wire format.
- `src/google_collector.py` fetches 14-day chunks, one request per data type, run concurrently on a thread pool under a shared throttle (`GOOGLE_MAX_REQUESTS_PER_MINUTE`=300, the per-user quota). Core rollups propagate errors; optional metrics log and skip; `RateLimitError` always propagates to the scheduler.
- `src/google_auth.py`: PKCE + offline access; persists the verifier in `data/pkce_verifier.txt` so URL generation and code exchange can be separate processes; preserves the refresh token (Google omits it on refresh); `refresh_access_token()` re-reads the token file first; records granted scopes and warns on missing ones.
- `src/scheduler.py`: backfill in `BACKFILL_CHUNK_DAYS`=14 chunks with no fixed sleeps; stops without advancing state when a Victoria write fails. Collector is injected; `main.build_provider()` chooses it.
- `src/victoria_writer.py`: Prometheus lines, ms timestamps, labels `user` + `device` (`DEVICE_LABEL`, default `fitbit_air`).
- Entry point is `main.py` in the repo root (not `src/main.py`).

## Invariants

- **Metric names and label values are a public contract.** Dashboards depend on them; Google values are mapped onto Fitbit-era names (zones `out_of_range/fat_burn/cardio/peak`, activity `lightly_active/fairly_active/very_active`, stages `deep/light/rem/wake`). Change them only together with the dashboards.
- **Sync state never advances past an unwritten day.**
- **`google_tokens.json` is rewritten on every hourly refresh**, so it must live on writable storage (the PVC on Kubernetes), never a ConfigMap or Secret.
- Tests must not depend on a developer's `.env`: set every `Config` value a test needs with `monkeypatch.setattr`. CI has no credentials. `test_config.py` reloads the config module, so patch `Config` through the module under test (e.g. `src.scheduler.Config`).

## Google Health API Quirks

Verified against a live account; several contradict the published reference:

- Daily-summary list filters need a **snake_case** prefix: `daily_resting_heart_rate.date >= "YYYY-MM-DD"`. The documented camelCase prefix is rejected.
- `daily-*` types reject `dailyRollUp`; use a date-filtered `list`.
- Identity is `users/me/identity`; `users/me:getIdentity` 404s.
- Paired devices need `googlehealth.settings.readonly`, which is not requested, so device metrics are empty.
- `rollUp` intermittently omits zero-valued windows; totals are unaffected.
- No `Retry-After` on 429 is documented; default wait is 60 s.
- Users can untick scopes on the consent screen; the token is then valid but partial and the affected types return 403 `MISSING_OAUTH_SCOPE`.

When a response shape is uncertain, run `scripts/probe_google_health.py` rather than trusting the reference.

## Operational Gotchas

- **Port 8080** on the dev machine is often held by another local dev server; then `--authorize` (callback mode) silently gets a 404 from the wrong process. Use `--authorize --manual`.
- OAuth app in *Testing* mode: refresh tokens expire after 7 days. The app is published (unverified, fine for personal use); re-authorize after any change of publishing status.
- The image has no tzdata, so the container runs in UTC and day boundaries are UTC midnight.
- After a backfill of historical dates, Grafana can show "No data" because Victoria Metrics caches query results; `/internal/resetRollupResultCache` clears it.
- Docker build: dependencies install into `/install` in the builder and are copied without a version-specific path; pip is removed from the runtime image (its SBOM declares a vendored setuptools that fails the Trivy HIGH gate). Keep both when touching the Dockerfile.
- Container runs as UID 1000; a bind-mounted `./data` must be writable by it.

## Dependency Management

### Renovate Configuration

**Python Version Strategy:**
- Docker images (`Dockerfile`): Auto-update when available
- Devbox packages (`devbox.json`): Require manual approval via Dependency Dashboard
- Reason: nixpkgs lags behind Docker Hub by weeks (e.g., `python314` package unavailable when Docker image exists)

**Auto-Merge Rules (.renovaterc.json5):**
1. CLI tools (direnv, gh, docker-client): patch + minor updates → auto-merge immediately (no CI wait)
2. All other deps: patch + digest updates → auto-merge after CI passes (test, lint, build-and-scan)
3. Minor updates: manual review required
4. Major updates: require approval via Dependency Dashboard

**Digest Pinning:**
- Docker images: Pinned to SHA256 (`python:3.14-alpine@sha256:...`)
- GitHub Actions: Pinned to commit SHA (`actions/checkout@v6@sha256:...`)
- Digest updates auto-merge after CI (security without breaking changes)

**Checking Python Package Availability:**
```bash
# Check if python314 exists in nixpkgs
devbox search python314

# If not found, wait for nixpkgs to catch up before approving devbox update
# Docker images can update first (production gets patches)
```

## Commit Message Format

Uses Conventional Commits. Format: `<type>[optional scope]: <description>`

**Types:** feat, fix, docs, style, refactor, perf, test, chore, build, ci

**Examples:**
```
feat: add sleep tracking metrics
fix(auth): handle token refresh edge case
docs: update deployment instructions
refactor(collector)!: change API response structure
```

Git hook validates commit messages automatically.

## Adding New Metrics

1. Add the field to `src/health_types.py`.
2. Fetch and normalize it in `src/google_collector.py`: find the real request and response shape with `scripts/probe_google_health.py` first.
3. Map it to a metric in `src/victoria_writer.py` (Fitbit-era naming: `fitbit_<thing>_<unit>`).
4. Add tests built from real (trimmed) response shapes, and document the metric in README.md.
