"""Tests for Google OAuth2 authentication."""

import base64
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from src.config import Config
from src.google_auth import GoogleAuth, GoogleTokenManager, generate_pkce_pair


@pytest.fixture
def token_file(tmp_path):
    """Path to a token file inside a temporary data directory."""
    return tmp_path / "data" / "google_tokens.json"


@pytest.fixture
def auth(token_file, monkeypatch):
    """GoogleAuth wired to a temporary token file and test credentials."""
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(Config, "GOOGLE_REDIRECT_URI", "http://localhost:8080/callback")
    monkeypatch.setattr(Config, "GOOGLE_TOKEN_FILE", token_file)
    return GoogleAuth()


# --- PKCE ---


def test_generate_pkce_pair_challenge_matches_verifier():
    verifier, challenge = generate_pkce_pair()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge


def test_generate_pkce_pair_length_within_rfc7636_bounds():
    verifier, _ = generate_pkce_pair()

    assert 43 <= len(verifier) <= 128


def test_generate_pkce_pair_is_random():
    assert generate_pkce_pair()[0] != generate_pkce_pair()[0]


# --- Authorization URL ---


def test_authorization_url_requests_offline_access(auth):
    """Without access_type=offline and prompt=consent Google issues no refresh token."""
    params = parse_qs(urlparse(auth.get_authorization_url()).query)

    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]


def test_authorization_url_includes_pkce_challenge(auth):
    params = parse_qs(urlparse(auth.get_authorization_url()).query)

    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    # The matching verifier is retained for the subsequent code exchange.
    assert auth._code_verifier is not None


def test_authorization_url_includes_scopes_and_client(auth):
    params = parse_qs(urlparse(auth.get_authorization_url()).query)

    assert params["client_id"] == ["test-client-id"]
    assert params["response_type"] == ["code"]
    assert params["scope"][0].split(" ") == Config.GOOGLE_SCOPES


def test_authorization_url_rotates_verifier_per_call(auth):
    auth.get_authorization_url()
    first = auth._code_verifier
    auth.get_authorization_url()

    assert auth._code_verifier != first


# --- Code exchange ---


@responses.activate
def test_exchange_code_sends_verifier_and_stores_tokens(auth, token_file):
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        },
        status=200,
    )
    auth.get_authorization_url()
    verifier = auth._code_verifier

    auth.exchange_code_for_token("the-code")

    sent = parse_qs(responses.calls[0].request.body)
    assert sent["code"] == ["the-code"]
    assert sent["code_verifier"] == [verifier]
    assert sent["grant_type"] == ["authorization_code"]

    stored = json.loads(token_file.read_text())
    assert stored["access_token"] == "access-1"
    assert stored["refresh_token"] == "refresh-1"


@responses.activate
def test_exchange_code_requires_a_pending_verifier(auth):
    with pytest.raises(RuntimeError, match="No PKCE verifier"):
        auth.exchange_code_for_token("the-code")


@responses.activate
def test_exchange_code_raises_on_error_response(auth):
    responses.add(
        responses.POST, Config.GOOGLE_TOKEN_URL, json={"error": "invalid_grant"}, status=400
    )
    auth.get_authorization_url()

    with pytest.raises(RuntimeError, match="Token exchange failed"):
        auth.exchange_code_for_token("bad-code")


def test_token_file_is_not_world_readable(auth, token_file):
    auth.token_manager.update_tokens({"access_token": "a", "refresh_token": "r"})

    assert token_file.stat().st_mode & 0o077 == 0


# --- Refresh ---


@responses.activate
def test_refresh_preserves_stored_refresh_token(auth, token_file):
    """Google omits refresh_token on refresh; the stored one must survive.

    Overwriting it would wipe the credential on the first refresh and force a
    full re-authorization roughly an hour after setup appeared to succeed.
    """
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
    )

    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "access-2", "expires_in": 3600},  # no refresh_token
        status=200,
    )

    auth.refresh_access_token()

    assert auth.token_manager.access_token == "access-2"
    assert auth.token_manager.refresh_token == "refresh-1"
    assert json.loads(token_file.read_text())["refresh_token"] == "refresh-1"


@responses.activate
def test_refresh_accepts_a_rotated_refresh_token(auth):
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
    )
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600},
        status=200,
    )

    auth.refresh_access_token()

    assert auth.token_manager.refresh_token == "refresh-2"


@responses.activate
def test_refresh_rereads_token_file_before_using_it(auth, token_file):
    """Another process may have refreshed since this instance loaded."""
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
    )
    token_file.write_text(
        json.dumps(
            {
                "access_token": "access-1",
                "refresh_token": "refresh-from-other-process",
                "expires_at": datetime.now().isoformat(),
            }
        )
    )
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "access-2", "expires_in": 3600},
        status=200,
    )

    auth.refresh_access_token()

    sent = parse_qs(responses.calls[0].request.body)
    assert sent["refresh_token"] == ["refresh-from-other-process"]


def test_refresh_without_token_raises(auth):
    with pytest.raises(RuntimeError, match="No refresh token"):
        auth.refresh_access_token()


# --- Expiry / valid token ---


def test_expiry_uses_a_safety_buffer(auth):
    auth.token_manager.update_tokens(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    )

    # 3600s minus the 300s buffer, so expiry lands ~55 minutes out.
    remaining = auth.token_manager.expires_at - datetime.now()
    assert timedelta(minutes=54) < remaining < timedelta(minutes=56)


