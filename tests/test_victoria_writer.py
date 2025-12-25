"""Tests for Victoria Metrics writer."""

import json
import pytest
import responses
from datetime import datetime

from src.victoria_writer import VictoriaMetricsWriter
from src.config import Config


@pytest.fixture
def writer(mock_env_vars):
    """Create VictoriaMetricsWriter instance for testing."""
    return VictoriaMetricsWriter()


def test_format_metric_basic(writer):
    """Test basic Prometheus format generation."""
    metric = writer._format_metric(
        name="fitbit_steps_total",
        value=10000,
        timestamp=1735084800000,
        labels={"user": "testuser", "device": "charge6"}
    )
    
    assert metric == 'fitbit_steps_total{user="testuser",device="charge6"} 10000 1735084800000'


def test_format_metric_no_labels(writer):
    """Test metric format without labels."""
    metric = writer._format_metric(
        name="fitbit_calories_total",
        value=2500,
        timestamp=1735084800000
    )
    
    assert metric == "fitbit_calories_total 2500 1735084800000"


def test_format_metric_float_value(writer):
    """Test metric format with float value."""
    metric = writer._format_metric(
        name="fitbit_distance_km",
        value=6.52,
        timestamp=1735084800000
    )
    
    assert metric == "fitbit_distance_km 6.52 1735084800000"


def test_format_metric_zero_value(writer):
    """Test metric format with zero value."""
    metric = writer._format_metric(
        name="fitbit_floors_total",
        value=0,
        timestamp=1735084800000
    )
    
    assert metric == "fitbit_floors_total 0 1735084800000"


def test_convert_daily_data_to_metrics(writer):
    """Test converting daily data to Prometheus format."""
    data = {
        'date': '2025-12-24',
        'timestamp': 1735084800,
        'steps': 8543,
        'distance': 6.52,
        'calories': 2734,
        'active_minutes': {
            'sedentary': 720,
            'lightly_active': 180,
            'fairly_active': 20,
            'very_active': 30,
        },
        'floors': 12,
        'elevation': 30.48,
        'heart_rate': {
            'resting': 62,
            'zones': [
                {
                    'name': 'Out of Range',
                    'minutes': 720,
                    'caloriesOut': 100.0
                },
                {
                    'name': 'Fat Burn',
                    'minutes': 180,
                    'caloriesOut': 200.0
                }
            ]
        }
    }
    
    metrics = writer._convert_daily_data_to_metrics(data)
    
    # Should have multiple metrics
    assert len(metrics) > 0
    
    # Check some key metrics exist
    metric_lines = '\n'.join(metrics)
    assert 'fitbit_steps_total' in metric_lines
    assert '8543' in metric_lines
    assert 'fitbit_distance_km' in metric_lines
    assert '6.52' in metric_lines
    assert 'fitbit_resting_heart_rate_bpm' in metric_lines
    assert '62' in metric_lines


def test_timestamp_conversion(writer):
    """Test timestamp is converted to milliseconds."""
    data = {
        'date': '2025-12-24',
        'timestamp': 1735084800,  # seconds
        'steps': 100,
        'distance': 1.0,
        'calories': 500,
        'active_minutes': {
            'sedentary': 0,
            'lightly_active': 0,
            'fairly_active': 0,
            'very_active': 0,
        },
        'floors': 0,
        'elevation': 0,
        'heart_rate': {
            'resting': 60,
            'zones': []
        }
    }
    
    metrics = writer._convert_daily_data_to_metrics(data)
    
    # Timestamp should be in milliseconds
    for metric in metrics:
        assert '1735084800000' in metric  # milliseconds


@responses.activate
def test_write_daily_data_success(writer, mock_env_vars):
    """Test successful write to Victoria Metrics."""
    # Mock the Victoria Metrics endpoint
    responses.add(
        responses.POST,
        "http://localhost:8428/api/v1/import/prometheus",
        status=204
    )
    
    data = {
        'date': '2025-12-24',
        'timestamp': 1735084800,
        'steps': 8543,
        'distance': 6.52,
        'calories': 2734,
        'active_minutes': {
            'sedentary': 720,
            'lightly_active': 180,
            'fairly_active': 20,
            'very_active': 30,
        },
        'floors': 12,
        'elevation': 30.48,
        'heart_rate': {
            'resting': 62,
            'zones': []
        }
    }
    
    result = writer.write_daily_data(data)
    
    assert result is True
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == "http://localhost:8428/api/v1/import/prometheus"


@responses.activate
def test_write_daily_data_failure(writer, mock_env_vars):
    """Test handling of write failure."""
    # Mock failed response
    responses.add(
        responses.POST,
        "http://localhost:8428/api/v1/import/prometheus",
        status=500
    )
    
    data = {
        'date': '2025-12-24',
        'timestamp': 1735084800,
        'steps': 100,
        'distance': 1.0,
        'calories': 500,
        'active_minutes': {
            'sedentary': 0,
            'lightly_active': 0,
            'fairly_active': 0,
            'very_active': 0,
        },
        'floors': 0,
        'elevation': 0,
        'heart_rate': {
            'resting': 60,
            'zones': []
        }
    }
    
    result = writer.write_daily_data(data)
    
    assert result is False


@responses.activate
def test_test_connection_success(writer):
    """Test successful connection test."""
    responses.add(
        responses.POST,
        "http://localhost:8428/api/v1/import/prometheus",
        status=204
    )
    
    result = writer.test_connection()
    
    assert result is True


@responses.activate
def test_test_connection_failure(writer):
    """Test failed connection test."""
    responses.add(
        responses.POST,
        "http://localhost:8428/api/v1/import/prometheus",
        status=500
    )
    
    result = writer.test_connection()
    
    assert result is False
