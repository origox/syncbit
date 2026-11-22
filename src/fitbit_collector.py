"""Fitbit data collector.

Translates the Fitbit Web API's response shapes into the provider-neutral types
in `health_types`, so that nothing downstream of this module needs to know
Fitbit's field names.
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from .collector import RateLimitError
from .config import Config
from .fitbit_auth import FitbitAuth
from .health_types import DailyRecord, DeviceInfo, HeartRateZone, Sleep, SpO2

# Re-exported so existing importers of RateLimitError keep working.
__all__ = ["FitbitCollector", "RateLimitError"]

logger = logging.getLogger(__name__)

# Fitbit reports coarse text battery levels on some devices; map them to
# approximate percentages so the metric is always numeric.
_BATTERY_TEXT_LEVELS = {
    "high": 80.0,
    "medium": 50.0,
    "low": 20.0,
    "full": 100.0,
    "empty": 0.0,
}


class FitbitCollector:
    """Collects data from Fitbit API."""

    def __init__(self, auth: FitbitAuth):
        """Initialize collector.

        Args:
            auth: Fitbit authentication handler
        """
        self.auth = auth
        self.base_url = Config.FITBIT_API_BASE_URL

    def _make_request(self, endpoint: str, params: dict | None = None) -> dict:
        """Make authenticated request to Fitbit API.

        Args:
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            Response JSON
        """
        token = self.auth.get_valid_token()

        headers = {
            "Authorization": f"Bearer {token}",
        }

        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                # Rate limited - extract retry time and quota info from headers
                retry_after = int(e.response.headers.get("Retry-After", "60"))
                limit = e.response.headers.get("Fitbit-Rate-Limit-Limit", "unknown")
                remaining_str = e.response.headers.get("Fitbit-Rate-Limit-Remaining", "unknown")
                reset_str = e.response.headers.get("Fitbit-Rate-Limit-Reset", "unknown")

                # Parse remaining and reset as integers
                try:
                    remaining = int(remaining_str)
                except (ValueError, TypeError):
                    remaining = None

                try:
                    quota_reset = int(reset_str)
                except (ValueError, TypeError):
                    quota_reset = None

                logger.warning(
                    f"Rate limited: Retry-After={retry_after}s, "
                    f"Limit={limit}, Remaining={remaining}, Reset={quota_reset}"
                )
                raise RateLimitError(
                    "Rate limited by Fitbit API",
                    retry_after,
                    quota_reset=quota_reset,
                    remaining=remaining,
                )
            logger.error(f"API request failed: {e}")
            raise

    def get_activity_summary(self, date: datetime) -> dict:
        """Get activity summary for a specific date.

        Args:
            date: Date to fetch data for

        Returns:
            Activity summary data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/activities/date/{date_str}.json"

        logger.debug(f"Fetching activity summary for {date_str}")
        data = self._make_request(endpoint)

        return data.get("summary", {})

    def get_heart_rate(self, date: datetime) -> dict:
        """Get heart rate data for a specific date.

        Args:
            date: Date to fetch data for

        Returns:
            Heart rate data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/activities/heart/date/{date_str}/1d.json"

        logger.debug(f"Fetching heart rate for {date_str}")
        data = self._make_request(endpoint)

        activities_heart = data.get("activities-heart", [])
        if activities_heart:
            return activities_heart[0].get("value", {})
        return {}

    def get_steps(self, date: datetime) -> int:
        """Get step count for a specific date.

        Args:
            date: Date to fetch data for

        Returns:
            Step count
        """
        # Steps are part of activity summary, but also available separately
        activity = self.get_activity_summary(date)
        return activity.get("steps", 0)

    @staticmethod
    def _first_nested(entries: list[dict] | None, *keys: str):
        """Return the first non-null value found at `keys` across `entries`.

        Fitbit returns most daily metrics as a single-element list wrapped in a
        `value` object. Multiple entries for one date would all carry the same
        metric timestamp and so collapse to a single sample on write, which is
        why only the first usable value is kept.
        """
        for entry in entries or []:
            value = entry
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    value = None
                    break
                value = value[key]
            if value is not None:
                return value
        return None

    @staticmethod
    def _normalize_heart_rate_zones(zones: list[dict]) -> list[HeartRateZone]:
        """Convert Fitbit heart rate zones to normalized zone entries."""
        normalized: list[HeartRateZone] = []
        for zone in zones or []:
            entry: HeartRateZone = {"zone": zone.get("name", "unknown").lower().replace(" ", "_")}
            if "minutes" in zone:
                entry["minutes"] = zone["minutes"]
            if "caloriesOut" in zone:
                entry["calories"] = zone["caloriesOut"]
            normalized.append(entry)
        return normalized

    @staticmethod
    def _normalize_sleep(raw: dict) -> Sleep:
        """Convert a Fitbit sleep response to normalized sleep totals/stages."""
        summary = (raw or {}).get("summary") or {}
        sleep: Sleep = {}

        if "totalMinutesAsleep" in summary:
            sleep["total_minutes"] = summary["totalMinutesAsleep"]

        stages = {
            name: minutes
            for name, minutes in (summary.get("stages") or {}).items()
            if isinstance(minutes, (int, float))
        }
        if stages:
            sleep["stages"] = stages

        return sleep

    @staticmethod
    def _normalize_spo2(raw: dict) -> SpO2:
        """Convert a Fitbit SpO2 response to normalized avg/min/max."""
        value = (raw or {}).get("value") or {}
        return {key: value[key] for key in ("avg", "min", "max") if key in value}

    @classmethod
    def _normalize_device(cls, device: dict) -> DeviceInfo:
        """Convert a Fitbit device entry to normalized device state."""
        info: DeviceInfo = {
            "device_id": device.get("id", "unknown"),
            "device_type": device.get("deviceVersion", "unknown"),
        }

        # Prefer the exact percentage; fall back to the coarse text level.
        battery_level = device.get("batteryLevel")
        if battery_level is None:
            battery = device.get("battery")
            if battery:
                battery_level = cls._parse_battery_level(battery)
        if battery_level is not None:
            info["battery_percent"] = battery_level

        last_sync = device.get("lastSyncTime")
        if last_sync:
            try:
                info["last_sync"] = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            except (ValueError, AttributeError) as e:
                logger.warning(f"Error parsing last sync time: {e}")

        return info

    @staticmethod
    def _parse_battery_level(battery_str: str) -> float | None:
        """Parse a Fitbit battery level into a percentage.

        Args:
            battery_str: Battery level (e.g. "High", "Medium", "Low", "85%")

        Returns:
            Battery percentage, or None if unparseable
        """
        battery_str = battery_str.strip()

        if "%" in battery_str:
            try:
                return float(battery_str.replace("%", "").strip())
            except ValueError:
                pass

        return _BATTERY_TEXT_LEVELS.get(battery_str.lower())

    def _collect_optional(self, data: DailyRecord, key: str, fetch, normalize=None) -> None:
        """Fetch one optional metric, normalize it, and store it under `key`.

        Optional metrics are frequently unavailable (404) or not yet processed
        by Fitbit. Any failure other than rate limiting is logged and skipped so
        that one missing metric never costs the rest of the day's data.
        """
        try:
            raw = fetch()
        except RateLimitError:
            raise  # Handled by the scheduler, which owns backoff.
        except Exception as e:
            logger.error(f"Error collecting {key}: {e}")
            return

        value = normalize(raw) if normalize else raw
        if value is not None:
            data[key] = value

        time.sleep(5)  # Rate limit delay

    def get_daily_data(self, date: datetime) -> DailyRecord:
        """Get all daily data for a specific date.

        Args:
            date: Date to fetch data for

        Returns:
            Normalized daily record for all enabled metrics
        """
        logger.info(f"Collecting data for {date.strftime('%Y-%m-%d')}")

        # Core metrics (always collected)
        activity = self.get_activity_summary(date)
        heart_rate = self.get_heart_rate(date)

        distances = activity.get("distances")

        data: DailyRecord = {
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": int(date.timestamp()),
            "steps": activity.get("steps", 0),
            "distance": distances[0].get("distance", 0.0) if distances else 0.0,
            "calories": activity.get("caloriesOut", 0),
            "active_minutes": {
                "sedentary": activity.get("sedentaryMinutes", 0),
                "lightly_active": activity.get("lightlyActiveMinutes", 0),
                "fairly_active": activity.get("fairlyActiveMinutes", 0),
                "very_active": activity.get("veryActiveMinutes", 0),
            },
            "resting_heart_rate": heart_rate.get("restingHeartRate", 0),
            "heart_rate_zones": self._normalize_heart_rate_zones(
                heart_rate.get("heartRateZones", [])
            ),
        }

        # Optional metrics, each gated on its own configuration toggle.
        if Config.COLLECT_SLEEP:
            self._collect_optional(
                data, "sleep", lambda: self.get_sleep_data(date), self._normalize_sleep
            )

        if Config.COLLECT_SPO2:
            self._collect_optional(
                data, "spo2", lambda: self.get_spo2_data(date), self._normalize_spo2
            )

        if Config.COLLECT_BREATHING_RATE:
            self._collect_optional(
                data,
                "breathing_rate",
                lambda: self.get_breathing_rate(date),
                lambda raw: self._first_nested(raw, "value", "breathingRate"),
            )

        if Config.COLLECT_HRV:
            self._collect_optional(
                data,
                "hrv_rmssd_ms",
                lambda: self.get_hrv_data(date),
                lambda raw: self._first_nested(raw, "value", "rmssd"),
            )

        if Config.COLLECT_CARDIO_FITNESS:
            self._collect_optional(
                data,
                "vo2_max",
                lambda: self.get_cardio_fitness_score(date),
                lambda raw: self._first_nested(raw, "vo2Max"),
            )

        if Config.COLLECT_TEMPERATURE:
            self._collect_optional(
                data,
                "skin_temp_relative_c",
                lambda: self.get_temperature_data(date),
                lambda raw: self._first_nested(raw, "value", "nightlyRelative"),
            )

        return data

    def get_historical_data(self, start_date: datetime, end_date: datetime) -> list[DailyRecord]:
        """Get historical data for a date range.

        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)

        Returns:
            List of daily data
        """
        data_points = []
        current_date = start_date
        retry_count = 0
        max_retries = 3

        while current_date <= end_date:
            try:
                daily_data = self.get_daily_data(current_date)
                data_points.append(daily_data)
                retry_count = 0  # Reset retry counter on success

                # Add delay to respect rate limits (150 req/hour = 1 req every 24 seconds)
                # We make 2 requests per day (activity + heart rate), so wait 30 seconds
                time.sleep(30)

            except RateLimitError as e:
                # Use the exact retry time from Fitbit
                retry_after = e.retry_after
                logger.warning(
                    f"Rate limited on {current_date.strftime('%Y-%m-%d')}, "
                    f"waiting {retry_after} seconds as requested by Fitbit..."
                )
                time.sleep(retry_after)
                continue  # Retry this date without incrementing

            except requests.exceptions.HTTPError as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        f"Failed to fetch data for {current_date.strftime('%Y-%m-%d')} "
                        f"after {max_retries} retries: {e}"
                    )
                    retry_count = 0
                    current_date += timedelta(days=1)  # Skip this date and move on
                else:
                    logger.warning(
                        f"Error fetching {current_date.strftime('%Y-%m-%d')}, "
                        f"retry {retry_count}/{max_retries}"
                    )
                    time.sleep(5)  # Short delay before retry
                    continue

            except Exception as e:
                logger.error(f"Unexpected error for {current_date.strftime('%Y-%m-%d')}: {e}")
                current_date += timedelta(days=1)  # Skip and continue
            else:
                # Only increment date if no exception or after max retries
                current_date += timedelta(days=1)

        return data_points

    def get_first_available_date(self) -> datetime | None:
        """Attempt to find the first date with available data.

        This tries to fetch data going back from today to find when
        the user first started using Fitbit.

        Returns:
            First date with data, or None if unable to determine
        """
        # Try to get member since date from profile
        try:
            token = self.auth.get_valid_token()
            headers = {"Authorization": f"Bearer {token}"}

            response = requests.get("https://api.fitbit.com/1/user/-/profile.json", headers=headers)
            response.raise_for_status()

            profile = response.json().get("user", {})
            member_since = profile.get("memberSince")

            if member_since:
                logger.info(f"User member since: {member_since}")
                return datetime.strptime(member_since, "%Y-%m-%d")
        except Exception as e:
            logger.error(f"Error fetching profile: {e}")

        # Fallback: assume data exists from 30 days ago
        return datetime.now() - timedelta(days=30)

    def get_intraday_activity(
        self, date: datetime, resource: str, detail_level: str = "5min"
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

    def get_intraday_heart_rate(self, date: datetime, detail_level: str = "1min") -> dict:
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
            "resources": {},
        }

        # Collect activity resources
        for resource in Config.INTRADAY_RESOURCES:
            try:
                if resource == "heart_rate":
                    dataset = self.get_intraday_heart_rate(date, Config.INTRADAY_HEART_RATE_DETAIL)
                else:
                    dataset = self.get_intraday_activity(
                        date, resource, Config.INTRADAY_DETAIL_LEVEL
                    )

                intraday_data["resources"][resource] = dataset

                # Rate limit delay between resources
                time.sleep(5)  # Conservative 5-second delay

            except RateLimitError:
                # Re-raise rate limit errors to be handled by caller
                raise
            except Exception as e:
                # Log error but continue with other resources
                logger.error(
                    f"Error collecting intraday {resource} for {date.strftime('%Y-%m-%d')}: {e}"
                )
                intraday_data["resources"][resource] = {}

        return intraday_data

    def get_sleep_data(self, date: datetime) -> dict:
        """Get sleep data including stages, duration, and efficiency.

        Args:
            date: Date to fetch data for

        Returns:
            Sleep data with stages and metrics
        """
        date_str = date.strftime("%Y-%m-%d")

        # Sleep API uses v1.2 instead of v1, so construct full URL manually
        token = self.auth.get_valid_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://api.fitbit.com/1.2/user/-/sleep/date/{date_str}.json"

        logger.debug(f"Fetching sleep data for {date_str}")

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", "60"))
                logger.warning(f"Rate limited, must wait {retry_after} seconds")
                raise RateLimitError("Rate limited by Fitbit API", retry_after)
            logger.error(f"API request failed: {e}")
            raise

        sleep_data = data.get("sleep", [])
        summary = data.get("summary", {})

        return {
            "sleep": sleep_data,
            "summary": summary,
        }

    def get_spo2_data(self, date: datetime) -> dict:
        """Get SpO2 (blood oxygen saturation) data.

        Args:
            date: Date to fetch data for

        Returns:
            SpO2 data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/spo2/date/{date_str}.json"

        logger.debug(f"Fetching SpO2 data for {date_str}")
        try:
            data = self._make_request(endpoint)
            return data
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No SpO2 data available for {date_str}")
                return {}
            raise

    def get_breathing_rate(self, date: datetime) -> dict:
        """Get breathing rate data.

        Args:
            date: Date to fetch data for

        Returns:
            Breathing rate data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/br/date/{date_str}.json"

        logger.debug(f"Fetching breathing rate for {date_str}")
        try:
            data = self._make_request(endpoint)
            return data.get("br", [])
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No breathing rate data available for {date_str}")
                return []
            raise

    def get_hrv_data(self, date: datetime) -> dict:
        """Get heart rate variability data.

        Args:
            date: Date to fetch data for

        Returns:
            HRV data with RMSSD values
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/hrv/date/{date_str}.json"

        logger.debug(f"Fetching HRV data for {date_str}")
        try:
            data = self._make_request(endpoint)
            return data.get("hrv", [])
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No HRV data available for {date_str}")
                return []
            raise

    def get_cardio_fitness_score(self, date: datetime) -> dict:
        """Get cardio fitness (VO2 Max) score.

        Args:
            date: Date to fetch data for

        Returns:
            Cardio fitness score data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/cardioscore/date/{date_str}.json"

        logger.debug(f"Fetching cardio fitness score for {date_str}")
        try:
            data = self._make_request(endpoint)
            return data.get("cardioScore", [])
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No cardio fitness data available for {date_str}")
                return []
            raise

    def get_temperature_data(self, date: datetime) -> dict:
        """Get skin temperature variation data.

        Args:
            date: Date to fetch data for

        Returns:
            Temperature data
        """
        date_str = date.strftime("%Y-%m-%d")
        endpoint = f"/temp/skin/date/{date_str}.json"

        logger.debug(f"Fetching temperature data for {date_str}")
        try:
            data = self._make_request(endpoint)
            return data.get("tempSkin", [])
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No temperature data available for {date_str}")
                return []
            raise

    def get_device_info(self) -> list[DeviceInfo]:
        """Get device state including battery level and last sync time.

        Returns:
            Normalized device state, one entry per paired device
        """
        endpoint = "/devices.json"

        logger.debug("Fetching device information")
        try:
            data = self._make_request(endpoint)
        except RateLimitError:
            raise  # Re-raise rate limit errors to be handled by scheduler
        except Exception as e:
            logger.error(f"Error fetching device info: {e}")
            return []

        if not isinstance(data, list):
            return []

        return [self._normalize_device(device) for device in data]
