# SyncBit Architecture

This document describes the technical architecture, design decisions, and key implementation details of SyncBit.

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Component Design](#component-design)
- [Technical Decisions](#technical-decisions)
- [Data Flow](#data-flow)
- [API Constraints](#api-constraints)
- [State Management](#state-management)
- [Error Handling](#error-handling)
- [Future Considerations](#future-considerations)

## Overview

SyncBit is a Python-based data synchronization service that bridges Fitbit's health tracking data with Victoria Metrics for long-term storage and analysis. The application runs as a long-lived process that periodically polls Fitbit's API and pushes metrics in Prometheus format to Victoria Metrics.

**Key Requirements:**
- Reliable data collection without gaps
- OAuth2 token lifecycle management
- Fitbit API rate limit compliance (150 requests/hour)
- Resumable backfill for historical data
- Production deployment on Kubernetes

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         SyncBit App                          │
│                                                               │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  Scheduler │─▶│   Collector  │─▶│ Victoria Writer  │    │
│  └─────┬──────┘  └──────┬───────┘  └────────┬─────────┘    │
│        │                 │                    │               │
│        │                 │                    │               │
│  ┌─────▼──────┐  ┌──────▼───────┐  ┌────────▼─────────┐    │
│  │ Sync State │  │ Fitbit Auth  │  │  HTTP Client     │    │
│  │   (JSON)   │  │ Token Manager│  │  (requests)      │    │
│  └────────────┘  └──────────────┘  └──────────────────┘    │
└───────────────────────────┬─────────────────┬───────────────┘
                            │                 │
                    ┌───────▼────────┐ ┌─────▼──────────┐
                    │   Fitbit API   │ │ Victoria       │
                    │   (OAuth2)     │ │ Metrics        │
                    └────────────────┘ └────────────────┘
```

## Component Design

### 1. Scheduler (`scheduler.py`)

**Purpose:** Orchestrates periodic sync and historical backfill.

**Key Design Decisions:**
- Uses `APScheduler` with `BlockingScheduler` for simplicity
- Runs backfill once at startup, then schedules periodic sync
- **Why APScheduler over cron?** 
  - Better error handling and retry logic
  - Python-native (no external dependencies)
  - Easier to test and control programmatically
  - Works identically in local/Docker/K8s environments

**Rate Limit Strategy:**
- 30-second delay between requests during backfill
- Tracks consecutive rate limit failures (stops after 3)
- Writes partial batches before waiting to preserve progress
- Separate retry logic for scheduled sync (2 retries)

### 2. Fitbit Collector (`fitbit_collector.py`)

**Purpose:** Fetches activity data from Fitbit API.

**API Quirks Learned:**
- **Split domain names:** Auth uses `www.fitbit.com`, API uses `api.fitbit.com`
- **Rate limits:** 150 req/hour per user, enforced with 429 responses
- **Retry-After header:** Fitbit returns exact wait time (usually 60 seconds)
- **User ID:** Can use `-` for authenticated user (simpler than user ID lookup)
- **Data completeness:** Yesterday's data is most reliable; today's is incomplete

**Implementation Notes:**
```python
# Custom exception for rate limiting
class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int = None):
        self.retry_after = retry_after  # Preserve API's wait time
```

- Extracts `Retry-After` header value for precise wait times
- Max 3 retries per request with 5-second delays
- All requests go through `_make_request()` for consistent error handling

### 3. Fitbit Auth (`fitbit_auth.py`)

**Purpose:** OAuth2 authorization and token lifecycle management.

**Token Management Strategy:**
- Access token valid for 8 hours
- Refresh token used to get new access tokens
- **Critical fix:** Reload tokens from disk before refresh
  - Problem: In-memory tokens can be stale if another process updated file
  - Solution: `refresh_access_token()` calls `_load_tokens()` first

**Authorization Flow:**
1. Start local HTTP server on port 8080
2. Open browser to Fitbit authorization URL
3. User approves access
4. Fitbit redirects to `http://localhost:8080/callback?code=...`
5. Exchange code for tokens
6. Save tokens to `data/fitbit_tokens.json`
7. Shutdown local server

**Why local callback server?**
- Simple, works without external infrastructure
- No need to expose app to internet
- Standard OAuth2 flow for desktop apps

### 4. Victoria Writer (`victoria_writer.py`)

**Purpose:** Convert Fitbit data to Prometheus format and POST to Victoria Metrics.

**Prometheus Format:**
```
fitbit_steps_total{user="origox",device="charge6"} 12543 1735084800000
```

**Design Notes:**
- Millisecond timestamps (Victoria Metrics requirement)
- Labels for user and device (enables multi-user support later)
- Basic auth for Victoria Metrics endpoint
- Batch writes to reduce HTTP overhead
- Test connection on startup to fail fast

### 5. Sync State (`sync_state.py`)

**Purpose:** Track progress for resumable backfill.

**State File Structure:**
```json
{
  "last_sync": "2025-12-24 11:22:51.791809",
  "last_successful_date": "2025-12-12"
}
```

**Why JSON over database?**
- Simple, human-readable
- No external dependencies
- Sufficient for single-user application
- Easy to inspect/modify manually
- Works well with volume mounts in containers

## Technical Decisions

### Why Python 3.11?

- Modern async support (future expansion)
- Good library ecosystem for HTTP/OAuth
- Excellent Kubernetes client libraries available
- Type hints for better IDE support
- Match with Devbox/Nix packaging

### Why requests over httpx/aiohttp?

- Simpler API for synchronous requests
- Battle-tested and stable
- Fitbit API calls are sequential (rate limited anyway)
- Async complexity not needed for current use case

### Why Devbox over virtualenv/poetry?

- Reproducible dev environments with Nix
- Declarative dependency management
- Works across different systems
- Includes system-level dependencies

## Data Flow

### Daily Sync Flow

```
1. Scheduler triggers (every 15 minutes)
2. Calculate yesterday's date
3. Collector fetches from Fitbit:
   - Activity summary (/activities/date/{date}.json)
   - Heart rate (/activities/heart/date/{date}/1d.json)
4. Parse and combine data
5. Convert to Prometheus format
6. POST to Victoria Metrics
7. Update sync state on success
```

### Backfill Flow

```
1. On startup, check last successful date
2. If gap exists (or first run):
   - Start from BACKFILL_START_DATE or last_successful_date + 1
   - Process in 10-day batches
   - For each date:
     a. Fetch data from Fitbit (30s delay between requests)
     b. Add to batch
     c. On batch full (10 days) or end of range:
        - Write entire batch to Victoria Metrics
        - Update state to last date in batch
        - Clear batch
3. If rate limited 3 times consecutively:
   - Write partial batch
   - Save progress
   - Stop backfill (will resume on next run)
```

**Why 10-day batches?**
- Small enough to fit in memory
- Large enough to reduce Victoria Metrics requests
- Reasonable checkpoint granularity for resume
- ~5 minutes per batch at 30s/request rate

## API Constraints

### Fitbit Rate Limits

**Limit:** 150 requests per hour per user

**Math:**
- 150 req/hour = 2.5 req/minute = 1 req per 24 seconds
- We use 30-second delays for safety margin
- Backfill: ~120 days × 30s = 1 hour of runtime
- Each day requires 2 API calls (activity + heart rate)

**Handling Strategy:**
1. Respect delays proactively (30s between dates)
2. Parse `Retry-After` header on 429 responses
3. Implement exponential backoff for other failures
4. Track consecutive rate limits (stop after 3)
5. Preserve progress before waiting

### Token Expiry

- Access tokens expire every 8 hours
- Refresh tokens are long-lived but can be revoked
- Must handle refresh during long-running backfills
- Check token validity before each request batch

## State Management

### Why File-Based State?

1. **Persistence:** Survives container restarts
2. **Simplicity:** No database to manage
3. **Debuggability:** Can inspect with `cat`
4. **Version Control:** Can check into git for initial state
5. **Kubernetes:** Easy to mount as PVC

### State Location

- Local: `./data/sync_state.json`
- Docker: `/app/data/sync_state.json` (volume mount)
- Kubernetes: `/app/data/sync_state.json` (PVC mount)

### Concurrency Considerations

- Single replica deployment (no concurrent writes)
- File locks not needed for single-writer scenario
- If scaling needed, would migrate to Redis/PostgreSQL

## Error Handling

### Retry Strategy by Error Type

| Error Type | Retry Strategy | Reason |
|------------|----------------|--------|
| Rate Limit (429) | Wait `Retry-After`, retry same date | Temporary, predictable |
| Auth Error (401) | Refresh token, retry once | Token may be expired |
| Network Error | Retry 3 times with backoff | Transient failures |
| 5xx Server Error | Retry 3 times with backoff | Fitbit API issues |
| 4xx Client Error | Log and skip | Bad request, won't succeed |
| Victoria Write Fail | Log and skip day | Don't block backfill |

### Logging Strategy

- **INFO:** Normal operations, sync results, progress
- **WARNING:** Rate limits, retries, degraded performance
- **ERROR:** Failures that skip data, auth problems
- **DEBUG:** API responses, detailed flow (not in production)

**Structured Logging:**
```python
logger.info(f"Synced batch: {successful} days written, progress: {batch[-1]['date']}")
```

Includes context for debugging (counts, dates, specifics).

## Future Considerations

### Scalability

**Current Limits:**
- Single Fitbit account
- Single Victoria Metrics instance
- Sequential processing

**For Multi-User:**
1. Add user table/config
2. Parallel collectors (one per user)
3. Separate state files per user
4. User-specific rate limit tracking

### Monitoring

**Potential Additions:**
- Prometheus exporter for app metrics
  - Sync success rate
  - API latency
  - Rate limit events
  - Backfill progress
- Alerting on sync failures
- Dashboard for sync status

### Additional Metrics

**Fitbit API Supports:**
- Sleep stages and scores
- SpO2 measurements
- Breathing rate
- HRV (heart rate variability)
- Water intake
- Food logs
- Weight/body composition

**Implementation Pattern:**
1. Add method to `fitbit_collector.py`
2. Add metric formatting to `victoria_writer.py`
3. Update `get_daily_data()` to include new data
4. Document in README

### Performance Optimizations

**Not Currently Needed, But Possible:**
- Async API calls (parallel activity + heart rate fetch)
- Batch multiple days in single Victoria Metrics request
- Cache frequently accessed config
- Connection pooling for HTTP requests

**Why Not Now:**
- Rate limits prevent benefits of parallelization
- Current performance is acceptable (<1s per day)
- Premature optimization

## Development Patterns

### Adding a New Feature

1. Update `config.py` if new env vars needed
2. Implement logic in appropriate module
3. Update tests (when test suite exists)
4. Update README.md documentation
5. Use conventional commit format

### Debugging Issues

1. Check logs for ERROR/WARNING messages
2. Verify token file exists and is valid
3. Test API connectivity manually:
   ```bash
   curl -u "$VICTORIA_USER:$VICTORIA_PASSWORD" "$VICTORIA_ENDPOINT"
   ```
4. Enable DEBUG logging: `python main.py --log-level DEBUG`
5. Check sync state file for progress

### Testing Locally

- Use `devbox shell` for consistent environment
- Test with `--authorize` first
- Monitor logs in real-time
- Check Victoria Metrics UI for data arrival

## Security Considerations

**Current Approach:**
- Secrets in environment variables (12-factor app)
- Token file on persistent volume (encrypted at rest in K8s)
- Basic auth for Victoria Metrics
- OAuth2 for Fitbit

**Production Hardening:**
- Use Kubernetes Secrets for credentials
- Consider sealed secrets or external secret management
- Rotate Victoria Metrics credentials periodically
- Monitor for token leakage in logs

**No PII Storage:**
- Only stores aggregated daily metrics
- No detailed activity logs (GPS, etc.)
- User ID configurable (pseudonymous labels)

## References

- [Fitbit Web API Documentation](https://dev.fitbit.com/build/reference/web-api/)
- [Victoria Metrics Prometheus Import](https://docs.victoriametrics.com/#how-to-import-data-in-prometheus-exposition-format)
- [Conventional Commits](https://www.conventionalcommits.org/)
- [OAuth 2.0 RFC](https://datatracker.ietf.org/doc/html/rfc6749)
- [Prometheus Exposition Format](https://prometheus.io/docs/instrumenting/exposition_formats/)
