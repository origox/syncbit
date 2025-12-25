"""Tests for configuration management."""

import pytest

from src.config import Config


def test_config_loads_from_env(monkeypatch, tmp_path):
    """Test configuration loads from environment variables."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FITBIT_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("FITBIT_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("VICTORIA_USER", "test_user")
    monkeypatch.setenv("VICTORIA_PASSWORD", "test_password")

    # Import fresh to pick up env vars
    import importlib

    from src import config as config_module

    importlib.reload(config_module)

    assert config_module.Config.FITBIT_CLIENT_ID == "test_client_id"
    assert config_module.Config.FITBIT_CLIENT_SECRET == "test_client_secret"


def test_config_validation_missing_fitbit_client_id(monkeypatch, tmp_path):
    """Test validation fails when FITBIT_CLIENT_ID is missing."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FITBIT_CLIENT_ID", raising=False)

    # Force reload
    Config.FITBIT_CLIENT_ID = ""

    with pytest.raises(ValueError) as exc_info:
        Config.validate()

    assert "FITBIT_CLIENT_ID is required" in str(exc_info.value)


def test_config_validation_missing_fitbit_client_secret(monkeypatch, tmp_path):
    """Test validation fails when FITBIT_CLIENT_SECRET is missing."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FITBIT_CLIENT_ID", "test_id")
    monkeypatch.delenv("FITBIT_CLIENT_SECRET", raising=False)

    Config.FITBIT_CLIENT_ID = "test_id"
    Config.FITBIT_CLIENT_SECRET = ""

    with pytest.raises(ValueError) as exc_info:
        Config.validate()

    assert "FITBIT_CLIENT_SECRET is required" in str(exc_info.value)


def test_config_validation_missing_victoria_password(monkeypatch, tmp_path):
    """Test validation fails when VICTORIA_PASSWORD is missing."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FITBIT_CLIENT_ID", "test_id")
    monkeypatch.setenv("FITBIT_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("VICTORIA_USER", "test_user")
    monkeypatch.delenv("VICTORIA_PASSWORD", raising=False)

    Config.FITBIT_CLIENT_ID = "test_id"
    Config.FITBIT_CLIENT_SECRET = "test_secret"
    Config.VICTORIA_USER = "test_user"
    Config.VICTORIA_PASSWORD = ""

    with pytest.raises(ValueError) as exc_info:
        Config.validate()

    assert "VICTORIA_PASSWORD is required" in str(exc_info.value)


def test_config_validation_success(mock_env_vars, tmp_path, monkeypatch):
    """Test validation passes with all required variables."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    # Reload config values
    Config.FITBIT_CLIENT_ID = "test_client_id"
    Config.FITBIT_CLIENT_SECRET = "test_client_secret"
    Config.VICTORIA_USER = "test_user"
    Config.VICTORIA_PASSWORD = "test_password"
    Config.DATA_DIR = tmp_path

    # Should not raise
    Config.validate()

    # Should create data directory
    assert tmp_path.exists()


def test_config_default_values():
    """Test configuration has sensible defaults."""
    assert Config.FITBIT_REDIRECT_URI == "http://localhost:8080/callback"
    assert Config.FITBIT_API_BASE_URL == "https://api.fitbit.com/1/user/-"
    assert Config.FITBIT_AUTH_URL == "https://www.fitbit.com/oauth2/authorize"
    assert Config.SYNC_INTERVAL_MINUTES == 15
    assert Config.FITBIT_USER_ID == "default"
    assert Config.LOG_LEVEL == "INFO"


def test_config_file_paths(tmp_path, monkeypatch):
    """Test token and state file paths are constructed correctly."""
    data_dir = tmp_path / "custom_data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    # Import fresh
    import importlib

    from src import config as config_module

    importlib.reload(config_module)

    assert data_dir / "fitbit_tokens.json" == config_module.Config.TOKEN_FILE
    assert data_dir / "sync_state.json" == config_module.Config.STATE_FILE
