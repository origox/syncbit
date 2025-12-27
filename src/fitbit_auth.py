"""Fitbit OAuth2 authentication and token management."""

import json
import logging
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import Config

logger = logging.getLogger(__name__)


class TokenManager:
    """Manages Fitbit OAuth2 tokens with automatic refresh."""

    def __init__(self, token_file: Path):
        """Initialize token manager.

        Args:
            token_file: Path to store tokens
        """
        self.token_file = token_file
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None
        self.user_id: str | None = None

        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file if they exist."""
        if self.token_file.exists():
            try:
                with open(self.token_file) as f:
                    data = json.load(f)

                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.user_id = data.get("user_id")

                expires_at_str = data.get("expires_at")
                if expires_at_str:
                    self.expires_at = datetime.fromisoformat(expires_at_str)

                logger.info(f"Loaded tokens from {self.token_file}")
            except Exception as e:
                logger.error(f"Error loading tokens: {e}")

    def _save_tokens(self) -> None:
        """Save tokens to file."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

        try:
            # Ensure parent directory exists
            self.token_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.token_file, "w") as f:
                json.dump(data, f, indent=2)

            # Set restrictive permissions
            self.token_file.chmod(0o600)
            logger.info(f"Saved tokens to {self.token_file}")
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
            raise

    def update_tokens(self, token_response: dict) -> None:
        """Update tokens from OAuth response.

        Args:
            token_response: Response from token endpoint
        """
        self.access_token = token_response["access_token"]
        self.refresh_token = token_response["refresh_token"]
        self.user_id = token_response["user_id"]

        # Tokens expire in 8 hours, set expiry slightly earlier for safety
        expires_in = token_response.get("expires_in", 28800)  # Default 8 hours
        self.expires_at = datetime.now() + timedelta(seconds=expires_in - 300)  # 5 min buffer

        self._save_tokens()
        logger.info(f"Updated tokens, expires at {self.expires_at}")

    def is_expired(self) -> bool:
        """Check if access token is expired."""
        if not self.expires_at:
            return True
        return datetime.now() >= self.expires_at

    def has_tokens(self) -> bool:
        """Check if we have valid tokens."""
        return bool(self.access_token and self.refresh_token)


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    authorization_code: str | None = None

    def do_GET(self):
        """Handle GET request from OAuth callback."""
        query = urlparse(self.path).query
        params = parse_qs(query)

        if "code" in params:
            CallbackHandler.authorization_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"""
                <html>
                <body>
                    <h1>Authorization successful!</h1>
                    <p>You can close this window and return to the application.</p>
                </body>
                </html>
            """
            )
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authorization failed</h1></body></html>")

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class FitbitAuth:
    """Fitbit OAuth2 authentication handler."""

    def __init__(self):
        """Initialize Fitbit authentication."""
        self.client_id = Config.FITBIT_CLIENT_ID
        self.client_secret = Config.FITBIT_CLIENT_SECRET
        self.redirect_uri = Config.FITBIT_REDIRECT_URI
        self.token_manager = TokenManager(Config.TOKEN_FILE)

    def get_authorization_url(self) -> str:
        """Generate authorization URL for user consent.

        Returns:
            Authorization URL
        """
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": " ".join(Config.FITBIT_SCOPES),
            "redirect_uri": self.redirect_uri,
        }

        url = f"{Config.FITBIT_AUTH_URL}?{urlencode(params)}"
        return url

    def exchange_code_for_token(self, authorization_code: str) -> None:
        """Exchange authorization code for access token.

        Args:
            authorization_code: Code received from authorization
        """
        token_url = Config.FITBIT_TOKEN_URL

        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self.redirect_uri,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = requests.post(
            token_url, data=data, headers=headers, auth=(self.client_id, self.client_secret)
        )

        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.text}")

        self.token_manager.update_tokens(response.json())
        logger.info("Successfully exchanged authorization code for tokens")

    def refresh_access_token(self) -> None:
        """Refresh access token using refresh token."""
        # Reload tokens from file to get the latest refresh token
        # This handles cases where tokens were refreshed by another process
        self.token_manager._load_tokens()

        if not self.token_manager.refresh_token:
            raise Exception("No refresh token available")

        token_url = Config.FITBIT_TOKEN_URL

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.token_manager.refresh_token,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = requests.post(
            token_url, data=data, headers=headers, auth=(self.client_id, self.client_secret)
        )

        if response.status_code != 200:
            raise Exception(f"Token refresh failed: {response.text}")

        self.token_manager.update_tokens(response.json())
        logger.info("Successfully refreshed access token")

    def get_valid_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns:
            Valid access token
        """
        if not self.token_manager.has_tokens():
            raise Exception("No tokens available. Please authorize first.")

        if self.token_manager.is_expired():
            logger.info("Access token expired, refreshing...")
            self.refresh_access_token()

        return self.token_manager.access_token

    def authorize(self, port: int = 8080) -> None:
        """Complete OAuth2 authorization flow with local callback server.

        Args:
            port: Port for local callback server
        """
        # Start local server for callback
        # Bind to 0.0.0.0 to allow access from outside container (Docker/K8s)
        server = HTTPServer(("0.0.0.0", port), CallbackHandler)

        # Open browser for authorization
        auth_url = self.get_authorization_url()
        # Log only the base URL to avoid exposing client_id in logs
        logger.info("Opening browser for Fitbit authorization")
        webbrowser.open(auth_url)

        print("\nPlease authorize the application in your browser.")
        print(f"If the browser doesn't open, visit: {auth_url}\n")

        # Wait for callback
        logger.info(f"Waiting for callback on port {port}...")
        while CallbackHandler.authorization_code is None:
            server.handle_request()

        # Exchange code for token
        self.exchange_code_for_token(CallbackHandler.authorization_code)
        logger.info("Authorization complete!")

    def is_authorized(self) -> bool:
        """Check if we have valid authorization.

        Returns:
            True if authorized
        """
        return self.token_manager.has_tokens()
