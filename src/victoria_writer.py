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

        # Floors
        metrics.append(self._format_metric("fitbit_floors_total", data["floors"], timestamp))

        # Elevation
        metrics.append(self._format_metric("fitbit_elevation_meters", data["elevation"], timestamp))

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
