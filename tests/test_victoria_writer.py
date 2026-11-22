"""Tests for Victoria Metrics writer."""

from datetime import UTC, datetime

import pytest
import responses

from src.config import Config
from src.victoria_writer import VictoriaMetricsWriter


@pytest.fixture
def writer():
    """Create VictoriaMetricsWriter instance for testing."""
    return VictoriaMetricsWriter()


def test_format_metric_basic(writer):
    """Test basic Prometheus format generation."""
    metric = writer._format_metric(
        name="fitbit_steps_total",
        value=10000,
        timestamp=1735084800000,
        labels={"user": "testuser", "device": "charge6"},
    )

    # Labels are sorted alphabetically, timestamp has 3 extra zeros (microseconds), has newline
    assert 'fitbit_steps_total{device="charge6",user="testuser"} 10000 1735084800000000' in metric


def test_format_metric_no_labels(writer):
    """Test metric format without labels (actually has default labels)."""
    metric = writer._format_metric(
        name="fitbit_calories_total", value=2500, timestamp=1735084800000
    )

    # Writer adds default labels automatically
    assert "fitbit_calories_total" in metric
    assert "2500" in metric
    assert "1735084800000000" in metric


def test_format_metric_float_value(writer):
    """Test metric format with float value."""
    metric = writer._format_metric(name="fitbit_distance_km", value=6.52, timestamp=1735084800000)

    assert "fitbit_distance_km" in metric
    assert "6.52" in metric


def test_format_metric_zero_value(writer):
    """Test metric format with zero value."""
    metric = writer._format_metric(name="fitbit_floors_total", value=0, timestamp=1735084800000)

    assert "fitbit_floors_total" in metric
    assert " 0 " in metric


def test_write_daily_data_converts_to_metrics(writer):
    """Test that daily data is converted to metrics."""
    data = {
        "date": "2025-12-24",
        "timestamp": 1735084800,
        "steps": 8543,
        "distance": 6.52,
        "calories": 2734,
        "active_minutes": {
            "sedentary": 720,
            "lightly_active": 180,
            "fairly_active": 20,
            "very_active": 30,
        },
        "resting_heart_rate": 62,
        "heart_rate_zones": [
            {"zone": "out_of_range", "minutes": 720, "calories": 100.0},
            {"zone": "fat_burn", "minutes": 180, "calories": 200.0},
        ],
        "sleep": {"total_minutes": 432, "stages": {"deep": 80, "rem": 90}},
        "spo2": {"avg": 96.5, "min": 92.0, "max": 99.0},
        "breathing_rate": 14.5,
        "hrv_rmssd_ms": 42.1,
        "vo2_max": 47.3,
        "skin_temp_relative_c": -0.3,
    }

    captured = []
    writer._send_metrics = lambda metrics: bool(captured.extend(metrics) or True)

    assert writer.write_daily_data(data) is True

    rendered = "".join(captured)
    assert 'fitbit_steps_total{device="fitbit_air",user="default"} 8543 1735084800000' in rendered
    assert 'fitbit_resting_heart_rate_bpm{device="fitbit_air",user="default"} 62' in rendered
    assert (
        'fitbit_heart_rate_zone_minutes{device="fitbit_air",user="default",zone="fat_burn"} 180'
        in rendered
    )
    assert (
        'fitbit_heart_rate_zone_calories{device="fitbit_air",user="default",zone="fat_burn"} 200.0'
        in rendered
    )
    assert 'fitbit_sleep_minutes_total{device="fitbit_air",user="default"} 432' in rendered
    assert (
        'fitbit_sleep_stage_minutes{device="fitbit_air",stage="deep",user="default"} 80' in rendered
    )
    assert 'fitbit_spo2_avg_percent{device="fitbit_air",user="default"} 96.5' in rendered
    assert 'fitbit_breathing_rate_bpm{device="fitbit_air",user="default"} 14.5' in rendered
    assert 'fitbit_hrv_rmssd_ms{device="fitbit_air",user="default"} 42.1' in rendered
    assert 'fitbit_vo2_max{device="fitbit_air",user="default"} 47.3' in rendered
    assert 'fitbit_temp_skin_relative_celsius{device="fitbit_air",user="default"} -0.3' in rendered


