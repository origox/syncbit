# SyncBit

**Fitbit to Victoria Metrics Data Synchronization**

SyncBit automatically collects activity data from your Fitbit account (Fitbit Charge 6) and pushes metrics to Victoria Metrics in Prometheus format. It handles OAuth2 authentication, automatic token refresh, and periodic data synchronization.

## Features

- 🔐 **OAuth2 Authentication** - Secure Fitbit authorization with automatic token refresh
- 📊 **Comprehensive Metrics** - Collects steps, heart rate, activity minutes, calories, and more
- 🔄 **Automatic Sync** - Runs every 15 minutes to keep data up-to-date
- 📈 **Historical Backfill** - Automatically syncs historical data from your Fitbit account
- 🐳 **Docker & Kubernetes Ready** - Easy deployment with provided manifests
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

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd syncbit

# Copy environment template
cp .env.example .env
```

### 2. Configure Environment

Edit `.env` with your credentials:

```env
FITBIT_CLIENT_ID=your_client_id_here
FITBIT_CLIENT_SECRET=your_client_secret_here
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
# Build image
docker build -t syncbit:latest .

# Run authorization (first time only)
docker run --rm -it \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  -p 8080:8080 \
  syncbit:latest python main.py --authorize

# Run sync service
docker run -d \
  --name syncbit \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  syncbit:latest
```

### 5. Kubernetes Deployment

```bash
# Create namespace (optional)
kubectl create namespace syncbit

# Create secrets
cp k8s/secret.yaml.example k8s/secret.yaml
# Edit k8s/secret.yaml with your credentials
kubectl apply -f k8s/secret.yaml

# Deploy application
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml

# Check status
kubectl get pods -l app=syncbit
kubectl logs -f deployment/syncbit
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

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FITBIT_CLIENT_ID` | Fitbit OAuth Client ID | Required |
| `FITBIT_CLIENT_SECRET` | Fitbit OAuth Client Secret | Required |
| `FITBIT_REDIRECT_URI` | OAuth redirect URI | `http://localhost:8080/callback` |
| `VICTORIA_ENDPOINT` | Victoria Metrics import endpoint |
| `VICTORIA_USER` | Victoria Metrics username | Required |
| `VICTORIA_PASSWORD` | Victoria Metrics password | Required |
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
│   ├── config.py             # Configuration management
│   ├── fitbit_auth.py        # OAuth2 authentication
│   ├── fitbit_collector.py   # Data collection from Fitbit
│   ├── victoria_writer.py    # Victoria Metrics writer
│   ├── scheduler.py          # Sync scheduler
│   └── sync_state.py         # State management
├── k8s/
│   ├── deployment.yaml       # Kubernetes deployment
│   ├── configmap.yaml        # Configuration
│   ├── secret.yaml.example   # Secrets template
│   └── pvc.yaml              # Persistent volume claim
├── main.py                   # Entry point
├── requirements.txt          # Python dependencies
├── Dockerfile                # Docker image
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

### Adding New Metrics

1. Add collection logic in [src/fitbit_collector.py](src/fitbit_collector.py)
2. Add metric formatting in [src/victoria_writer.py](src/victoria_writer.py)
3. Update this README with new metrics

### Testing Locally

```bash
# Enter dev environment
devbox shell

# Install dependencies
pip install -r requirements.txt

# Run with debug logging
python main.py --log-level DEBUG
```

## License

MIT