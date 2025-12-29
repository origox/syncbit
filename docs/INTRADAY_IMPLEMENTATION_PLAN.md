# Intraday Data Collection - Implementation Best Practices

## Executive Summary

This document outlines the best practices and implementation strategy for adding Fitbit intraday time-series data collection to SyncBit. The goal is to extend current daily summary collection with minute-level or second-level granular data while maintaining system stability, respecting API rate limits, and managing storage implications.

**Related Issue:** #38

## 1. Current Architecture Analysis

### Existing System Overview

**Strengths:**
- ✅ Clean separation of concerns (collector, writer, scheduler, state)
- ✅ Robust rate limit handling with exponential backoff
- ✅ Resumable backfill via state persistence
- ✅ Proper token refresh management
- ✅ 30-second delays between requests (150 req/hour limit)

**Current Data Flow:**
```
Scheduler → Collector (2 API calls/day) → Writer → Victoria Metrics
              ↓
        - Activity Summary
        - Heart Rate Zones
```

**Key Files:**
- `src/fitbit_collector.py`: Makes API requests, handles rate limits
- `src/victoria_writer.py`: Formats Prometheus metrics
- `src/scheduler.py`: Orchestrates sync and backfill
- `src/sync_state.py`: Tracks last successful sync

### Current Limitations

1. **Daily Granularity Only**: Can't track minute-by-minute activity patterns
2. **2 API Calls Per Day**: Activity summary + heart rate zones
3. **Fixed Backfill Strategy**: 30-second delays between dates
4. **State Tracking**: Only tracks last successful date (not granularity level)

## 2. Fitbit Intraday API Requirements

### Endpoint Structure

**Activity Intraday:**
```
GET /1/user/-/activities/{resource}/date/{date}/1d/{detail-level}.json
```
- **Resources**: `steps`, `calories`, `distance`, `elevation`, `floors`
- **Detail Levels**: `1min`, `5min`, `15min`
- **Scope Required**: `activity` (already granted)

**Heart Rate Intraday:**
```
GET /1/user/-/activities/heart/date/{date}/1d/{detail-level}.json
```
- **Detail Levels**: `1sec`, `1min`, `5min`, `15min`
- **Scope Required**: `heartrate` (already granted)
- **Note**: `1sec` may not always return 1-second sampling outside recorded exercises

### Response Structure

**Activity Intraday Example:**
```json
{
  "activities-steps": [{
    "dateTime": "2024-01-15",
    "value": "12543"
  }],
  "activities-steps-intraday": {
    "dataset": [
      {"time": "00:00:00", "value": 0},
      {"time": "00:01:00", "value": 12},
      {"time": "00:02:00", "value": 8}
    ],
    "datasetInterval": 1,
    "datasetType": "minute"
  }
}
```

**Heart Rate Intraday Example:**
```json
{
  "activities-heart": [{
    "dateTime": "2024-01-15",
    "value": {
      "restingHeartRate": 62,
      "heartRateZones": [...]
    }
  }],
  "activities-heart-intraday": {
    "dataset": [
      {"time": "00:00:00", "value": 68},
      {"time": "00:01:00", "value": 70}
    ],
    "datasetInterval": 1,
    "datasetType": "minute"
  }
}
```

### Key API Constraints

1. **24-Hour Limit**: Cannot retrieve >24 hours of intraday data per request
2. **Personal App Only**: Intraday data only available for personal apps (✅ already configured)
3. **Current Day Partial**: Today's data only includes values up to current timestamp
4. **Rate Limits**: Same 150 req/hour limit applies
5. **No Multi-Day Batch**: Must request each day individually

## 3. Data Volume Analysis

### Storage Impact Calculation

**Daily Summary (Current):**
- ~15 metrics per day
- Storage: negligible

**1-Minute Granularity (Proposed):**
- 1440 minutes/day
- 6 resources (steps, calories, distance, elevation, floors, heart_rate)
- **8,640 data points per day**

**1-Second Granularity (Heart Rate Only):**
- 86,400 seconds/day
- 1 resource (heart_rate)
- **86,400 data points per day**
- ⚠️ **NOT RECOMMENDED** due to storage explosion

