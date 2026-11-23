# SyncBit Architecture

This document describes the technical architecture, design decisions and key implementation details of SyncBit.

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Component Design](#component-design)
- [Data Flow](#data-flow)
- [API Constraints](#api-constraints)
- [Google Health API Quirks](#google-health-api-quirks)
- [State Management](#state-management)
- [Error Handling](#error-handling)
- [Technical Decisions](#technical-decisions)
- [Security Considerations](#security-considerations)
- [Future Considerations](#future-considerations)

## Overview

SyncBit is a long-running Python service that copies Fitbit health data into Victoria Metrics. It reads from the **Google Health API v4**, normalizes the responses into provider-neutral records, and pushes them in Prometheus exposition format.

Google shut down the legacy Fitbit Web API on 2026-11-02. Since 4.0.0, Google Health is the default provider (`HEALTH_PROVIDER=google`). The Fitbit implementation remains behind the same interface, but it can no longer fetch data.

**Key requirements:**
- Reliable collection without gaps, resumable after interruption
- OAuth2 token lifecycle management (1-hour access tokens)
- Stay within the per-user quota (300 requests/minute) while backfilling as fast as possible
- Metric names and labels stable across providers, so dashboards survive the migration
- Production deployment on Kubernetes

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                              SyncBit                                 │
│                                                                      │
│  ┌────────────┐   HealthDataCollector   ┌──────────────────────────┐ │
│  │ Scheduler  │────────────────────────▶│ GoogleHealthCollector    │ │
│  │(APScheduler)│   (src/collector.py)    │  thread pool + throttle  │ │
│  └─────┬──────┘                          └────────────┬─────────────┘ │
│        │ DailyRecord / IntradayRecord                 │               │
│        │ (src/health_types.py)            ┌───────────▼────────────┐  │
│  ┌─────▼──────────┐  ┌──────────────┐     │ GoogleAuth             │  │
│  │ VictoriaWriter │  │ SyncState    │     │ PKCE, token refresh,   │  │
│  │ (Prometheus)   │  │ (JSON file)  │     │ granted-scope tracking │  │
│  └─────┬──────────┘  └──────────────┘     └───────────┬────────────┘  │
└────────┼──────────────────────────────────────────────┼──────────────┘
         │                                              │
  ┌──────▼──────────┐                         ┌─────────▼──────────┐
  │ Victoria Metrics│                         │ Google Health API  │
  │ /api/v1/import/ │                         │ health.googleapis  │
  │ prometheus      │                         │ .com/v4            │
  └─────────────────┘                         └────────────────────┘
```

The scheduler, writer and sync state depend only on the `HealthDataCollector` protocol and the types in `health_types.py`. Swapping the upstream API means supplying a new collector; nothing downstream changes. This is what made the Fitbit to Google migration a collector-only change from the writer's point of view.

## Component Design

### 1. Collector interface (`collector.py`, `health_types.py`)

`HealthDataCollector` is a `typing.Protocol` with five methods: `get_daily_data`, `get_historical_data`, `get_first_available_date`, `get_intraday_data` and `get_device_info`. Collectors raise `RateLimitError(retry_after, quota_reset=None, remaining=None)` when throttled and return empty values (not exceptions) when data is merely unavailable.

`DailyRecord` guarantees only `date` and `timestamp`; every metric is optional, because availability varies by device and day. Heart rate zones, activity levels and sleep stages use fixed lower_snake_case names that the writer maps directly onto labels.

### 2. Google Health collector (`google_collector.py`)

**Endpoints used:**

| Data | Method | Notes |
|---|---|---|
| steps, distance, total-calories, active-minutes, time-in-heart-rate-zone, calories-in-heart-rate-zone | `dataPoints:dailyRollUp` | One point per civil day; range ≤ 14 days for some types |
| daily-resting-heart-rate, daily-oxygen-saturation, daily-respiratory-rate, daily-heart-rate-variability, daily-vo2-max, daily-sleep-temperature-derivations | `dataPoints` list with a `.date` filter | These types reject `dailyRollUp` |
| sleep | `dataPoints` list filtered on `civil_end_time` | Sessions summed per wake-up date |
| intraday steps / distance / calories / heart-rate | `dataPoints:rollUp` with a fixed `windowSize` | One request per resource per day |
| membership start (backfill start) | `users/me/profile` | |
| paired devices | `users/me/pairedDevices` | Needs `googlehealth.settings.readonly` (not requested) |

**Chunking.** `get_historical_data` splits a range into chunks of 14 days, the smallest maximum range among the rollup types. Each chunk costs one request per data type (13 with every metric enabled), regardless of how many days it holds.

**Concurrency and throttling.** The requests inside a chunk are independent, so they run on a thread pool (`GOOGLE_MAX_CONCURRENT_REQUESTS`, default 8). Per-request latency is about 1 s, so without parallelism latency, not quota, is the bottleneck. A shared lock-protected throttle spaces request *starts* to `GOOGLE_MAX_REQUESTS_PER_MINUTE` (default 300, the documented per-user quota). Token reads and refreshes are serialized with a separate lock.

**Normalization** keeps Fitbit-era names:
- Heart rate zones: `LIGHT → out_of_range`, `MODERATE → fat_burn`, `VIGOROUS → cardio`, `PEAK → peak`. This was verified on real data against Active Zone Minutes: MODERATE minutes equal fat-burn AZM, and VIGOROUS/PEAK count double, as AZM does for cardio and peak.
- Activity levels: `LIGHT → lightly_active`, `MODERATE → fairly_active`, `VIGOROUS → very_active`. There is no sedentary level.
- Units: distance millimetres → km; durations such as `"80160s"` → minutes; int64 values arrive as JSON strings and are parsed.
- Skin temperature: nightly minus baseline, matching Fitbit's `nightlyRelative`.
- Sleep: attributed to the civil date of the interval end (end time + UTC offset), like Fitbit's `dateOfSleep`; all sessions ending that day are summed.

**Optional vs. core metrics.** Failures of the six rollups propagate so the scheduler can retry the whole range. Every other metric is optional: any error except `RateLimitError` is logged and yields no value, so one missing metric never costs a day.

### 3. Google auth (`google_auth.py`)

- Authorization code flow with **PKCE** (S256), `access_type=offline` and `prompt=consent`. Without both, Google issues no refresh token.
- The PKCE verifier is persisted to `data/pkce_verifier.txt` (mode 600), so the URL and the code exchange can happen in different processes, e.g. generate the URL in a pod and exchange the pasted code later. It is deleted after a successful exchange.
- Two ways to finish: a callback server on `0.0.0.0:8080`, or `--manual` (paste the `code`).
- **Refresh token preservation:** Google omits `refresh_token` from refresh responses, so an absent value keeps the stored one.
- **Reload before refresh:** `refresh_access_token()` re-reads the token file first, so a token written by another process (for example a re-authorization) is picked up.
- **Granted scopes** from the `scope` field are stored in the token file. Users can untick individual scopes on Google's consent screen, which still yields a valid token; a warning names any missing scope instead of leaving it to surface as 403s later.
- Access tokens last about 1 hour; expiry is recorded with a 5-minute safety buffer.

### 4. Scheduler (`scheduler.py`)

- `APScheduler` `BlockingScheduler`. At startup: authorization check, Victoria Metrics connection test, daily backfill, optional intraday backfill, one immediate sync, then periodic jobs every `SYNC_INTERVAL_MINUTES`.
- **Backfill** asks the collector for 14-day ranges (`BACKFILL_CHUNK_DAYS`), writes each chunk, and checkpoints the last date. There are no fixed sleeps.
- **Scheduled sync** requests yesterday and (if `INCLUDE_TODAY_DATA`) today in one range call. Only complete days advance the sync state. If the last synced date is before yesterday, it first runs a gap backfill.
- **Intraday sync** re-fetches yesterday and, under the same `INCLUDE_TODAY_DATA` switch, today. An empty day does not skip the next; a `RateLimitError` ends the run.
- The collector is injected (`SyncScheduler(auth=..., collector=...)`); `main.build_provider()` picks it from `HEALTH_PROVIDER`.

### 5. Victoria writer (`victoria_writer.py`)

```
fitbit_steps_total{device="fitbit_air",user="default"} 8000 1767225600000
```

- Provider-agnostic: maps `DailyRecord` keys onto metric names
- Millisecond timestamps; daily metrics for today use the sync time (one point per sync, so the day's progress is visible) and completed days use 23:59:59 of their date (the final totals)
- Labels: `user` (`FITBIT_USER_ID`), `device` (`DEVICE_LABEL`), plus `type`, `zone`, `stage`, `device_id`, `device_type` where relevant
- Basic auth; one POST per day of metrics; connection test on startup

### 6. Sync state (`sync_state.py`)

```json
{
  "last_sync": "2026-11-22 19:29:10.192000",
  "last_successful_date": "2026-11-21",
  "last_intraday_backfill_date": null
}
```

## Data Flow

### Scheduled sync (every 15 minutes)

```
1. Gap check: last_successful_date < yesterday → run backfill first
2. get_device_info()             (403 without settings scope → skipped)
3. get_historical_data(yesterday, today)
     └─ 13 concurrent requests under the throttle
4. write_daily_data() per record
5. update_last_sync(yesterday) if written and no gap remains
```

### Backfill

```
start = last_successful_date + 1
     or BACKFILL_START_DATE
     or profile.membershipStartDate
for each 14-day chunk up to yesterday:
    records = collector.get_historical_data(chunk)
    written, failed = writer.write_multiple_days(records)
    if failed: stop (state NOT advanced; resume next run)
    state.update_last_sync(records[-1].date)
```

Measured on a live account: a 356-day backfill took 1 min 47 s, and full history (about 4,100 days) about 21 minutes. The Fitbit-era loop (one day per call, 30 s apart) would have needed about 34 hours.

## API Constraints

| Limit | Value | Source |
|---|---|---|
| Per-user requests | 300 / minute | Google Health rate-limit guide |
| Per-project requests | 120,000 / minute, 86.4M / day | Google Health rate-limit guide |
| `dailyRollUp` / `rollUp` range | 14 days for total-calories, active-minutes, calories-in-heart-rate-zone, heart-rate; 90 days for others | API reference |
| `list` page size | default 1440, max 10000 (sleep: 25) | API reference |
| Unverified OAuth app | 100 users | Google Auth Platform |
| Refresh token, app in *Testing* | expires after 7 days | Google OAuth |
| Access token | ~1 hour | Google OAuth |

429 responses carry no documented `Retry-After`; SyncBit honours the header if present and otherwise waits 60 s.

## Google Health API Quirks

Found by probing a live account with `scripts/probe_google_health.py`. These are pinned in code and tests because they contradict or extend the published reference:

- **Daily-summary filters need a snake_case prefix:** `daily_resting_heart_rate.date >= "2026-11-20"`. The documented camelCase prefix (`dailyRestingHeartRate.date`) is rejected with `INVALID_DATA_POINT_FILTER_DATA_TYPE_RESTRICTION`.
- **`daily-*` types reject `dailyRollUp`**; use a filtered `list`.
- **Identity endpoint** is `users/me/identity`, not `users/me:getIdentity`.
- **Scope errors come in two shapes:** `MISSING_OAUTH_SCOPE` with `any_of_required` (data types) and `ACCESS_TOKEN_SCOPE_INSUFFICIENT` (profile, paired devices).
- **`rollUp` sometimes omits zero-valued windows.** Repeating the same request alternates between returning all windows and only non-zero ones; totals are identical.
- **Account linking:** the Google account must have signed in to the Google Health mobile app first, or the API answers `ACCOUNT_NOT_LINKED`.
- **Raw heart-rate samples** arrive roughly every 2 seconds (about 40k per day), which is why intraday uses `rollUp` windows instead of `list`.

## State Management

File-based JSON in `DATA_DIR`:

| File | Contents | Written |
|---|---|---|
| `google_tokens.json` (600) | access/refresh token, expiry, granted scopes | at authorization and every refresh |
| `pkce_verifier.txt` (600) | pending PKCE verifier | between URL generation and code exchange |
| `sync_state.json` | progress | after every written chunk / complete day |
| `syncbit.log` | log copy | continuously |

Because `google_tokens.json` is rewritten about hourly, it must live on writable storage: a PVC on Kubernetes, never a ConfigMap or Secret. The deployment is single-replica (RWO volume, single writer), so no locking is needed across processes; within the process, threads share the token through a lock.

## Error Handling

| Error | Handling | Reason |
|---|---|---|
| 429 | `RateLimitError` → scheduler waits `retry_after` (default 60 s) and retries the same range; stops after 3 in a row | Quota resets per minute |
| 401 | Refresh token once, retry | Token invalidated before recorded expiry |
| 500/502/503/504 | Retry up to 3 times, backoff 2/4/8 s | Recommended by the rate-limit guide |
| 403 on optional metric | Logged, metric skipped | Usually a missing scope |
| Other errors on optional metric | Logged, metric skipped | One metric must not cost the day |
| Core rollup failure | Chunk retried 3 times (5 s apart), then skipped | Avoid a permanent block |
| Victoria Metrics write failure | Backfill stops **without** advancing state | Advancing would lose those days permanently |

Logging levels: INFO for progress, WARNING for rate limits, retries and missing scopes, ERROR for skipped data.

## Technical Decisions

### Provider-neutral interface
The writer and scheduler were coupled to Fitbit's JSON. Introducing `health_types` first made the Google collector a self-contained addition and kept metric continuity a property of the collector's mapping.

### Threads instead of asyncio
Concurrency is only needed inside a chunk (at most about 13 requests). A `ThreadPoolExecutor` around the existing synchronous `requests` code gives the same throughput without rewriting transport, auth and tests for async.

### Client-side throttle at the documented quota
With no fixed sleeps, a long backfill would otherwise hit 429s continually. Spacing request starts at 300/min keeps a backfill at the quota without bouncing off it.

### APScheduler over cron
Python-native, easy to test, identical behaviour locally, in Docker and in Kubernetes, and keeps the process (and its in-memory token) long-lived.

### File-based state
Human-readable, no database, survives restarts on a volume, sufficient for one user.

### Runtime image without pip
Dependencies are installed into an isolated prefix in the builder stage and copied in; pip is uninstalled from the runtime image. The app never installs packages at runtime, and pip's own CVEs (plus its SBOM declaring a vendored setuptools that Trivy flags) would otherwise fail the image scan.

## Security Considerations

- Secrets via `/run/secrets/*` (External Secrets Operator + 1Password) or environment variables; never in the repo
- Token files created with mode 600; `.env`, `data/` and probe output are gitignored
- Read-only Google scopes only
- Non-root container (UID 1000), no pip in the runtime image
- Victoria Metrics over HTTPS with basic auth
- Only daily aggregates and coarse intraday windows are stored; labels are pseudonymous (`user` is configurable)

## Future Considerations

- **Device metrics:** request `googlehealth.settings.readonly` to populate battery and last-sync metrics.
- **Time zone:** the image has no tzdata, so day boundaries are UTC. Adding `tzdata` and `TZ` would align them with the user's day.
- **Victoria Metrics cache:** after backfilling historical data, call `/internal/resetRollupResultCache` so dashboards don't serve stale empty results.
- **Multi-user:** one collector, token file and state file per user; the per-user quota makes this scale linearly.
- **App metrics:** expose sync success, latency and rate-limit events for alerting.

## References

- [Google Health API](https://developers.google.com/health)
- [Google Health API REST reference](https://developers.google.com/health/reference/rest)
- [Google Health API scopes](https://developers.google.com/health/scopes)
- [Victoria Metrics: importing Prometheus exposition format](https://docs.victoriametrics.com/#how-to-import-data-in-prometheus-exposition-format)
- [OAuth 2.0 PKCE (RFC 7636)](https://datatracker.ietf.org/doc/html/rfc7636)
- [Prometheus exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/)
- [Conventional Commits](https://www.conventionalcommits.org/)
