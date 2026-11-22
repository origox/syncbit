"""Tests for sync scheduler."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from src.collector import RateLimitError
from src.scheduler import SyncScheduler


@pytest.fixture
def mock_auth():
    """Mock FitbitAuth."""
    with patch("src.scheduler.FitbitAuth") as mock:
        yield mock.return_value


@pytest.fixture
def mock_collector():
    """Mock FitbitCollector."""
    with patch("src.scheduler.FitbitCollector") as mock:
        yield mock.return_value


@pytest.fixture
def mock_writer():
    """Mock VictoriaMetricsWriter."""
    with patch("src.scheduler.VictoriaMetricsWriter") as mock:
        mock_instance = mock.return_value
        mock_instance.write_daily_data.return_value = True
        mock_instance.write_multiple_days.return_value = (5, 0)  # 5 successful, 0 failed
        yield mock_instance


@pytest.fixture
def mock_state():
    """Mock SyncState."""
    with patch("src.scheduler.SyncState") as mock:
        mock_instance = mock.return_value
        # Return None by default to skip gap detection in sync_data()
        mock_instance.get_last_successful_date.return_value = None
        yield mock_instance


@pytest.fixture
def scheduler(mock_auth, mock_collector, mock_writer, mock_state):
    """Create SyncScheduler with mocked dependencies."""
    return SyncScheduler()


def day(date: str, steps: int = 1000) -> dict:
    return {"date": date, "steps": steps}


def days(start: str, count: int) -> list[dict]:
    first = datetime.strptime(start, "%Y-%m-%d")
    return [day((first + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(count)]


def fetched_ranges(mock_collector) -> list[tuple[str, str]]:
    return [
        (c.args[0].strftime("%Y-%m-%d"), c.args[1].strftime("%Y-%m-%d"))
        for c in mock_collector.get_historical_data.call_args_list
    ]


# --- sync_data ---


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_fetches_yesterday_and_today_in_one_request(
    scheduler, mock_collector, mock_writer, mock_state
):
    mock_collector.get_historical_data.return_value = [day("2024-01-14"), day("2024-01-15")]

    scheduler.sync_data()

    assert fetched_ranges(mock_collector) == [("2024-01-14", "2024-01-15")]
    assert mock_writer.write_daily_data.call_count == 2
    # Only yesterday's date updates state (today is incomplete)
    mock_state.update_last_sync.assert_called_once_with("2024-01-14")


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_without_today(scheduler, mock_collector, mock_state):
    mock_collector.get_historical_data.return_value = [day("2024-01-14")]

    with patch("src.scheduler.Config.INCLUDE_TODAY_DATA", False):
        scheduler.sync_data()

    assert fetched_ranges(mock_collector) == [("2024-01-14", "2024-01-14")]


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_write_failure(scheduler, mock_collector, mock_writer, mock_state):
    """State must not advance past a day that was not written."""
    mock_collector.get_historical_data.return_value = [day("2024-01-14"), day("2024-01-15")]
    mock_writer.write_daily_data.return_value = False

    scheduler.sync_data()

    mock_state.update_last_sync.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_rate_limit_retry(scheduler, mock_collector, mock_writer, mock_state):
    mock_collector.get_historical_data.side_effect = [
        RateLimitError("Rate limited", retry_after=1),
        [day("2024-01-14"), day("2024-01-15")],
    ]

    with patch("time.sleep") as sleep:
        scheduler.sync_data()

    sleep.assert_called_once_with(1)
    assert mock_collector.get_historical_data.call_count == 2
    assert mock_writer.write_daily_data.call_count == 2
    mock_state.update_last_sync.assert_called_once_with("2024-01-14")


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_rate_limit_max_retries(scheduler, mock_collector, mock_writer):
    mock_collector.get_historical_data.side_effect = RateLimitError("Rate limited", retry_after=1)

    with patch("time.sleep"):
        scheduler.sync_data()

    # Initial attempt + 2 retries
    assert mock_collector.get_historical_data.call_count == 3
    mock_writer.write_daily_data.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_exhausted_quota_does_not_wait(scheduler, mock_collector):
    mock_collector.get_historical_data.side_effect = RateLimitError(
        "Rate limited", retry_after=60, quota_reset=900, remaining=0
    )

    with patch("time.sleep") as sleep:
        scheduler.sync_data()

    sleep.assert_not_called()
    assert mock_collector.get_historical_data.call_count == 1


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_unexpected_error(scheduler, mock_collector, mock_writer, mock_state):
    mock_collector.get_historical_data.side_effect = Exception("Unexpected error")

    # Should not raise
    scheduler.sync_data()

    mock_state.update_last_sync.assert_not_called()


# --- backfill ---


@freeze_time("2024-01-15 12:00:00")
def test_backfill_no_state_no_config(scheduler, mock_state, mock_collector):
    mock_state.get_last_successful_date.return_value = None
    mock_collector.get_first_available_date.return_value = None

    with patch("src.config.Config.BACKFILL_START_DATE", None):
        scheduler.backfill_data()

    mock_collector.get_historical_data.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_from_last_synced(scheduler, mock_state, mock_collector, mock_writer):
    mock_state.get_last_successful_date.return_value = "2024-01-10"
    mock_collector.get_historical_data.return_value = days("2024-01-11", 4)
    mock_writer.write_multiple_days.return_value = (4, 0)

    scheduler.backfill_data()

    assert fetched_ranges(mock_collector) == [("2024-01-11", "2024-01-14")]
    mock_state.update_last_sync.assert_called_once_with("2024-01-14")


@freeze_time("2024-01-15 12:00:00")
def test_backfill_has_no_fixed_delays(scheduler, mock_state, mock_collector, mock_writer):
    """Speed comes from removing the Fitbit-era 30s per-date sleeps."""
    mock_state.get_last_successful_date.return_value = "2023-11-30"
    mock_collector.get_historical_data.side_effect = lambda s, e: days(
        s.strftime("%Y-%m-%d"), (e.date() - s.date()).days + 1
    )
    mock_writer.write_multiple_days.side_effect = lambda records: (len(records), 0)

    with patch("time.sleep") as sleep:
        scheduler.backfill_data()

    sleep.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_is_chunked_and_saves_progress_per_chunk(
    scheduler, mock_state, mock_collector, mock_writer
):
    mock_state.get_last_successful_date.return_value = "2023-12-14"
    mock_collector.get_historical_data.side_effect = lambda s, e: days(
        s.strftime("%Y-%m-%d"), (e.date() - s.date()).days + 1
    )
    mock_writer.write_multiple_days.side_effect = lambda records: (len(records), 0)

    scheduler.backfill_data()

    assert fetched_ranges(mock_collector) == [
        ("2023-12-15", "2023-12-28"),
        ("2023-12-29", "2024-01-11"),
        ("2024-01-12", "2024-01-14"),
    ]
    assert [c.args[0] for c in mock_state.update_last_sync.call_args_list] == [
        "2023-12-28",
        "2024-01-11",
        "2024-01-14",
    ]


@freeze_time("2024-01-15 12:00:00")
def test_backfill_no_data_needed(scheduler, mock_state, mock_collector):
    mock_state.get_last_successful_date.return_value = "2024-01-14"

    scheduler.backfill_data()

    mock_collector.get_historical_data.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_retries_chunk_after_rate_limit(
    scheduler, mock_state, mock_collector, mock_writer
):
    mock_state.get_last_successful_date.return_value = "2024-01-12"
    mock_collector.get_historical_data.side_effect = [
        RateLimitError("Rate limited", retry_after=7),
        days("2024-01-13", 2),
    ]
    mock_writer.write_multiple_days.return_value = (2, 0)

    with patch("time.sleep") as sleep:
        scheduler.backfill_data()

    sleep.assert_called_once_with(7)
    assert fetched_ranges(mock_collector) == [("2024-01-13", "2024-01-14")] * 2
    mock_state.update_last_sync.assert_called_once_with("2024-01-14")


@freeze_time("2024-01-15 12:00:00")
def test_backfill_stops_on_consecutive_rate_limits(
    scheduler, mock_state, mock_collector, mock_writer
):
    mock_state.get_last_successful_date.return_value = "2024-01-12"
    mock_collector.get_historical_data.side_effect = RateLimitError("Rate limited", retry_after=1)

    with patch("time.sleep"):
        scheduler.backfill_data()

    assert mock_collector.get_historical_data.call_count == 3
    mock_state.update_last_sync.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_stops_without_advancing_state_on_write_failure(
    scheduler, mock_state, mock_collector, mock_writer
):
    """Advancing past unwritten days would lose them permanently."""
    mock_state.get_last_successful_date.return_value = "2023-12-14"
    mock_collector.get_historical_data.side_effect = lambda s, e: days(
        s.strftime("%Y-%m-%d"), (e.date() - s.date()).days + 1
    )
    mock_writer.write_multiple_days.return_value = (10, 4)

    scheduler.backfill_data()

    assert mock_collector.get_historical_data.call_count == 1
    mock_state.update_last_sync.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_skips_chunk_after_repeated_errors(
    scheduler, mock_state, mock_collector, mock_writer
):
    mock_state.get_last_successful_date.return_value = "2023-12-28"
    mock_collector.get_historical_data.side_effect = [
        Exception("Network error"),
        Exception("Network error"),
        Exception("Network error"),
        days("2024-01-12", 3),
    ]
    mock_writer.write_multiple_days.return_value = (3, 0)

    with patch("time.sleep"):
        scheduler.backfill_data()

    assert fetched_ranges(mock_collector)[-1] == ("2024-01-12", "2024-01-14")
    mock_state.update_last_sync.assert_called_once_with("2024-01-14")
