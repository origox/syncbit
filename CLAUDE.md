# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SyncBit is a Python service that syncs Fitbit health data to Victoria Metrics. It handles OAuth2 authentication, automatic token refresh, rate limit management, and periodic/backfill data synchronization.

**Key Characteristics:**
- Long-running scheduler process (not one-shot script)
- Fitbit API has strict rate limits (150 req/hour, 30s delays between requests)
- Dual-mode secret loading: `/run/secrets/*` files (K8s) OR environment variables (local)
- State persistence via JSON files for resumable backfill

## Development Commands

### Local Development
```bash
# Setup environment (uses devbox/nix)
devbox shell

# Install dependencies
devbox run install        # Production deps only
devbox run install-dev    # Production + dev deps

# Run application
python main.py --authorize  # First-time OAuth flow
python main.py              # Start sync scheduler
python main.py --log-level DEBUG  # Debug mode

# Testing
devbox run test           # Run all tests
devbox run test-cov       # With coverage report
python -m pytest tests/test_config.py -v  # Single file
python -m pytest -k "test_auth" -v        # Pattern match
```

### Docker
```bash
# Build (Alpine-based multi-stage)
docker build -t syncbit:latest .

# Run authorization (requires browser access on localhost:8080)
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  -p 8080:8080 \
  -e FITBIT_CLIENT_ID \
  -e FITBIT_CLIENT_SECRET \
  -e VICTORIA_ENDPOINT \
  -e VICTORIA_USER \
  -e VICTORIA_PASSWORD \
  syncbit:latest --authorize

# Run sync service
docker run -d --name syncbit \
  -v $(pwd)/data:/app/data \
  -e FITBIT_CLIENT_ID \
  -e FITBIT_CLIENT_SECRET \
  -e VICTORIA_ENDPOINT \
  -e VICTORIA_USER \
  -e VICTORIA_PASSWORD \
  syncbit:latest
```

