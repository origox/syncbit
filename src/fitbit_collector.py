"""Fitbit data collector for activity, heart rate, and steps."""

import logging
import time
from datetime import datetime, timedelta

import requests

from .config import Config
from .fitbit_auth import FitbitAuth

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Exception raised when rate limited by Fitbit API."""

    def __init__(self, message: str, retry_after: int):
        """Initialize with retry time.

        Args:
            message: Error message
            retry_after: Seconds to wait before retry
        """
        super().__init__(message)
        self.retry_after = retry_after


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
                # Rate limited - extract retry time from header
                retry_after = int(e.response.headers.get("Retry-After", "60"))
                logger.warning(f"Rate limited, must wait {retry_after} seconds")
                raise RateLimitError("Rate limited by Fitbit API", retry_after)
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

    def get_daily_data(self, date: datetime) -> dict:
        """Get all daily data for a specific date.

        Args:
            date: Date to fetch data for

        Returns:
            Combined daily data including all enabled metrics
        """
        logger.info(f"Collecting data for {date.strftime('%Y-%m-%d')}")

        # Core metrics (always collected)
        activity = self.get_activity_summary(date)
        heart_rate = self.get_heart_rate(date)

        data = {
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": int(date.timestamp()),
            "steps": activity.get("steps", 0),
            "distance": (
                activity.get("distances", [{}])[0].get("distance", 0.0)
                if activity.get("distances")
                else 0.0
            ),
            "calories": activity.get("caloriesOut", 0),
            "active_minutes": {
                "sedentary": activity.get("sedentaryMinutes", 0),
                "lightly_active": activity.get("lightlyActiveMinutes", 0),
                "fairly_active": activity.get("fairlyActiveMinutes", 0),
                "very_active": activity.get("veryActiveMinutes", 0),
            },
            "heart_rate": {
                "resting": heart_rate.get("restingHeartRate", 0),
                "zones": heart_rate.get("heartRateZones", []),
            },
        }

        # Optional metrics based on configuration
        if Config.COLLECT_SLEEP:
            try:
                data["sleep"] = self.get_sleep_data(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting sleep data: {e}")
                data["sleep"] = {}

        if Config.COLLECT_SPO2:
            try:
                data["spo2"] = self.get_spo2_data(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting SpO2 data: {e}")
                data["spo2"] = {}

        if Config.COLLECT_BREATHING_RATE:
            try:
                data["breathing_rate"] = self.get_breathing_rate(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting breathing rate: {e}")
                data["breathing_rate"] = []

        if Config.COLLECT_HRV:
            try:
                data["hrv"] = self.get_hrv_data(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting HRV data: {e}")
                data["hrv"] = []

        if Config.COLLECT_CARDIO_FITNESS:
            try:
                data["cardio_fitness"] = self.get_cardio_fitness_score(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting cardio fitness: {e}")
                data["cardio_fitness"] = []

        if Config.COLLECT_TEMPERATURE:
            try:
                data["temperature"] = self.get_temperature_data(date)
                time.sleep(5)  # Rate limit delay
            except Exception as e:
                logger.error(f"Error collecting temperature data: {e}")
                data["temperature"] = []

        return data

    def get_historical_data(self, start_date: datetime, end_date: datetime) -> list[dict]:
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
        # Note: Sleep API uses version 1.2, need to construct full URL
        endpoint = f"/../1.2/user/-/sleep/date/{date_str}.json"

        logger.debug(f"Fetching sleep data for {date_str}")
        data = self._make_request(endpoint)

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

    def get_device_info(self) -> list[dict]:
        """Get device information including battery, firmware, type, and last sync.

        Returns:
            List of device information dictionaries
        """
        endpoint = "/devices.json"

        logger.debug("Fetching device information")
        try:
            data = self._make_request(endpoint)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error fetching device info: {e}")
            return []
