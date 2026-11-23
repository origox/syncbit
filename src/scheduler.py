"""Scheduler for periodic data synchronization."""

import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .collector import HealthDataCollector, RateLimitError
from .config import Config
from .fitbit_auth import FitbitAuth
from .fitbit_collector import FitbitCollector
from .sync_state import SyncState
from .victoria_writer import VictoriaMetricsWriter

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Schedules and manages periodic health data synchronization.

    The collector is injected so that swapping the upstream provider does not
    require changes here; orchestration only depends on the
    `HealthDataCollector` protocol and on `RateLimitError`.
    """

    def __init__(
        self,
        auth: FitbitAuth | None = None,
        collector: HealthDataCollector | None = None,
        writer: VictoriaMetricsWriter | None = None,
        state: SyncState | None = None,
    ):
        """Initialize scheduler.

        Args:
            auth: Authentication handler; defaults to the Fitbit handler
            collector: Data collector; defaults to the Fitbit collector
            writer: Metrics writer; defaults to the Victoria Metrics writer
            state: Sync state tracker; defaults to the configured state file
        """
        self.auth = auth or FitbitAuth()
        self.collector = collector or FitbitCollector(self.auth)
        self.writer = writer or VictoriaMetricsWriter()
        self.state = state or SyncState(Config.STATE_FILE)
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

        # Yesterday is complete; today is partial but current. Both are fetched
        # in one range request.
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        end = now if Config.INCLUDE_TODAY_DATA else yesterday

        max_retries = 2
        records = None
        for attempt in range(max_retries + 1):
            try:
                records = self.collector.get_historical_data(yesterday, end)
                break
            except RateLimitError as e:
                if e.remaining is not None and e.remaining <= 0:
                    logger.warning(
                        f"Quota exhausted during sync (remaining={e.remaining}, resets in "
                        f"{e.quota_reset or 'unknown'}s). Will retry in next scheduled run."
                    )
                    return
                if attempt == max_retries:
                    logger.error(f"Sync failed after {max_retries} retries due to rate limiting")
                    return
                wait_time = e.retry_after or 60
                logger.warning(
                    f"Rate limited during sync (retry {attempt + 1}/{max_retries}), "
                    f"waiting {wait_time} seconds..."
                )
                time.sleep(wait_time)
            except Exception as e:
                logger.error(f"Error during sync: {e}", exc_info=True)
                return

        today_str = now.strftime("%Y-%m-%d")
        for data in records or []:
            # Only complete days (yesterday or earlier) advance the sync state.
            is_complete_day = data["date"] < today_str
            if not self.writer.write_daily_data(data):
                logger.error(f"Failed to write data to Victoria Metrics for {data['date']}")
                continue
            if is_complete_day and not skip_state_update:
                self.state.update_last_sync(data["date"])
                logger.info(f"Successfully synced data for {data['date']}")
            elif is_complete_day:
                logger.info(
                    f"Successfully synced data for {data['date']} (state NOT updated due to gap)"
                )
            else:
                logger.info(f"Successfully synced incomplete data for {data['date']} (today)")

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

    # Days fetched per collector call during backfill. Matches the Google
    # collector's largest single-request window, so each chunk costs one request
    # per data type.
    BACKFILL_CHUNK_DAYS = 14

    def _backfill_with_incremental_sync(self, start_date: datetime, end_date: datetime) -> None:
        """Backfill a date range in chunks, writing and saving progress per chunk.

        Runs as fast as the upstream API allows: there are no fixed delays, and
        throttling is handled by the collector's client-side limiter plus the
        RateLimitError backoff here. Progress is saved after every chunk that
        is fully written, so an interruption resumes where it stopped.

        Args:
            start_date: Start date for backfill
            end_date: End date for backfill
        """
        total_successful = 0
        total_failed = 0
        current = start_date
        consecutive_rate_limits = 0
        max_consecutive_rate_limits = 3
        consecutive_errors = 0
        max_consecutive_errors = 3

        while current.date() <= end_date.date():
            chunk_end = min(current + timedelta(days=self.BACKFILL_CHUNK_DAYS - 1), end_date)
            span = f"{current.strftime('%Y-%m-%d')}..{chunk_end.strftime('%Y-%m-%d')}"

            try:
                records = self.collector.get_historical_data(current, chunk_end)
            except RateLimitError as e:
                consecutive_rate_limits += 1
                if consecutive_rate_limits >= max_consecutive_rate_limits:
                    logger.error(
                        f"Rate limited {consecutive_rate_limits} times in a row on {span}. "
                        f"Stopping backfill; progress saved at "
                        f"{self.state.get_last_successful_date()}. Will resume on next run."
                    )
                    break
                wait_time = e.retry_after or 60
                logger.warning(
                    f"Rate limited on {span} ({consecutive_rate_limits}/"
                    f"{max_consecutive_rate_limits}), waiting {wait_time}s..."
                )
                time.sleep(wait_time)
                continue
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors < max_consecutive_errors:
                    logger.warning(
                        f"Error fetching {span} ({consecutive_errors}/{max_consecutive_errors}): "
                        f"{e}. Retrying..."
                    )
                    time.sleep(5)
                    continue
                logger.error(f"Giving up on {span} after {consecutive_errors} errors: {e}")
                consecutive_errors = 0
                current = chunk_end + timedelta(days=1)
                continue

            consecutive_rate_limits = 0
            consecutive_errors = 0

            successful, failed = self.writer.write_multiple_days(records)
            total_successful += successful
            total_failed += failed

            if failed:
                # Advancing the state past unwritten days would lose them for good.
                logger.error(
                    f"Failed to write {failed} day(s) in {span}. Stopping backfill; "
                    f"will resume from {self.state.get_last_successful_date()} on next run."
                )
                break

            if records:
                self.state.update_last_sync(records[-1]["date"])
                logger.info(f"Synced {successful} days, progress: {records[-1]['date']}")

            current = chunk_end + timedelta(days=1)

        logger.info(f"Backfill complete: {total_successful} successful, {total_failed} failed")

    def backfill_intraday_data(self) -> None:
        """Backfill historical intraday data if enabled."""
        if not Config.ENABLE_INTRADAY_COLLECTION or not Config.ENABLE_INTRADAY_BACKFILL:
            return

        logger.info("Checking for intraday data to backfill...")

        try:
            # Determine start date for intraday backfill
            last_intraday_backfill = self.state.get_last_intraday_backfill_date()

            # End at yesterday (most recent complete day)
            end_date = datetime.now() - timedelta(days=1)

            if last_intraday_backfill:
                # Continue from where we left off
                start_date = datetime.strptime(last_intraday_backfill, "%Y-%m-%d") + timedelta(
                    days=1
                )
                logger.info(
                    f"Resuming intraday backfill from {start_date.strftime('%Y-%m-%d')} "
                    f"(last completed: {last_intraday_backfill})"
                )
            elif Config.INTRADAY_BACKFILL_DAYS > 0:
                # Use configured number of days
                start_date = datetime.now() - timedelta(days=Config.INTRADAY_BACKFILL_DAYS)
                logger.info(
                    f"Starting intraday backfill for last {Config.INTRADAY_BACKFILL_DAYS} days "
                    f"from {start_date.strftime('%Y-%m-%d')}"
                )
            elif Config.BACKFILL_START_DATE:
                # Use same start date as daily backfill
                start_date = datetime.strptime(Config.BACKFILL_START_DATE, "%Y-%m-%d")
                logger.info(
                    f"Starting intraday backfill from configured date {start_date.strftime('%Y-%m-%d')}"
                )
            else:
                # Default to last 30 days
                start_date = datetime.now() - timedelta(days=30)
                logger.info(
                    f"Starting intraday backfill for last 30 days from {start_date.strftime('%Y-%m-%d')}"
                )

            if start_date > end_date:
                logger.info("No intraday data to backfill")
                return

            logger.info(
                f"Backfilling intraday data from {start_date.strftime('%Y-%m-%d')} "
                f"to {end_date.strftime('%Y-%m-%d')}"
            )

            # Backfill intraday data incrementally
            self._backfill_intraday_with_incremental_sync(start_date, end_date)

        except Exception as e:
            logger.error(f"Error during intraday backfill: {e}", exc_info=True)

    def _backfill_intraday_with_incremental_sync(
        self, start_date: datetime, end_date: datetime
    ) -> None:
        """Backfill intraday data with incremental syncing.

        Args:
            start_date: Start date for backfill
            end_date: End date for backfill
        """
        total_successful = 0
        total_failed = 0
        current_date = start_date
        consecutive_rate_limits = 0
        max_consecutive_rate_limits = 3

        while current_date <= end_date:
            try:
                # Fetch intraday data for one day
                logger.info(f"Backfilling intraday data for {current_date.strftime('%Y-%m-%d')}")
                intraday_data = self.collector.get_intraday_data(current_date)

                if intraday_data and intraday_data.get("resources"):
                    # Write to Victoria Metrics
                    success = self.writer.write_intraday_data(intraday_data)

                    if success:
                        total_successful += 1
                        # Update state to track progress
                        self.state.update_intraday_backfill(intraday_data["date"])
                        logger.info(
                            f"Successfully backfilled intraday data for {intraday_data['date']}"
                        )
                    else:
                        total_failed += 1
                        logger.error(
                            f"Failed to write intraday data for {current_date.strftime('%Y-%m-%d')}"
                        )
                else:
                    logger.info(
                        f"No intraday data available for {current_date.strftime('%Y-%m-%d')}"
                    )
                    # Still update state to avoid re-processing
                    self.state.update_intraday_backfill(current_date.strftime("%Y-%m-%d"))

                consecutive_rate_limits = 0  # Reset on success

                # Move to next date. No fixed delay: the collector throttles
                # itself and rate limits are handled below.
                current_date += timedelta(days=1)

            except RateLimitError as e:
                consecutive_rate_limits += 1
                logger.warning(
                    f"Rate limited on intraday {current_date.strftime('%Y-%m-%d')} "
                    f"({consecutive_rate_limits}/{max_consecutive_rate_limits})"
                )

                # Determine wait time based on quota status
                base_wait_time = (
                    e.retry_after if hasattr(e, "retry_after") and e.retry_after else 60
                )

                # Check if we should stop due to persistent rate limiting
                if consecutive_rate_limits >= max_consecutive_rate_limits:
                    # Check if quota is exhausted
                    quota_exhausted = (
                        hasattr(e, "remaining") and e.remaining is not None and e.remaining <= 0
                    )

                    if quota_exhausted and hasattr(e, "quota_reset") and e.quota_reset:
                        # Use exact quota reset time from API
                        wait_time = e.quota_reset
                        logger.warning(
                            f"Hit {consecutive_rate_limits} consecutive rate limits on intraday backfill. "
                            f"Quota exhausted (remaining={e.remaining}). "
                            f"Waiting {wait_time}s until quota resets..."
                        )
                    else:
                        # Fall back to extended wait
                        wait_time = 300
                        logger.warning(
                            f"Hit {consecutive_rate_limits} consecutive rate limits on intraday backfill. "
                            f"Waiting {wait_time}s before final retry..."
                        )

                    time.sleep(wait_time)

                    # Try one final time after extended wait
                    try:
                        logger.info(
                            f"Final retry for intraday {current_date.strftime('%Y-%m-%d')} after extended wait"
                        )
                        intraday_data = self.collector.get_intraday_data(current_date)

                        if intraday_data and intraday_data.get("resources"):
                            success = self.writer.write_intraday_data(intraday_data)
                            if success:
                                total_successful += 1
                                self.state.update_intraday_backfill(intraday_data["date"])
                                logger.info(
                                    f"Successfully backfilled intraday after extended wait: {intraday_data['date']}"
                                )
                            else:
                                total_failed += 1
                        else:
                            self.state.update_intraday_backfill(current_date.strftime("%Y-%m-%d"))

                        consecutive_rate_limits = 0  # Reset on success
                        current_date += timedelta(days=1)

                    except RateLimitError:
                        logger.error(
                            f"Still rate limited after {wait_time}s wait on intraday backfill. "
                            f"Stopping backfill. Progress saved at {self.state.get_last_intraday_backfill_date()}. "
                            f"Will resume on next run."
                        )
                        break
                    except Exception as e:
                        logger.error(f"Error on final retry for intraday: {e}")
                        current_date += timedelta(days=1)

                    continue  # Continue to next iteration

                logger.info(f"Waiting {base_wait_time} seconds before retrying...")
                time.sleep(base_wait_time)
                # Don't increment date - retry the same date

            except Exception as e:
                logger.error(
                    f"Error fetching intraday for {current_date.strftime('%Y-%m-%d')}: {e}"
                )
                total_failed += 1
                # Move to next date to avoid getting stuck
                current_date += timedelta(days=1)

        logger.info(
            f"Intraday backfill complete: {total_successful} successful, {total_failed} failed"
        )

    def sync_intraday_data(self) -> None:
        """Perform intraday data synchronization."""
        if not Config.ENABLE_INTRADAY_COLLECTION:
            return

        logger.info("Starting intraday data synchronization...")

        # Yesterday is the most recent complete day; it is re-fetched because
        # a device can sync late. Today is partial but current, gated by the
        # same switch as the daily sync.
        now = datetime.now()
        days = [now - timedelta(days=1)]
        if Config.INCLUDE_TODAY_DATA:
            days.append(now)

        try:
            for day in days:
                data = self.collector.get_intraday_data(day)

                if not data or not data.get("resources"):
                    logger.info(f"No intraday data collected for {day.strftime('%Y-%m-%d')}")
                    continue

                if self.writer.write_intraday_data(data):
                    logger.info(f"Successfully synced intraday data for {data['date']}")
                else:
                    logger.error(
                        f"Failed to write intraday data for {data['date']} to Victoria Metrics"
                    )

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

        # Log configuration for diagnostics
        logger.info(f"DEBUG: ENABLE_INTRADAY_COLLECTION={Config.ENABLE_INTRADAY_COLLECTION}")
        logger.info(f"DEBUG: ENABLE_INTRADAY_BACKFILL={Config.ENABLE_INTRADAY_BACKFILL}")
        logger.info(f"DEBUG: BACKFILL_START_DATE={Config.BACKFILL_START_DATE}")
        logger.info(f"DEBUG: INTRADAY_BACKFILL_DAYS={Config.INTRADAY_BACKFILL_DAYS}")

        # Perform initial backfill
        self.backfill_data()

        # Perform intraday backfill if enabled
        if Config.ENABLE_INTRADAY_COLLECTION and Config.ENABLE_INTRADAY_BACKFILL:
            self.backfill_intraday_data()
        else:
            logger.info(
                f"Intraday backfill skipped: ENABLE_INTRADAY_COLLECTION={Config.ENABLE_INTRADAY_COLLECTION}, "
                f"ENABLE_INTRADAY_BACKFILL={Config.ENABLE_INTRADAY_BACKFILL}"
            )

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
