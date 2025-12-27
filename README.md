# SyncBit

**Fitbit to Victoria Metrics Data Synchronization**

SyncBit automatically collects activity data from your Fitbit account (Fitbit Charge 6) and pushes metrics to Victoria Metrics in Prometheus format. It handles OAuth2 authentication, automatic token refresh, and periodic data synchronization.

## Features

- 🔐 **OAuth2 Authentication** - Secure Fitbit authorization with automatic token refresh
- 📊 **Comprehensive Metrics** - Collects steps, heart rate, activity minutes, calories, and more
- 🔄 **Automatic Sync** - Runs every 15 minutes to keep data up-to-date
- 📈 **Historical Backfill** - Automatically syncs historical data from your Fitbit account
- 🐳 **Docker & Kubernetes Ready** - Alpine-based multi-stage build, non-root user
- 🔒 **Secure Secret Management** - External Secrets Operator + 1Password integration for production
- 🏥 **Health Monitoring** - Built-in health checks and structured logging

## Collected Metrics

- **Steps** (`fitbit_steps_total`)
- **Distance** (`fitbit_distance_km`)
- **Calories** (`fitbit_calories_total`)
- **Active Minutes** (`fitbit_active_minutes`) - by activity type
- **Floors** (`fitbit_floors_total`)
- **Elevation** (`fitbit_elevation_meters`)
- **Resting Heart Rate** (`fitbit_resting_heart_rate_bpm`)
- **Heart Rate Zones** (`fitbit_heart_rate_zone_minutes`, `fitbit_heart_rate_zone_calories`)

## Prerequisites