**Recommendation:** Start with **5-minute granularity** (288 data points/day × 6 resources = 1,728 points/day)

### Victoria Metrics Storage

**Per-day storage estimate (5-min granularity):**
```
1,728 data points × 6 metrics × 365 days = 3.78M data points/year
```

**With compression:** Victoria Metrics typically achieves 10:1 compression
**Actual storage:** ~50KB per day, ~18MB per year per user

**Recommendation:** Perfectly manageable for single-user deployment

## 4. Implementation Strategy

### Phase 1: Core Intraday Collection

**Goal:** Add basic intraday collection without breaking existing functionality

#### 4.1 Extend Configuration (`src/config.py`)

```python
class Config:
    # ... existing config ...

    # Intraday settings
    ENABLE_INTRADAY_COLLECTION: bool = os.getenv("ENABLE_INTRADAY_COLLECTION", "false").lower() == "true"
    INTRADAY_DETAIL_LEVEL: str = os.getenv("INTRADAY_DETAIL_LEVEL", "5min")  # 1min, 5min, 15min
    INTRADAY_HEART_RATE_DETAIL: str = os.getenv("INTRADAY_HEART_RATE_DETAIL", "1min")  # 1sec, 1min, 5min, 15min
    INTRADAY_RESOURCES: list[str] = os.getenv(
        "INTRADAY_RESOURCES",
        "steps,calories,distance,heart_rate"
    ).split(",")
```

**Why:**
- Opt-in feature (default disabled)
- Configurable granularity
- User can select which resources to collect
- Separate detail level for heart rate (supports 1sec)

#### 4.2 Extend Collector (`src/fitbit_collector.py`)

**Add new methods:**

```python
def get_intraday_activity(
    self,
    date: datetime,
    resource: str,
    detail_level: str = "5min"
) -> dict:
    """Get intraday activity data for a specific resource.

    Args:
        date: Date to fetch data for
        resource: Activity resource (steps, calories, distance, elevation, floors)
        detail_level: Data granularity (1min, 5min, 15min)

    Returns:
        Intraday dataset with time-series data
    """
    date_str = date.strftime("%Y-%m-%d")
    endpoint = f"/activities/{resource}/date/{date_str}/1d/{detail_level}.json"

    logger.debug(f"Fetching intraday {resource} for {date_str} at {detail_level}")
    data = self._make_request(endpoint)

    # Extract intraday dataset
    intraday_key = f"activities-{resource}-intraday"
    return data.get(intraday_key, {})

def get_intraday_heart_rate(
    self,
    date: datetime,
    detail_level: str = "1min"
) -> dict:
    """Get intraday heart rate data.

    Args:
        date: Date to fetch data for
        detail_level: Data granularity (1sec, 1min, 5min, 15min)

    Returns:
        Intraday heart rate dataset
    """
    date_str = date.strftime("%Y-%m-%d")
    endpoint = f"/activities/heart/date/{date_str}/1d/{detail_level}.json"

    logger.debug(f"Fetching intraday heart rate for {date_str} at {detail_level}")
    data = self._make_request(endpoint)

    return data.get("activities-heart-intraday", {})

def get_intraday_data(self, date: datetime) -> dict:
    """Get all configured intraday data for a specific date.

    Args:
        date: Date to fetch data for

    Returns:
        Combined intraday data for all enabled resources
    """
    if not Config.ENABLE_INTRADAY_COLLECTION:
        return {}

    logger.info(f"Collecting intraday data for {date.strftime('%Y-%m-%d')}")

    intraday_data = {
        "date": date.strftime("%Y-%m-%d"),
        "timestamp": int(date.timestamp()),
        "resources": {}
    }

    # Collect activity resources
    for resource in Config.INTRADAY_RESOURCES:
        if resource == "heart_rate":
            dataset = self.get_intraday_heart_rate(
                date,
                Config.INTRADAY_HEART_RATE_DETAIL
            )
        else:
            dataset = self.get_intraday_activity(
                date,
                resource,
                Config.INTRADAY_DETAIL_LEVEL
            )

        intraday_data["resources"][resource] = dataset

        # Rate limit delay between resources
        time.sleep(5)  # Conservative 5-second delay

    return intraday_data
```

