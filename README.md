# SyncBit

**Fitbit health data to Victoria Metrics, via the Google Health API**

SyncBit collects health data from your Fitbit device through the [Google Health API](https://developers.google.com/health) and writes it to Victoria Metrics in Prometheus format. It handles OAuth2 authorization, automatic token refresh, historical backfill and periodic sync.

> **The legacy Fitbit Web API was shut down by Google on 2026-11-02** (it returns `410 Gone`). Since 4.0.0, SyncBit uses the Google Health API by default (`HEALTH_PROVIDER=google`). The Fitbit provider is kept only for reference.

## Features

- 🔐 **OAuth2 with PKCE**: Google authorization with automatic token refresh; warns when a permission was left unticked on the consent screen
- 📊 **Comprehensive health metrics**: activity, heart rate, sleep, SpO2, breathing rate, HRV, VO2 max, skin temperature
- ⚡ **Fast**: 14-day range requests and concurrent calls under a client-side throttle at Google's 300 requests/minute quota; a year of history backfills in about 2 minutes
- 🔄 **Current-day sync**: syncs yesterday (complete) and today (partial) every 15 minutes
- 📈 **Resumable backfill**: progress is saved after every chunk and never advances past an unwritten day
- 🐳 **Docker & Kubernetes ready**: Alpine multi-stage image, non-root, no pip at runtime; Helm chart with External Secrets support
- 🔒 **Secret management**: External Secrets Operator + 1Password in production, direnv + 1Password locally
- ⚙️ **Configurable collection**: toggle individual metrics

## Collected Metrics

Metric names are unchanged from the Fitbit era, so existing dashboards keep working. Every metric carries the labels `user` (`FITBIT_USER_ID`) and `device` (`DEVICE_LABEL`, default `fitbit_air`).

### Activity
- **Steps** (`fitbit_steps_total`)
- **Distance** (`fitbit_distance_km`)
- **Calories** (`fitbit_calories_total`)
- **Active Minutes** (`fitbit_active_minutes`) by `type`: `lightly_active`, `fairly_active`, `very_active`. Google reports no sedentary minutes.

### Heart
- **Resting Heart Rate** (`fitbit_resting_heart_rate_bpm`)
- **Heart Rate Zones** (`fitbit_heart_rate_zone_minutes`, `fitbit_heart_rate_zone_calories`) by `zone`: `out_of_range`, `fat_burn`, `cardio`, `peak`. These are Google's LIGHT/MODERATE/VIGOROUS/PEAK zones, mapped to the Fitbit-era names.
- **Heart Rate Variability** (`fitbit_hrv_rmssd_ms`): average nightly HRV

### Sleep
- **Sleep Duration** (`fitbit_sleep_minutes_total`): all sessions that ended on the date (main sleep and naps)
- **Sleep Stages** (`fitbit_sleep_stage_minutes`) by `stage`: `deep`, `light`, `rem`, `wake`

Sleep is attributed to the date you woke up.

### Respiratory
- **Breathing Rate** (`fitbit_breathing_rate_bpm`)
- **SpO2** (`fitbit_spo2_avg_percent`, `fitbit_spo2_min_percent`, `fitbit_spo2_max_percent`)

### Fitness & Temperature
- **VO2 Max** (`fitbit_vo2_max`)
- **Skin Temperature** (`fitbit_temp_skin_relative_celsius`): nightly temperature relative to your personal baseline

### Intraday (optional)
- `fitbit_steps_intraday`, `fitbit_distance_intraday`, `fitbit_calories_intraday`, `fitbit_heart_rate_intraday`

### Device Information
- `fitbit_device_battery_percent`, `fitbit_device_last_sync_timestamp`. These require the optional `googlehealth.settings.readonly` scope, which SyncBit does not request yet, so they are currently empty.

> **Upgrading from 3.x:** metrics written before 4.0.0 are labelled `device="charge6"`. Dashboards that filter on `device` should use `device=~"charge6|fitbit_air"` or drop the filter.

## Prerequisites

1. **Google Cloud project with the Google Health API**
   - Sign in to the **Google Health** mobile app with the Google account that owns your Fitbit data (create a profile or migrate your Fitbit account). Without this the API answers `ACCOUNT_NOT_LINKED`.
   - Enable the API: https://console.cloud.google.com/apis/library/health.googleapis.com
   - **Google Auth Platform**:
     - [Branding](https://console.cloud.google.com/auth/branding): app name and contact emails
     - [Audience](https://console.cloud.google.com/auth/audience): User type **External**. Add yourself as a test user, or **Publish app**.
     - [Data Access](https://console.cloud.google.com/auth/scopes): add the four `googlehealth.*.readonly` scopes listed below
     - [Clients](https://console.cloud.google.com/auth/clients): a **Web application** client with redirect URI `http://localhost:8080/callback`. Note the client ID and secret; Google shows the secret only once.
   - Scopes requested:
     - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
     - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
     - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
     - `https://www.googleapis.com/auth/googlehealth.profile.readonly`

2. **Victoria Metrics** with the Prometheus import API (`/api/v1/import/prometheus`) and basic-auth credentials

3. **For Kubernetes**: Helm 3, and optionally [External Secrets Operator](https://external-secrets.io/) with [1Password](https://1password.com/)

### Testing vs. production OAuth app

While the app's publishing status is **Testing**, Google refresh tokens expire after **7 days** and you must re-authorize weekly. Click **Publish app** on the Audience page to avoid this.

An unverified app in production works for up to 100 users; the consent screen shows "Google hasn't verified this app" (continue via **Advanced**). Verification is not needed for personal use. **Re-authorize once after publishing**: tokens issued while the app was in testing keep the 7-day expiry.

## Quick Start

### 1. Clone

```bash
git clone https://github.com/origox/syncbit.git
cd syncbit
```

### 2. Configure environment

**Recommended: direnv + 1Password.** Create `.envrc`:

```bash
export GOOGLE_CLIENT_ID=$(op read "op://kubernetes/syncbit/GOOGLE_CLIENT_ID")
export GOOGLE_CLIENT_SECRET=$(op read "op://kubernetes/syncbit/GOOGLE_CLIENT_SECRET")
export VICTORIA_ENDPOINT=$(op read "op://kubernetes/victoria-metrics/ENDPOINT")
export VICTORIA_USER=$(op read "op://kubernetes/victoria-metrics/USER")
export VICTORIA_PASSWORD=$(op read "op://kubernetes/victoria-metrics/PASSWORD")
export DATA_DIR=./data
```

then run `direnv allow`. On WSL use `op.exe` instead of `op`.

**Alternative: `.env` file** (gitignored, loaded by python-dotenv; real environment variables take precedence):

```env
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret
VICTORIA_ENDPOINT=https://victoria-metrics.example.com/api/v1/import/prometheus
VICTORIA_USER=your_username
VICTORIA_PASSWORD=your_password
DATA_DIR=./data
```

### 3. Local development (Devbox)

```bash
devbox shell
devbox run install

python main.py --authorize            # opens the consent URL, waits on localhost:8080
python main.py --authorize --manual   # or: paste the code from the redirect URL yourself
python main.py                        # run the sync scheduler
```

### 4. Docker

```bash
docker build -t syncbit:latest .

# Authorize (first time only)
docker run --rm -it -v $(pwd)/data:/app/data -p 8080:8080 \
  -e GOOGLE_CLIENT_ID -e GOOGLE_CLIENT_SECRET \
  -e VICTORIA_ENDPOINT -e VICTORIA_USER -e VICTORIA_PASSWORD \
  syncbit:latest --authorize

# Run the sync service
docker run -d --name syncbit --restart unless-stopped -v $(pwd)/data:/app/data \
  -e GOOGLE_CLIENT_ID -e GOOGLE_CLIENT_SECRET \
  -e VICTORIA_ENDPOINT -e VICTORIA_USER -e VICTORIA_PASSWORD \
  syncbit:latest
```

The image runs as non-root user `syncbit` (UID 1000), so `./data` must be writable by UID 1000.

### 5. Kubernetes (Helm)

```bash
helm install syncbit oci://ghcr.io/origox/charts/syncbit \
  --namespace syncbit --create-namespace \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=onepassword \
  --set externalSecrets.secretStore.kind=ClusterSecretStore
```

With External Secrets, the chart reads `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from the 1Password item `syncbit`, and `USER`, `PASSWORD` and `ENDPOINT` from `victoria-metrics`, both in the `kubernetes` vault. Without it, pass `secrets.googleClientId`, `secrets.googleClientSecret` and the `secrets.victoria*` values. See [helm/syncbit/README.md](helm/syncbit/README.md).

## Authorization

You must authorize once before SyncBit can sync. Tokens are stored in `$DATA_DIR/google_tokens.json` and **rewritten on every hourly refresh**. On Kubernetes the file must therefore live on the PVC, never in a read-only ConfigMap or Secret.

On Google's consent screen, **tick every permission**. A scope left unticked is silently missing from the token, and its data returns `403`. SyncBit logs a warning naming any missing scope.

### Locally

`python main.py --authorize` prints the consent URL and waits for the redirect on port 8080. If something else already uses port 8080 or the browser can't reach it, use `--manual`. After approving, the browser lands on a `localhost:8080/callback?...code=...` page that may fail to load; paste the `code` value at the prompt. The code expires after about 10 minutes.

### Kubernetes

Run the manual flow inside the pod; no port-forward is needed:

```bash
kubectl exec -it deploy/syncbit -- python main.py --authorize --manual
```

Alternatively authorize locally and copy the file onto the PVC with `kubectl cp ./data/google_tokens.json <pod>:/app/data/`. The local copy and the cluster then share one refresh token, so re-authorizing on one side affects the other. The Helm chart also offers an authorization Job (`authorization.createJob=true`); see the chart README.

## Secret Management

Secrets are read from `/run/secrets/{name}` if that file exists, otherwise from the environment variable:

| Secret file | Environment variable |
|---|---|
| `google_client_id` | `GOOGLE_CLIENT_ID` |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` |
| `victoria_endpoint` | `VICTORIA_ENDPOINT` |
| `victoria_user` | `VICTORIA_USER` |
| `victoria_password` | `VICTORIA_PASSWORD` |
| `fitbit_client_id`, `fitbit_client_secret` | `FITBIT_CLIENT_ID`, `FITBIT_CLIENT_SECRET` (only when `HEALTH_PROVIDER=fitbit`) |

Production flow: `1Password → External Secrets Operator → Kubernetes Secret → pod`.

## Configuration

### Required

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `VICTORIA_ENDPOINT` | Victoria Metrics import endpoint, e.g. `https://vm.example.com/api/v1/import/prometheus` |
| `VICTORIA_USER` | Victoria Metrics username |
| `VICTORIA_PASSWORD` | Victoria Metrics password |

### General

| Variable | Description | Default |
|---|---|---|
| `HEALTH_PROVIDER` | `google` or `fitbit` (legacy, shut down) | `google` |
| `GOOGLE_REDIRECT_URI` | OAuth redirect URI registered on the client | `http://localhost:8080/callback` |
| `SYNC_INTERVAL_MINUTES` | Sync interval | `15` |
| `DATA_DIR` | Directory for tokens, state and log | `/app/data` |
| `FITBIT_USER_ID` | Value of the `user` label | `default` |
| `DEVICE_LABEL` | Value of the `device` label | `fitbit_air` |
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR | `INFO` |
| `BACKFILL_START_DATE` | Backfill start (YYYY-MM-DD) | account membership start date |
| `INCLUDE_TODAY_DATA` | Also sync today's partial data, daily and intraday | `true` |

### Throughput

| Variable | Description | Default |
|---|---|---|
| `GOOGLE_MAX_REQUESTS_PER_MINUTE` | Client-side throttle; Google's per-user quota is 300/min. `0` disables it. | `300` |
| `GOOGLE_MAX_CONCURRENT_REQUESTS` | Requests in flight at once | `8` |

### Metric toggles

| Variable | Default |
|---|---|
| `COLLECT_SLEEP` | `true` |
| `COLLECT_SPO2` | `true` |
| `COLLECT_BREATHING_RATE` | `true` |
| `COLLECT_HRV` | `true` |
| `COLLECT_CARDIO_FITNESS` (VO2 max) | `true` |
| `COLLECT_TEMPERATURE` | `true` |
| `COLLECT_DEVICE_INFO` | `true` |

### Intraday

| Variable | Description | Default |
|---|---|---|
| `ENABLE_INTRADAY_COLLECTION` | Collect intraday series | `false` |
| `INTRADAY_DETAIL_LEVEL` | Window for steps/distance/calories (`1min`, `5min`, `15min`) | `5min` |
| `INTRADAY_HEART_RATE_DETAIL` | Window for heart rate (`1sec`, `1min`, `5min`, `15min`) | `1min` |
| `INTRADAY_RESOURCES` | `steps`, `calories`, `distance`, `heart_rate` | `steps,calories,distance,heart_rate` |
| `ENABLE_INTRADAY_BACKFILL` | Backfill intraday history | `false` |
| `INTRADAY_BACKFILL_DAYS` | Days of intraday backfill | `30` |

## How It Works

1. **Authorization**: OAuth2 authorization code flow with PKCE and offline access; granted scopes are recorded in the token file.
2. **Token refresh**: access tokens last about 1 hour and are refreshed automatically. The token file is re-read before every refresh, and the refresh token is preserved because Google omits it from refresh responses.
3. **Backfill**: on start, fills from the last synced date (or `BACKFILL_START_DATE`, or the account's membership start) to yesterday, in 14-day chunks. Each chunk is one request per data type, run concurrently, then written and checkpointed.
4. **Scheduled sync**: every `SYNC_INTERVAL_MINUTES`, fetches yesterday (complete) and today (partial) in one range request. Only complete days advance the sync state. When intraday collection is on, it re-fetches yesterday's intraday series and, with `INCLUDE_TODAY_DATA`, today's.
5. **Rate limits**: requests are spaced to the per-user quota; on `429` the scheduler backs off and retries, and after 3 consecutive rate limits it stops and resumes on the next run. Transient `5xx` responses are retried with exponential backoff.
6. **Export**: records are converted to Prometheus exposition format with millisecond timestamps and POSTed to Victoria Metrics with basic auth.

## Data Flow

```
Google Health API ──► GoogleHealthCollector ──► DailyRecord / IntradayRecord ──► VictoriaMetricsWriter ──► Victoria Metrics
   (OAuth2 + PKCE)     (dailyRollUp, list,        (provider-neutral types,        (Prometheus format,
                        rollUp)                    src/health_types.py)            HTTP POST)
```

## Project Structure

```
syncbit/
├── main.py                      # Entry point and CLI (--authorize, --manual, --log-level)
├── src/
│   ├── config.py                # Configuration and secret loading
│   ├── collector.py             # Provider-neutral HealthDataCollector protocol, RateLimitError
│   ├── health_types.py          # Provider-neutral record types
│   ├── google_auth.py           # Google OAuth2 (PKCE) and token management
│   ├── google_collector.py      # Google Health API collector
│   ├── fitbit_auth.py           # Legacy Fitbit OAuth2
│   ├── fitbit_collector.py      # Legacy Fitbit Web API collector
│   ├── victoria_writer.py       # Victoria Metrics writer
│   ├── scheduler.py             # Backfill and periodic sync
│   └── sync_state.py            # Resumable progress (data/sync_state.json)
├── scripts/
│   └── probe_google_health.py   # Dump raw Google Health API responses for debugging
├── tests/                       # pytest suite
├── helm/syncbit/                # Helm chart (External Secrets support)
├── k8s/                         # Plain Kubernetes manifests
├── docs/                        # Architecture and query docs
└── Dockerfile                   # Alpine multi-stage build
```

## Troubleshooting

### Authorization

| Symptom | Cause and fix |
|---|---|
| `403 MISSING_OAUTH_SCOPE` / warning "Google token is missing scopes" | A permission was unticked on the consent screen. Revoke SyncbitHealth at https://myaccount.google.com/connections and authorize again, ticking every box. |
| `invalid_grant` on refresh after ~7 days | The OAuth app is in Testing. Publish it and re-authorize. |
| Browser shows 404 or "not found" on `localhost:8080/callback` | Another program owns port 8080, or you used `--manual` (no callback server; expected). Paste the `code` at the prompt. |
| `ACCOUNT_NOT_LINKED` | Sign in to the Google Health mobile app first. |

```bash
python main.py --authorize                    # re-authorize
python scripts/probe_google_health.py         # check which endpoints and scopes work
```

### No data in Grafana after a backfill

Victoria Metrics caches query results for time ranges older than a few minutes, and does not invalidate that cache when historical samples are imported. If a dashboard was opened before the backfill, it can keep showing "No data". Reset the cache:

```bash
curl -X POST http://<victoria-metrics>:8428/internal/resetRollupResultCache
```

### Victoria Metrics connection

```bash
curl -u "$VICTORIA_USER:$VICTORIA_PASSWORD" \
  --data-binary 'syncbit_test 1' "$VICTORIA_ENDPOINT"
```

### Timezone

Day boundaries use the container's local time zone. The image ships without time zone data, so it runs in UTC.

## Monitoring

```bash
tail -f data/syncbit.log                 # local
docker logs -f syncbit                   # Docker
kubectl logs -f deployment/syncbit       # Kubernetes
```

The Deployment's liveness and readiness probes run `Config.validate()`.

## Dependency Management

This project uses [Renovate](https://docs.renovatebot.com/) for automated dependency updates with a carefully configured strategy that balances security, stability, and convenience.

### Update Strategy

**Python Versions:**
- **Docker images** (`Dockerfile`): Updates automatically when new Python versions are available
- **Devbox** (`devbox.json`): Requires manual approval via Renovate Dashboard
  - Reason: nixpkgs may lag behind official Python releases by several weeks
  - Example: Python 3.14 available in Docker Hub, but `python314` package not yet in nixpkgs

**Auto-Merge Rules:**
- **CLI Tools** (direnv, gh, docker-client): Patch + minor updates auto-merge immediately
  - Low risk, dev-only tools
  - No CI wait required
- **All Other Dependencies**: Patch + digest updates auto-merge after CI passes
  - Requires: test suite, linting, and Docker build/security scan to pass
  - Examples: Python base image digest updates, pip package patches
- **Minor Updates**: Require manual review and merge
- **Major Updates**: Require approval via Renovate Dependency Dashboard

**Security & Reproducibility:**
- Docker images pinned to SHA256 digests (`python:3.14-alpine@sha256:...`)
- GitHub Actions pinned to commit SHAs (`actions/checkout@v6@sha256:...`)
- Prevents tag poisoning and ensures immutable builds

### Checking for Updates

Renovate runs automatically every Monday before 6am UTC. You can also:

```bash
# View pending updates in GitHub
# Go to: Issues → Dependency Dashboard

# Or check Renovate logs
gh run list --workflow=renovate.yml --limit 5
gh run view <run-id> --log
```

### Python Version Sync

When upgrading Python versions:

1. Check if nixpkgs has the version available:
   ```bash
   devbox search python314  # Example for Python 3.14
   ```

2. If available, approve the devbox update in Renovate Dashboard

3. If not available yet:
   - Docker images will update first (production gets security patches)
   - Devbox update waits until nixpkgs catches up (dev environment stays stable)
   - This temporary version drift is intentional and safe

### Manual Dependency Updates

```bash
# Update Python packages
pip install --upgrade -r requirements.txt

# Update devbox packages
devbox update

# Check outdated packages
pip list --outdated
```

## Development

### Commit Message Format

This project uses [Conventional Commits](https://www.conventionalcommits.org/). All commits must follow this format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc)
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `test`: Adding or updating tests
- `chore`: Maintenance tasks
- `build`: Build system changes
- `ci`: CI/CD changes

**Examples:**
```
feat: add sleep tracking metrics
fix(auth): handle token refresh edge case
docs: update deployment instructions
refactor(collector)!: change API response structure
```

A git hook validates commit messages automatically.

### Adding New Metrics

1. Add the field to the provider-neutral types in [src/health_types.py](src/health_types.py)
2. Fetch and normalize it in [src/google_collector.py](src/google_collector.py) (find the request shape with `scripts/probe_google_health.py`)
3. Add metric formatting in [src/victoria_writer.py](src/victoria_writer.py)
4. Add tests and update this README

### Testing

```bash
devbox run install-dev                       # test dependencies
devbox run test                              # all tests
devbox run test-cov                          # with coverage
python -m pytest tests/test_google_collector.py -v
python -m pytest -k "rate_limit" -v
```

### Testing Locally

```bash
# Enter dev environment
devbox shell

# Install dependencies
pip install -r requirements.txt

# Run with debug logging
python main.py --log-level DEBUG
```

## Versioning

This project uses [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/) for automated version management.

### Version Format

- `MAJOR.MINOR.PATCH` (e.g., `v1.2.3`)
- `MAJOR`: Breaking changes
- `MINOR`: New features (backwards compatible)
- `PATCH`: Bug fixes (backwards compatible)

### Docker Image Tags

Docker images are automatically tagged with multiple formats on each release:

```
ghcr.io/origox/syncbit:latest          # Latest stable release
ghcr.io/origox/syncbit:4.0.0           # Full semantic version
ghcr.io/origox/syncbit:4.0             # Major.minor version
ghcr.io/origox/syncbit:4               # Major version
ghcr.io/origox/syncbit:sha-<commit>    # Specific commit (all branches)
```

**Production Recommendation:** Use specific version tags (e.g., `4.0.0`) rather than `latest` for stability. The Helm chart is published to `oci://ghcr.io/origox/charts/syncbit` with the same version.

### Commit Message Format

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat:` - New feature (triggers MINOR version bump)
- `fix:` - Bug fix (triggers PATCH version bump)
- `docs:` - Documentation changes (no version bump)
- `chore:` - Maintenance tasks (no version bump)
- `refactor:` - Code refactoring (triggers PATCH version bump)
- `perf:` - Performance improvements (triggers PATCH version bump)
- `test:` - Test changes (no version bump)
- `ci:` - CI/CD changes (no version bump)
- `BREAKING CHANGE:` - Breaking changes (triggers MAJOR version bump)

**Examples:**
```bash
feat(metrics): add support for sleep data collection
fix(auth): resolve token refresh timing issue
docs(readme): update installation instructions
chore(deps): update dependencies to latest versions
```

### Release Process

Releases are fully automated:

1. Merge commits to `main` branch following conventional commit format
2. Semantic-release automatically:
   - Analyzes commit messages
   - Determines next version number
   - Generates CHANGELOG.md
   - Creates GitHub release with notes
   - Triggers Docker build with version tags

**Repository Setup Required:**

For automated Docker builds to trigger on releases, create a Personal Access Token (PAT) with `repo` scope and add it as a repository secret named `RELEASE_TOKEN`:

1. Generate PAT: [GitHub Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens)
2. Grant `repo` scope
3. Add as secret: Repository Settings → Secrets and variables → Actions → New repository secret
4. Name: `RELEASE_TOKEN`

> **Note:** Without `RELEASE_TOKEN`, the release workflow falls back to `GITHUB_TOKEN` which cannot trigger other workflows (Docker builds). Releases will be created but Docker images won't be automatically tagged with version numbers.

### Viewing Releases

- [GitHub Releases](https://github.com/origox/syncbit/releases) - View all releases and changelogs
- [Container Images](https://github.com/origox/syncbit/pkgs/container/syncbit) - Browse available Docker images

## License

MIT