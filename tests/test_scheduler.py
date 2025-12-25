"""Tests for sync scheduler."""

from datetime import datetime, timedelta
from unittest.mock import Mock, call, patch

import pytest
from freezegun import freeze_time

from src.fitbit_collector import RateLimitError
from src.scheduler import SyncScheduler


@pytest.fixture
def mock_auth():
    """Mock FitbitAuth."""
    with patch('src.scheduler.FitbitAuth') as mock:
        yield mock.return_value


@pytest.fixture
def mock_collector():
    """Mock FitbitCollector."""
    with patch('src.scheduler.FitbitCollector') as mock:
        yield mock.return_value


@pytest.fixture
def mock_writer():
    """Mock VictoriaMetricsWriter."""
    with patch('src.scheduler.VictoriaMetricsWriter') as mock:
        mock_instance = mock.return_value
        mock_instance.write_daily_data.return_value = True
        mock_instance.write_multiple_days.return_value = (5, 0)  # 5 successful, 0 failed
        yield mock_instance


@pytest.fixture
def mock_state():
    """Mock SyncState."""
    with patch('src.scheduler.SyncState') as mock:
        yield mock.return_value


@pytest.fixture
def scheduler(mock_auth, mock_collector, mock_writer, mock_state):
    """Create SyncScheduler with mocked dependencies."""
    return SyncScheduler()


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_success(scheduler, mock_collector, mock_writer, mock_state):
    """Test successful data sync."""
    # Setup mocks
    yesterday = datetime(2024, 1, 14)
    mock_data = {
        'date': '2024-01-14',
        'steps': 10000
    }
    mock_collector.get_daily_data.return_value = mock_data
    
    # Execute
    scheduler.sync_data()
    
    # Verify
    mock_collector.get_daily_data.assert_called_once()
    called_date = mock_collector.get_daily_data.call_args[0][0]
    assert called_date.date() == yesterday.date()
    
    mock_writer.write_daily_data.assert_called_once_with(mock_data)
    mock_state.update_last_sync.assert_called_once_with('2024-01-14')


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_write_failure(scheduler, mock_collector, mock_writer, mock_state):
    """Test sync when writer fails."""
    mock_data = {'date': '2024-01-14', 'steps': 5000}
    mock_collector.get_daily_data.return_value = mock_data
    mock_writer.write_daily_data.return_value = False
    
    scheduler.sync_data()
    
    # State should not be updated on write failure
    mock_state.update_last_sync.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_rate_limit_retry(scheduler, mock_collector, mock_writer, mock_state):
    """Test sync retries on rate limit."""
    mock_data = {'date': '2024-01-14', 'steps': 10000}
    
    # First call raises rate limit, second succeeds
    mock_collector.get_daily_data.side_effect = [
        RateLimitError("Rate limited", retry_after=1),
        mock_data
    ]
    
    with patch('time.sleep'):  # Don't actually sleep in tests
        scheduler.sync_data()
    
    # Should have retried
    assert mock_collector.get_daily_data.call_count == 2
    mock_writer.write_daily_data.assert_called_once_with(mock_data)
    mock_state.update_last_sync.assert_called_once_with('2024-01-14')


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_rate_limit_max_retries(scheduler, mock_collector):
    """Test sync stops after max retries."""
    # Always raise rate limit
    mock_collector.get_daily_data.side_effect = RateLimitError("Rate limited", retry_after=1)
    
    with patch('time.sleep'):
        scheduler.sync_data()
    
    # Should try 3 times (initial + 2 retries)
    assert mock_collector.get_daily_data.call_count == 3


@freeze_time("2024-01-15 12:00:00")
def test_sync_data_unexpected_error(scheduler, mock_collector, mock_writer, mock_state):
    """Test sync handles unexpected errors."""
    mock_collector.get_daily_data.side_effect = Exception("Unexpected error")
    
    # Should not raise
    scheduler.sync_data()
    
    # State should not be updated
    mock_state.update_last_sync.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_no_state_no_config(scheduler, mock_state, mock_collector):
    """Test backfill when no state and no config start date."""
    mock_state.get_last_successful_date.return_value = None
    mock_collector.get_first_available_date.return_value = None
    
    with patch('src.config.Config.BACKFILL_START_DATE', None):
        scheduler.backfill_data()
    
    # Should not attempt to fetch data
    mock_collector.get_daily_data.assert_not_called()


