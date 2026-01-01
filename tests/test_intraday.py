"""Tests for intraday data collection and backfill."""

from datetime import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from src.fitbit_collector import RateLimitError
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
        mock_instance.write_intraday_data.return_value = True
        yield mock_instance


@pytest.fixture
def mock_state():
    """Mock SyncState."""
    with patch("src.scheduler.SyncState") as mock:
        mock_instance = mock.return_value
        mock_instance.get_last_successful_date.return_value = None
        mock_instance.get_last_intraday_backfill_date.return_value = None
        yield mock_instance


@pytest.fixture
def mock_config():
    """Mock Config with intraday enabled."""
    with patch("src.scheduler.Config") as mock:
        mock.ENABLE_INTRADAY_COLLECTION = True
        mock.ENABLE_INTRADAY_BACKFILL = True
        mock.BACKFILL_START_DATE = "2024-01-01"
        mock.INTRADAY_BACKFILL_DAYS = 0
        mock.COLLECT_DEVICE_INFO = False
        yield mock


@pytest.fixture
def scheduler(mock_auth, mock_collector, mock_writer, mock_state):
    """Create SyncScheduler with mocked dependencies."""
    return SyncScheduler()


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_disabled(scheduler, mock_collector, mock_config):
    """Test intraday backfill skipped when disabled."""
    mock_config.ENABLE_INTRADAY_COLLECTION = False

    scheduler.backfill_intraday_data()

    # Should not collect any data
    mock_collector.get_intraday_data.assert_not_called()


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_from_config_start_date(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill from configured start date."""
    # Mock intraday data
    mock_intraday = {
        "date": "2024-01-01",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }
    mock_collector.get_intraday_data.return_value = mock_intraday

    with patch("time.sleep"):  # Don't actually sleep
        scheduler.backfill_intraday_data()

    # Should backfill from 2024-01-01 to yesterday (2024-01-09)
    assert mock_collector.get_intraday_data.call_count == 9
    assert mock_writer.write_intraday_data.call_count == 9
    assert mock_state.update_intraday_backfill.call_count == 9


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_resume_from_last_date(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill resumes from last completed date."""
    # Simulate already backfilled to 2024-01-05
    mock_state.get_last_intraday_backfill_date.return_value = "2024-01-05"

    mock_intraday = {
        "date": "2024-01-06",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }
    mock_collector.get_intraday_data.return_value = mock_intraday

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should backfill from 2024-01-06 to yesterday (2024-01-09) = 4 days
    assert mock_collector.get_intraday_data.call_count == 4


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_with_backfill_days(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill using INTRADAY_BACKFILL_DAYS."""
    mock_config.INTRADAY_BACKFILL_DAYS = 3
    mock_config.BACKFILL_START_DATE = ""  # Clear start date

    mock_intraday = {
        "date": "2024-01-07",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }
    mock_collector.get_intraday_data.return_value = mock_intraday

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should backfill last 3 days (2024-01-07, 2024-01-08, 2024-01-09)
    assert mock_collector.get_intraday_data.call_count == 3


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_no_data_available(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill when no data available."""
    # Return empty resources
    mock_collector.get_intraday_data.return_value = {"date": "2024-01-01", "resources": {}}

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should still update state to avoid re-processing
    assert mock_state.update_intraday_backfill.call_count == 9


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_handles_rate_limit(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill handles rate limits."""
    mock_intraday = {
        "date": "2024-01-01",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }

    # First call rate limited, second succeeds
    mock_collector.get_intraday_data.side_effect = [
        RateLimitError("Rate limited", retry_after=1),
        mock_intraday,
    ] + [
        mock_intraday
    ] * 8  # Remaining days

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should retry and eventually succeed for all 9 days
    assert mock_collector.get_intraday_data.call_count == 10  # 1 retry + 9 successful
    assert mock_writer.write_intraday_data.call_count == 9


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_stops_on_quota_exhausted(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill stops when quota exhausted."""
    # Simulate quota exhaustion after 3 consecutive rate limits
    rate_limit = RateLimitError("Rate limited", retry_after=60)
    rate_limit.remaining = -1
    rate_limit.quota_reset = 1800

    mock_collector.get_intraday_data.side_effect = [rate_limit] * 10

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should try 3 times, then wait for quota reset, then try once more, then stop
    # Only processes first date before stopping
    assert mock_state.update_intraday_backfill.call_count == 0  # None succeeded


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_write_failure(
    scheduler, mock_collector, mock_writer, mock_state, mock_config
):
    """Test intraday backfill handles write failures."""
    mock_intraday = {
        "date": "2024-01-01",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }
    mock_collector.get_intraday_data.return_value = mock_intraday
    mock_writer.write_intraday_data.return_value = False  # Write fails

    with patch("time.sleep"):
        scheduler.backfill_intraday_data()

    # Should still try all days even with write failures
    assert mock_collector.get_intraday_data.call_count == 9
    # Should NOT update state on write failure
    assert mock_state.update_intraday_backfill.call_count == 0


@freeze_time("2024-01-10 12:00:00")
def test_backfill_intraday_no_data_needed(scheduler, mock_collector, mock_state, mock_config):
    """Test intraday backfill when already up to date."""
    # Last backfill was yesterday
    mock_state.get_last_intraday_backfill_date.return_value = "2024-01-09"

    scheduler.backfill_intraday_data()

    # Should not collect any data (start_date > end_date)
    mock_collector.get_intraday_data.assert_not_called()


@freeze_time("2024-01-10 12:00:00")
def test_sync_intraday_data_success(scheduler, mock_collector, mock_writer, mock_config):
    """Test successful intraday sync."""
    mock_intraday = {
        "date": "2024-01-09",
        "resources": {"steps": {"dataset": [{"time": "00:00:00", "value": 100}]}},
    }
    mock_collector.get_intraday_data.return_value = mock_intraday

    scheduler.sync_intraday_data()

    # Should sync yesterday's intraday data
    mock_collector.get_intraday_data.assert_called_once()
    called_date = mock_collector.get_intraday_data.call_args[0][0]
    assert called_date.date() == datetime(2024, 1, 9).date()

    mock_writer.write_intraday_data.assert_called_once_with(mock_intraday)


@freeze_time("2024-01-10 12:00:00")
def test_sync_intraday_data_disabled(scheduler, mock_collector, mock_config):
    """Test intraday sync skipped when disabled."""
    mock_config.ENABLE_INTRADAY_COLLECTION = False

    scheduler.sync_intraday_data()

    mock_collector.get_intraday_data.assert_not_called()


@freeze_time("2024-01-10 12:00:00")
def test_sync_intraday_handles_rate_limit(scheduler, mock_collector, mock_config):
    """Test intraday sync handles rate limits."""
    mock_collector.get_intraday_data.side_effect = RateLimitError("Rate limited", retry_after=60)

    with patch("time.sleep"):
        scheduler.sync_intraday_data()

    # Should have tried to collect
    mock_collector.get_intraday_data.assert_called_once()


@freeze_time("2024-01-10 12:00:00")
def test_sync_intraday_no_data(scheduler, mock_collector, mock_writer, mock_config):
    """Test intraday sync when no data available."""
    mock_collector.get_intraday_data.return_value = None

    scheduler.sync_intraday_data()

    # Should not write if no data
    mock_writer.write_intraday_data.assert_not_called()
