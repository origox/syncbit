"""Google OAuth2 authentication and token management for the Google Health API.

Differs from the Fitbit flow in four ways that matter operationally:

1. PKCE is used on the authorization code exchange.
2. `access_type=offline` and `prompt=consent` are required, or Google issues no
   refresh token at all.
3. Google omits `refresh_token` from *refresh* responses, so the stored refresh
   token must be preserved rather than overwritten.
4. Access tokens live ~1 hour rather than Fitbit's 8, so refreshes are frequent
   and point 3 is load-bearing.
"""

import base64
import hashlib
import json
import logging
import secrets
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import Config

logger = logging.getLogger(__name__)

# Refresh this long before the token actually expires, so a request that is
# in-flight when the clock rolls over does not fail with a 401.
_EXPIRY_BUFFER_SECONDS = 300

# Google access tokens are typically valid for one hour.
_DEFAULT_EXPIRES_IN = 3600


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code verifier and its S256 challenge.

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    # RFC 7636 allows 43-128 characters; token_urlsafe(64) lands inside that.
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class GoogleTokenManager:
    """Persists Google OAuth2 tokens and tracks their expiry."""

    def __init__(self, token_file: Path):
        """Initialize token manager.

        Args:
            token_file: Path to store tokens
        """
        self.token_file = token_file
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None
        # Scopes the user actually granted. Google's consent screen lets users
        # untick individual scopes, so this can be a subset of what was
        # requested. None means unknown (e.g. a token file from before scopes
        # were recorded); it is filled in by the next token response.
        self.scopes: list[str] | None = None

        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file if they exist."""
        if self.token_file.exists():
            try:
                with open(self.token_file) as f:
                    data = json.load(f)

                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")

                expires_at_str = data.get("expires_at")
                if expires_at_str:
                    self.expires_at = datetime.fromisoformat(expires_at_str)

                self.scopes = data.get("scopes")

                logger.info(f"Loaded tokens from {self.token_file}")
            except Exception as e:
                logger.error(f"Error loading tokens: {e}")

    def _save_tokens(self) -> None:
        """Save tokens to file with restrictive permissions."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "scopes": self.scopes,
        }

        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.token_file, "w") as f:
                json.dump(data, f, indent=2)

            self.token_file.chmod(0o600)
            logger.info(f"Saved tokens to {self.token_file}")
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
            raise

    def update_tokens(self, token_response: dict) -> None:
        """Update tokens from an OAuth response.

        Google returns `refresh_token` only on the initial authorization code
        exchange, never on subsequent refreshes. Overwriting it unconditionally
        would wipe the stored token on the very first refresh and force
        re-authorization, so an absent value leaves the existing one in place.

        Args:
            token_response: Response from the token endpoint
        """
        self.access_token = token_response["access_token"]

        refresh_token = token_response.get("refresh_token")
        if refresh_token:
            self.refresh_token = refresh_token
        elif not self.refresh_token:
            logger.warning("Token response had no refresh_token and none was stored")

        # `scope` is a space-separated list of what was granted. Keep the stored
        # value when it is absent rather than forgetting what we knew.
        scope = token_response.get("scope")
        if scope:
            self.scopes = sorted(scope.split())

        expires_in = token_response.get("expires_in", _DEFAULT_EXPIRES_IN)
        self.expires_at = datetime.now() + timedelta(seconds=expires_in - _EXPIRY_BUFFER_SECONDS)

        self._save_tokens()
        logger.info(f"Updated tokens, expires at {self.expires_at}")

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.expires_at:
            return True
        return datetime.now() >= self.expires_at

    def has_tokens(self) -> bool:
        """Check if both an access and refresh token are present."""
        return bool(self.access_token and self.refresh_token)

    def missing_scopes(self, required: list[str]) -> list[str] | None:
        """Return the required scopes that were not granted.

        Args:
            required: Scopes the application needs

        Returns:
            Missing scopes (empty if all granted), or None if the granted
            scopes are not known
        """
        if self.scopes is None:
            return None
        granted = set(self.scopes)
        return [s for s in required if s not in granted]


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for the OAuth callback."""

    authorization_code: str | None = None
    error: str | None = None

    def do_GET(self):
        """Handle the redirect back from Google."""
        params = parse_qs(urlparse(self.path).query)

        if "code" in params:
            CallbackHandler.authorization_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html>
                <body>
                    <h1>Authorization successful!</h1>
                    <p>You can close this window and return to the application.</p>
                </body>
                </html>
            """)
        else:
            CallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authorization failed</h1></body></html>")

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class GoogleAuth:
    """Google OAuth2 authentication handler for the Google Health API."""

    def __init__(self):
        """Initialize Google authentication."""
        self.client_id = Config.GOOGLE_CLIENT_ID
        self.client_secret = Config.GOOGLE_CLIENT_SECRET
        self.redirect_uri = Config.GOOGLE_REDIRECT_URI
        self.token_manager = GoogleTokenManager(Config.GOOGLE_TOKEN_FILE)
        self._code_verifier: str | None = None
        self._verifier_file = Config.DATA_DIR / "pkce_verifier.txt"

    def _save_verifier(self, verifier: str) -> None:
        """Persist the PKCE verifier so a later process can complete the exchange.

        On headless and WSL setups consent is done by hand in a browser, which
        means the URL is generated by one invocation and the code is redeemed by
        another. PKCE requires the same verifier for both halves.
        """
        try:
            self._verifier_file.parent.mkdir(parents=True, exist_ok=True)
            self._verifier_file.write_text(verifier)
            self._verifier_file.chmod(0o600)
        except OSError as e:
            logger.debug(f"Could not persist the PKCE verifier: {e}")

    def _load_verifier(self) -> str | None:
        """Load a PKCE verifier saved by an earlier invocation."""
        try:
            if self._verifier_file.exists():
                return self._verifier_file.read_text().strip() or None
        except OSError as e:
            logger.debug(f"Could not read the PKCE verifier: {e}")
        return None

    def _clear_verifier(self) -> None:
        """Remove a stored verifier once it has been redeemed."""
        try:
            self._verifier_file.unlink(missing_ok=True)
        except OSError as e:
            logger.debug(f"Could not remove the PKCE verifier: {e}")

    def get_authorization_url(self) -> str:
        """Generate the authorization URL for user consent.

        Generates and stores a fresh PKCE verifier as a side effect; the
        matching code exchange must happen on this same instance.

        Returns:
            Authorization URL
        """
        self._code_verifier, code_challenge = generate_pkce_pair()
        self._save_verifier(self._code_verifier)

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": " ".join(Config.GOOGLE_SCOPES),
            "redirect_uri": self.redirect_uri,
            # Without both of these Google returns no refresh token, which
            # would make unattended long-running sync impossible.
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{Config.GOOGLE_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_token(self, authorization_code: str) -> None:
        """Exchange an authorization code for access and refresh tokens.

        Args:
            authorization_code: Code received from the authorization redirect

        Raises:
            RuntimeError: If no PKCE verifier is pending or the exchange fails
        """
        verifier = self._code_verifier or self._load_verifier()
        if not verifier:
            raise RuntimeError("No PKCE verifier available; call get_authorization_url() first")

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }

        response = requests.post(
            Config.GOOGLE_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {response.text}")

        payload = response.json()
        if not payload.get("refresh_token"):
            logger.warning(
                "Google returned no refresh_token. The app may already have been "
                "authorized; revoke access and re-authorize to obtain one."
            )

        self.token_manager.update_tokens(payload)
        self._warn_on_missing_scopes()
        self._code_verifier = None
        self._clear_verifier()
        logger.info("Successfully exchanged authorization code for tokens")

    def refresh_access_token(self) -> None:
        """Refresh the access token using the stored refresh token."""
        # Reload from disk first: another process may hold a newer token.
        self.token_manager._load_tokens()

        if not self.token_manager.refresh_token:
            raise RuntimeError("No refresh token available. Please authorize first.")

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.token_manager.refresh_token,
        }

        response = requests.post(
            Config.GOOGLE_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {response.text}")

        self.token_manager.update_tokens(response.json())
        self._warn_on_missing_scopes()
        logger.info("Successfully refreshed access token")

    def _warn_on_missing_scopes(self) -> None:
        """Log a warning if the user did not grant every requested scope.

        A partial grant does not fail the token exchange; it only surfaces
        later as 403 MISSING_OAUTH_SCOPE on the affected data types, which is
        easy to mistake for an API or configuration problem.
        """
        missing = self.token_manager.missing_scopes(Config.GOOGLE_SCOPES)
        if missing:
            logger.warning(
                "Google token is missing scopes: %s. Data behind these scopes will "
                "return 403. Revoke access at https://myaccount.google.com/connections "
                "and re-authorize, ticking every permission on the consent screen.",
                ", ".join(missing),
            )

    def get_valid_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns:
            Valid access token
        """
        if not self.token_manager.has_tokens():
            raise RuntimeError("No tokens available. Please authorize first.")

        if self.token_manager.is_expired():
            logger.info("Access token expired, refreshing...")
            self.refresh_access_token()

        return self.token_manager.access_token

    def _present_authorization_url(self, auth_url: str, open_browser: bool) -> None:
        """Show the user the consent URL, and write it somewhere copyable.

        Headless and WSL environments often have no usable browser opener, and
        the URL is long enough that terminal wrapping corrupts it on copy. So it
        is always written to a file, and printed last so it is the final thing
        on screen.
        """
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception as e:  # no browser handler (headless, WSL, ...)
                logger.debug(f"Could not open a browser automatically: {e}")

        url_file = Config.DATA_DIR / "authorize_url.txt"
        try:
            url_file.parent.mkdir(parents=True, exist_ok=True)
            url_file.write_text(auth_url + "\n")
            print(f"\nAuthorization URL also written to: {url_file}")
        except OSError as e:
            logger.debug(f"Could not write the authorization URL to a file: {e}")

        print("\nOpen this URL to authorize (copy the whole line):\n")
        print(auth_url)
        print()

    def authorize(self, port: int = 8080, manual: bool = False, open_browser: bool = True) -> None:
        """Complete the OAuth2 authorization flow.

        Args:
            port: Port for the local callback server
            manual: Prompt for a pasted code instead of running a callback
                server. Required when the OAuth client's redirect URI is not a
                localhost address.
            open_browser: Attempt to launch a browser automatically
        """
        auth_url = self.get_authorization_url()
        self._present_authorization_url(auth_url, open_browser)

        if manual:
            print("After approving, copy the `code` parameter from the redirect URL.")
            code = input("Authorization code: ").strip()
            if not code:
                raise RuntimeError("No authorization code provided")
            self.exchange_code_for_token(code)
            logger.info("Authorization complete!")
            return

        # Bind to 0.0.0.0 so the callback works from outside a container.
        server = HTTPServer(("0.0.0.0", port), CallbackHandler)
        CallbackHandler.authorization_code = None
        CallbackHandler.error = None

        print(f"Waiting for the redirect on port {port}...")
        logger.info(f"Waiting for callback on port {port}...")
        while CallbackHandler.authorization_code is None:
            server.handle_request()
            if CallbackHandler.error:
                raise RuntimeError(f"Authorization failed: {CallbackHandler.error}")

        self.exchange_code_for_token(CallbackHandler.authorization_code)
        logger.info("Authorization complete!")

    def is_authorized(self) -> bool:
        """Check if valid authorization is stored.

        Returns:
            True if authorized
        """
        return self.token_manager.has_tokens()
