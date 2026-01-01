"""Victoria Metrics writer for Fitbit data in Prometheus format."""

import logging
from datetime import datetime

import requests

from .config import Config

logger = logging.getLogger(__name__)


class VictoriaMetricsWriter:
    """Writes metrics to Victoria Metrics in Prometheus format."""

    def __init__(self):
        """Initialize Victoria Metrics writer."""
        self.endpoint = Config.VICTORIA_ENDPOINT
        self.auth = (Config.VICTORIA_USER, Config.VICTORIA_PASSWORD)
        self.user_id = Config.get_fitbit_user_id()

    def _format_metric(
        self, name: str, value: float, timestamp: int, labels: dict[str, str] = None
    ) -> str:
        """Format a single metric in Prometheus format.

        Args:
            name: Metric name
            value: Metric value
            timestamp: Unix timestamp in seconds
            labels: Additional labels

        Returns:
            Formatted metric line
        """
        # Base labels
        all_labels = {
            "user": self.user_id,
            "device": "charge6",
        }

        # Add custom labels
        if labels:
            all_labels.update(labels)

        # Format labels
        label_str = ",".join([f'{k}="{v}"' for k, v in sorted(all_labels.items())])

        # Convert timestamp to milliseconds for Prometheus
        timestamp_ms = timestamp * 1000

        # Format: metric_name{labels} value timestamp
        return f"{name}{{{label_str}}} {value} {timestamp_ms}\n"

    def write_daily_data(self, data: dict) -> bool:
        """Write daily Fitbit data to Victoria Metrics.

        Args:
            data: Daily data from Fitbit collector

        Returns:
            True if successful
        """
        timestamp = data["timestamp"]
        metrics = []

        # Steps
        metrics.append(self._format_metric("fitbit_steps_total", data["steps"], timestamp))

        # Distance (in kilometers)
        metrics.append(self._format_metric("fitbit_distance_km", data["distance"], timestamp))

        # Calories
        metrics.append(self._format_metric("fitbit_calories_total", data["calories"], timestamp))

        # Active minutes by type
        for activity_type, minutes in data["active_minutes"].items():
            metrics.append(
                self._format_metric(
                    "fitbit_active_minutes", minutes, timestamp, {"type": activity_type}
                )
            )

        # Resting heart rate
        if data["heart_rate"]["resting"]:
            metrics.append(
                self._format_metric(
                    "fitbit_resting_heart_rate_bpm", data["heart_rate"]["resting"], timestamp
                )
            )

        # Heart rate zones
        for zone in data["heart_rate"]["zones"]:
            zone_name = zone.get("name", "unknown").lower().replace(" ", "_")

            # Minutes in zone
            if "minutes" in zone:
                metrics.append(
                    self._format_metric(
                        "fitbit_heart_rate_zone_minutes",
                        zone["minutes"],
                        timestamp,
                        {"zone": zone_name},
                    )
                )

            # Calories in zone
            if "caloriesOut" in zone:
                metrics.append(
                    self._format_metric(
                        "fitbit_heart_rate_zone_calories",
                        zone["caloriesOut"],
                        timestamp,
                        {"zone": zone_name},
                    )
                )

        # Sleep data
        if "sleep" in data and data["sleep"]:
            sleep_summary = data["sleep"].get("summary", {})

            # Total sleep minutes
            if "totalMinutesAsleep" in sleep_summary:
                metrics.append(
                    self._format_metric(
                        "fitbit_sleep_minutes_total",
                        sleep_summary["totalMinutesAsleep"],
                        timestamp,
                    )
                )

            # Sleep stages
            stages = sleep_summary.get("stages", {})
            for stage_name, minutes in stages.items():
                if isinstance(minutes, (int, float)):
                    metrics.append(
                        self._format_metric(
                            "fitbit_sleep_stage_minutes",
                            minutes,
                            timestamp,
                            {"stage": stage_name},
                        )
                    )

        # SpO2 data
        if "spo2" in data and data["spo2"]:
            spo2_value = data["spo2"].get("value", {})
            if "avg" in spo2_value:
                metrics.append(
                    self._format_metric("fitbit_spo2_avg_percent", spo2_value["avg"], timestamp)
                )
            if "min" in spo2_value:
                metrics.append(
                    self._format_metric("fitbit_spo2_min_percent", spo2_value["min"], timestamp)
                )
            if "max" in spo2_value:
                metrics.append(
                    self._format_metric("fitbit_spo2_max_percent", spo2_value["max"], timestamp)
                )

        # Breathing rate
        if "breathing_rate" in data and data["breathing_rate"]:
            for br_entry in data["breathing_rate"]:
                if "value" in br_entry:
                    br_value = br_entry["value"].get("breathingRate")
                    if br_value:
                        metrics.append(
                            self._format_metric("fitbit_breathing_rate_bpm", br_value, timestamp)
                        )

        # HRV (Heart Rate Variability)
        if "hrv" in data and data["hrv"]:
            for hrv_entry in data["hrv"]:
                if "value" in hrv_entry:
                    rmssd = hrv_entry["value"].get("rmssd")
                    if rmssd:
                        metrics.append(self._format_metric("fitbit_hrv_rmssd_ms", rmssd, timestamp))

        # Cardio fitness score
        if "cardio_fitness" in data and data["cardio_fitness"]:
            for cf_entry in data["cardio_fitness"]:
                if "vo2Max" in cf_entry:
                    metrics.append(
                        self._format_metric("fitbit_vo2_max", cf_entry["vo2Max"], timestamp)
                    )

        # Temperature
        if "temperature" in data and data["temperature"]:
            for temp_entry in data["temperature"]:
                if "value" in temp_entry:
                    temp_value = temp_entry["value"].get("nightlyRelative")
                    if temp_value is not None:
                        metrics.append(
                            self._format_metric(
                                "fitbit_temp_skin_relative_celsius", temp_value, timestamp
                            )
                        )

        # Send metrics
        return self._send_metrics(metrics)

    def write_multiple_days(self, data_points: list[dict]) -> tuple[int, int]:
        """Write multiple days of data.

        Args:
            data_points: List of daily data

        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0

        for data in data_points:
            try:
                if self.write_daily_data(data):
                    successful += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Error writing data for {data.get('date')}: {e}")
                failed += 1

        return successful, failed

    def _send_metrics(self, metrics: list[str]) -> bool:
        """Send metrics to Victoria Metrics.

        Args:
            metrics: List of formatted metric lines

        Returns:
            True if successful
        """
        if not metrics:
            logger.warning("No metrics to send")
            return False

        # Combine all metrics
        payload = "".join(metrics)

        try:
            response = requests.post(
                self.endpoint,
                data=payload.encode("utf-8"),
                auth=self.auth,
                headers={"Content-Type": "text/plain"},
                timeout=30,
            )

            response.raise_for_status()
            logger.info(f"Successfully wrote {len(metrics)} metrics to Victoria Metrics")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to write metrics to Victoria Metrics: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return False

    def write_intraday_data(self, data: dict) -> bool:
        """Write intraday Fitbit data to Victoria Metrics.

        Args:
            data: Intraday data from Fitbit collector

        Returns:
            True if successful
        """
        if not data or not data.get("resources"):
            logger.debug("No intraday data to write")
            return True  # Nothing to write is success

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
                try:
                    hour, minute, second = map(int, time_str.split(":"))
                    point_datetime = base_date.replace(hour=hour, minute=minute, second=second)
                    timestamp = int(point_datetime.timestamp())

                    # Add metric
                    metrics.append(self._format_metric(metric_name, value, timestamp))
                except (ValueError, KeyError) as e:
                    logger.error(f"Error parsing time point {time_str} for {resource}: {e}")
                    continue

        # Send all metrics in batch
        if not metrics:
            logger.warning(f"No intraday metrics to write for {date_str}")
            return True

        success = self._send_metrics(metrics)

        if success:
            logger.info(f"Successfully wrote {len(metrics)} intraday metrics for {date_str}")

        return success

    def write_device_info(self, devices: list[dict]) -> bool:
        """Write device information to Victoria Metrics.

        Args:
            devices: List of device information dictionaries

        Returns:
            True if successful
        """
        if not devices:
            logger.debug("No device info to write")
            return True

        timestamp = int(datetime.now().timestamp())
        metrics = []

        for device in devices:
            device_id = device.get("id", "unknown")
            device_type = device.get("deviceVersion", "unknown")

            # Battery level - prefer batteryLevel (exact %) over battery (text)
            battery_level = device.get("batteryLevel")
            if battery_level is None:
                # Fallback to parsing text battery level if batteryLevel not available
                battery = device.get("battery")
                if battery:
                    battery_level = self._parse_battery_level(battery)

            if battery_level is not None:
                metrics.append(
                    self._format_metric(
                        "fitbit_device_battery_percent",
                        battery_level,
                        timestamp,
                        {"device_id": device_id, "device_type": device_type},
                    )
                )

            # Last sync time
            last_sync_time = device.get("lastSyncTime")
            if last_sync_time:
                try:
                    # Parse ISO 8601 datetime
                    sync_dt = datetime.fromisoformat(last_sync_time.replace("Z", "+00:00"))
                    sync_timestamp = int(sync_dt.timestamp())
                    metrics.append(
                        self._format_metric(
                            "fitbit_device_last_sync_timestamp",
                            sync_timestamp,
                            timestamp,
                            {"device_id": device_id, "device_type": device_type},
                        )
                    )
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Error parsing last sync time: {e}")

        return self._send_metrics(metrics) if metrics else True

    def _parse_battery_level(self, battery_str: str) -> float | None:
        """Parse battery level from string.

        Args:
            battery_str: Battery level string (e.g., "High", "Medium", "Low", "85%")

        Returns:
            Battery percentage as float, or None if unparseable
        """
        battery_str = battery_str.strip()

        # Try to parse percentage
        if "%" in battery_str:
            try:
                return float(battery_str.replace("%", "").strip())
            except ValueError:
                pass

        # Map text levels to approximate percentages
        battery_map = {
            "high": 80.0,
            "medium": 50.0,
            "low": 20.0,
            "full": 100.0,
            "empty": 0.0,
        }

        return battery_map.get(battery_str.lower())

    def test_connection(self) -> bool:
        """Test connection to Victoria Metrics.

        Returns:
            True if connection successful
        """
        test_metric = self._format_metric(
            "fitbit_test_connection", 1.0, int(datetime.now().timestamp())
        )

        try:
            return self._send_metrics([test_metric])
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
