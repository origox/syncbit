"""Provider-neutral health data types.

These types are the contract between a collector (which knows how to talk to a
specific upstream health API) and the Victoria Metrics writer (which knows
nothing about any provider). A collector is responsible for translating its
provider's wire format into these shapes; the writer only maps these keys onto
metric names.

Keeping this contract provider-neutral is what allows the upstream API to be
swapped without touching the writer, the scheduler or the sync state.
"""

from datetime import datetime
from typing import TypedDict


class ActiveMinutes(TypedDict, total=False):
    """Minutes spent at each activity level during a day.

    Every level is optional: Google Health reports no sedentary minutes.
    """

    sedentary: int
    lightly_active: int
    fairly_active: int
    very_active: int


class HeartRateZone(TypedDict, total=False):
    """Time and energy spent in a single heart rate zone.

    `zone` is a normalized lower_snake_case name (out_of_range, fat_burn,
    cardio, peak). `minutes` and `calories` are independently optional: a
    provider may report one without the other.
    """

    zone: str
    minutes: float
    calories: float


class SleepStages(TypedDict, total=False):
    """Minutes spent in each sleep stage."""

    deep: float
    light: float
    rem: float
    wake: float


class Sleep(TypedDict, total=False):
    """Sleep totals and stage breakdown for a night."""

    total_minutes: float
    stages: SleepStages


class SpO2(TypedDict, total=False):
    """Blood oxygen saturation percentages for a day."""

    avg: float
    min: float
    max: float


class DailyRecord(TypedDict, total=False):
    """One calendar day of normalized health data.

    Only `date` and `timestamp` are guaranteed. Every metric is optional, since
    availability varies by device, by provider and by day (sleep, SpO2 and HRV
    in particular are frequently missing).

    `timestamp` is unix seconds and is the timestamp applied to every metric
    derived from this record.
    """

    date: str  # YYYY-MM-DD
    timestamp: int  # unix seconds
    steps: int
    distance: float  # kilometers
    calories: float
    active_minutes: ActiveMinutes
    resting_heart_rate: float
    heart_rate_zones: list[HeartRateZone]
    sleep: Sleep
    spo2: SpO2
    breathing_rate: float  # breaths per minute
    hrv_rmssd_ms: float
    vo2_max: float
    skin_temp_relative_c: float


class DeviceInfo(TypedDict, total=False):
    """State of a single paired device.

    `battery_percent` is normalized to a 0-100 float even when the provider
    reports a coarse text level.
    """

    device_id: str
    device_type: str
    battery_percent: float
    last_sync: datetime


class IntradayDataset(TypedDict):
    """A series of same-day samples for one resource.

    Each point is `{"time": "HH:MM:SS", "value": <number>}`, relative to the
    record's date.
    """

    dataset: list[dict]


class IntradayRecord(TypedDict):
    """Intraday samples for one calendar day, keyed by resource name."""

    date: str  # YYYY-MM-DD
    resources: dict[str, IntradayDataset]
