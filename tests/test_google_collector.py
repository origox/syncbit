"""Tests for the Google Health API collector.

Response bodies mirror shapes captured from a live account with
`scripts/probe_google_health.py`, trimmed to the fields the collector reads.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from src.collector import HealthDataCollector, RateLimitError
from src.config import Config
from src.google_collector import GoogleHealthCollector

BASE = f"{Config.GOOGLE_API_BASE_URL}/users/me/dataTypes"
DAY = datetime(2026, 11, 19)


def civil(day: int) -> dict:
    return {"date": {"year": 2026, "month": 11, "day": day}, "time": {}}


def rollup(day: int, key: str, value: dict) -> dict:
    return {"civilStartTime": civil(day), "civilEndTime": civil(day + 1), key: value}


def daily(key: str, day: int, **fields) -> dict:
    return {
        "dataSource": {"platform": "FITBIT"},
        key: {"date": {"year": 2026, "month": 11, "day": day}, **fields},
    }


SLEEP_SESSION = {
    "sleep": {
        # 21:31Z + 1h offset on the 19th, ending 06:39Z + 1h on the 20th.
        "interval": {
            "startTime": "2026-11-19T21:31:00Z",
            "startUtcOffset": "3600s",
            "endTime": "2026-11-20T06:39:00Z",
            "endUtcOffset": "3600s",
        },
        "type": "STAGES",
        "summary": {
            "minutesAsleep": "478",
            "stagesSummary": [
                {"type": "AWAKE", "minutes": "70"},
                {"type": "LIGHT", "minutes": "301"},
                {"type": "DEEP", "minutes": "65"},
                {"type": "REM", "minutes": "112"},
            ],
        },
    }
}


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """Disable the client-side request spacing; tested explicitly below."""
    monkeypatch.setattr(Config, "GOOGLE_MAX_REQUESTS_PER_MINUTE", 0)


@pytest.fixture
def auth():
    mock = MagicMock()
    mock.get_valid_token.return_value = "token"
    return mock


@pytest.fixture
def collector(auth):
    return GoogleHealthCollector(auth)


@pytest.fixture
def all_metrics_enabled(monkeypatch):
    for toggle in (
        "COLLECT_SLEEP",
        "COLLECT_SPO2",
        "COLLECT_BREATHING_RATE",
        "COLLECT_HRV",
        "COLLECT_CARDIO_FITNESS",
        "COLLECT_TEMPERATURE",
    ):
        monkeypatch.setattr(Config, toggle, True)


def add_rollup(data_type: str, points: list[dict]) -> None:
    responses.add(
        responses.POST,
        f"{BASE}/{data_type}/dataPoints:dailyRollUp",
        json={"rollupDataPoints": points},
    )


def add_list(data_type: str, points: list[dict], status: int = 200) -> None:
    responses.add(
        responses.GET,
        f"{BASE}/{data_type}/dataPoints",
        json={"dataPoints": points} if status == 200 else {"error": {"code": status}},
        status=status,
    )


def register_day(day: int = 19, sleep: list[dict] | None = None) -> None:
    """Register every endpoint a one-day collection hits."""
    add_rollup("steps", [rollup(day, "steps", {"countSum": "18647"})])
    add_rollup("distance", [rollup(day, "distance", {"millimetersSum": "14755900"})])
    add_rollup("total-calories", [rollup(day, "totalCalories", {"kcalSum": 3764.9})])
    add_rollup(
        "active-minutes",
        [
            rollup(
                day,
                "activeMinutes",
                {
                    "activeMinutesRollupByActivityLevel": [
                        {"activityLevel": "LIGHT", "activeMinutesSum": "114"},
                        {"activityLevel": "MODERATE", "activeMinutesSum": "9"},
                        {"activityLevel": "VIGOROUS", "activeMinutesSum": "135"},
                    ]
                },
            )
        ],
    )
    add_rollup(
        "time-in-heart-rate-zone",
        [
            rollup(
                day,
                "timeInHeartRateZone",
                {
                    "timeInHeartRateZones": [
                        {"heartRateZone": "LIGHT", "duration": "76920s"},
                        {"heartRateZone": "MODERATE", "duration": "5340s"},
                        {"heartRateZone": "VIGOROUS", "duration": "1980s"},
                        {"heartRateZone": "PEAK", "duration": "120s"},
                    ]
                },
            )
        ],
    )
    add_rollup(
        "calories-in-heart-rate-zone",
        [
            rollup(
                day,
                "caloriesInHeartRateZone",
                {
                    "caloriesInHeartRateZones": [
                        {"heartRateZone": "LIGHT", "kcal": 2375.4},
                        {"heartRateZone": "PEAK", "kcal": 16.6},
                    ]
                },
            )
        ],
    )
    add_list("daily-resting-heart-rate", [daily("dailyRestingHeartRate", day, beatsPerMinute="59")])
    add_list(
        "daily-oxygen-saturation",
        [
            daily(
                "dailyOxygenSaturation",
                day,
                averagePercentage=95.7,
                lowerBoundPercentage=93.1,
                upperBoundPercentage=98.7,
            )
        ],
    )
    add_list("daily-respiratory-rate", [daily("dailyRespiratoryRate", day, breathsPerMinute=16.6)])
    add_list(
        "daily-heart-rate-variability",
        [daily("dailyHeartRateVariability", day, averageHeartRateVariabilityMilliseconds=28.85)],
    )
    add_list("daily-vo2-max", [daily("dailyVo2Max", day, vo2Max=41.5)])
    add_list(
        "daily-sleep-temperature-derivations",
        [
            daily(
                "dailySleepTemperatureDerivations",
                day,
                nightlyTemperatureCelsius=34.5,
                baselineTemperatureCelsius=34.0,
            )
        ],
    )
    add_list("sleep", sleep if sleep is not None else [])


def requests_to(path_fragment: str) -> list:
    return [c.request for c in responses.calls if path_fragment in c.request.url]


# --- Contract ---


def test_implements_collector_protocol(collector):
    assert isinstance(collector, HealthDataCollector)


# --- Daily data ---


@responses.activate
def test_daily_record_maps_core_metrics(collector, all_metrics_enabled):
    register_day()

    record = collector.get_daily_data(DAY)

    assert record["date"] == "2026-11-19"
    assert record["timestamp"] == int(DAY.timestamp())
    assert record["steps"] == 18647
    assert record["distance"] == pytest.approx(14.7559)  # millimetres -> km
    assert record["calories"] == pytest.approx(3764.9)
    assert record["resting_heart_rate"] == 59.0


@responses.activate
def test_activity_levels_map_to_fitbit_names_without_sedentary(collector, all_metrics_enabled):
    register_day()

    record = collector.get_daily_data(DAY)

    assert record["active_minutes"] == {
        "lightly_active": 114,
        "fairly_active": 9,
        "very_active": 135,
    }


@responses.activate
def test_heart_rate_zones_keep_fitbit_labels(collector, all_metrics_enabled):
    """Zone labels must match the Fitbit era so dashboards stay continuous."""
    register_day()

    zones = {z["zone"]: z for z in collector.get_daily_data(DAY)["heart_rate_zones"]}

    assert set(zones) == {"out_of_range", "fat_burn", "cardio", "peak"}
    assert zones["fat_burn"]["minutes"] == 89.0
    assert zones["cardio"]["minutes"] == 33.0
    assert zones["out_of_range"]["calories"] == pytest.approx(2375.4)
    # Calories reported for only some zones are not invented for the rest.
    assert "calories" not in zones["cardio"]


@responses.activate
def test_optional_daily_summaries_are_mapped(collector, all_metrics_enabled):
    register_day()

    record = collector.get_daily_data(DAY)

    assert record["spo2"] == {"avg": 95.7, "min": 93.1, "max": 98.7}
    assert record["breathing_rate"] == 16.6
    assert record["hrv_rmssd_ms"] == 28.85
    assert record["vo2_max"] == 41.5
    # Fitbit reported temperature relative to baseline; Google gives both.
    assert record["skin_temp_relative_c"] == pytest.approx(0.5)


@responses.activate
def test_daily_summary_filter_uses_snake_case_prefix(collector, all_metrics_enabled):
    """The documented camelCase prefix is rejected by the live API."""
    register_day()

    collector.get_daily_data(DAY)

    (request,) = requests_to("daily-resting-heart-rate")
    filter_expr = parse_qs(urlparse(request.url).query)["filter"][0]
    assert filter_expr == (
        'daily_resting_heart_rate.date >= "2026-11-19" '
        'AND daily_resting_heart_rate.date < "2026-11-20"'
    )


@responses.activate
def test_sleep_is_attributed_to_the_wake_up_date(collector, all_metrics_enabled):
    register_day(day=20, sleep=[SLEEP_SESSION])

    record = collector.get_daily_data(datetime(2026, 11, 20))

    assert record["sleep"] == {
        "total_minutes": 478.0,
        "stages": {"wake": 70.0, "light": 301.0, "deep": 65.0, "rem": 112.0},
    }


@responses.activate
def test_sleep_sessions_ending_on_one_date_are_summed(collector, all_metrics_enabled):
    nap = json.loads(json.dumps(SLEEP_SESSION))
    nap["sleep"]["interval"]["endTime"] = "2026-11-20T14:00:00Z"
    nap["sleep"]["summary"] = {
        "minutesAsleep": "30",
        "stagesSummary": [{"type": "LIGHT", "minutes": "30"}],
    }
    register_day(day=20, sleep=[SLEEP_SESSION, nap])

    sleep = collector.get_daily_data(datetime(2026, 11, 20))["sleep"]

    assert sleep["total_minutes"] == 508.0
    assert sleep["stages"]["light"] == 331.0


@responses.activate
def test_disabled_metrics_are_not_requested(collector, monkeypatch, all_metrics_enabled):
    monkeypatch.setattr(Config, "COLLECT_SLEEP", False)
    monkeypatch.setattr(Config, "COLLECT_SPO2", False)
    register_day()

    record = collector.get_daily_data(DAY)

    assert "sleep" not in record and "spo2" not in record
    assert not requests_to("/sleep/dataPoints")
    assert not requests_to("daily-oxygen-saturation")


@responses.activate
def test_failed_optional_metric_does_not_lose_the_day(collector, monkeypatch, all_metrics_enabled):
    monkeypatch.setattr("src.google_collector.time.sleep", lambda _: None)
    register_day()
    responses.replace(
        responses.GET, f"{BASE}/daily-oxygen-saturation/dataPoints", json={}, status=500
    )

    record = collector.get_daily_data(DAY)

    assert record["steps"] == 18647
    assert "spo2" not in record


@responses.activate
def test_day_without_data_yields_zeroed_core_metrics(collector, all_metrics_enabled):
    for data_type in (
        "steps",
        "distance",
        "total-calories",
        "active-minutes",
        "time-in-heart-rate-zone",
        "calories-in-heart-rate-zone",
    ):
        add_rollup(data_type, [])
    for data_type in (
        "daily-resting-heart-rate",
        "daily-oxygen-saturation",
        "daily-respiratory-rate",
        "daily-heart-rate-variability",
        "daily-vo2-max",
        "daily-sleep-temperature-derivations",
        "sleep",
    ):
        add_list(data_type, [])

    record = collector.get_daily_data(DAY)

    assert record["steps"] == 0
    assert record["distance"] == 0.0
    assert record["heart_rate_zones"] == []
    assert "resting_heart_rate" not in record


# --- Ranges ---


@responses.activate
def test_historical_range_is_chunked_to_fourteen_days(collector, monkeypatch):
    for toggle in (
        "COLLECT_SLEEP",
        "COLLECT_SPO2",
        "COLLECT_BREATHING_RATE",
        "COLLECT_HRV",
        "COLLECT_CARDIO_FITNESS",
        "COLLECT_TEMPERATURE",
    ):
        monkeypatch.setattr(Config, toggle, False)
    for data_type in (
        "steps",
        "distance",
        "total-calories",
        "active-minutes",
        "time-in-heart-rate-zone",
        "calories-in-heart-rate-zone",
    ):
        add_rollup(data_type, [])
    add_list("daily-resting-heart-rate", [])

    records = collector.get_historical_data(datetime(2026, 10, 1), datetime(2026, 10, 20))

    assert [r["date"] for r in records][0] == "2026-10-01"
    assert [r["date"] for r in records][-1] == "2026-10-20"
    assert len(records) == 20

    def october(day: int) -> dict:
        return {"date": {"year": 2026, "month": 10, "day": day}}

    bodies = [json.loads(r.body) for r in requests_to("/steps/dataPoints:dailyRollUp")]
    # Two chunks: Oct 1-14 and Oct 15-20, each with an exclusive end date.
    assert [b["range"] for b in bodies] == [
        {"start": october(1), "end": october(15)},
        {"start": october(15), "end": october(21)},
    ]


@responses.activate
def test_list_follows_page_tokens(collector, all_metrics_enabled):
    register_day()
    responses.replace(
        responses.GET,
        f"{BASE}/daily-resting-heart-rate/dataPoints",
        json={"dataPoints": [], "nextPageToken": "page-2"},
    )
    responses.add(
        responses.GET,
        f"{BASE}/daily-resting-heart-rate/dataPoints",
        json={"dataPoints": [daily("dailyRestingHeartRate", 19, beatsPerMinute="61")]},
    )

    record = collector.get_daily_data(DAY)

    assert record["resting_heart_rate"] == 61.0
    second = requests_to("daily-resting-heart-rate")[1]
    assert parse_qs(urlparse(second.url).query)["pageToken"] == ["page-2"]


# --- Transport ---


@responses.activate
def test_rate_limit_raises_with_retry_after(collector):
    responses.add(
        responses.POST,
        f"{BASE}/steps/dataPoints:dailyRollUp",
        status=429,
        headers={"Retry-After": "42"},
        json={},
    )

    with pytest.raises(RateLimitError) as exc_info:
        collector.get_daily_data(DAY)

    assert exc_info.value.retry_after == 42


@responses.activate
def test_rate_limit_without_header_defaults_to_a_minute(collector):
    responses.add(responses.POST, f"{BASE}/steps/dataPoints:dailyRollUp", status=429, json={})

    with pytest.raises(RateLimitError) as exc_info:
        collector.get_daily_data(DAY)

    assert exc_info.value.retry_after == 60


@responses.activate
def test_rate_limit_on_optional_metric_still_propagates(collector, all_metrics_enabled):
    """Optional metrics swallow errors, but never the scheduler's backoff signal."""
    register_day()
    responses.replace(responses.GET, f"{BASE}/daily-oxygen-saturation/dataPoints", status=429)

    with pytest.raises(RateLimitError):
        collector.get_daily_data(DAY)