**Design Decisions:**
- Separate methods for activity vs heart rate (different endpoints/detail levels)
- Configurable resource selection
- 5-second delays between resource requests
- Returns structured data with metadata

#### 4.3 Extend Writer (`src/victoria_writer.py`)

**Add intraday formatting:**

```python
def write_intraday_data(self, data: dict) -> bool:
    """Write intraday Fitbit data to Victoria Metrics.

    Args:
        data: Intraday data from Fitbit collector

    Returns:
        True if successful
    """
    if not data or not data.get("resources"):
        return True  # Nothing to write

    date_str = data["date"]
    base_date = datetime.strptime(date_str, "%Y-%m-%d")
    metrics = []

    for resource, dataset in data["resources"].items():
        dataset_points = dataset.get("dataset", [])

        if not dataset_points:
            logger.warning(f"No intraday data for {resource} on {date_str}")
            continue

        # Format metric name
        metric_name = f"fitbit_{resource}_intraday"

        # Process each time point
        for point in dataset_points:
            time_str = point["time"]  # Format: "HH:MM:SS"
            value = point["value"]

            # Parse time and create full timestamp
            hour, minute, second = map(int, time_str.split(":"))
            point_datetime = base_date.replace(
                hour=hour,
                minute=minute,
                second=second
            )
            timestamp = int(point_datetime.timestamp())

            # Add metric
            metrics.append(
                self._format_metric(metric_name, value, timestamp)
            )

    # Batch write all metrics
    if not metrics:
        logger.warning(f"No intraday metrics to write for {date_str}")
        return True

    metrics_data = "".join(metrics)

    try:
        response = requests.post(
            self.endpoint,
            data=metrics_data,
            auth=self.auth,
            headers={"Content-Type": "text/plain"},
        )
        response.raise_for_status()

        logger.info(
            f"Successfully wrote {len(metrics)} intraday metrics for {date_str}"
        )
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to write intraday metrics: {e}")
        return False
```

**Design Decisions:**
- Batch writes all intraday points at once (efficient)
- Converts time strings to full timestamps
- Separate metric names (`fitbit_steps_intraday` vs `fitbit_steps_total`)
- Graceful handling of missing data

#### 4.4 Extend Scheduler (`src/scheduler.py`)

**Add intraday sync job:**

```python
def sync_intraday_data(self) -> None:
    """Perform intraday data synchronization."""
    if not Config.ENABLE_INTRADAY_COLLECTION:
        return

    logger.info("Starting intraday data synchronization...")

    try:
        # Get yesterday's data (most recent complete day)
        yesterday = datetime.now() - timedelta(days=1)

        # Collect intraday data
        data = self.collector.get_intraday_data(yesterday)

        # Write to Victoria Metrics
        success = self.writer.write_intraday_data(data)

        if success:
            logger.info(f"Successfully synced intraday data for {data['date']}")
        else:
            logger.error("Failed to write intraday data to Victoria Metrics")

    except RateLimitError as e:
        wait_time = e.retry_after if hasattr(e, "retry_after") else 60
        logger.warning(f"Rate limited during intraday sync, waiting {wait_time}s")
        time.sleep(wait_time)

    except Exception as e:
        logger.error(f"Error during intraday sync: {e}", exc_info=True)

def start(self) -> None:
    """Start the scheduler."""
    logger.info("Starting SyncBit scheduler...")

    # Run backfill on startup
    self.backfill_data()

    # Schedule regular daily summary sync
    self.scheduler.add_job(
        self.sync_data,
        trigger=IntervalTrigger(minutes=Config.SYNC_INTERVAL_MINUTES),
        id="sync_daily_data",
        name="Sync daily Fitbit data",
        replace_existing=True,
    )

    # Schedule intraday sync (if enabled)
    if Config.ENABLE_INTRADAY_COLLECTION:
        # Run slightly offset from daily sync to spread API load
        self.scheduler.add_job(
            self.sync_intraday_data,
            trigger=IntervalTrigger(minutes=Config.SYNC_INTERVAL_MINUTES),
            id="sync_intraday_data",
            name="Sync intraday Fitbit data",
            replace_existing=True,
        )
        logger.info("Intraday sync enabled")

    logger.info(f"Sync interval: {Config.SYNC_INTERVAL_MINUTES} minutes")

    try:
        self.scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
```

