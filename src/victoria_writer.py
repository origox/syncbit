"""Victoria Metrics writer.

Maps normalized health records onto Prometheus metric names. This module knows
nothing about any upstream provider: collectors are responsible for translating
their wire format into `health_types` first.
"""

import logging
from datetime import datetime

import requests

from .config import Config
from .health_types import DailyRecord, DeviceInfo, IntradayRecord

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
            "device": Config.DEVICE_LABEL,
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

    def write_daily_data(self, data: DailyRecord) -> bool:
        """Write one normalized daily record to Victoria Metrics.

        Args:
            data: Normalized daily record from a collector

        Returns:
            True if successful
        """
        timestamp = data["timestamp"]
        metrics = []

        def add(name: str, value, labels: dict[str, str] | None = None) -> None:
            metrics.append(self._format_metric(name, value, timestamp, labels))

        add("fitbit_steps_total", data["steps"])
        add("fitbit_distance_km", data["distance"])
        add("fitbit_calories_total", data["calories"])

        for activity_type, minutes in data["active_minutes"].items():
            add("fitbit_active_minutes", minutes, {"type": activity_type})

        if data.get("resting_heart_rate"):
            add("fitbit_resting_heart_rate_bpm", data["resting_heart_rate"])

        for zone in data.get("heart_rate_zones", []):
            zone_label = {"zone": zone["zone"]}
            if "minutes" in zone:
                add("fitbit_heart_rate_zone_minutes", zone["minutes"], zone_label)
            if "calories" in zone:
                add("fitbit_heart_rate_zone_calories", zone["calories"], zone_label)

        sleep = data.get("sleep") or {}
        if "total_minutes" in sleep:
            add("fitbit_sleep_minutes_total", sleep["total_minutes"])
        for stage_name, minutes in (sleep.get("stages") or {}).items():
            add("fitbit_sleep_stage_minutes", minutes, {"stage": stage_name})

        spo2 = data.get("spo2") or {}
        for bound, metric_name in (
            ("avg", "fitbit_spo2_avg_percent"),
            ("min", "fitbit_spo2_min_percent"),
            ("max", "fitbit_spo2_max_percent"),
        ):
            if bound in spo2:
                add(metric_name, spo2[bound])

        for key, metric_name in (
            ("breathing_rate", "fitbit_breathing_rate_bpm"),
            ("hrv_rmssd_ms", "fitbit_hrv_rmssd_ms"),
            ("vo2_max", "fitbit_vo2_max"),
            ("skin_temp_relative_c", "fitbit_temp_skin_relative_celsius"),
        ):
            if data.get(key) is not None:
                add(metric_name, data[key])

        return self._send_metrics(metrics)

    def write_multiple_days(self, data_points: list[DailyRecord]) -> tuple[int, int]:
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

    def write_intraday_data(self, data: IntradayRecord) -> bool:
        """Write intraday samples to Victoria Metrics.

        Args:
            data: Normalized intraday record from a collector

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

    def write_device_info(self, devices: list[DeviceInfo]) -> bool:
        """Write normalized device state to Victoria Metrics.

        Args:
            devices: Normalized device state from a collector

        Returns:
            True if successful
        """
        if not devices:
            logger.debug("No device info to write")
            return True

        timestamp = int(datetime.now().timestamp())
        metrics = []

        for device in devices:
            labels = {
                "device_id": device.get("device_id", "unknown"),
                "device_type": device.get("device_type", "unknown"),
            }

            if device.get("battery_percent") is not None:
                metrics.append(
                    self._format_metric(
                        "fitbit_device_battery_percent",
                        device["battery_percent"],
                        timestamp,
                        labels,
                    )
                )

            last_sync = device.get("last_sync")
            if last_sync is not None:
                metrics.append(
                    self._format_metric(
                        "fitbit_device_last_sync_timestamp",
                        int(last_sync.timestamp()),
                        timestamp,
                        labels,
                    )
                )

        return self._send_metrics(metrics) if metrics else True

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
