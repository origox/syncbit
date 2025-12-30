"""Configuration management for SyncBit."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file (if exists)
# Note: .envrc users (direnv/1Password) will have vars already loaded
load_dotenv(override=False)


def _load_secret(name: str) -> str:
    """Load secret from mounted file or fall back to environment variable.

    Production: Reads from /run/secrets/* (populated by ESO from 1Password)
    Local Dev: Falls back to environment variables

    Args:
        name: Secret file name (e.g., "fitbit_client_id")

    Returns:
        Secret value as string

    Raises:
        ValueError: If secret is not found in either location
    """
    secret_path = Path(f"/run/secrets/{name}")

    # Try mounted file first (production)
    if secret_path.exists():
        return secret_path.read_text().strip()

    # Fallback to environment variable (local development)
    env_var_map = {
        "fitbit_client_id": "FITBIT_CLIENT_ID",
        "fitbit_client_secret": "FITBIT_CLIENT_SECRET",
        "victoria_endpoint": "VICTORIA_ENDPOINT",
        "victoria_user": "VICTORIA_USER",
        "victoria_password": "VICTORIA_PASSWORD",
    }

    env_value = os.getenv(env_var_map.get(name, ""))
    if not env_value:
        raise ValueError(
            f"Secret not found: {name} "
            f"(neither /run/secrets/{name} nor {env_var_map.get(name, 'N/A')} env var)"
        )

    return env_value


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

    # Victoria Metrics settings (secrets - from mounted files or env vars)
    VICTORIA_ENDPOINT: str = _load_secret("victoria_endpoint")
    VICTORIA_USER: str = _load_secret("victoria_user")
    VICTORIA_PASSWORD: str = _load_secret("victoria_password")

    # Application settings
    SYNC_INTERVAL_MINUTES: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/app/data"))
    TOKEN_FILE: Path = DATA_DIR / "fitbit_tokens.json"
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
    COLLECT_CARDIO_FITNESS: bool = (
        os.getenv("COLLECT_CARDIO_FITNESS", "true").lower() == "true"
    )
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

        if not cls.FITBIT_CLIENT_ID:
            errors.append("FITBIT_CLIENT_ID is required")
        if not cls.FITBIT_CLIENT_SECRET:
            errors.append("FITBIT_CLIENT_SECRET is required")
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
    def get_fitbit_user_id(cls) -> str:
        """Get Fitbit user ID for metric labels."""
        return cls.FITBIT_USER_ID