def test_timestamp_conversion(writer):
    """Test timestamp format in metrics."""
    # Test with a single metric - timestamp gets multiplied by 1000 for milliseconds
    metric = writer._format_metric(name="test_metric", value=100, timestamp=1735084800)  # seconds

    # Should contain timestamp in milliseconds
    assert "1735084800000" in metric


@responses.activate
def test_write_daily_data_success(writer):
    """Test successful write to Victoria Metrics."""
    # Mock the Victoria Metrics endpoint
    test_endpoint = "https://victoria-metrics.example.com/api/v1/import/prometheus"
    responses.add(responses.POST, test_endpoint, status=204)

    # Override writer endpoint for this test
    writer.endpoint = test_endpoint

    data = {
        "date": "2025-12-24",
        "timestamp": 1735084800,
        "steps": 8543,
        "distance": 6.52,
        "calories": 2734,
        "active_minutes": {
            "sedentary": 720,
            "lightly_active": 180,
            "fairly_active": 20,
            "very_active": 30,
        },
        "resting_heart_rate": 62,
        "heart_rate_zones": [],
    }

    result = writer.write_daily_data(data)

    assert result is True
    assert len(responses.calls) == 1


@responses.activate
def test_write_daily_data_failure(writer):
    """Test handling of write failure."""
    # Mock failed response
    responses.add(responses.POST, Config.VICTORIA_ENDPOINT, status=500)

    data = {
        "date": "2025-12-24",
        "timestamp": 1735084800,
        "steps": 100,
        "distance": 1.0,
        "calories": 500,
        "active_minutes": {
            "sedentary": 0,
            "lightly_active": 0,
            "fairly_active": 0,
            "very_active": 0,
        },
        "resting_heart_rate": 60,
        "heart_rate_zones": [],
    }

    result = writer.write_daily_data(data)

    assert result is False


@responses.activate
def test_test_connection_success(writer):
    """Test successful connection test."""
    test_endpoint = "https://victoria-metrics.example.com/api/v1/import/prometheus"
    responses.add(responses.POST, test_endpoint, status=204)

    # Override writer endpoint for this test
    writer.endpoint = test_endpoint

    result = writer.test_connection()

    assert result is True


@responses.activate
def test_test_connection_failure(writer):
    """Test failed connection test."""
    responses.add(responses.POST, Config.VICTORIA_ENDPOINT, status=500)

    result = writer.test_connection()

    assert result is False


@responses.activate
def test_write_device_info_with_battery_level(writer):
    """Test device info write from a normalized device record."""
    test_endpoint = "https://victoria-metrics.example.com/api/v1/import/prometheus"
    responses.add(responses.POST, test_endpoint, status=204)
    writer.endpoint = test_endpoint

    devices = [
        {
            "device_id": "12345",
            "device_type": "Charge 6",
            "battery_percent": 85,
            "last_sync": datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        }
    ]

    result = writer.write_device_info(devices)

    assert result is True
    assert len(responses.calls) == 1

    # Verify the request body contains exact battery level
    request_body = responses.calls[0].request.body
    assert b"fitbit_device_battery_percent" in request_body
    assert b"85" in request_body  # Exact percentage
    assert b'device_id="12345"' in request_body
    assert b'device_type="Charge 6"' in request_body


@responses.activate
def test_write_device_info_battery_percent_is_written_verbatim(writer):
    """Test an already-normalized battery percentage is written as-is."""
    test_endpoint = "https://victoria-metrics.example.com/api/v1/import/prometheus"
    responses.add(responses.POST, test_endpoint, status=204)
    writer.endpoint = test_endpoint

    devices = [
        {
            "device_id": "67890",
            "device_type": "Inspire 3",
            "battery_percent": 50.0,
            "last_sync": datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        }
    ]

    result = writer.write_device_info(devices)

    assert result is True
    assert len(responses.calls) == 1

    # Verify the request body contains parsed battery level
    request_body = responses.calls[0].request.body
    assert b"fitbit_device_battery_percent" in request_body
    assert b"50.0" in request_body
    assert b'device_id="67890"' in request_body
    assert b"fitbit_device_last_sync_timestamp" in request_body
