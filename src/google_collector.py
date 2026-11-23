"""Google Health API data collector.

Translates Google Health API v4 responses into the provider-neutral types in
`health_types`, so the writer, scheduler and sync state need no changes.

Request shapes were verified against a live account (see
`scripts/probe_google_health.py`). Two of them contradict the published
reference and are pinned here deliberately:

- Daily summary filters take a snake_case prefix
  (`daily_resting_heart_rate.date`); the documented camelCase form is rejected
  with INVALID_DATA_POINT_FILTER_DATA_TYPE_RESTRICTION.
- `daily-*` types do not support `dailyRollUp`; they are fetched with a
  date-filtered `list`, which yields one point per day.

Label continuity: heart rate zones and activity levels are mapped onto the
names the Fitbit collector produced, so existing dashboards keep working. The
zone mapping was checked against Active Zone Minutes on real data (MODERATE
minutes == fat burn AZM; VIGOROUS and PEAK count double, as AZM does for
cardio and peak).
"""

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import requests

from .collector import RateLimitError
from .config import Config
from .google_auth import GoogleAuth
from .health_types import (
    ActiveMinutes,
    DailyRecord,
    DeviceInfo,
    HeartRateZone,
    IntradayRecord,
    Sleep,
    SpO2,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# dailyRollUp accepts at most 14 days for total-calories, active-minutes and
# calories-in-heart-rate-zone (90 for the rest). Chunking every range to 14
# keeps one request per data type per chunk.
_MAX_CHUNK_DAYS = 14

# Retry transient server errors with exponential backoff, as the rate limit
# guide recommends. 429 is not retried here: the scheduler owns that backoff.
_TRANSIENT_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 3

# The API does not document a Retry-After header on 429. Honour one if present,
# otherwise fall back to a minute (the per-user quota is per minute).
_DEFAULT_RETRY_AFTER = 60

_ZONE_NAMES = {
    "LIGHT": "out_of_range",
    "MODERATE": "fat_burn",
    "VIGOROUS": "cardio",
    "PEAK": "peak",
}

_ACTIVITY_LEVELS = {
    "LIGHT": "lightly_active",
    "MODERATE": "fairly_active",
    "VIGOROUS": "very_active",
}

_SLEEP_STAGES = {
    "DEEP": "deep",
    "LIGHT": "light",
    "REM": "rem",
    "AWAKE": "wake",
}

# Intraday resource name (as configured in INTRADAY_RESOURCES) ->
# (data type, rollup value key, field, scale). Values are converted to the
# units the Fitbit collector produced (km for distance).
_INTRADAY_TYPES: dict[str, tuple[str, str, str, float]] = {
    "steps": ("steps", "steps", "countSum", 1.0),
    "distance": ("distance", "distance", "millimetersSum", 1e-6),
    "calories": ("total-calories", "totalCalories", "kcalSum", 1.0),
    "heart_rate": ("heart-rate", "heartRate", "beatsPerMinuteAvg", 1.0),
}

_DETAIL_LEVEL_SECONDS = {"1sec": 1, "1min": 60, "5min": 300, "15min": 900}


def _num(value) -> float | None:
    """Parse a numeric API value; int64 fields arrive as JSON strings."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _civil_date(value: dict) -> str | None:
    """Render a google.type.dt.date (`{"year", "month", "day"}`) as YYYY-MM-DD."""
    try:
        return f"{value['year']:04d}-{value['month']:02d}-{value['day']:02d}"
    except (KeyError, TypeError):
        return None


def _now() -> datetime:
    """Current local time; a seam so tests can fix the clock."""
    return datetime.now()


def _record_timestamp(day: dt.date) -> int:
    """Unix seconds to stamp a day's totals with.

    Today's (partial) totals are stamped with the sync time, so every sync
    adds a point and the day's progress is visible as it accumulates.
    A completed day is stamped at its last second, so its final totals close
    the day they describe rather than appearing at its start.
    """
    now = _now()
    if day >= now.date():
        return int(now.timestamp())
    return int((datetime(day.year, day.month, day.day) + timedelta(days=1, seconds=-1)).timestamp())


def _civil(day: dt.date) -> dict:
    """Render a date as a CivilDateTime at midnight."""
    return {"date": {"year": day.year, "month": day.month, "day": day.day}}


def _duration_seconds(value: str | None) -> float | None:
    """Parse a protobuf Duration string such as "80160s"."""
    if not value or not value.endswith("s"):
        return None
    return _num(value[:-1])


def _civil_end_date(interval: dict) -> str | None:
    """Return the local calendar date an interval ends on.

    Sleep is attributed to the date the user woke up, matching Fitbit's
    `dateOfSleep`. The interval carries UTC times plus the UTC offset in effect,
    so the civil date is recovered without knowing the user's time zone.
    """
    try:
        end = datetime.fromisoformat(interval["endTime"].replace("Z", "+00:00"))
        offset = _duration_seconds(interval.get("endUtcOffset")) or 0
    except (KeyError, ValueError, AttributeError):
        return None
    return (end + timedelta(seconds=offset)).strftime("%Y-%m-%d")


class GoogleHealthCollector:
    """Collects data from the Google Health API v4."""

    def __init__(self, auth: GoogleAuth):
        """Initialize collector.

        Args:
            auth: Google authentication handler
        """
        self.auth = auth
        self.base_url = f"{Config.GOOGLE_API_BASE_URL}/users/me"
        self._devices_unavailable_logged = False
        self._last_request_at: float | None = None
        # Chunk fetches run on a thread pool; these keep the shared throttle
        # and the token refresh from racing.
        self._throttle_lock = threading.Lock()
        self._token_lock = threading.Lock()

    def _throttle(self) -> None:
        """Space requests to stay within the per-user quota.

        Backfill issues requests back to back with no other delay, so this is
        what keeps a long run under the quota instead of bouncing off 429s.
        """
        per_minute = Config.GOOGLE_MAX_REQUESTS_PER_MINUTE
        if per_minute <= 0:
            return
        interval = 60.0 / per_minute
        # Holding the lock while sleeping serializes request *starts* at the
        # configured spacing; the requests themselves still overlap.
        with self._throttle_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                wait = self._last_request_at + interval - now
                if wait > 0:
                    time.sleep(wait)
                    now += wait
            self._last_request_at = now

    # --- Transport ---

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        """Make an authenticated request, handling auth, throttling and retries.

        Raises:
            RateLimitError: On 429
            requests.HTTPError: On any other non-success status
        """
        url = f"{self.base_url}/{path}"
        refreshed = False
        attempt = 0

        while True:
            with self._token_lock:
                token = self.auth.get_valid_token()
            headers = {"Authorization": f"Bearer {token}"}
            self._throttle()
            response = requests.request(
                method, url, headers=headers, params=params, json=json, timeout=60
            )

            if response.status_code == 401 and not refreshed:
                # The token can be invalidated before its recorded expiry.
                logger.info("Access token rejected, refreshing and retrying")
                with self._token_lock:
                    self.auth.refresh_access_token()
                refreshed = True
                continue

            if response.status_code == 429:
                retry_after = int(_num(response.headers.get("Retry-After")) or _DEFAULT_RETRY_AFTER)
                logger.warning(f"Rate limited by Google Health API, retry after {retry_after}s")
                raise RateLimitError("Rate limited by Google Health API", retry_after=retry_after)

            if response.status_code in _TRANSIENT_STATUSES and attempt < _MAX_RETRIES:
                attempt += 1
                delay = 2**attempt
                logger.warning(
                    f"{response.status_code} from {path}, retry {attempt}/{_MAX_RETRIES} in {delay}s"
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response.json()

    def _list(self, data_type: str, filter_expr: str, page_size: int) -> list[dict]:
        """List all data points of a type matching a filter, following pages."""
        points: list[dict] = []
        params = {"filter": filter_expr, "pageSize": page_size}

        while True:
            body = self._request("GET", f"dataTypes/{data_type}/dataPoints", params=params)
            points.extend(body.get("dataPoints", []))
            token = body.get("nextPageToken")
            if not token:
                return points
            params = {**params, "pageToken": token}

    def _daily_rollup(self, data_type: str, start: dt.date, end: dt.date) -> dict[str, dict]:
        """Daily rollup over [start, end], keyed by YYYY-MM-DD."""
        body = {
            "range": {"start": _civil(start), "end": _civil(end + timedelta(days=1))},
            "windowSizeDays": 1,
        }
        result = self._request("POST", f"dataTypes/{data_type}/dataPoints:dailyRollUp", json=body)

        by_date: dict[str, dict] = {}
        for point in result.get("rollupDataPoints", []):
            day = _civil_date((point.get("civilStartTime") or {}).get("date"))
            if day:
                by_date[day] = point
        return by_date

    def _daily_list(self, data_type: str, start: dt.date, end: dt.date) -> dict[str, dict]:
        """Daily summary points over [start, end], keyed by YYYY-MM-DD.

        Returns the inner value object (e.g. the `dailyRestingHeartRate` dict).
        """
        prefix = data_type.replace("-", "_")
        filter_expr = (
            f'{prefix}.date >= "{start.isoformat()}" '
            f'AND {prefix}.date < "{(end + timedelta(days=1)).isoformat()}"'
        )
        value_key = _camel(data_type)

        by_date: dict[str, dict] = {}
        for point in self._list(data_type, filter_expr, page_size=_MAX_CHUNK_DAYS * 2):
            value = point.get(value_key) or {}
            day = _civil_date(value.get("date"))
            # Results are newest first; keep the first point per date.
            if day and day not in by_date:
                by_date[day] = value
        return by_date

    def _optional(self, name: str, fetch: Callable[[], T], default: T) -> T:
        """Run an optional fetch; any failure except rate limiting yields `default`.

        One unavailable metric must never cost the rest of the day's data.
        """
        try:
            return fetch()
        except RateLimitError:
            raise
        except Exception as e:
            logger.error(f"Error collecting {name}: {e}")
            return default

    # --- Normalization ---

    @staticmethod
    def _normalize_active_minutes(point: dict | None) -> ActiveMinutes:
        """Map Google activity levels onto the Fitbit-era active minute names.

        Google reports no sedentary minutes, so that key is absent rather than
        reported as zero.
        """
        minutes: ActiveMinutes = dict.fromkeys(_ACTIVITY_LEVELS.values(), 0)
        levels = ((point or {}).get("activeMinutes") or {}).get(
            "activeMinutesRollupByActivityLevel", []
        )
        for level in levels:
            name = _ACTIVITY_LEVELS.get(level.get("activityLevel"))
            value = _num(level.get("activeMinutesSum"))
            if name and value is not None:
                minutes[name] = int(value)
        return minutes

    @staticmethod
    def _normalize_heart_rate_zones(
        time_point: dict | None, calories_point: dict | None
    ) -> list[HeartRateZone]:
        """Merge per-zone time and calories into normalized zone entries."""
        zones: dict[str, HeartRateZone] = {}

        def entry(raw_zone: str | None) -> HeartRateZone:
            name = _ZONE_NAMES.get(raw_zone or "", (raw_zone or "unknown").lower())
            return zones.setdefault(name, {"zone": name})

        time_zones = ((time_point or {}).get("timeInHeartRateZone") or {}).get(
            "timeInHeartRateZones", []
        )
        for zone in time_zones:
            seconds = _duration_seconds(zone.get("duration"))
            if seconds is not None:
                entry(zone.get("heartRateZone"))["minutes"] = seconds / 60

        calorie_zones = ((calories_point or {}).get("caloriesInHeartRateZone") or {}).get(
            "caloriesInHeartRateZones", []
        )
        for zone in calorie_zones:
            kcal = _num(zone.get("kcal"))
            if kcal is not None:
                entry(zone.get("heartRateZone"))["calories"] = kcal

        return list(zones.values())

    @staticmethod
    def _normalize_sleep(sessions: list[dict]) -> Sleep:
        """Sum all sleep sessions that ended on one date (main sleep and naps).

        Matches Fitbit's daily summary, which also totals every session.
        """
        sleep: Sleep = {}
        stages: dict[str, float] = {}
        total = 0.0
        has_total = False

        for session in sessions:
            summary = session.get("summary") or {}
            asleep = _num(summary.get("minutesAsleep"))
            if asleep is not None:
                total += asleep
                has_total = True
            for stage in summary.get("stagesSummary") or []:
                name = _SLEEP_STAGES.get(stage.get("type"))
                minutes = _num(stage.get("minutes"))
                if name and minutes is not None:
                    stages[name] = stages.get(name, 0.0) + minutes

        if has_total:
            sleep["total_minutes"] = total
        if stages:
            sleep["stages"] = stages
        return sleep

    @staticmethod
    def _normalize_spo2(value: dict | None) -> SpO2:
        """Map daily oxygen saturation onto avg/min/max."""
        spo2: SpO2 = {}
        for source, target in (
            ("averagePercentage", "avg"),
            ("lowerBoundPercentage", "min"),
            ("upperBoundPercentage", "max"),
        ):
            number = _num((value or {}).get(source))
            if number is not None:
                spo2[target] = number
        return spo2

    @staticmethod
    def _relative_skin_temp(value: dict | None) -> float | None:
        """Nightly skin temperature relative to the personal baseline.

        Fitbit reported this difference directly (`nightlyRelative`); Google
        reports both absolute values instead.
        """
        nightly = _num((value or {}).get("nightlyTemperatureCelsius"))
        baseline = _num((value or {}).get("baselineTemperatureCelsius"))
        if nightly is None or baseline is None:
            return None
        return nightly - baseline

    # --- HealthDataCollector ---

    def get_daily_data(self, date: datetime) -> DailyRecord:
        """Collect one calendar day of data."""
        return self.get_historical_data(date, date)[0]

    def get_historical_data(self, start_date: datetime, end_date: datetime) -> list[DailyRecord]:
        """Collect a date range, inclusive of both endpoints.

        Returns one record per date, in ascending order, including dates with
        no data (core metrics zeroed, as the Fitbit collector did).
        """
        records: list[DailyRecord] = []
        chunk_start = start_date
        while chunk_start.date() <= end_date.date():
            chunk_end = min(chunk_start + timedelta(days=_MAX_CHUNK_DAYS - 1), end_date)
            records.extend(self._collect_chunk(chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)
        return records

    def _collect_chunk(self, start_date: datetime, end_date: datetime) -> list[DailyRecord]:
        """Collect up to _MAX_CHUNK_DAYS days with one request per data type."""
        start, end = start_date.date(), end_date.date()
        logger.info(f"Collecting Google Health data for {start} to {end}")

        # Every request in a chunk is independent, so they run concurrently.
        # Latency (~1s per request), not the quota, is otherwise the bottleneck;
        # the shared throttle still caps the overall request rate.
        fetches: dict[str, Callable[[], object]] = {
            # Core metrics: failures propagate so the scheduler can retry the range.
            "steps": lambda: self._daily_rollup("steps", start, end),
            "distance": lambda: self._daily_rollup("distance", start, end),
            "calories": lambda: self._daily_rollup("total-calories", start, end),
            "active": lambda: self._daily_rollup("active-minutes", start, end),
            "zone_time": lambda: self._daily_rollup("time-in-heart-rate-zone", start, end),
            "zone_calories": lambda: self._daily_rollup("calories-in-heart-rate-zone", start, end),
            "resting_hr": lambda: self._optional(
                "resting heart rate",
                lambda: self._daily_list("daily-resting-heart-rate", start, end),
                {},
            ),
        }
        for toggle, key, data_type in (
            (Config.COLLECT_SPO2, "spo2", "daily-oxygen-saturation"),
            (Config.COLLECT_BREATHING_RATE, "breathing", "daily-respiratory-rate"),
            (Config.COLLECT_HRV, "hrv", "daily-heart-rate-variability"),
            (Config.COLLECT_CARDIO_FITNESS, "vo2", "daily-vo2-max"),
            (Config.COLLECT_TEMPERATURE, "temp", "daily-sleep-temperature-derivations"),
        ):
            if toggle:
                fetches[key] = lambda t=data_type: self._optional(
                    t, lambda: self._daily_list(t, start, end), {}
                )
        if Config.COLLECT_SLEEP:
            fetches["sleep"] = lambda: self._optional("sleep", lambda: self._sleep(start, end), [])

        results = self._run_concurrently(fetches)
        steps, distance, calories = results["steps"], results["distance"], results["calories"]
        active, zone_time = results["active"], results["zone_time"]
        zone_calories, resting_hr = results["zone_calories"], results["resting_hr"]
        optional: dict[str, dict] = {
            key: results[key]
            for key in ("spo2", "breathing", "hrv", "vo2", "temp")
            if key in results
        }

        sleep_by_date: dict[str, list[dict]] = {}
        if Config.COLLECT_SLEEP:
            for session in results["sleep"]:
                day = _civil_end_date(session.get("interval") or {})
                if day:
                    sleep_by_date.setdefault(day, []).append(session)

        records = []
        day = start
        while day <= end:
            key = day.isoformat()

            record: DailyRecord = {
                "date": key,
                "timestamp": _record_timestamp(day),
                "steps": int(_num((steps.get(key) or {}).get("steps", {}).get("countSum")) or 0),
                "distance": (
                    _num((distance.get(key) or {}).get("distance", {}).get("millimetersSum")) or 0.0
                )
                / 1e6,
                "calories": _num((calories.get(key) or {}).get("totalCalories", {}).get("kcalSum"))
                or 0.0,
                "active_minutes": self._normalize_active_minutes(active.get(key)),
                "heart_rate_zones": self._normalize_heart_rate_zones(
                    zone_time.get(key), zone_calories.get(key)
                ),
            }

            rhr = _num((resting_hr.get(key) or {}).get("beatsPerMinute"))
            if rhr is not None:
                record["resting_heart_rate"] = rhr

            if key in sleep_by_date:
                sleep = self._normalize_sleep(sleep_by_date[key])
                if sleep:
                    record["sleep"] = sleep

            spo2 = self._normalize_spo2(optional.get("spo2", {}).get(key))
            if spo2:
                record["spo2"] = spo2

            for target, source, field in (
                ("breathing_rate", "breathing", "breathsPerMinute"),
                ("hrv_rmssd_ms", "hrv", "averageHeartRateVariabilityMilliseconds"),
                ("vo2_max", "vo2", "vo2Max"),
            ):
                number = _num((optional.get(source, {}).get(key) or {}).get(field))
                if number is not None:
                    record[target] = number

            skin_temp = self._relative_skin_temp(optional.get("temp", {}).get(key))
            if skin_temp is not None:
                record["skin_temp_relative_c"] = skin_temp

            records.append(record)
            day += timedelta(days=1)

        return records

    @staticmethod
    def _run_concurrently(fetches: dict[str, Callable[[], object]]) -> dict[str, object]:
        """Run independent fetches in parallel and return their results by key.

        The first failure (in submission order) is re-raised after all fetches
        settle, so a RateLimitError still reaches the scheduler intact.
        """
        workers = max(1, min(Config.GOOGLE_MAX_CONCURRENT_REQUESTS, len(fetches)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {key: pool.submit(fetch) for key, fetch in fetches.items()}
        # Leaving the `with` block waits for every future to finish.
        return {key: future.result() for key, future in futures.items()}

    def _sleep(self, start: dt.date, end: dt.date) -> list[dict]:
        """Sleep sessions whose civil end date falls in [start, end]."""
        filter_expr = (
            f'sleep.interval.civil_end_time >= "{start.isoformat()}" '
            f'AND sleep.interval.civil_end_time < "{(end + timedelta(days=1)).isoformat()}"'
        )
        sessions = self._list("sleep", filter_expr, page_size=25)
        return [s["sleep"] for s in sessions if "sleep" in s]

    def get_first_available_date(self) -> datetime:
        """Return the account's membership start date, or 30 days ago."""
        try:
            profile = self._request("GET", "profile")
            start = _civil_date(profile.get("membershipStartDate"))
            if start:
                logger.info(f"Member since: {start}")
                return datetime.strptime(start, "%Y-%m-%d")
        except RateLimitError:
            raise
        except Exception as e:
            logger.error(f"Error fetching profile: {e}")

        return datetime.now() - timedelta(days=30)

    def get_intraday_data(self, date: datetime) -> IntradayRecord:
        """Collect within-day samples for the configured resources.

        Uses `rollUp` with a fixed window, which returns a whole day in one
        request. Listing raw heart rate samples instead would mean ~40k points
        (one every ~2s) and many pages per day.
        """
        record: IntradayRecord = {"date": date.strftime("%Y-%m-%d"), "resources": {}}
        if not Config.ENABLE_INTRADAY_COLLECTION:
            return record

        # Local midnight to local midnight. The writer rebuilds timestamps from
        # the date plus HH:MM:SS in local time, so times are emitted the same way.
        day_start = datetime(date.year, date.month, date.day).astimezone()
        day_end = (day_start + timedelta(days=1)).astimezone()

        specs: dict[str, tuple[str, str, str, float]] = {}
        for resource in Config.INTRADAY_RESOURCES:
            spec = _INTRADAY_TYPES.get(resource)
            if spec is None:
                logger.warning(f"Intraday resource '{resource}' is not supported by Google Health")
                continue
            specs[resource] = spec

        def fetch(resource: str) -> list[dict] | None:
            detail = (
                Config.INTRADAY_HEART_RATE_DETAIL
                if resource == "heart_rate"
                else Config.INTRADAY_DETAIL_LEVEL
            )
            window = _DETAIL_LEVEL_SECONDS.get(detail, 300)
            try:
                return self._rollup_points(specs[resource][0], day_start, day_end, window)
            except RateLimitError:
                raise
            except Exception as e:
                logger.error(f"Error collecting intraday {resource} for {record['date']}: {e}")
                return None

        # One request per resource, independent of each other: run them together.
        results = self._run_concurrently({r: (lambda r=r: fetch(r)) for r in specs})

        for resource, (_, value_key, field, scale) in specs.items():
            points = results[resource]
            if points is None:
                continue

            dataset = []
            for point in points:
                number = _num((point.get(value_key) or {}).get(field))
                if number is None:
                    continue
                try:
                    start = datetime.fromisoformat(point["startTime"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                local = start.astimezone()
                if local.date() != day_start.date():
                    continue
                dataset.append({"time": local.strftime("%H:%M:%S"), "value": number * scale})

            record["resources"][resource] = {"dataset": sorted(dataset, key=lambda p: p["time"])}

        return record

    def _rollup_points(
        self, data_type: str, start: datetime, end: datetime, window_seconds: int
    ) -> list[dict]:
        """Fixed-window rollup over a physical time range, following pages."""
        body: dict = {
            "range": {
                "startTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "windowSize": f"{window_seconds}s",
            "pageSize": 10000,
        }
        points: list[dict] = []
        while True:
            result = self._request("POST", f"dataTypes/{data_type}/dataPoints:rollUp", json=body)
            points.extend(result.get("rollupDataPoints", []))
            token = result.get("nextPageToken")
            if not token:
                return points
            body = {**body, "pageToken": token}

    def get_device_info(self) -> list[DeviceInfo]:
        """Return the state of each paired device.

        Requires the optional `googlehealth.settings.readonly` scope. Without
        it the API answers 403 and this returns an empty list.
        """
        try:
            body = self._request("GET", "pairedDevices", params={"pageSize": 100})
        except RateLimitError:
            raise
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                if not self._devices_unavailable_logged:
                    logger.info(
                        "Paired device info unavailable: needs the "
                        "googlehealth.settings.readonly scope"
                    )
                    self._devices_unavailable_logged = True
                return []
            logger.error(f"Error fetching paired devices: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching paired devices: {e}")
            return []

        devices: list[DeviceInfo] = []
        for device in body.get("pairedDevices", []):
            # Shape not yet observed on a live account; read defensively.
            info: DeviceInfo = {
                "device_id": device.get("name", "unknown").rsplit("/", 1)[-1],
                "device_type": device.get("displayName", "unknown"),
            }
            battery = _num(device.get("batteryPercentage", device.get("batteryLevel")))
            if battery is not None:
                info["battery_percent"] = battery
            devices.append(info)
        return devices


def _camel(data_type: str) -> str:
    """Convert a kebab-case data type to its camelCase response key."""
    head, *rest = data_type.split("-")
    return head + "".join(part.capitalize() for part in rest)
