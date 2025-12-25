"""Configuration management for SyncBit."""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file (if exists)
# Note: .envrc users (direnv/1Password) will have vars already loaded
load_dotenv(override=False)


class Config:
    """Application configuration."""

    # Fitbit OAuth2 settings
    FITBIT_CLIENT_ID: str = os.getenv("FITBIT_CLIENT_ID", "")
    FITBIT_CLIENT_SECRET: str = os.getenv("FITBIT_CLIENT_SECRET", "")
    FITBIT_REDIRECT_URI: str = os.getenv("FITBIT_REDIRECT_URI", "http://localhost:8080/callback")
    
    # Fitbit API settings
    FITBIT_API_BASE_URL: str = "https://api.fitbit.com/1/user/-"
    FITBIT_AUTH_URL: str = "https://www.fitbit.com/oauth2/authorize"
    FITBIT_TOKEN_URL: str = "https://api.fitbit.com/oauth2/token"
    FITBIT_SCOPES: list[str] = ["activity", "heartrate", "profile", "sleep"]
    
    # Victoria Metrics settings
    VICTORIA_ENDPOINT: str = os.getenv("VICTORIA_ENDPOINT", "")
    VICTORIA_USER: str = os.getenv("VICTORIA_USER", "")
    VICTORIA_PASSWORD: str = os.getenv("VICTORIA_PASSWORD", "")
    
    # Application settings
    SYNC_INTERVAL_MINUTES: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/app/data"))
    TOKEN_FILE: Path = DATA_DIR / "fitbit_tokens.json"
    STATE_FILE: Path = DATA_DIR / "sync_state.json"
    
    # Backfill settings
    BACKFILL_START_DATE: str = os.getenv("BACKFILL_START_DATE", "")  # Format: YYYY-MM-DD
    
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
