# Victoria Metrics Queries for Intraday Data

This document provides example PromQL queries for querying and visualizing Fitbit intraday data in Victoria Metrics/Grafana.

## Quick Start

### Verify Data Collection

After enabling intraday collection, wait 15+ minutes for first sync, then run:

```promql
# Check if any intraday data exists
{__name__=~"fitbit_.*_intraday"}

# Count data points collected
count({__name__=~"fitbit_.*_intraday"})

# See which resources have data
count by (__name__) ({__name__=~"fitbit_.*_intraday"})
```

**Expected Results:**
- First sync collects yesterday's data (24 hours ago)
- Heart rate: ~1,440 points (1-minute intervals)
- Steps/calories/distance: ~288 points each (5-minute intervals)
- Total: ~2,304 metrics per day

If queries return empty, see [Troubleshooting](#troubleshooting) section below.

## Metric Names

The intraday implementation writes the following metrics:

- `fitbit_steps_intraday` - Steps count per interval
- `fitbit_calories_intraday` - Calories burned per interval
- `fitbit_distance_intraday` - Distance traveled per interval (km)
- `fitbit_heart_rate_intraday` - Heart rate per interval (bpm)

All metrics include labels:
- `user` - Fitbit user ID (from `FITBIT_USER_ID` env var)
- `device` - Device type (currently "charge6")

## Basic Queries

### View Raw Intraday Data

```promql
# All intraday heart rate data for today
fitbit_heart_rate_intraday{user="default"}

# Intraday steps for specific user
fitbit_steps_intraday{user="youruser"}

# Intraday calories for specific device
fitbit_calories_intraday{device="charge6"}
```

### Time Range Selection

```promql
# Last 24 hours of heart rate data
fitbit_heart_rate_intraday{user="default"}[24h]

# Last 6 hours of steps
fitbit_steps_intraday{user="default"}[6h]

# Specific time range (use Grafana time picker or absolute time)
fitbit_heart_rate_intraday{user="default"}
```

## Aggregation Queries

### Hourly Aggregations

```promql
# Average heart rate per hour
avg_over_time(fitbit_heart_rate_intraday{user="default"}[1h])

# Total steps per hour (sum of all 5-minute intervals)
sum_over_time(fitbit_steps_intraday{user="default"}[1h])

# Total calories per hour
sum_over_time(fitbit_calories_intraday{user="default"}[1h])

# Total distance per hour (km)
sum_over_time(fitbit_distance_intraday{user="default"}[1h])
```

### Daily Aggregations

```promql
# Average heart rate for the day
avg_over_time(fitbit_heart_rate_intraday{user="default"}[24h])

# Total steps for the day
sum_over_time(fitbit_steps_intraday{user="default"}[24h])

# Total calories for the day
sum_over_time(fitbit_calories_intraday{user="default"}[24h])

# Total distance for the day (km)
sum_over_time(fitbit_distance_intraday{user="default"}[24h])
```

### Peak and Min Values

```promql
# Maximum heart rate in last 24 hours
max_over_time(fitbit_heart_rate_intraday{user="default"}[24h])

# Minimum resting heart rate (exclude zeros)
min_over_time(fitbit_heart_rate_intraday{user="default"} > 0[24h])

# Peak step interval (busiest 5 minutes)
max_over_time(fitbit_steps_intraday{user="default"}[24h])
```

## Advanced Analysis

### Heart Rate Zones

```promql
# Time spent in different heart rate zones (customize thresholds)
# Resting zone (< 100 bpm)
count_over_time((fitbit_heart_rate_intraday{user="default"} < 100)[24h])

# Moderate activity (100-140 bpm)
count_over_time((fitbit_heart_rate_intraday{user="default"} >= 100 < 140)[24h])

# Vigorous activity (140-180 bpm)
count_over_time((fitbit_heart_rate_intraday{user="default"} >= 140 < 180)[24h])

# Maximum effort (>= 180 bpm)
count_over_time((fitbit_heart_rate_intraday{user="default"} >= 180)[24h])
```

### Activity Detection

```promql
# Identify active intervals (> 0 steps)
fitbit_steps_intraday{user="default"} > 0

# Count active 5-minute intervals in last 24h
count_over_time((fitbit_steps_intraday{user="default"} > 0)[24h])

# Sedentary time (0 steps for extended period)
count_over_time((fitbit_steps_intraday{user="default"} == 0)[24h])
```

### Rate of Change

```promql
# Heart rate change per minute
rate(fitbit_heart_rate_intraday{user="default"}[1m])

# Step velocity (steps accumulation rate)
rate(fitbit_steps_intraday{user="default"}[5m])

# Calorie burn rate
rate(fitbit_calories_intraday{user="default"}[5m])
```

### Moving Averages

```promql
# 30-minute moving average heart rate
avg_over_time(fitbit_heart_rate_intraday{user="default"}[30m])

# 1-hour moving average steps
avg_over_time(fitbit_steps_intraday{user="default"}[1h])

# Smooth out noise with longer window
avg_over_time(fitbit_heart_rate_intraday{user="default"}[15m])
```

## Comparison Queries

### Intraday vs Daily Summary

```promql
# Compare intraday total steps with daily summary
sum_over_time(fitbit_steps_intraday{user="default"}[24h])
# vs
fitbit_steps_total{user="default"}

# Difference between intraday sum and daily summary
fitbit_steps_total{user="default"} - sum_over_time(fitbit_steps_intraday{user="default"}[24h])
```

### Day-over-Day Comparison

```promql
# Today's heart rate vs yesterday (offset by 24h)
fitbit_heart_rate_intraday{user="default"} - fitbit_heart_rate_intraday{user="default"} offset 24h

# Week-over-week step comparison
sum_over_time(fitbit_steps_intraday{user="default"}[24h]) - sum_over_time(fitbit_steps_intraday{user="default"}[24h] offset 7d)
```

### Multi-User Comparison

```promql
# Compare heart rates across users
avg_over_time(fitbit_heart_rate_intraday[1h])

# Total steps by user (if multiple users configured)
sum by (user) (sum_over_time(fitbit_steps_intraday[24h]))
```

## Grafana Dashboard Examples

### Panel: Heart Rate Throughout Day

**Query:**
```promql
fitbit_heart_rate_intraday{user="default"}
```

**Visualization:** Time series graph
**Legend:** `{{user}} - Heart Rate (bpm)`
**Unit:** bpm
**Time Range:** Last 24 hours

### Panel: Hourly Step Count

**Query:**
```promql
sum_over_time(fitbit_steps_intraday{user="default"}[1h])
```

**Visualization:** Bar chart or time series
**Legend:** `Steps per Hour`
**Unit:** steps
**Time Range:** Last 24 hours

### Panel: Calories Burned Rate

**Query:**
```promql
rate(fitbit_calories_intraday{user="default"}[5m]) * 60
```

**Visualization:** Time series graph
**Legend:** `Calorie Burn Rate (cal/min)`
**Unit:** cal/min
**Time Range:** Last 24 hours

### Panel: Activity Heatmap

**Query:**
```promql
fitbit_steps_intraday{user="default"} > 0
```

**Visualization:** Heatmap
**Bucket Data:** Time of day (X-axis), Days (Y-axis)
**Color:** Step count intensity
**Time Range:** Last 7 days

### Panel: Daily Summary Stats (Single Stat)

**Query (Steps):**
```promql
sum_over_time(fitbit_steps_intraday{user="default"}[24h])
```

**Query (Avg HR):**
```promql
avg_over_time(fitbit_heart_rate_intraday{user="default"} > 0[24h])
```

**Query (Total Distance):**
```promql
sum_over_time(fitbit_distance_intraday{user="default"}[24h])
```

**Visualization:** Stat panel
**Time Range:** Last 24 hours

## Alert Examples

### High Heart Rate Alert

```promql
# Alert if heart rate exceeds 180 bpm for 5 minutes
max_over_time(fitbit_heart_rate_intraday{user="default"}[5m]) > 180
```

### Inactivity Alert

```promql
# Alert if no steps recorded for 2 hours during waking hours
sum_over_time(fitbit_steps_intraday{user="default"}[2h]) == 0
```

### Data Collection Failure Alert

```promql
# Alert if no intraday data received in last 30 minutes
absent_over_time(fitbit_heart_rate_intraday{user="default"}[30m])
```

## Performance Optimization

### Downsampling for Long Time Ranges

```promql
# For viewing weeks/months of data, downsample to hourly
avg_over_time(fitbit_heart_rate_intraday{user="default"}[1h])

# Or use recording rules (configure in Victoria Metrics)
```

### Recording Rules Example

Create these in Victoria Metrics for better performance:

```yaml
# /etc/victoria-metrics/recording-rules.yml
groups:
  - name: fitbit_hourly_aggregates
    interval: 1h
    rules:
      - record: fitbit:heart_rate:hourly_avg
        expr: avg_over_time(fitbit_heart_rate_intraday[1h])

      - record: fitbit:steps:hourly_sum
        expr: sum_over_time(fitbit_steps_intraday[1h])

      - record: fitbit:calories:hourly_sum
        expr: sum_over_time(fitbit_calories_intraday[1h])

      - record: fitbit:distance:hourly_sum
        expr: sum_over_time(fitbit_distance_intraday[1h])
```

Then query the pre-aggregated metrics:

```promql
# Much faster for long time ranges
fitbit:heart_rate:hourly_avg{user="default"}
fitbit:steps:hourly_sum{user="default"}
```

## Data Volume Considerations

With default configuration (5-minute granularity for most metrics, 1-minute for heart rate):

- **Heart rate**: 1,440 data points/day (1-minute intervals)
- **Steps**: 288 data points/day (5-minute intervals)
- **Calories**: 288 data points/day (5-minute intervals)
- **Distance**: 288 data points/day (5-minute intervals)

**Total**: ~2,304 intraday metrics per day per user

For longer time ranges (months/years), use aggregated queries or recording rules to maintain query performance.

## Troubleshooting

### No Data Showing

**Common Causes:**
1. **First Run**: Intraday sync runs every 15 minutes and fetches yesterday's data. First data appears after initial sync completes.
2. **Feature Not Enabled**: Check `ENABLE_INTRADAY_COLLECTION=true` in environment
3. **Rate Limited**: Check application logs for rate limit errors
4. **Wrong User ID**: Verify label matches your `FITBIT_USER_ID` env var

**Diagnostic Queries:**

```promql
# Check if any intraday metrics exist
count(fitbit_heart_rate_intraday)
count(fitbit_steps_intraday)

# List all available intraday metrics
{__name__=~"fitbit_.*_intraday"}

# Check last update time
timestamp(fitbit_heart_rate_intraday{user="default"})

# Show all unique user labels (if query returns empty, wrong user ID)
group by (user) ({__name__=~"fitbit_.*_intraday"})
```

### Verify Data Range

```promql
# See first and last data points
fitbit_heart_rate_intraday{user="default"} @ start()
fitbit_heart_rate_intraday{user="default"} @ end()
```

### Check for Gaps

```promql
# Detect missing data intervals (expect ~288 points per day for 5min intervals)
count_over_time(fitbit_steps_intraday{user="default"}[24h])
```

Expected: ~288 for 5-minute intervals, ~1440 for 1-minute intervals

## Additional Resources

- Victoria Metrics PromQL: https://docs.victoriametrics.com/MetricsQL.html
- Grafana Dashboard Examples: https://grafana.com/grafana/dashboards/
- Fitbit Intraday API Docs: https://dev.fitbit.com/build/reference/web-api/intraday/

## Notes

- All timestamps are in Unix epoch format (milliseconds)
- Time ranges use Victoria Metrics time duration syntax: `5m`, `1h`, `24h`, `7d`, etc.
- For Grafana, use `$__interval` variable for dynamic aggregation based on zoom level
- Label filters are case-sensitive: `user="default"` not `user="Default"`
