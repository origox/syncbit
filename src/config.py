"""Configuration management for SyncBit."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file (if exists)
# Note: .envrc users (direnv/1Password) will have vars already loaded
load_dotenv(override=False)


_ENV_VAR_MAP = {
    "fitbit_client_id": "FITBIT_CLIENT_ID",
    "fitbit_client_secret": "FITBIT_CLIENT_SECRET",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
    "victoria_endpoint": "VICTORIA_ENDPOINT",
    "victoria_user": "VICTORIA_USER",
    "victoria_password": "VICTORIA_PASSWORD",
}


def _load_secret(name: str) -> str:
    """Load secret from mounted file or fall back to environment variable.

    Production: Reads from /run/secrets/* (populated by ESO from 1Password)
    Local Dev: Falls back to environment variables

    A missing secret returns "" rather than raising, so that importing this
    module never fails. Enforcement lives in `validate()` / `validate_google()`,
    which run at startup and report every missing secret at once instead of
    aborting on the first. This also lets single-purpose tools (such as the
    Google probe script) import the config without the unrelated providers'
    secrets being present.

    Args:
        name: Secret file name (e.g., "fitbit_client_id")

    Returns:
        Secret value, or "" when not configured
    """
    secret_path = Path(f"/run/secrets/{name}")

    # Try mounted file first (production)
    if secret_path.exists():
        return secret_path.read_text().strip()

    # Fallback to environment variable (local development)
    return os.getenv(_ENV_VAR_MAP.get(name, ""), "")


class Config:
    """Application configuration."""

    # Fitbit OAuth2 settings (secrets - from mounted files or env vars)
    FITBIT_CLIENT_ID: str = _load_secret("fitbit_client_id")
    FITBIT_CLIENT_SECRET: str = _load_secret("fitbit_client_secret")
    FITBIT_REDIRECT_URI: str = os.getenv("FITBIT_REDIRECT_URI", "http://localhost:8080/callback")

    # Fitbit API settings
    FITBIT_API_BASE_URL: str = "https://api.fitbit.com/1/user/-"
    FITBIT_AUTH_URL: str = "https://www.fitbit.com/oauth2/authorize"
    FITBIT_TOKEN_URL: str = "https://api.fitbit.com/oauth2/token"
    FITBIT_SCOPES: list[str] = [
        "activity",
        "heartrate",
        "profile",
        "sleep",
        "respiratory_rate",
        "oxygen_saturation",
        "temperature",
        "cardio_fitness",
        "settings",
    ]

    # Upstream health data provider: "google" (Google Health API) or "fitbit"
    # (legacy Fitbit Web API, shut down by Google on 2026-11-02).
    HEALTH_PROVIDER: str = os.getenv("HEALTH_PROVIDER", "google").strip().lower()

    # Google Health API OAuth2 settings.
    # Only required by the Google code paths; see validate_google().
    GOOGLE_CLIENT_ID: str = _load_secret("google_client_id")
    GOOGLE_CLIENT_SECRET: str = _load_secret("google_client_secret")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/callback")

    # Device label applied to every metric. Fitbit-era data was written as
    # "charge6"; change this only together with dashboards that filter on it.
    DEVICE_LABEL: str = os.getenv("DEVICE_LABEL", "fitbit_air")

    # Client-side throttle for the Google Health API. The documented per-user
    # quota is 300 requests/minute; staying at it avoids 429s during backfill.
    GOOGLE_MAX_REQUESTS_PER_MINUTE: int = int(os.getenv("GOOGLE_MAX_REQUESTS_PER_MINUTE", "300"))
    # Requests in flight at once. ~1s latency per request means about 5 in
    # parallel are needed to actually reach the per-minute quota.
    GOOGLE_MAX_CONCURRENT_REQUESTS: int = int(os.getenv("GOOGLE_MAX_CONCURRENT_REQUESTS", "8"))

    # Google Health API settings
    GOOGLE_API_BASE_URL: str = "https://health.googleapis.com/v4"
    GOOGLE_AUTH_URL: str = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL: str = "https://oauth2.googleapis.com/token"
    _GOOGLE_SCOPE_PREFIX: str = "https://www.googleapis.com/auth/googlehealth"
    GOOGLE_SCOPES: list[str] = [
        f"{_GOOGLE_SCOPE_PREFIX}.activity_and_fitness.readonly",
        f"{_GOOGLE_SCOPE_PREFIX}.health_metrics_and_measurements.readonly",
        f"{_GOOGLE_SCOPE_PREFIX}.sleep.readonly",
        f"{_GOOGLE_SCOPE_PREFIX}.profile.readonly",
    ]

    # Victoria Metrics settings (secrets - from mounted files or env vars)
    VICTORIA_ENDPOINT: str = _load_secret("victoria_endpoint")
    VICTORIA_USER: str = _load_secret("victoria_user")
    VICTORIA_PASSWORD: str = _load_secret("victoria_password")

    # Application settings
    SYNC_INTERVAL_MINUTES: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/app/data"))
    TOKEN_FILE: Path = DATA_DIR / "fitbit_tokens.json"
    GOOGLE_TOKEN_FILE: Path = DATA_DIR / "google_tokens.json"
    STATE_FILE: Path = DATA_DIR / "sync_state.json"

    # Backfill settings
    BACKFILL_START_DATE: str = os.getenv("BACKFILL_START_DATE", "")  # Format: YYYY-MM-DD

    # Sync settings
    INCLUDE_TODAY_DATA: bool = os.getenv("INCLUDE_TODAY_DATA", "true").lower() == "true"

    # Metric collection toggles (all enabled by default for comprehensive collection)
    COLLECT_SLEEP: bool = os.getenv("COLLECT_SLEEP", "true").lower() == "true"
    COLLECT_SPO2: bool = os.getenv("COLLECT_SPO2", "true").lower() == "true"
    COLLECT_BREATHING_RATE: bool = os.getenv("COLLECT_BREATHING_RATE", "true").lower() == "true"
    COLLECT_HRV: bool = os.getenv("COLLECT_HRV", "true").lower() == "true"
    COLLECT_CARDIO_FITNESS: bool = os.getenv("COLLECT_CARDIO_FITNESS", "true").lower() == "true"
    COLLECT_TEMPERATURE: bool = os.getenv("COLLECT_TEMPERATURE", "true").lower() == "true"
    COLLECT_DEVICE_INFO: bool = os.getenv("COLLECT_DEVICE_INFO", "true").lower() == "true"

    # Intraday data collection settings
    ENABLE_INTRADAY_COLLECTION: bool = (
        os.getenv("ENABLE_INTRADAY_COLLECTION", "false").lower() == "true"
    )
    INTRADAY_DETAIL_LEVEL: str = os.getenv("INTRADAY_DETAIL_LEVEL", "5min")  # 1min, 5min, 15min
    INTRADAY_HEART_RATE_DETAIL: str = os.getenv(
        "INTRADAY_HEART_RATE_DETAIL", "1min"
    )  # 1sec, 1min, 5min, 15min
    INTRADAY_RESOURCES: list[str] = [
        r.strip()
        for r in os.getenv("INTRADAY_RESOURCES", "steps,calories,distance,heart_rate").split(",")
    ]
    ENABLE_INTRADAY_BACKFILL: bool = (
        os.getenv("ENABLE_INTRADAY_BACKFILL", "false").lower() == "true"
    )
    INTRADAY_BACKFILL_DAYS: int = int(os.getenv("INTRADAY_BACKFILL_DAYS", "30"))

    # User identification
    FITBIT_USER_ID: str = os.getenv("FITBIT_USER_ID", "default")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> None:
        """Validate required configuration."""
        errors = []

        if cls.HEALTH_PROVIDER == "fitbit":
            if not cls.FITBIT_CLIENT_ID:
                errors.append("FITBIT_CLIENT_ID is required")
            if not cls.FITBIT_CLIENT_SECRET:
                errors.append("FITBIT_CLIENT_SECRET is required")
        elif cls.HEALTH_PROVIDER == "google":
            if not cls.GOOGLE_CLIENT_ID:
                errors.append("GOOGLE_CLIENT_ID is required")
            if not cls.GOOGLE_CLIENT_SECRET:
                errors.append("GOOGLE_CLIENT_SECRET is required")
        else:
            errors.append(
                f"HEALTH_PROVIDER must be 'google' or 'fitbit', got '{cls.HEALTH_PROVIDER}'"
            )

        if not cls.VICTORIA_ENDPOINT:
            errors.append("VICTORIA_ENDPOINT is required")
        if not cls.VICTORIA_USER:
            errors.append("VICTORIA_USER is required")
        if not cls.VICTORIA_PASSWORD:
            errors.append("VICTORIA_PASSWORD is required")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        # Ensure data directory exists
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate_google(cls) -> None:
        """Validate the configuration required by the Google Health API paths."""
        errors = []

        if not cls.GOOGLE_CLIENT_ID:
            errors.append("GOOGLE_CLIENT_ID is required")
        if not cls.GOOGLE_CLIENT_SECRET:
            errors.append("GOOGLE_CLIENT_SECRET is required")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

    @classmethod
    def get_fitbit_user_id(cls) -> str:
        """Get Fitbit user ID for metric labels."""
        return cls.FITBIT_USER_ID
