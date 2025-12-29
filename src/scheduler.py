"""Scheduler for periodic data synchronization."""

import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Config
from .fitbit_auth import FitbitAuth
from .fitbit_collector import FitbitCollector, RateLimitError
from .sync_state import SyncState
from .victoria_writer import VictoriaMetricsWriter

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Schedules and manages periodic Fitbit data synchronization."""

    def __init__(self):
        """Initialize scheduler."""
        self.auth = FitbitAuth()
        self.collector = FitbitCollector(self.auth)
        self.writer = VictoriaMetricsWriter()
        self.state = SyncState(Config.STATE_FILE)
        self.scheduler = BlockingScheduler()

    def sync_data(self) -> None:
        """Perform data synchronization."""
        logger.info("Starting data synchronization...")

        max_retries = 2
        retry_count = 0

        while retry_count <= max_retries:
            try:
                # Get yesterday's data (most recent complete day)
                yesterday = datetime.now() - timedelta(days=1)

                # Collect data
                data = self.collector.get_daily_data(yesterday)

                # Write to Victoria Metrics
                success = self.writer.write_daily_data(data)

                if success:
                    self.state.update_last_sync(data["date"])
                    logger.info(f"Successfully synced data for {data['date']}")
                else:
                    logger.error("Failed to write data to Victoria Metrics")

                return  # Success - exit

            except RateLimitError as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"Failed to sync after {max_retries} retries due to rate limiting")
                    return

                wait_time = e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                logger.warning(
                    f"Rate limited during sync (retry {retry_count}/{max_retries}), "
                    f"waiting {wait_time} seconds..."
                )
                time.sleep(wait_time)

            except Exception as e:
                logger.error(f"Error during sync: {e}", exc_info=True)
                return

    def backfill_data(self) -> None:
        """Backfill historical data if needed with incremental syncing."""
        logger.info("Checking for data to backfill...")

        try:
            # Determine start date for backfill
            last_synced = self.state.get_last_successful_date()

            if last_synced:
                # Continue from where we left off
                start_date = datetime.strptime(last_synced, "%Y-%m-%d") + timedelta(days=1)
                logger.info(f"Continuing backfill from {start_date.strftime('%Y-%m-%d')}")
            elif Config.BACKFILL_START_DATE:
                # Use configured backfill start date
                start_date = datetime.strptime(Config.BACKFILL_START_DATE, "%Y-%m-%d")
                logger.info(
                    f"Starting backfill from configured date {start_date.strftime('%Y-%m-%d')}"
                )
            else:
                # Get first available date from Fitbit
                first_date = self.collector.get_first_available_date()
                if not first_date:
                    logger.warning("Could not determine first available date")
                    return
                start_date = first_date
                logger.info(
                    f"Starting backfill from first available date {start_date.strftime('%Y-%m-%d')}"
                )

            # End at yesterday (most recent complete day)
            end_date = datetime.now() - timedelta(days=1)

            if start_date > end_date:
                logger.info("No data to backfill")
                return

            logger.info(
                f"Backfilling data from {start_date.strftime('%Y-%m-%d')} "
                f"to {end_date.strftime('%Y-%m-%d')}"
            )

            # Fetch and sync data incrementally with batching
            self._backfill_with_incremental_sync(start_date, end_date)

        except Exception as e:
            logger.error(f"Error during backfill: {e}", exc_info=True)

    def _backfill_with_incremental_sync(self, start_date: datetime, end_date: datetime) -> None:
        """Backfill data with incremental syncing to Victoria Metrics.

        Fetches data in batches and writes to Victoria Metrics incrementally.
        This ensures progress is saved even if rate limited or interrupted.

        Args:
            start_date: Start date for backfill
            end_date: End date for backfill
        """
        batch_size = 10  # Write to Victoria Metrics every N days
        batch = []
        total_successful = 0
        total_failed = 0
        current_date = start_date
        consecutive_rate_limits = 0
        max_consecutive_rate_limits = 3

        while current_date <= end_date:
            try:
                # Fetch one day's data
                daily_data = self.collector.get_daily_data(current_date)
                batch.append(daily_data)
                consecutive_rate_limits = 0  # Reset on success

                # Write batch when full or at end
                if len(batch) >= batch_size or current_date == end_date:
                    successful, failed = self.writer.write_multiple_days(batch)
                    total_successful += successful
                    total_failed += failed

                    if batch:
                        # Update state to last successfully written date
                        self.state.update_last_sync(batch[-1]["date"])
                        logger.info(
                            f"Synced batch: {successful} days written, "
                            f"progress: {batch[-1]['date']}"
                        )

                    batch = []  # Clear batch

                # Move to next date
                current_date += timedelta(days=1)

                # Rate limit compliance: wait 30 seconds between requests
                if current_date <= end_date:
                    time.sleep(30)

            except RateLimitError as e:
                consecutive_rate_limits += 1
                logger.warning(
                    f"Rate limited on {current_date.strftime('%Y-%m-%d')} "
                    f"({consecutive_rate_limits}/{max_consecutive_rate_limits})"
                )

                # Write partial batch before handling error
                if batch:
                    logger.info(
                        f"Writing partial batch of {len(batch)} days before rate limit wait..."
                    )
                    successful, failed = self.writer.write_multiple_days(batch)
                    total_successful += successful
                    total_failed += failed

                    if batch:
                        self.state.update_last_sync(batch[-1]["date"])

                    batch = []

                # Check if we should stop due to persistent rate limiting
                if consecutive_rate_limits >= max_consecutive_rate_limits:
                    logger.error(
                        f"Stopping backfill after {consecutive_rate_limits} consecutive rate limits. "
                        f"Progress saved at {self.state.get_last_successful_date()}. "
                        f"Will resume on next run."
                    )
                    break

                # Wait for the retry period specified by API
                wait_time = e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                logger.info(f"Waiting {wait_time} seconds before retrying...")
                time.sleep(wait_time)
                # Don't increment date - retry the same date

            except Exception as e:
                logger.error(f"Error fetching {current_date.strftime('%Y-%m-%d')}: {e}")

                # Write partial batch before handling error
                if batch:
                    logger.info(f"Writing partial batch of {len(batch)} days before error...")
                    successful, failed = self.writer.write_multiple_days(batch)
                    total_successful += successful
                    total_failed += failed

                    if batch:
                        self.state.update_last_sync(batch[-1]["date"])

                    batch = []

                # Move to next date
                current_date += timedelta(days=1)

        logger.info(f"Backfill complete: {total_successful} successful, {total_failed} failed")

    def sync_intraday_data(self) -> None:
        """Perform intraday data synchronization."""
        if not Config.ENABLE_INTRADAY_COLLECTION:
            return

        logger.info("Starting intraday data synchronization...")

        try:
            # Get yesterday's data (most recent complete day)
            yesterday = datetime.now() - timedelta(days=1)

            # Collect intraday data
            data = self.collector.get_intraday_data(yesterday)

            if not data or not data.get("resources"):
                logger.info("No intraday data collected")
                return

            # Write to Victoria Metrics
            success = self.writer.write_intraday_data(data)

            if success:
                logger.info(f"Successfully synced intraday data for {data['date']}")
            else:
                logger.error("Failed to write intraday data to Victoria Metrics")

        except RateLimitError as e:
            wait_time = e.retry_after if hasattr(e, "retry_after") else 60
            logger.warning(f"Rate limited during intraday sync, waiting {wait_time}s")
            time.sleep(wait_time)

        except Exception as e:
            logger.error(f"Error during intraday sync: {e}", exc_info=True)

    def start(self) -> None:
        """Start the scheduler."""
        logger.info("Starting SyncBit scheduler...")

        # Check authorization
        if not self.auth.is_authorized():
            logger.error("Not authorized. Please run authorization first.")
            logger.info("Run: python main.py --authorize")
            return

        # Test Victoria Metrics connection
        logger.info("Testing Victoria Metrics connection...")
        if not self.writer.test_connection():
            logger.error("Failed to connect to Victoria Metrics")
            return

        logger.info("Victoria Metrics connection successful")

        # Perform initial backfill
        self.backfill_data()

        # Wait a bit after backfill to avoid immediate rate limiting
        logger.info("Waiting 60 seconds before starting regular sync...")
        time.sleep(60)

        # Perform immediate sync
        self.sync_data()

        # Schedule periodic sync
        interval_minutes = Config.SYNC_INTERVAL_MINUTES
        logger.info(f"Scheduling sync every {interval_minutes} minutes")

        self.scheduler.add_job(
            self.sync_data,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="sync_job",
            name="Fitbit data sync",
            replace_existing=True,
        )

        # Schedule intraday sync (if enabled)
        if Config.ENABLE_INTRADAY_COLLECTION:
            logger.info(f"Scheduling intraday sync every {interval_minutes} minutes")
            self.scheduler.add_job(
                self.sync_intraday_data,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id="sync_intraday_job",
                name="Fitbit intraday data sync",
                replace_existing=True,
            )
            logger.info("Intraday data collection enabled")

        # Start scheduler (blocking)
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped")
