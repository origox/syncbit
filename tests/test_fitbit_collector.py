"""Tests for Fitbit data collector."""

from datetime import datetime
from unittest.mock import Mock

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
            "distances": [{"activity": "total", "distance": 8.5}],
        },
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
                            "caloriesOut": 1500,
                        },
                        {
                            "name": "Fat Burn",
                            "min": 91,
                            "max": 127,
                            "minutes": 30,
                            "caloriesOut": 200,
                        },
                    ],
                },
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
        status=200,
    )

    result = collector._make_request("/test/endpoint")

    assert result == {"result": "success"}
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test_access_token"


@responses.activate
def test_make_request_rate_limit(collector):
    """Test rate limit error handling."""
    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/test/endpoint",
        json={"errors": [{"errorType": "rate_limit"}]},
        status=429,
        headers={"Retry-After": "120"},
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
        status=429,
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
        status=401,
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
        status=200,
    )

    result = collector.get_activity_summary(test_date)

    assert result == sample_activity_response["summary"]
    assert result["steps"] == 10000
    assert result["caloriesOut"] == 2500


@responses.activate
def test_get_heart_rate(collector, sample_heart_rate_response):
    """Test fetching heart rate data."""
    test_date = datetime(2024, 1, 15)

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200,
    )

    result = collector.get_heart_rate(test_date)

    assert result["restingHeartRate"] == 62
    assert len(result["heartRateZones"]) == 2


@responses.activate
def test_get_heart_rate_no_data(collector):
    """Test heart rate when no data available."""
    test_date = datetime(2024, 1, 15)

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json={"activities-heart": []},
        status=200,
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
        status=200,
    )

    result = collector.get_steps(test_date)

    assert result == 10000


@responses.activate
def test_get_daily_data(
    collector, sample_activity_response, sample_heart_rate_response, monkeypatch
):
    """Test fetching complete daily data."""
    # Disable all optional metrics for this basic test
    monkeypatch.setattr(Config, "COLLECT_SLEEP", False)
    monkeypatch.setattr(Config, "COLLECT_SPO2", False)
    monkeypatch.setattr(Config, "COLLECT_BREATHING_RATE", False)
    monkeypatch.setattr(Config, "COLLECT_HRV", False)
    monkeypatch.setattr(Config, "COLLECT_CARDIO_FITNESS", False)
    monkeypatch.setattr(Config, "COLLECT_TEMPERATURE", False)

    test_date = datetime(2024, 1, 15)

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=sample_activity_response,
        status=200,
    )

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200,
    )

    result = collector.get_daily_data(test_date)

    assert result["date"] == "2024-01-15"
    assert result["steps"] == 10000
    assert result["calories"] == 2500
    assert result["distance"] == 8.5
    # Floors and elevation no longer collected (Charge 6 has no altimeter)
    assert "floors" not in result
    assert "elevation" not in result
    assert result["active_minutes"]["sedentary"] == 600
    assert result["active_minutes"]["very_active"] == 60
    assert result["heart_rate"]["resting"] == 62
    assert len(result["heart_rate"]["zones"]) == 2


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
    activity_response = {"summary": {"steps": 5000, "caloriesOut": 1500}}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/date/2024-01-15.json",
        json=activity_response,
        status=200,
    )

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/activities/heart/date/2024-01-15/1d.json",
        json=sample_heart_rate_response,
        status=200,
    )

    result = collector.get_daily_data(test_date)

    # Distance should default to 0.0
    assert result["distance"] == 0.0
    assert result["steps"] == 5000


@responses.activate
def test_get_sleep_data(collector):
    """Test fetching sleep data."""
    test_date = datetime(2024, 1, 15)

    sleep_response = {
        "sleep": [
            {
                "dateOfSleep": "2024-01-15",
                "duration": 28800000,
                "efficiency": 92,
                "stages": {
                    "deep": 120,
                    "light": 240,
                    "rem": 90,
                    "wake": 30,
                },
            }
        ],
        "summary": {
            "totalMinutesAsleep": 450,
            "totalTimeInBed": 480,
            "stages": {"deep": 120, "light": 240, "rem": 90, "wake": 30},
        },
    }

    responses.add(
        responses.GET,
        "https://api.fitbit.com/1.2/user/-/sleep/date/2024-01-15.json",
        json=sleep_response,
        status=200,
    )

    result = collector.get_sleep_data(test_date)

    assert len(result["sleep"]) == 1
    assert result["summary"]["totalMinutesAsleep"] == 450
    assert result["summary"]["stages"]["deep"] == 120