**Design Decisions:**
- Separate scheduled job for intraday (can disable independently)
- Same 15-minute interval as daily sync
- Reuses existing rate limit handling
- Graceful degradation if disabled

### Phase 2: Backfill Strategy

**Challenge:** Backfilling intraday data dramatically increases API calls

**Current backfill:**
- 2 API calls per day (activity + heart rate)
- 120 days = 240 API calls
- At 30s intervals = 2 hours total

**Intraday backfill (4 resources):**
- 4 API calls per day (steps, calories, distance, heart_rate)
- 120 days = 480 API calls
- At 30s intervals = 4 hours total
- ⚠️ **Doubles backfill time**

**Recommended Strategy:**

#### Option 1: No Intraday Backfill (Recommended)

```python
def backfill_intraday_data(self) -> None:
    """Backfill intraday data (disabled by default)."""
    if not Config.ENABLE_INTRADAY_BACKFILL:
        logger.info("Intraday backfill disabled - collecting prospectively only")
        return

    # ... implementation if needed ...
```

**Rationale:**
- Intraday data is most valuable for recent analysis
- Historical intraday data is less critical
- Reduces initial setup time
- Avoids rate limit exhaustion

**Config:**
```python
ENABLE_INTRADAY_BACKFILL: bool = os.getenv("ENABLE_INTRADAY_BACKFILL", "false").lower() == "true"
```

#### Option 2: Limited Backfill Window

```python
# Only backfill last 30 days of intraday data
INTRADAY_BACKFILL_DAYS: int = int(os.getenv("INTRADAY_BACKFILL_DAYS", "30"))
```

#### Option 3: Separate Backfill Processes

```python
def backfill_data(self, include_intraday: bool = False) -> None:
    """Backfill daily summaries first, then optionally intraday."""

    # Phase 1: Daily summaries (fast)
    self._backfill_daily_summaries()

    # Phase 2: Intraday data (slow, optional)
    if include_intraday and Config.ENABLE_INTRADAY_COLLECTION:
        self._backfill_intraday_data()
```

### Phase 3: State Management

**Challenge:** Track both daily summary and intraday sync states separately

**Extend `src/sync_state.py`:**

```python
class SyncState:
    """Manages synchronization state."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from file."""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {
            "daily_summary": {"last_successful_date": None},
            "intraday": {"last_successful_date": None}
        }

    def update_last_sync(self, date: str, sync_type: str = "daily_summary") -> None:
        """Update last successful sync date.

        Args:
            date: Date in YYYY-MM-DD format
            sync_type: "daily_summary" or "intraday"
        """
        self._state[sync_type]["last_successful_date"] = date
        self._save_state()

    def get_last_successful_date(self, sync_type: str = "daily_summary") -> str | None:
        """Get last successful sync date.

        Args:
            sync_type: "daily_summary" or "intraday"

        Returns:
            Date string or None
        """
        return self._state[sync_type].get("last_successful_date")
```

**Benefits:**
- Independent sync tracking
- Can resume intraday backfill separately
- Graceful migration from existing state

### Phase 4: Rate Limit Optimization

**Current rate limit handling:**
- 30-second delays between dates
- Retry with exponential backoff
- Uses `Retry-After` header

**Intraday optimization:**

```python
class FitbitCollector:
    def __init__(self, auth: FitbitAuth):
        self.auth = auth
        self.base_url = Config.FITBIT_API_BASE_URL
        self._rate_limit_state = {
            "remaining": 150,
            "reset_at": None
        }

    def _make_request(self, endpoint: str, params: dict | None = None) -> dict:
        """Make authenticated request with rate limit tracking."""

        # Check rate limit before making request
        if self._rate_limit_state["remaining"] <= 10:
            wait_time = self._get_wait_time()
            if wait_time > 0:
                logger.warning(f"Approaching rate limit, waiting {wait_time}s")
                time.sleep(wait_time)

        # ... existing request logic ...

        # Update rate limit state from response headers
        if "fitbit-rate-limit-remaining" in response.headers:
            self._rate_limit_state["remaining"] = int(
                response.headers["fitbit-rate-limit-remaining"]
            )
        if "fitbit-rate-limit-reset" in response.headers:
            self._rate_limit_state["reset_at"] = time.time() + int(
                response.headers["fitbit-rate-limit-reset"]
            )

        return response.json()

    def _get_wait_time(self) -> int:
        """Calculate seconds to wait before next request."""
        if not self._rate_limit_state["reset_at"]:
            return 0

        wait = int(self._rate_limit_state["reset_at"] - time.time())
        return max(0, wait)
```

