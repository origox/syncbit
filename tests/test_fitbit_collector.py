"""Tests for Fitbit data collector."""

import json
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
import responses
from requests.exceptions import HTTPError

from src.config import Config
from src.fitbit_auth import FitbitAuth
from src.fitbit_collector import FitbitCollector, RateLimitError


@pytest.fixture
def mock_auth():
    """Mock FitbitAuth instance."""
    auth = Mock(spec=FitbitAuth)
    auth.get_valid_token.return_value = "test_access_token"
    return auth


@pytest.fixture
def collector(mock_auth):
    """Create FitbitCollector instance with mock auth."""
    return FitbitCollector(mock_auth)


@pytest.fixture
def sample_activity_response():
    """Sample activity API response."""
    return {
        "activities": [],
        "goals": {},
        "summary": {
            "steps": 10000,
            "caloriesOut": 2500,
            "sedentaryMinutes": 600,
            "lightlyActiveMinutes": 180,
            "fairlyActiveMinutes": 30,
            "veryActiveMinutes": 60,
            "floors": 15,
            "elevation": 45.72,
            "distances": [
                {
                    "activity": "total",
                    "distance": 8.5
                }
            ]
        }
    }


@pytest.fixture
def sample_heart_rate_response():
    """Sample heart rate API response."""
    return {
        "activities-heart": [
            {
                "dateTime": "2024-01-15",
                "value": {
                    "restingHeartRate": 62,
                    "heartRateZones": [
                        {
                            "name": "Out of Range",
                            "min": 30,
                            "max": 91,
                            "minutes": 1200,
                            "caloriesOut": 1500
                        },
                        {
                            "name": "Fat Burn",
                            "min": 91,
                            "max": 127,
                            "minutes": 30,
                            "caloriesOut": 200
                        }
                    ]
                }
            }
        ]
    }


def test_collector_init(mock_auth):
    """Test collector initialization."""
    collector = FitbitCollector(mock_auth)
    
    assert collector.auth is mock_auth
    assert collector.base_url == Config.FITBIT_API_BASE_URL


@responses.activate
def test_make_request_success(collector):
    """Test successful API request."""
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/test/endpoint",
        json={"result": "success"},
        status=200
    )
    
    result = collector._make_request("/test/endpoint")
    
    assert result == {"result": "success"}
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers['Authorization'] == 'Bearer test_access_token'


@responses.activate
def test_make_request_rate_limit(collector):
    """Test rate limit error handling."""
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/test/endpoint",
        json={"errors": [{"errorType": "rate_limit"}]},
        status=429,
        headers={'Retry-After': '120'}
    )
    
    with pytest.raises(RateLimitError) as exc_info:
        collector._make_request("/test/endpoint")
    
    assert exc_info.value.retry_after == 120


@responses.activate
def test_make_request_rate_limit_no_header(collector):
    """Test rate limit without Retry-After header uses default."""
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/test/endpoint",
        json={"errors": [{"errorType": "rate_limit"}]},
        status=429
    )
    
    with pytest.raises(RateLimitError) as exc_info:
        collector._make_request("/test/endpoint")
    
    # Should default to 60 seconds
    assert exc_info.value.retry_after == 60


@responses.activate
def test_make_request_http_error(collector):
    """Test HTTP error handling."""
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/test/endpoint",
        json={"errors": [{"errorType": "invalid_token"}]},
        status=401
    )
    
    with pytest.raises(HTTPError):
        collector._make_request("/test/endpoint")


@responses.activate
def test_get_activity_summary(collector, sample_activity_response):
    """Test fetching activity summary."""
    test_date = datetime(2024, 1, 15)
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=sample_activity_response,
        status=200
    )
    
    result = collector.get_activity_summary(test_date)
    
    assert result == sample_activity_response['summary']
    assert result['steps'] == 10000
    assert result['caloriesOut'] == 2500


@responses.activate
def test_get_heart_rate(collector, sample_heart_rate_response):
    """Test fetching heart rate data."""
    test_date = datetime(2024, 1, 15)
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200
    )
    
    result = collector.get_heart_rate(test_date)
    
    assert result['restingHeartRate'] == 62
    assert len(result['heartRateZones']) == 2


@responses.activate
def test_get_heart_rate_no_data(collector):
    """Test heart rate when no data available."""
    test_date = datetime(2024, 1, 15)
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json={"activities-heart": []},
        status=200
    )
    
    result = collector.get_heart_rate(test_date)
    
    assert result == {}


@responses.activate
def test_get_steps(collector, sample_activity_response):
    """Test fetching step count."""
    test_date = datetime(2024, 1, 15)
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=sample_activity_response,
        status=200
    )
    
    result = collector.get_steps(test_date)
    
    assert result == 10000


@responses.activate
def test_get_daily_data(collector, sample_activity_response, sample_heart_rate_response):
    """Test fetching complete daily data."""
    test_date = datetime(2024, 1, 15)
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=sample_activity_response,
        status=200
    )
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200
    )
    
    result = collector.get_daily_data(test_date)
    
    assert result['date'] == '2024-01-15'
    assert result['steps'] == 10000
    assert result['calories'] == 2500
    assert result['distance'] == 8.5
    assert result['floors'] == 15
    assert result['active_minutes']['sedentary'] == 600
    assert result['active_minutes']['very_active'] == 60
    assert result['heart_rate']['resting'] == 62
    assert len(result['heart_rate']['zones']) == 2


@responses.activate
def test_rate_limit_error_attributes():
    """Test RateLimitError has correct attributes."""
    error = RateLimitError("Rate limited", 120)
    
    assert str(error) == "Rate limited"
    assert error.retry_after == 120


@responses.activate
def test_get_daily_data_no_distance(collector, sample_heart_rate_response):
    """Test daily data when activity has no distances."""
    test_date = datetime(2024, 1, 15)
    
    # Activity response without distances
    activity_response = {
        "summary": {
            "steps": 5000,
            "caloriesOut": 1500
        }
    }
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=activity_response,
        status=200
    )
    
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200
    )
    
    result = collector.get_daily_data(test_date)
    
    # Distance should default to 0.0
    assert result['distance'] == 0.0
    assert result['steps'] == 5000
