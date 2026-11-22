"""Provider-neutral collector interface.

The scheduler drives data collection exclusively through `HealthDataCollector`,
so swapping the upstream health API means supplying a new implementation of
this protocol rather than editing the orchestration layer.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from .health_types import DailyRecord, DeviceInfo, IntradayRecord


class RateLimitError(Exception):
    """Raised when the upstream API rate limits a request.

    Only `retry_after` is required. `quota_reset` and `remaining` come from
    provider-specific response headers and are absent for providers that do not
    report them, so consumers must treat them as optional.
    """

    def __init__(
        self,
        message: str,
        retry_after: int,
        quota_reset: int | None = None,
        remaining: int | None = None,
    ):
        """Initialize with retry time and quota info.

        Args:
            message: Error message
            retry_after: Seconds to wait before retry
            quota_reset: Seconds until the quota window resets, if reported
            remaining: Requests left in the current quota window, if reported
        """
        super().__init__(message)
        self.retry_after = retry_after
        self.quota_reset = quota_reset
        self.remaining = remaining


@runtime_checkable
class HealthDataCollector(Protocol):
    """Fetches normalized health data from an upstream provider.

    Implementations raise `RateLimitError` when throttled; all other transport
    errors propagate. Methods return empty/neutral values rather than raising
    when data is merely unavailable for a date.
    """

    def get_daily_data(self, date: datetime) -> DailyRecord:
        """Collect one calendar day of data."""
        ...

    def get_historical_data(self, start_date: datetime, end_date: datetime) -> list[DailyRecord]:
        """Collect a date range, inclusive of both endpoints."""
        ...

    def get_first_available_date(self) -> datetime:
        """Return the earliest date this account has data for."""
        ...

    def get_intraday_data(self, date: datetime) -> IntradayRecord:
        """Collect within-day samples for the configured resources."""
        ...

    def get_device_info(self) -> list[DeviceInfo]:
        """Return the state of each paired device."""
        ...
