"""Tests for Fitbit OAuth2 authentication."""

import json
from unittest.mock import patch

from src.fitbit_auth import FitbitAuth, TokenManager


def test_token_manager_init(temp_token_file):
    """Test TokenManager initialization."""
    tm = TokenManager(temp_token_file)

    assert tm.token_file == temp_token_file
    assert tm.access_token is None
    assert tm.refresh_token is None


def test_token_manager_update_tokens(temp_token_file, sample_tokens):
    """Test updating tokens from OAuth response."""
    tm = TokenManager(temp_token_file)
    tm.update_tokens(sample_tokens)

    assert temp_token_file.exists()
    assert tm.access_token == "test_access_token_123"
    assert tm.refresh_token == "test_refresh_token_456"
    assert tm.expires_at is not None


def test_token_manager_load_tokens(temp_token_file, sample_tokens):
    """Test loading tokens from file."""
    # Save tokens first
    with open(temp_token_file, "w") as f:
        json.dump(sample_tokens, f)

    # Load them
    tm = TokenManager(temp_token_file)

    assert tm.access_token == "test_access_token_123"
    assert tm.refresh_token == "test_refresh_token_456"


def test_token_manager_missing_file(temp_token_file):
    """Test handling of missing token file."""
    # File doesn't exist
    tm = TokenManager(temp_token_file)

    assert tm.access_token is None
    assert tm.refresh_token is None


def test_token_manager_invalid_json(temp_token_file):
    """Test handling of corrupted token file."""
    # Write invalid JSON
    with open(temp_token_file, "w") as f:
        f.write("invalid json {")

    tm = TokenManager(temp_token_file)

    assert tm.access_token is None
    assert tm.refresh_token is None


def test_token_manager_get_access_token(temp_token_file, sample_tokens):
    """Test getting access token."""
    tm = TokenManager(temp_token_file)
    tm.update_tokens(sample_tokens)

    assert tm.access_token == "test_access_token_123"


def test_token_manager_get_refresh_token(temp_token_file, sample_tokens):
    """Test getting refresh token."""
    tm = TokenManager(temp_token_file)
    tm.update_tokens(sample_tokens)

    assert tm.refresh_token == "test_refresh_token_456"


def test_fitbit_auth_init():
    """Test FitbitAuth initialization uses Config."""
    auth = FitbitAuth()

    # Just verify it initializes without error and has expected attributes
    assert auth.token_manager is not None
    assert hasattr(auth, "client_id")
    assert hasattr(auth, "client_secret")


def test_fitbit_auth_is_authorized_true(temp_token_file, sample_tokens):
    """Test is_authorized returns True when tokens exist."""
    # Save tokens
    with open(temp_token_file, "w") as f:
        json.dump(sample_tokens, f)

    # Mock Config.TOKEN_FILE to point to our temp file
    from pathlib import Path

    with patch("src.fitbit_auth.Config") as mock_config:
        mock_config.TOKEN_FILE = Path(temp_token_file)
        auth = FitbitAuth()

        assert auth.is_authorized() is True


def test_fitbit_auth_get_valid_token_no_refresh_needed(temp_token_file, sample_tokens):
    """Test getting valid token when not expired."""
    # Save tokens manually
    with open(temp_token_file, "w") as f:
        json.dump(sample_tokens, f)

    # Mock Config.TOKEN_FILE to point to our temp file
    from pathlib import Path

    with patch("src.fitbit_auth.Config") as mock_config:
        mock_config.TOKEN_FILE = Path(temp_token_file)
        auth = FitbitAuth()

        # Mock is_expired to return False - token is still valid
        with patch.object(auth.token_manager, "is_expired", return_value=False):
            token = auth.get_valid_token()
            # Should return the access token
            assert token is not None
            assert isinstance(token, str)


def test_fitbit_auth_get_authorization_url():
    """Test getting authorization URL has correct structure."""
    auth = FitbitAuth()
    url = auth.get_authorization_url()

    # Verify URL structure without checking exact client_id value
    assert "https://www.fitbit.com/oauth2/authorize" in url
    assert "client_id=" in url
    assert "response_type=code" in url
    assert "scope=" in url
    assert "redirect_uri=" in url


def test_fitbit_auth_build_authorization_url_params():
    """Test authorization URL contains required params."""
    auth = FitbitAuth()
    url = auth.get_authorization_url()

    # Check required OAuth params
    assert "client_id" in url
    assert "redirect_uri" in url
    assert "response_type=code" in url
    assert "scope=" in url


def test_token_manager_is_expired_no_expiry(temp_token_file, sample_tokens):
    """Test is_expired when no expiry time saved."""
    # Remove expires_at if it exists
    tokens = sample_tokens.copy()
    tokens.pop("expires_at", None)

    with open(temp_token_file, "w") as f:
        json.dump(tokens, f)

    tm = TokenManager(temp_token_file)

    # Without expiry info, should be considered expired
    assert tm.is_expired() is True


def test_token_manager_token_persistence(temp_token_file, sample_tokens):
    """Test tokens persist across manager instances."""
    # Save with first instance
    tm1 = TokenManager(temp_token_file)
    tm1.update_tokens(sample_tokens)

    # Load with second instance
    tm2 = TokenManager(temp_token_file)

    assert tm2.access_token == tm1.access_token
    assert tm2.refresh_token == tm1.refresh_token