def test_defaults_to_one_hour_when_expires_in_absent(auth):
    """Google's default lifetime is 1h, unlike Fitbit's 8h."""
    auth.token_manager.update_tokens({"access_token": "a", "refresh_token": "r"})

    remaining = auth.token_manager.expires_at - datetime.now()
    assert remaining < timedelta(hours=1)


def test_is_expired_when_no_expiry_recorded(token_file):
    assert GoogleTokenManager(token_file).is_expired() is True


@responses.activate
def test_get_valid_token_refreshes_when_expired(auth):
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 0}
    )
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "access-2", "expires_in": 3600},
        status=200,
    )

    assert auth.get_valid_token() == "access-2"


@responses.activate
def test_get_valid_token_skips_refresh_when_fresh(auth):
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
    )

    assert auth.get_valid_token() == "access-1"
    assert len(responses.calls) == 0


def test_get_valid_token_without_authorization_raises(auth):
    with pytest.raises(RuntimeError, match="No tokens available"):
        auth.get_valid_token()


# --- Persistence ---


def test_tokens_round_trip_through_disk(auth, token_file):
    auth.token_manager.update_tokens(
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
    )

    reloaded = GoogleTokenManager(token_file)

    assert reloaded.access_token == "access-1"
    assert reloaded.refresh_token == "refresh-1"
    assert reloaded.has_tokens() is True


def test_missing_token_file_is_not_an_error(token_file):
    manager = GoogleTokenManager(token_file)

    assert manager.has_tokens() is False


def test_corrupt_token_file_is_tolerated(token_file):
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{not json")

    assert GoogleTokenManager(token_file).has_tokens() is False


def test_is_authorized_reflects_stored_tokens(auth):
    assert auth.is_authorized() is False

    auth.token_manager.update_tokens({"access_token": "a", "refresh_token": "r"})

    assert auth.is_authorized() is True


# --- Two-step (headless) authorization ---


@responses.activate
def test_verifier_persists_across_instances(auth, token_file, monkeypatch, tmp_path):
    """Consent URL and code redemption may happen in separate processes.

    On headless/WSL setups the URL is opened by hand, so the exchange runs in a
    later invocation. PKCE requires the same verifier for both halves.
    """
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)

    first = GoogleAuth()
    first.get_authorization_url()
    expected_verifier = first._code_verifier

    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        status=200,
    )

    # A fresh instance, as a second invocation would have.
    second = GoogleAuth()
    assert second._code_verifier is None
    second.exchange_code_for_token("the-code")

    sent = parse_qs(responses.calls[0].request.body)
    assert sent["code_verifier"] == [expected_verifier]


@responses.activate
def test_verifier_file_removed_after_exchange(auth, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    instance = GoogleAuth()
    instance.get_authorization_url()
    assert instance._verifier_file.exists()

    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        status=200,
    )
    instance.exchange_code_for_token("the-code")

    assert not instance._verifier_file.exists()


def test_verifier_file_is_not_world_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    instance = GoogleAuth()
    instance.get_authorization_url()

    assert instance._verifier_file.stat().st_mode & 0o077 == 0


def test_exchange_without_any_verifier_still_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "GOOGLE_TOKEN_FILE", tmp_path / "google_tokens.json")
    instance = GoogleAuth()

    with pytest.raises(RuntimeError, match="No PKCE verifier"):
        instance.exchange_code_for_token("the-code")


# --- Granted scopes ---


ALL_SCOPES = " ".join(Config.GOOGLE_SCOPES)


def test_granted_scopes_are_persisted(auth, token_file):
    auth.token_manager.update_tokens(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": ALL_SCOPES}
    )

    reloaded = GoogleTokenManager(token_file)

    assert reloaded.scopes == sorted(Config.GOOGLE_SCOPES)
    assert reloaded.missing_scopes(Config.GOOGLE_SCOPES) == []


def test_missing_scopes_lists_unticked_permissions(auth):
    """Users can untick scopes on consent; the token is then a partial grant."""
    activity, *rest = Config.GOOGLE_SCOPES
    auth.token_manager.update_tokens(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": activity}
    )

    assert auth.token_manager.missing_scopes(Config.GOOGLE_SCOPES) == rest


def test_missing_scopes_unknown_without_scope_field(token_file):
    """Token files written before scopes were recorded report 'unknown', not 'none'."""
    manager = GoogleTokenManager(token_file)
    manager.update_tokens({"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    assert manager.missing_scopes(Config.GOOGLE_SCOPES) is None


def test_scopes_survive_a_response_without_scope(auth):
    auth.token_manager.update_tokens(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": ALL_SCOPES}
    )
    auth.token_manager.update_tokens({"access_token": "b", "expires_in": 3600})

    assert auth.token_manager.scopes == sorted(Config.GOOGLE_SCOPES)


@responses.activate
def test_partial_grant_logs_warning_on_exchange(auth, caplog):
    auth.get_authorization_url()
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
            "scope": Config.GOOGLE_SCOPES[0],
        },
        status=200,
    )

    with caplog.at_level("WARNING"):
        auth.exchange_code_for_token("code")

    assert "missing scopes" in caplog.text
    assert "sleep.readonly" in caplog.text


@responses.activate
def test_full_grant_logs_no_scope_warning(auth, caplog):
    auth.get_authorization_url()
    responses.add(
        responses.POST,
        Config.GOOGLE_TOKEN_URL,
        json={"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": ALL_SCOPES},
        status=200,
    )

    with caplog.at_level("WARNING"):
        auth.exchange_code_for_token("code")

    assert "missing scopes" not in caplog.text
