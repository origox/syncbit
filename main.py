"""Main entry point for SyncBit."""

import argparse
import logging
import sys

from src.config import Config
from src.fitbit_auth import FitbitAuth
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


def authorize() -> None:
    """Run OAuth authorization flow."""
    logger.info("Starting OAuth authorization...")

    auth = FitbitAuth()

    if auth.is_authorized():
        logger.info("Already authorized!")
        response = input("Re-authorize? (y/n): ")
        if response.lower() != "y":
            return

    try:
        auth.authorize()
        logger.info("Authorization successful!")
    except Exception as e:
        logger.error(f"Authorization failed: {e}")
        sys.exit(1)


def run_sync() -> None:
    """Run the synchronization scheduler."""
    logger.info("Starting SyncBit...")

    scheduler = SyncScheduler()
    scheduler.start()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="SyncBit - Fitbit to Victoria Metrics sync")
    parser.add_argument("--authorize", action="store_true", help="Run OAuth authorization flow")
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
            authorize()
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