**Important:** The data directory must be owned by UID 1000 (container's `syncbit` user). If permission errors occur, run: `sudo chown -R $USER:$USER ./data` or recreate it.

### Code Quality
```bash
# Formatting & Linting (defined in pyproject.toml)
black --check --diff src tests main.py
ruff check src tests main.py

# Auto-fix
black src tests main.py
ruff check --fix src tests main.py
```

## Architecture

### Module Responsibilities

**src/config.py** - Configuration and secret management
- Dual-mode secret loading: tries `/run/secrets/{name}` first, falls back to env vars
- Secrets: `fitbit_client_id`, `fitbit_client_secret`, `victoria_endpoint`, `victoria_user`, `victoria_password`
- Validates required config on startup via `Config.validate()`

**src/fitbit_auth.py** - OAuth2 authentication and token lifecycle
- Token file: `data/fitbit_tokens.json` (access + refresh tokens)
- Access tokens expire every 8 hours, auto-refresh using refresh token
- **Critical pattern:** `refresh_access_token()` reloads from disk first to avoid stale in-memory tokens
- Authorization flow uses local HTTP server on port 8080 bound to `0.0.0.0` (Docker/K8s compatible)

**src/fitbit_collector.py** - Fetches data from Fitbit API
- Rate limit handling: raises `RateLimitError` with `retry_after` from API header
- Uses user ID `-` (means authenticated user, avoids extra API call)
- Two API calls per day: activity summary + heart rate data
- Max 3 retries per request with 5-second delays

**src/scheduler.py** - Orchestrates backfill and periodic sync
- Uses APScheduler's `BlockingScheduler`
- Backfill: processes 10-day batches with 30s delays between dates
- Rate limit strategy: stops after 3 consecutive 429s, preserves progress
- Scheduled sync: every 15 minutes, fetches yesterday's complete data

**src/victoria_writer.py** - Writes metrics to Victoria Metrics
- Prometheus exposition format with millisecond timestamps
- Basic auth for Victoria Metrics endpoint
- Batch writes to reduce HTTP requests

**src/sync_state.py** - Progress tracking for resumable backfill
- JSON file: `data/sync_state.json`
- Tracks `last_successful_date` to resume from correct point
- Single-writer design (no locking needed)

**main.py** - Entry point and CLI
- **Location:** Root directory (not in src/)
- Argument parsing: `--authorize` for OAuth, `--log-level` for debug
- Logging setup: writes to both stdout and `data/syncbit.log`

### Critical Design Patterns

**Secret Loading (config.py):**
```python
# Production: reads /run/secrets/fitbit_client_id
# Dev: reads FITBIT_CLIENT_ID env var
FITBIT_CLIENT_ID = _load_secret("fitbit_client_id")
```

**Rate Limit Handling (fitbit_collector.py):**
```python
if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    raise RateLimitError(f"Rate limited", retry_after=retry_after)
```

**Token Refresh (fitbit_auth.py):**
```python
def refresh_access_token(self):
    # CRITICAL: Reload from disk first (may be updated by another process)
    self.token_manager._load_tokens()
    # Then refresh...
```

**Backfill Batching (scheduler.py):**
```python
# Process in 10-day batches
# 30-second delays between dates to respect rate limits
# Write batch to Victoria Metrics, update state, then continue
```

### Docker/Kubernetes Specifics

**Dockerfile:**
- Entry point location: `main.py` (root, not `src/main.py`)
- Dependencies: installed from `requirements.txt` (not `pyproject.toml`)
- Multi-stage build: builder stage installs deps, runtime stage copies them
- Non-root user: `syncbit` (UID 1000)
- Bind address: `0.0.0.0:8080` for OAuth callback server (container-friendly)

**Secret Loading:**
- K8s mounts secrets to `/run/secrets/*` (External Secrets Operator pattern)
- Config automatically detects environment and uses appropriate source
- No code changes needed between local and production

## Important Constraints

**Fitbit API Rate Limits:**
- 150 requests/hour per user = ~1 request per 24 seconds
- Implementation uses 30-second delays for safety margin
- Backfill of 120 days takes ~1 hour with 30s delays
- Each day requires 2 API calls (activity + heart rate)

**Token Expiry:**
- Access tokens expire every 8 hours
- Must handle refresh during long-running backfills
- Refresh token is long-lived but can be revoked (requires re-authorization)

**Data Completeness:**
- Yesterday's data is most reliable (complete day)
- Today's data is incomplete (don't sync it)
- Scheduled sync runs every 15 minutes but always fetches yesterday

## Common Issues

**"No module named src.main"** - The entry point is `main.py` in the root, not `src/main.py`. Dockerfile must copy `main.py` to `/app/` and use `ENTRYPOINT ["python", "main.py"]`.

**"Permission denied: /app/data/syncbit.log"** - Data directory ownership mismatch. Container runs as UID 1000, mounted volume must be owned by same UID. Fix: `chown -R $USER:$USER ./data` or recreate directory.

**OAuth callback "ERR_EMPTY_RESPONSE"** - Callback server bound to `localhost` instead of `0.0.0.0`. In containers, must bind to all interfaces. Fixed in `fitbit_auth.py:249`.

**Secrets not loading** - Check both `/run/secrets/{name}` files AND environment variables. Config tries files first, falls back to env vars. See `config.py:_load_secret()` for mapping.

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
- Docker images: Pinned to SHA256 (`python:3.11-alpine@sha256:...`)
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

1. Add collection in `src/fitbit_collector.py` - new method to fetch from Fitbit API
2. Add formatting in `src/victoria_writer.py` - convert to Prometheus format
3. Update `scheduler.py` if new API endpoint requires different rate limiting
4. Update README.md with new metric documentation
5. Add tests in `tests/` for new functionality

**Pattern to follow:**
- Each metric type has dedicated collection method
- All API calls go through `_make_request()` for consistent error handling
- Metrics use labels: `{user="...", device="charge6"}`
- Timestamps in milliseconds (Victoria Metrics requirement)
