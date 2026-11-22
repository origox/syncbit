"""Tests for the Fitbit -> normalized health data translation.

These pin the contract that `victoria_writer` consumes. Any new provider
collector must produce these same shapes, so a break here means the writer and
the new collector have drifted apart.
"""

from datetime import UTC, datetime

from src.fitbit_collector import FitbitCollector


def test_normalize_heart_rate_zones_snake_cases_names():
    zones = FitbitCollector._normalize_heart_rate_zones(
        [
            {"name": "Out of Range", "minutes": 720, "caloriesOut": 100.0},
            {"name": "Fat Burn", "minutes": 180, "caloriesOut": 200.5},
        ]
    )

    assert [z["zone"] for z in zones] == ["out_of_range", "fat_burn"]
    assert zones[0]["minutes"] == 720
    assert zones[0]["calories"] == 100.0


def test_normalize_heart_rate_zones_keeps_minutes_and_calories_independent():
    """A zone may report one without the other; neither should be invented."""
    zones = FitbitCollector._normalize_heart_rate_zones(
        [
            {"name": "Cardio", "minutes": 20},
            {"name": "Peak", "caloriesOut": 30.0},
            {"minutes": 5},
        ]
    )

    assert "calories" not in zones[0]
    assert "minutes" not in zones[1]
    assert zones[2]["zone"] == "unknown"


def test_normalize_sleep_extracts_totals_and_stages():
    sleep = FitbitCollector._normalize_sleep(
        {
            "sleep": [{"logId": 1}],
            "summary": {
                "totalMinutesAsleep": 432,
                "totalTimeInBed": 465,
                "stages": {"deep": 80, "light": 250, "rem": 90, "wake": 45},
            },
        }
    )

    assert sleep["total_minutes"] == 432
    assert sleep["stages"] == {"deep": 80, "light": 250, "rem": 90, "wake": 45}


def test_normalize_sleep_drops_non_numeric_stages():
    """Fitbit mixes non-numeric keys into `stages`; they are not metrics."""
    sleep = FitbitCollector._normalize_sleep(
        {"summary": {"totalMinutesAsleep": 400, "stages": {"deep": 80, "note": "partial"}}}
    )

    assert sleep["stages"] == {"deep": 80}


def test_normalize_sleep_handles_missing_data():
    assert FitbitCollector._normalize_sleep({}) == {}
    assert FitbitCollector._normalize_sleep(None) == {}


def test_normalize_spo2_keeps_only_present_bounds():
    assert FitbitCollector._normalize_spo2({"value": {"avg": 96.5, "min": 92.0}}) == {
        "avg": 96.5,
        "min": 92.0,
    }
    assert FitbitCollector._normalize_spo2({}) == {}


def test_first_nested_walks_the_value_envelope():
    assert FitbitCollector._first_nested([{"value": {"rmssd": 42.1}}], "value", "rmssd") == 42.1
    assert FitbitCollector._first_nested([{"vo2Max": 47.3}], "vo2Max") == 47.3


def test_first_nested_skips_entries_missing_the_key():
    entries = [{"value": {}}, {"value": {"breathingRate": 14.5}}]

    assert FitbitCollector._first_nested(entries, "value", "breathingRate") == 14.5


def test_first_nested_returns_none_when_absent():
    assert FitbitCollector._first_nested([], "value", "rmssd") is None
    assert FitbitCollector._first_nested(None, "value", "rmssd") is None
    assert FitbitCollector._first_nested([{"other": 1}], "value", "rmssd") is None


def test_first_nested_preserves_zero():
    """Zero is a real reading and must not be dropped as falsy."""
    assert (
        FitbitCollector._first_nested(
            [{"value": {"nightlyRelative": 0.0}}], "value", "nightlyRelative"
        )
        == 0.0
    )


def test_normalize_device_prefers_exact_battery_level():
    device = FitbitCollector._normalize_device(
        {
            "id": "12345",
            "deviceVersion": "Charge 6",
            "batteryLevel": 85,
            "battery": "High",
            "lastSyncTime": "2024-01-15T10:30:00.000Z",
        }
    )

    assert device["device_id"] == "12345"
    assert device["device_type"] == "Charge 6"
    assert device["battery_percent"] == 85
    assert device["last_sync"] == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)


def test_normalize_device_falls_back_to_text_battery():
    device = FitbitCollector._normalize_device(
        {"id": "67890", "deviceVersion": "Inspire 3", "battery": "Medium"}
    )

    assert device["battery_percent"] == 50.0
    assert "last_sync" not in device


def test_normalize_device_handles_unparseable_sync_time():
    device = FitbitCollector._normalize_device(
        {"id": "1", "deviceVersion": "X", "lastSyncTime": "not-a-timestamp"}
    )

    assert "last_sync" not in device


def test_normalize_device_defaults_missing_identifiers():
    device = FitbitCollector._normalize_device({})

    assert device["device_id"] == "unknown"
    assert device["device_type"] == "unknown"


def test_parse_battery_level_percentage_string():
    assert FitbitCollector._parse_battery_level("85%") == 85.0
    assert FitbitCollector._parse_battery_level(" 42 % ") == 42.0


def test_parse_battery_level_text_levels():
    assert FitbitCollector._parse_battery_level("High") == 80.0
    assert FitbitCollector._parse_battery_level("empty") == 0.0
    assert FitbitCollector._parse_battery_level("nonsense") is None
