"""Shared test fixtures and configuration."""


import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def temp_token_file(temp_data_dir):
    """Create a temporary token file path."""
    return temp_data_dir / "fitbit_tokens.json"


@pytest.fixture
def temp_state_file(temp_data_dir):
    """Create a temporary state file path."""
    return temp_data_dir / "sync_state.json"


@pytest.fixture
def sample_fitbit_activity():
    """Sample Fitbit activity API response."""
    return {
        "activities": [],
        "goals": {
            "activeMinutes": 30,
            "caloriesOut": 2500,
            "distance": 8.05,
            "floors": 10,
            "steps": 10000,
        },
        "summary": {
            "activeScore": -1,
            "activityCalories": 1234,
            "caloriesBMR": 1500,
            "caloriesOut": 2734,
            "distances": [{"activity": "total", "distance": 6.52}],
            "elevation": 30.48,
            "fairlyActiveMinutes": 20,
            "floors": 12,
            "lightlyActiveMinutes": 180,
            "marginalCalories": 500,
            "sedentaryMinutes": 720,
            "steps": 8543,
            "veryActiveMinutes": 30,
        },
    }


@pytest.fixture
def sample_fitbit_heart_rate():
    """Sample Fitbit heart rate API response."""
    return {
        "activities-heart": [
            {
                "dateTime": "2025-12-24",
                "value": {
                    "customHeartRateZones": [],
                    "heartRateZones": [
                        {
                            "caloriesOut": 100.0,
                            "max": 95,
                            "min": 30,
                            "minutes": 720,
                            "name": "Out of Range",
                        },
                        {
                            "caloriesOut": 200.0,
                            "max": 133,
                            "min": 95,
                            "minutes": 180,
                            "name": "Fat Burn",
                        },
                        {
                            "caloriesOut": 300.0,
                            "max": 162,
                            "min": 133,
                            "minutes": 20,
                            "name": "Cardio",
                        },
                        {
                            "caloriesOut": 100.0,
                            "max": 220,
                            "min": 162,
                            "minutes": 5,
                            "name": "Peak",
                        },
                    ],
                    "restingHeartRate": 62,
                },
            }
        ]
    }


@pytest.fixture
def sample_tokens():
    """Sample OAuth tokens."""
    return {
        "access_token": "test_access_token_123",
        "refresh_token": "test_refresh_token_456",
        "expires_in": 28800,
        "scope": "activity heartrate",
        "token_type": "Bearer",
        "user_id": "ABC123",
    }


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
    monkeypatch.setenv("FITBIT_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("FITBIT_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("VICTORIA_USER", "test_user")
    monkeypatch.setenv("VICTORIA_PASSWORD", "test_password")
    monkeypatch.setenv("VICTORIA_ENDPOINT", "http://localhost:8428/api/v1/import/prometheus")