@responses.activate
def test_unauthorized_refreshes_once_and_retries(collector, auth, all_metrics_enabled):
    register_day()
    responses.replace(responses.POST, f"{BASE}/steps/dataPoints:dailyRollUp", status=401)
    responses.add(
        responses.POST,
        f"{BASE}/steps/dataPoints:dailyRollUp",
        json={"rollupDataPoints": [rollup(19, "steps", {"countSum": "5"})]},
    )

    record = collector.get_daily_data(DAY)

    auth.refresh_access_token.assert_called_once()
    assert record["steps"] == 5


@responses.activate
def test_transient_errors_are_retried(collector, monkeypatch, all_metrics_enabled):
    monkeypatch.setattr("src.google_collector.time.sleep", lambda _: None)
    register_day()
    responses.replace(responses.POST, f"{BASE}/steps/dataPoints:dailyRollUp", status=503)
    responses.add(
        responses.POST,
        f"{BASE}/steps/dataPoints:dailyRollUp",
        json={"rollupDataPoints": [rollup(19, "steps", {"countSum": "7"})]},
    )

    assert collector.get_daily_data(DAY)["steps"] == 7


# --- Profile, intraday, devices ---


@responses.activate
def test_first_available_date_is_membership_start(collector):
    responses.add(
        responses.GET,
        f"{Config.GOOGLE_API_BASE_URL}/users/me/profile",
        json={"membershipStartDate": {"year": 2015, "month": 8, "day": 28}},
    )

    assert collector.get_first_available_date() == datetime(2015, 8, 28)