1. **Fitbit Developer Account**
   - Create an app at [dev.fitbit.com/apps](https://dev.fitbit.com/apps)
   - Set OAuth 2.0 Application Type to "Personal"
   - Set Redirect URL to `http://localhost:8080/callback`
   - Note your Client ID and Client Secret

2. **Victoria Metrics Instance**
   - Running Victoria Metrics with Prometheus import API enabled
   - Authentication credentials

3. **For Production (Kubernetes)**
   - [External Secrets Operator](https://external-secrets.io/) installed in cluster
   - [1Password](https://1password.com/) vault for secret storage
   - Helm 3.x for deployment

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd syncbit

# Copy environment template
### 2. Configure Environment

**Recommended: Using direnv + 1Password (for local development)**

```bash
# Install direnv if not present
cp .envrc.example .envrc
# Edit .envrc to point to your 1Password vault or secret source
direnv allow
```

This will automatically load secrets into your environment for all local commands and Docker builds/execs.

**Alternative: Using .env file**

```bash
cp .env.example .env
# Edit .env with your credentials
```

```env
FITBIT_CLIENT_ID=your_client_id_here
FITBIT_CLIENT_SECRET=your_client_secret_here
VICTORIA_ENDPOINT=https://victoria-metrics.example.com/api/v1/import/prometheus
VICTORIA_USER=your_username_here
VICTORIA_PASSWORD=your_password_here
```

### 3. Local Development (with Devbox)

```bash
# Install dependencies
devbox shell
devbox run install

# Authorize with Fitbit
python main.py --authorize

# Run the sync scheduler
python main.py
```

### 4. Docker Deployment

```bash

# Build image (Alpine-based, multi-stage)
docker build -t syncbit:latest .

# Run authorization (first time only)
# If using direnv/.envrc, environment is already loaded—no need for --env-file
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  -p 8080:8080 \
  syncbit:latest --authorize

# Run sync service
docker run -d \
  --name syncbit \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  syncbit:latest
```

> **Note:** If you use a `.env` file instead of direnv, add `--env-file .env` to the `docker run` commands above.

**Note:** The Docker image runs as non-root user `syncbit` (UID 1000) for security.

### 5. Kubernetes Deployment

**Production (with External Secrets Operator + 1Password):**

```bash
# Install using Helm with ESO support
helm repo add syncbit https://origox.github.io/syncbit
helm install syncbit syncbit/syncbit \
  --namespace syncbit \
  --create-namespace \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=1password-store \
  --set externalSecrets.secretStore.provider=onepassword

# Check status
kubectl get pods -n syncbit
kubectl logs -f deployment/syncbit -n syncbit
```

**Development (with plain Kubernetes secrets):**

```bash
# Create namespace
kubectl create namespace syncbit

# Create secrets from env vars
kubectl create secret generic syncbit-secrets -n syncbit \
  --from-literal=fitbit-client-id="$FITBIT_CLIENT_ID" \
  --from-literal=fitbit-client-secret="$FITBIT_CLIENT_SECRET" \
  --from-literal=victoria-user="$VICTORIA_USER" \
  --from-literal=victoria-password="$VICTORIA_PASSWORD"

# Deploy using kubectl
kubectl apply -f k8s/ -n syncbit
```

## Initial Authorization

**Important:** You must complete OAuth authorization before the app can sync data.

### Local Authorization

1. Run: `python main.py --authorize`
2. Browser opens automatically to Fitbit authorization page
3. Log in and approve access
4. Authorization complete - tokens saved to `data/fitbit_tokens.json`

### Kubernetes Authorization

Since OAuth requires browser access, you have two options:

**Option A: Authorize Locally First**
```bash
# Run locally to authorize
python main.py --authorize

# Copy tokens to Kubernetes Secret
kubectl create secret generic syncbit-tokens \
  --from-file=fitbit_tokens.json=data/fitbit_tokens.json

# Mount secret in deployment (add to deployment.yaml volumes)
```

**Option B: Port Forward**
```bash
# Deploy without running
kubectl scale deployment syncbit --replicas=0

# Port forward to pod
kubectl port-forward deployment/syncbit 8080:8080

# Run authorization in pod
kubectl exec -it deployment/syncbit -- python main.py --authorize
# Complete authorization in your browser

# Scale back up
kubectl scale deployment syncbit --replicas=1
```

## Secret Management


### Local Development

Secrets are loaded from **environment variables**:
- `.envrc` with direnv + 1Password CLI integration (recommended)
- `.env` file (loaded by python-dotenv)
- System environment variables

### Production (Kubernetes with External Secrets)

Secrets are loaded from **mounted files** at `/run/secrets/*`:

```
1Password Vault → External Secrets Operator → Kubernetes Secret → Pod Volume → /run/secrets/
```

The application automatically detects the environment:
- If `/run/secrets/{name}` exists → read from file (production)
- Otherwise → fall back to environment variable (local dev)

**Required secrets in production:**
- `/run/secrets/fitbit_client_id`
- `/run/secrets/fitbit_client_secret`
- `/run/secrets/victoria_user`
- `/run/secrets/victoria_password`

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FITBIT_CLIENT_ID` | Fitbit OAuth Client ID | Required (env or /run/secrets/fitbit_client_id) |
| `FITBIT_CLIENT_SECRET` | Fitbit OAuth Client Secret | Required (env or /run/secrets/fitbit_client_secret) |
| `FITBIT_REDIRECT_URI` | OAuth redirect URI | `http://localhost:8080/callback` |
| `VICTORIA_ENDPOINT` | Victoria Metrics import endpoint |
| `VICTORIA_USER` | Victoria Metrics username | Required (env or /run/secrets/victoria_user) |
| `VICTORIA_PASSWORD` | Victoria Metrics password | Required (env or /run/secrets/victoria_password) |
| `SYNC_INTERVAL_MINUTES` | Sync interval in minutes | `15` |
| `DATA_DIR` | Data directory for tokens/state | `/app/data` |
| `FITBIT_USER_ID` | User identifier for metric labels | `default` |
| `LOG_LEVEL` | Logging level | `INFO` |

## How It Works

1. **Authorization**: OAuth2 flow to obtain access and refresh tokens
2. **Token Management**: Automatically refreshes tokens every 8 hours
3. **Backfill**: On first run, syncs historical data from Fitbit
4. **Scheduled Sync**: Collects yesterday's complete data every 15 minutes
5. **Metrics Export**: Converts Fitbit data to Prometheus format
6. **Victoria Metrics**: POSTs metrics with authentication

## Data Flow

```
Fitbit API → Data Collector → Format Converter → Victoria Metrics
     ↓              ↓                ↓                    ↓
  OAuth2      Daily Summary    Prometheus Format    HTTP POST
```

## Project Structure

```
syncbit/
├── src/
│   ├── __init__.py           # Package init
│   ├── config.py             # Configuration + secret management
│   ├── main.py               # Entry point
│   ├── fitbit_auth.py        # OAuth2 authentication
│   ├── fitbit_collector.py   # Data collection from Fitbit
│   ├── victoria_writer.py    # Victoria Metrics writer
│   ├── scheduler.py          # Sync scheduler
│   └── sync_state.py         # State management
├── tests/                    # Test suite (74 tests, 69% coverage)
├── k8s/                      # Kubernetes manifests
├── helm/                     # Helm chart with ESO support
├── .github/workflows/        # CI/CD workflows
│   ├── test.yml             # Run tests on PR
│   └── docker-publish.yml   # Build and push Docker images
├── Dockerfile                # Alpine-based multi-stage build
├── .dockerignore             # Docker build context exclusions
├── pyproject.toml            # Python dependencies
├── devbox.json               # Devbox configuration
└── README.md                 # This file
```

## Troubleshooting

### Authorization Issues

```bash
# Check if authorized
ls -la data/fitbit_tokens.json

# Re-authorize
python main.py --authorize
```

### Connection Issues

```bash
# Test Victoria Metrics connection
curl -u "${VICTORIA_USERNAME]:${VICTORIA_PASSWORD} ${VICTORIA_ENDPOINT}"

# Check logs
kubectl logs -f deployment/syncbit
```

### Rate Limiting

Fitbit API has rate limits (150 requests/hour per user). The app:
- Respects rate limits
- Logs rate limit errors
- Waits before retry

## Monitoring

### Logs

```bash
# Local
tail -f data/syncbit.log

# Docker
docker logs -f syncbit

# Kubernetes
kubectl logs -f deployment/syncbit
```

### Health Check

The deployment includes liveness and readiness probes to ensure the pod is healthy.

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

1. Add collection logic in [src/fitbit_collector.py](src/fitbit_collector.py)
2. Add metric formatting in [src/victoria_writer.py](src/victoria_writer.py)
3. Update this README with new metrics

### Testing

The project includes a comprehensive test suite with 60+ tests covering all modules.

```bash
# Install test dependencies
devbox run install-dev

# Run all tests
devbox run test

# Run tests with coverage report
devbox run test-cov

# Run specific test file
python -m pytest tests/test_config.py -v

# Run tests matching pattern
python -m pytest -k "test_auth" -v
```

**Test Coverage:**
- Config: 98% coverage
- Sync State: 95% coverage
- Victoria Writer: 70% coverage
- Fitbit Auth: 61% coverage
- Fitbit Collector: 58% coverage
- Scheduler: 67% coverage
- **Overall: 69% coverage**

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
ghcr.io/origox/syncbit:latest          # Latest stable release (main branch)
ghcr.io/origox/syncbit:v1.2.3          # Full semantic version
ghcr.io/origox/syncbit:v1.2            # Major.minor version
ghcr.io/origox/syncbit:v1              # Major version
ghcr.io/origox/syncbit:sha-<commit>    # Specific commit (all branches)
```

**Production Recommendation:** Use specific version tags (e.g., `v1.2.3`) rather than `latest` for stability.

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

### Viewing Releases

- [GitHub Releases](https://github.com/origox/syncbit/releases) - View all releases and changelogs
- [Container Images](https://github.com/origox/syncbit/pkgs/container/syncbit) - Browse available Docker images

## License

MIT