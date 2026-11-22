"""Main entry point for SyncBit."""

import argparse
import logging
import sys

from src.config import Config
from src.fitbit_auth import FitbitAuth
from src.fitbit_collector import FitbitCollector
from src.google_auth import GoogleAuth
from src.google_collector import GoogleHealthCollector
from src.scheduler import SyncScheduler


def setup_logging(log_level: str = "INFO") -> None:
    """Setup logging configuration."""
    handlers = [logging.StreamHandler(sys.stdout)]

    # Add file handler if data directory exists
    if Config.DATA_DIR.exists():
        log_file = Config.DATA_DIR / "syncbit.log"
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


logger = logging.getLogger(__name__)


def build_provider() -> tuple[FitbitAuth | GoogleAuth, FitbitCollector | GoogleHealthCollector]:
    """Create the auth handler and collector for the configured provider."""
    if Config.HEALTH_PROVIDER == "fitbit":
        fitbit_auth = FitbitAuth()
        return fitbit_auth, FitbitCollector(fitbit_auth)

    google_auth = GoogleAuth()
    return google_auth, GoogleHealthCollector(google_auth)


def authorize(manual: bool = False) -> None:
    """Run OAuth authorization flow."""
    logger.info(f"Starting OAuth authorization ({Config.HEALTH_PROVIDER})...")

    auth, _ = build_provider()

    if auth.is_authorized():
        logger.info("Already authorized!")
        response = input("Re-authorize? (y/n): ")
        if response.lower() != "y":
            return

    try:
        if Config.HEALTH_PROVIDER == "google":
            auth.authorize(manual=manual)
        else:
            auth.authorize()
        logger.info("Authorization successful!")
    except Exception as e:
        logger.error(f"Authorization failed: {e}")
        sys.exit(1)


def run_sync() -> None:
    """Run the synchronization scheduler."""
    logger.info(f"Starting SyncBit (provider: {Config.HEALTH_PROVIDER})...")

    auth, collector = build_provider()
    scheduler = SyncScheduler(auth=auth, collector=collector)
    scheduler.start()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="SyncBit - health data to Victoria Metrics sync")
    parser.add_argument("--authorize", action="store_true", help="Run OAuth authorization flow")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="With --authorize (Google): paste the code instead of running a callback server",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )

    args = parser.parse_args()

    try:
        # Validate configuration (creates data dir)
        Config.validate()

        # Setup logging after data dir exists
        setup_logging(args.log_level)

        if args.authorize:
            authorize(manual=args.manual)
        else:
            run_sync()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