**Benefits:**
- Proactive rate limit management
- Reduces 429 errors
- Smarter delay timing

## 5. Testing Strategy

### Unit Tests

**Test collector methods:**
```python
# tests/test_fitbit_collector_intraday.py

def test_get_intraday_activity(mock_auth, mock_requests):
    """Test intraday activity collection."""
    collector = FitbitCollector(mock_auth)

    mock_response = {
        "activities-steps-intraday": {
            "dataset": [
                {"time": "00:00:00", "value": 0},
                {"time": "00:05:00", "value": 42}
            ],
            "datasetInterval": 5,
            "datasetType": "minute"
        }
    }

    mock_requests.get.return_value.json.return_value = mock_response

    result = collector.get_intraday_activity(
        datetime(2024, 1, 15),
        "steps",
        "5min"
    )

    assert len(result["dataset"]) == 2
    assert result["datasetInterval"] == 5
```

**Test writer formatting:**
```python
def test_write_intraday_data(mock_victoria):
    """Test intraday data formatting and writing."""
    writer = VictoriaMetricsWriter()

    data = {
        "date": "2024-01-15",
        "timestamp": 1705276800,
        "resources": {
            "steps": {
                "dataset": [
                    {"time": "00:00:00", "value": 0},
                    {"time": "00:05:00", "value": 42}
                ]
            }
        }
    }

    success = writer.write_intraday_data(data)

    assert success
    assert mock_victoria.post.called
    # Verify 2 metrics were sent
```

### Integration Tests

**Test rate limiting:**
```python
def test_intraday_rate_limit_handling(live_api):
    """Test that intraday collection respects rate limits."""
    collector = FitbitCollector(auth)

    dates = [datetime.now() - timedelta(days=i) for i in range(5)]

    for date in dates:
        data = collector.get_intraday_data(date)
        assert data is not None
        # Should not hit rate limits with proper delays
```

### Performance Tests

**Measure data volume:**
```python
def test_intraday_data_volume():
    """Measure actual data volume for storage planning."""
    data = collector.get_intraday_data(datetime.now() - timedelta(days=1))

    total_points = sum(
        len(dataset.get("dataset", []))
        for dataset in data["resources"].values()
    )

    # 5-min granularity should yield ~288 points per resource
    assert total_points < 2000  # Sanity check
```

## 6. Deployment Strategy

### Rollout Plan

**Phase 1: Soft Launch (Weeks 1-2)**
- Deploy with `ENABLE_INTRADAY_COLLECTION=false` (default)
- Monitor existing daily sync for regressions
- Verify no breaking changes

**Phase 2: Alpha Testing (Week 3)**
- Enable intraday for 1-2 resources only
- Use 5-minute granularity
- No backfill (`ENABLE_INTRADAY_BACKFILL=false`)
- Monitor Victoria Metrics storage growth
- Validate metric format in Grafana

**Phase 3: Beta (Week 4)**
- Enable all configured resources
- Test with limited backfill (7 days)
- Monitor API rate limit consumption
- Performance testing

**Phase 4: Production (Week 5+)**
- Full feature availability
- Document in README
- Update Grafana dashboards
- User can opt-in via config

### Configuration Examples

**Minimal (Recommended):**
```env
ENABLE_INTRADAY_COLLECTION=true
INTRADAY_DETAIL_LEVEL=5min
INTRADAY_RESOURCES=heart_rate,steps
ENABLE_INTRADAY_BACKFILL=false
```

