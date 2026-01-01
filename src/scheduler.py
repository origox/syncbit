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

        # Check if there's a gap that needs backfilling first
        last_synced = self.state.get_last_successful_date()
        yesterday = datetime.now() - timedelta(days=1)

        if last_synced:
            last_sync_date = datetime.strptime(last_synced, "%Y-%m-%d")
            # If there's a gap (last sync is before yesterday), trigger backfill
            if last_sync_date < yesterday.replace(hour=0, minute=0, second=0, microsecond=0):
                gap_days = (yesterday - last_sync_date).days
                logger.warning(
                    f"Gap detected: last_successful_date={last_synced}, yesterday={yesterday.strftime('%Y-%m-%d')}. "
                    f"Missing {gap_days} days. Triggering backfill to fill gap..."
                )
                # Run backfill to fill the gap
                self.backfill_data()
                # After backfill, reload state to check if gap is filled
                last_synced = self.state.get_last_successful_date()
                if last_synced:
                    last_sync_date = datetime.strptime(last_synced, "%Y-%m-%d")
                    if last_sync_date < yesterday.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ):
                        # Gap still exists, skip state updates
                        skip_state_update = True
                        logger.info(
                            f"Gap still exists after backfill (last_successful_date={last_synced}). "
                            f"Will continue backfill on next sync cycle."
                        )
                    else:
                        # Gap filled!
                        skip_state_update = False
                        logger.info("Gap filled! Regular sync will now update state.")
                else:
                    skip_state_update = False
            else:
                skip_state_update = False
        else:
            skip_state_update = False

        # Collect device info once per sync cycle
        if Config.COLLECT_DEVICE_INFO:
            try:
                device_info = self.collector.get_device_info()
                if device_info:
                    success = self.writer.write_device_info(device_info)
                    if success:
                        logger.info("Successfully synced device information")
            except RateLimitError as e:
                # Check if quota is exhausted (remaining <= 0)
                quota_exhausted = (
                    hasattr(e, "remaining") and e.remaining is not None and e.remaining <= 0
                )

                if quota_exhausted:
                    # Don't wait if quota is exhausted - fail fast and let next scheduled run retry
                    reset_time = (
                        e.quota_reset if hasattr(e, "quota_reset") and e.quota_reset else "unknown"
                    )
                    logger.warning(
                        f"Quota exhausted while collecting device info (remaining={e.remaining}, "
                        f"resets in {reset_time}s). Skipping device info collection this cycle."
                    )
                else:
                    # Transient rate limit - wait and continue
                    wait_time = e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                    logger.warning(
                        f"Rate limited while collecting device info, waiting {wait_time} seconds..."
                    )
                    time.sleep(wait_time)
            except Exception as e:
                logger.error(f"Error syncing device info: {e}")

        # Determine which dates to sync
        dates_to_sync = [datetime.now() - timedelta(days=1)]  # Yesterday (complete data)

        if Config.INCLUDE_TODAY_DATA:
            dates_to_sync.append(datetime.now())  # Today (incomplete but current)

        max_retries = 2
        for target_date in dates_to_sync:
            retry_count = 0
            is_complete_day = (
                target_date.date() < datetime.now().date()
            )  # Only yesterday or earlier is complete

            while retry_count <= max_retries:
                try:
                    # Collect data
                    data = self.collector.get_daily_data(target_date)

                    # Write to Victoria Metrics
                    success = self.writer.write_daily_data(data)

                    if success:
                        # Only update state for complete days (yesterday or earlier)
                        # AND only if there's no gap
                        if is_complete_day and not skip_state_update:
                            self.state.update_last_sync(data["date"])
                            logger.info(f"Successfully synced data for {data['date']}")
                        elif is_complete_day and skip_state_update:
                            logger.info(
                                f"Successfully synced data for {data['date']} "
                                f"(state NOT updated due to gap)"
                            )
                        else:
                            logger.info(
                                f"Successfully synced incomplete data for {data['date']} (today)"
                            )
                    else:
                        logger.error(f"Failed to write data to Victoria Metrics for {data['date']}")

                    break  # Success - move to next date

                except RateLimitError as e:
                    # Check if quota is exhausted (remaining <= 0)
                    quota_exhausted = (
                        hasattr(e, "remaining") and e.remaining is not None and e.remaining <= 0
                    )

                    if quota_exhausted:
                        # Quota exhausted - fail fast, don't retry
                        reset_time = (
                            e.quota_reset
                            if hasattr(e, "quota_reset") and e.quota_reset
                            else "unknown"
                        )
                        logger.warning(
                            f"Quota exhausted during sync for {target_date.strftime('%Y-%m-%d')} "
                            f"(remaining={e.remaining}, resets in {reset_time}s). "
                            f"Skipping this sync cycle - will retry in next scheduled run."
                        )
                        break  # Move to next date or exit

                    # Transient rate limit - retry with backoff
                    retry_count += 1
                    if retry_count > max_retries:
                        logger.error(
                            f"Failed to sync {target_date.strftime('%Y-%m-%d')} "
                            f"after {max_retries} retries due to rate limiting"
                        )
                        break

                    wait_time = e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                    logger.warning(
                        f"Rate limited during sync (retry {retry_count}/{max_retries}), "
                        f"waiting {wait_time} seconds..."
                    )
                    time.sleep(wait_time)

                except Exception as e:
                    logger.error(
                        f"Error during sync for {target_date.strftime('%Y-%m-%d')}: {e}",
                        exc_info=True,
                    )
                    break

    def backfill_data(self) -> None:
        """Backfill historical data if needed with incremental syncing."""
        logger.info("Checking for data to backfill...")

        try:
            # Determine start date for backfill
            last_synced = self.state.get_last_successful_date()

            # End at yesterday (most recent complete day)
            end_date = datetime.now() - timedelta(days=1)

            if last_synced:
                # Check if we need to fill a gap (app was down)
                last_sync_date = datetime.strptime(last_synced, "%Y-%m-%d")

                # If last sync was yesterday or today, no gap to fill
                if last_sync_date >= end_date:
                    logger.info(f"Last sync ({last_synced}) is current, no gap to fill")
                    # Still check for historical backfill if configured
                    if Config.BACKFILL_START_DATE:
                        start_date = datetime.strptime(Config.BACKFILL_START_DATE, "%Y-%m-%d")
                        if start_date < last_sync_date:
                            logger.info(
                                f"Starting historical backfill from {start_date.strftime('%Y-%m-%d')} "
                                f"to {(last_sync_date - timedelta(days=1)).strftime('%Y-%m-%d')}"
                            )
                            self._backfill_with_incremental_sync(
                                start_date, last_sync_date - timedelta(days=1)
                            )
                    return

                # Gap detected - fill from day after last sync to yesterday
                start_date = last_sync_date + timedelta(days=1)
                logger.info(
                    f"Gap detected: last sync was {last_synced}, filling gap from "
                    f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
                )
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

                # Determine wait time based on quota status
                base_wait_time = (
                    e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                )

                # Check if we should stop due to persistent rate limiting
                if consecutive_rate_limits >= max_consecutive_rate_limits:
                    # Check if quota is exhausted (remaining <= 0)
                    quota_exhausted = (
                        hasattr(e, "remaining") and e.remaining is not None and e.remaining <= 0
                    )

                    if quota_exhausted and hasattr(e, "quota_reset") and e.quota_reset:
                        # Use the exact quota reset time from API
                        wait_time = e.quota_reset
                        logger.warning(
                            f"Hit {consecutive_rate_limits} consecutive rate limits. "
                            f"Quota exhausted (remaining={e.remaining}). "
                            f"Waiting {wait_time}s until quota resets..."
                        )
                    else:
                        # Fall back to extended wait if no quota info available
                        wait_time = 300
                        logger.warning(
                            f"Hit {consecutive_rate_limits} consecutive rate limits. "
                            f"Waiting {wait_time}s before final retry..."
                        )

                    time.sleep(wait_time)

                    # Try one final time after extended wait
                    try:
                        logger.info(
                            f"Final retry for {current_date.strftime('%Y-%m-%d')} after extended wait"
                        )
                        daily_data = self.collector.get_daily_data(current_date)
                        batch.append(daily_data)
                        consecutive_rate_limits = 0  # Reset on success

                        # Write batch if needed
                        if len(batch) >= batch_size or current_date == end_date:
                            successful, failed = self.writer.write_multiple_days(batch)
                            total_successful += successful
                            total_failed += failed
                            if batch:
                                self.state.update_last_sync(batch[-1]["date"])
                                logger.info(
                                    f"Synced batch after extended wait: progress={batch[-1]['date']}"
                                )
                            batch = []

                        # Move to next date
                        current_date += timedelta(days=1)
                        if current_date <= end_date:
                            time.sleep(30)  # Rate limit delay

                    except RateLimitError:
                        logger.error(
                            f"Still rate limited after {wait_time}s wait. "
                            f"Stopping backfill. Progress saved at {self.state.get_last_successful_date()}. "
                            f"Will resume on next run."
                        )
                        break
                    except Exception as e:
                        logger.error(f"Error on final retry: {e}")
                        current_date += timedelta(days=1)

                    continue  # Continue to next iteration

                logger.info(f"Waiting {base_wait_time} seconds before retrying...")
                time.sleep(base_wait_time)
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