@responses.activate
def test_intraday_uses_rollup_windows_in_local_time(collector, monkeypatch):
    monkeypatch.setattr(Config, "ENABLE_INTRADAY_COLLECTION", True)
    monkeypatch.setattr(Config, "INTRADAY_RESOURCES", ["heart_rate", "distance"])
    monkeypatch.setattr(Config, "INTRADAY_HEART_RATE_DETAIL", "1min")
    monkeypatch.setattr(Config, "INTRADAY_DETAIL_LEVEL", "5min")

    # The API answers in UTC; local midnight must come back as "00:00:00".
    local_midnight_utc = (
        datetime(2026, 11, 19).astimezone().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    responses.add(
        responses.POST,
        f"{BASE}/heart-rate/dataPoints:rollUp",
        json={
            "rollupDataPoints": [
                {"startTime": local_midnight_utc, "heartRate": {"beatsPerMinuteAvg": 62.5}}
            ]
        },
    )
    responses.add(
        responses.POST,
        f"{BASE}/distance/dataPoints:rollUp",
        json={"rollupDataPoints": []},
    )

    record = collector.get_intraday_data(DAY)

    assert record["resources"]["heart_rate"]["dataset"] == [{"time": "00:00:00", "value": 62.5}]
    hr_body = json.loads(requests_to("heart-rate/dataPoints:rollUp")[0].body)
    assert hr_body["windowSize"] == "60s"
    distance_body = json.loads(requests_to("distance/dataPoints:rollUp")[0].body)
    assert distance_body["windowSize"] == "300s"


def test_intraday_disabled_makes_no_requests(collector, monkeypatch):
    monkeypatch.setattr(Config, "ENABLE_INTRADAY_COLLECTION", False)

    assert collector.get_intraday_data(DAY)["resources"] == {}


def test_throttle_spaces_requests_to_the_quota(collector, monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_MAX_REQUESTS_PER_MINUTE", 300)
    clock = {"now": 100.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr("src.google_collector.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("src.google_collector.time.sleep", fake_sleep)

    collector._throttle()  # first request: no wait
    clock["now"] += 0.05
    collector._throttle()  # 0.05s later: waits the remaining 0.15s of 0.2s
    clock["now"] += 1.0
    collector._throttle()  # well past the interval: no wait

    assert sleeps == [pytest.approx(0.15)]


@responses.activate
def test_devices_without_settings_scope_return_empty(collector):
    responses.add(
        responses.GET,
        f"{Config.GOOGLE_API_BASE_URL}/users/me/pairedDevices",
        status=403,
        json={"error": {"code": 403}},
    )

    assert collector.get_device_info() == []