**Moderate:**
```env
ENABLE_INTRADAY_COLLECTION=true
INTRADAY_DETAIL_LEVEL=1min
INTRADAY_RESOURCES=heart_rate,steps,calories,distance
ENABLE_INTRADAY_BACKFILL=true
INTRADAY_BACKFILL_DAYS=30
```

**Aggressive (Not Recommended):**
```env
ENABLE_INTRADAY_COLLECTION=true
INTRADAY_DETAIL_LEVEL=1min
INTRADAY_HEART_RATE_DETAIL=1sec
INTRADAY_RESOURCES=heart_rate,steps,calories,distance,elevation,floors
ENABLE_INTRADAY_BACKFILL=true
INTRADAY_BACKFILL_DAYS=120
```
⚠️ **Risk:** May exceed rate limits, massive storage requirements

### Monitoring

**Key metrics to track:**
- Intraday API call count per day
- Rate limit remaining (from headers)
- Victoria Metrics storage growth
- Sync job duration
- Error rates

**Alerts:**
```yaml
# Prometheus alerts
- alert: IntradayRateLimitExhausted
  expr: rate_limit_remaining < 20
  for: 5m

- alert: IntradaySyncFailing
  expr: intraday_sync_errors > 3
  for: 15m

- alert: StorageGrowthAbnormal
  expr: victoria_storage_gb > expected * 1.5
  for: 1h
```

## 7. Documentation Updates

### README.md Updates

**Add to "Collected Metrics" section:**

```markdown
### Intraday Time-Series Data (Optional)

When enabled, SyncBit can collect minute-level or second-level granular data:

**Heart Rate:**
- `fitbit_heart_rate_intraday` - Heart rate measurements (1sec, 1min, 5min, or 15min intervals)

**Activity:**
- `fitbit_steps_intraday` - Step counts per interval
- `fitbit_calories_intraday` - Calorie burn per interval
- `fitbit_distance_intraday` - Distance traveled per interval
- `fitbit_elevation_intraday` - Elevation/floors per interval

**Note:** Intraday data significantly increases storage requirements. Start with 5-minute granularity and heart rate + steps only.
```

**Add to "Configuration" section:**

```markdown
### Intraday Data Collection

| Variable | Description | Default |
|----------|-------------|---------|
| `ENABLE_INTRADAY_COLLECTION` | Enable intraday time-series data | `false` |
| `INTRADAY_DETAIL_LEVEL` | Activity granularity (1min, 5min, 15min) | `5min` |
| `INTRADAY_HEART_RATE_DETAIL` | Heart rate granularity (1sec, 1min, 5min, 15min) | `1min` |
| `INTRADAY_RESOURCES` | Comma-separated resources to collect | `steps,calories,distance,heart_rate` |
| `ENABLE_INTRADAY_BACKFILL` | Backfill historical intraday data | `false` |
| `INTRADAY_BACKFILL_DAYS` | Days to backfill (if enabled) | `30` |
```

### CLAUDE.md Updates

**Add to "Important Constraints" section:**

```markdown
**Intraday Data Constraints:**
- 1440+ data points per day per resource (at 1-min granularity)
- Same 150 req/hour rate limit applies
- Cannot fetch >24 hours per request
- Heart rate supports 1sec detail (may not always be available)
- Significantly increases Victoria Metrics storage
```

## 8. Risks and Mitigations

### Risk Matrix

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Rate limit exhaustion | High | Medium | Start disabled, conservative delays, proactive monitoring |
| Storage explosion | Medium | Low | Use 5min granularity, document limits, monitor growth |
| API changes | Medium | Low | Version endpoint URLs, add API error handling |
| Performance degradation | Low | Low | Separate scheduled jobs, batch writes |
| Data inconsistency | Medium | Low | Separate state tracking, atomic writes |

### Mitigation Details

**Rate Limit Exhaustion:**
- Default disabled (`ENABLE_INTRADAY_COLLECTION=false`)
- 5-second delays between resources
- Track `fitbit-rate-limit-remaining` header
- Stop collection if remaining < 10
- Alert on rate limit warnings

**Storage Explosion:**
- Document storage requirements prominently
- Start with 5-minute granularity
- Limit default resources (heart_rate + steps only)
- Add Victoria Metrics retention policy example
- Monitor storage growth metrics