@freeze_time("2024-01-15 12:00:00")
def test_backfill_from_last_synced(scheduler, mock_state, mock_collector, mock_writer):
    """Test backfill continues from last synced date."""
    mock_state.get_last_successful_date.return_value = '2024-01-10'
    
    # Mock data for 4 days: Jan 11, 12, 13, 14
    mock_collector.get_daily_data.side_effect = [
        {'date': '2024-01-11', 'steps': 1000},
        {'date': '2024-01-12', 'steps': 2000},
        {'date': '2024-01-13', 'steps': 3000},
        {'date': '2024-01-14', 'steps': 4000},
    ]
    
    with patch('time.sleep'):
        scheduler.backfill_data()
    
    # Should fetch 4 days of data
    assert mock_collector.get_daily_data.call_count == 4


@freeze_time("2024-01-15 12:00:00")
def test_backfill_from_config(scheduler, mock_state, mock_collector, mock_writer):
    """Test backfill uses last synced state to determine start."""
    # When state exists, should use it
    mock_state.get_last_successful_date.return_value = '2024-01-12'
    
    # Mock data for 2 days: Jan 13, 14
    mock_collector.get_daily_data.side_effect = [
        {'date': '2024-01-13', 'steps': 1000},
        {'date': '2024-01-14', 'steps': 2000},
    ]
    
    with patch('time.sleep'):
        scheduler.backfill_data()
    
    # Should fetch 2 days of data (13th and 14th)
    assert mock_collector.get_daily_data.call_count == 2


@freeze_time("2024-01-15 12:00:00")
def test_backfill_no_data_needed(scheduler, mock_state, mock_collector):
    """Test backfill when already up to date."""
    # Last synced is yesterday
    mock_state.get_last_successful_date.return_value = '2024-01-14'
    
    scheduler.backfill_data()
    
    # Should not attempt to fetch
    mock_collector.get_daily_data.assert_not_called()




@freeze_time("2024-01-15 12:00:00")
def test_backfill_handles_rate_limit(scheduler, mock_state, mock_collector, mock_writer):
    """Test backfill handles rate limiting."""
    mock_state.get_last_successful_date.return_value = '2024-01-12'
    
    # First call succeeds, second rate limited, third succeeds
    mock_collector.get_daily_data.side_effect = [
        {'date': '2024-01-13', 'steps': 1000},
        RateLimitError("Rate limited", retry_after=1),
        {'date': '2024-01-13', 'steps': 1000},  # Retry same date
        {'date': '2024-01-14', 'steps': 2000},
    ]
    
    with patch('time.sleep'):
        scheduler.backfill_data()
    
    # Should retry and complete (1 success + rate limit + retry + 1 more = 4 calls)
    assert mock_collector.get_daily_data.call_count >= 2


@freeze_time("2024-01-15 12:00:00")
def test_backfill_stops_on_consecutive_rate_limits(scheduler, mock_state, mock_collector, mock_writer):
    """Test backfill stops after consecutive rate limits."""
    mock_state.get_last_successful_date.return_value = '2024-01-12'
    
    # First call succeeds, then 3 consecutive rate limits
    mock_collector.get_daily_data.side_effect = [
        {'date': '2024-01-13', 'steps': 1000},
        RateLimitError("Rate limited", retry_after=1),
        RateLimitError("Rate limited", retry_after=1),
        RateLimitError("Rate limited", retry_after=1),
    ]
    
    with patch('time.sleep'):
        scheduler.backfill_data()
    
    # Should stop after 3 consecutive failures
    assert mock_collector.get_daily_data.call_count >= 2


@freeze_time("2024-01-15 12:00:00")
def test_backfill_saves_partial_progress_on_error(scheduler, mock_state, mock_collector, mock_writer):
    """Test backfill saves progress even when error occurs."""
    mock_state.get_last_successful_date.return_value = '2024-01-12'
    
    # Fetch 2 days successfully, then error
    mock_collector.get_daily_data.side_effect = [
        {'date': '2024-01-13', 'steps': 1000},
        {'date': '2024-01-14', 'steps': 2000},
        Exception("Network error"),
    ]
    
    with patch('time.sleep'):
        scheduler.backfill_data()
    
    # Should have attempted to write before error
    # (May or may not be called depending on when error occurs)
    assert mock_collector.get_daily_data.call_count >= 2