@responses.activate
def test_get_spo2_data(collector):
    """Test fetching SpO2 data."""
    test_date = datetime(2024, 1, 15)

    spo2_response = {"dateTime": "2024-01-15", "value": {"avg": 96.5, "min": 94, "max": 98}}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/spo2/date/2024-01-15.json",
        json=spo2_response,
        status=200,
    )

    result = collector.get_spo2_data(test_date)

    assert result["value"]["avg"] == 96.5
    assert result["value"]["min"] == 94
    assert result["value"]["max"] == 98


@responses.activate
def test_get_spo2_data_not_found(collector):
    """Test SpO2 data when not available returns empty dict."""
    test_date = datetime(2024, 1, 15)

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/spo2/date/2024-01-15.json",
        json={"errors": [{"errorType": "not_found"}]},
        status=404,
    )

    result = collector.get_spo2_data(test_date)

    assert result == {}


@responses.activate
def test_get_breathing_rate(collector):
    """Test fetching breathing rate data."""
    test_date = datetime(2024, 1, 15)

    br_response = {"br": [{"dateTime": "2024-01-15", "value": {"breathingRate": 15.5}}]}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/br/date/2024-01-15.json",
        json=br_response,
        status=200,
    )

    result = collector.get_breathing_rate(test_date)

    assert len(result) == 1
    assert result[0]["value"]["breathingRate"] == 15.5


@responses.activate
def test_get_hrv_data(collector):
    """Test fetching HRV data."""
    test_date = datetime(2024, 1, 15)

    hrv_response = {"hrv": [{"dateTime": "2024-01-15", "value": {"rmssd": 45.2}}]}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/hrv/date/2024-01-15.json",
        json=hrv_response,
        status=200,
    )

    result = collector.get_hrv_data(test_date)

    assert len(result) == 1
    assert result[0]["value"]["rmssd"] == 45.2


@responses.activate
def test_get_cardio_fitness_score(collector):
    """Test fetching cardio fitness score."""
    test_date = datetime(2024, 1, 15)

    cf_response = {"cardioScore": [{"dateTime": "2024-01-15", "vo2Max": "45-49"}]}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/cardioscore/date/2024-01-15.json",
        json=cf_response,
        status=200,
    )

    result = collector.get_cardio_fitness_score(test_date)

    assert len(result) == 1
    assert result[0]["vo2Max"] == "45-49"


@responses.activate
def test_get_temperature_data(collector):
    """Test fetching temperature data."""
    test_date = datetime(2024, 1, 15)

    temp_response = {"tempSkin": [{"dateTime": "2024-01-15", "value": {"nightlyRelative": 0.2}}]}

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/temp/skin/date/2024-01-15.json",
        json=temp_response,
        status=200,
    )

    result = collector.get_temperature_data(test_date)

    assert len(result) == 1
    assert result[0]["value"]["nightlyRelative"] == 0.2


@responses.activate
def test_get_device_info(collector):
    """Test fetching device information."""
    device_response = [
        {
            "id": "12345",
            "deviceVersion": "Charge 6",
            "battery": "High",
            "lastSyncTime": "2024-01-15T10:30:00.000Z",
            "type": "TRACKER",
        }
    ]

    responses.add(
        responses.GET,
        f"{Config.FITBIT_API_BASE_URL}/devices.json",
        json=device_response,
        status=200,
    )

    result = collector.get_device_info()

    assert len(result) == 1
    assert result[0]["id"] == "12345"
    assert result[0]["deviceVersion"] == "Charge 6"
    assert result[0]["battery"] == "High"