**API Changes:**
- Version check in requests
- Comprehensive error logging
- Graceful degradation (disable intraday, keep daily)
- Unit tests against mock responses

## 9. Success Criteria

### Phase 1 (Core Implementation)
- [ ] Intraday collection working for all 5 activity resources
- [ ] Heart rate intraday collection with configurable detail level
- [ ] Metrics correctly formatted and written to Victoria Metrics
- [ ] No regression in daily summary collection
- [ ] Test coverage ≥80% for new code

### Phase 2 (Backfill)
- [ ] Optional backfill with configurable window
- [ ] Separate state tracking for intraday vs daily
- [ ] Resumable backfill after interruption
- [ ] Rate limit handling during backfill

### Phase 3 (Production Ready)
- [ ] Documentation complete (README, CLAUDE.md)
- [ ] Default disabled with clear opt-in instructions
- [ ] Storage impact documented with examples
- [ ] Monitoring dashboards updated
- [ ] No rate limit violations in 1-week test

## 10. Open Questions

1. **Should we support partial resource collection during errors?**
   - Option A: All-or-nothing (if one resource fails, skip entire day)
   - Option B: Best-effort (collect what we can, log failures)
   - **Recommendation:** Option B with clear logging

2. **How to handle today's incomplete data?**
   - Option A: Skip today entirely (only sync yesterday)
   - Option B: Sync today's partial data, update later
   - **Recommendation:** Option A (cleaner, less confusion)

3. **Should intraday backfill be manual or automatic?**
   - Option A: Require explicit `--backfill-intraday` flag
   - Option B: Auto-backfill based on config
   - **Recommendation:** Option A for control

4. **What's the retry strategy for partial days?**
   - If 3 of 4 resources succeed, retry the 4th or skip?
   - **Recommendation:** Log and continue (best-effort)

## 11. Implementation Checklist

- [ ] Create feature branch `feat/intraday-data-collection`
- [ ] Update `src/config.py` with intraday settings
- [ ] Add `get_intraday_activity()` to `fitbit_collector.py`
- [ ] Add `get_intraday_heart_rate()` to `fitbit_collector.py`
- [ ] Add `get_intraday_data()` orchestration method
- [ ] Add `write_intraday_data()` to `victoria_writer.py`
- [ ] Add `sync_intraday_data()` to `scheduler.py`
- [ ] Update `SyncState` for dual tracking
- [ ] Add unit tests for collector methods
- [ ] Add unit tests for writer methods
- [ ] Add integration tests
- [ ] Update README.md with configuration docs
- [ ] Update CLAUDE.md with constraints
- [ ] Add example Grafana dashboard queries
- [ ] Test with rate limiting
- [ ] Test storage impact (7-day test)
- [ ] Create PR with detailed description
- [ ] Deploy to staging/dev first
- [ ] Monitor for 1 week before prod

## 12. Future Enhancements

**Out of scope for initial implementation:**

1. **Sleep Stages Intraday**: Requires separate API, different data structure
2. **Compression**: Client-side compression before sending to Victoria Metrics
3. **Delta Encoding**: Only send changes from previous interval
4. **On-Demand Backfill**: API endpoint to trigger backfill for specific date ranges
5. **Resource-Specific Intervals**: Different granularity per resource
6. **Downsampling**: Aggregate 1-min data to 5-min in Victoria Metrics
7. **Real-Time Sync**: Sync current day's data every 15 minutes

## Conclusion

The intraday data collection feature will significantly enhance SyncBit's value by providing minute-level granularity for activity and heart rate metrics. The implementation strategy prioritizes:

1. **Safety First**: Disabled by default, careful rate limiting
2. **Incremental Rollout**: Opt-in, configurable, monitored
3. **Clean Architecture**: Extends existing patterns without breaking changes
4. **Storage Awareness**: Documented impact, conservative defaults
5. **Operational Excellence**: Comprehensive testing, monitoring, documentation

**Recommended Timeline:** 4-5 weeks from start to production-ready

**Next Steps:** Begin Phase 1 implementation with focus on core collection methods.
