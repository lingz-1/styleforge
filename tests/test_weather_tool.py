"""Offline tests for typed weather facts and context routing."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from styleforge.orchestration.context_router import ContextRouter, register_weather_tool
from styleforge.tools.registry import ToolRegistry
from styleforge.tools.weather import (
    ContextRequirements,
    OpenMeteoProvider,
    WeatherContextRequirement,
    WeatherTool,
    WeatherToolInput,
)

_TODAY = date(2026, 8, 10)

# Per-day values; the first day matches the frozen V1 expectations.
_DAILY_FIRST = {
    "temperature_2m_max": 32.0,
    "temperature_2m_min": 25.0,
    "apparent_temperature_max": 35.0,
    "apparent_temperature_min": 27.0,
    "precipitation_probability_max": 70,
    "weather_code": 61,
    "wind_speed_10m_max": 18.0,
}
_DAILY_LATER = {
    "temperature_2m_max": 31.0,
    "temperature_2m_min": 24.0,
    "apparent_temperature_max": 34.0,
    "apparent_temperature_min": 26.0,
    "precipitation_probability_max": 10,
    "weather_code": 0,
    "wind_speed_10m_max": 12.0,
}


def _range_dates(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    values: list[str] = []
    while current <= stop:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _transport(url: str, timeout: float) -> dict:
    assert timeout == 3.0
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "geocoding-api" in parsed.netloc:
        assert query["name"] == ["上海"]
        return {
            "results": [
                {
                    "name": "上海",
                    "country": "中国",
                    "admin1": "上海",
                    "latitude": 31.23,
                    "longitude": 121.47,
                    "timezone": "Asia/Shanghai",
                }
            ]
        }
    if "reverse-geocode-client" in parsed.path:
        assert query["latitude"] == ["31.23"]
        assert query["longitude"] == ["121.47"]
        return {
            "city": "上海市",
            "countryName": "中国",
            "principalSubdivision": "上海市",
            "timezone": "Asia/Shanghai",
        }
    start = query["start_date"][0]
    end = query["end_date"][0]
    dates = _range_dates(start, end)
    daily = {**{key: [] for key in _DAILY_FIRST}, "time": []}
    for index, day in enumerate(dates):
        values = _DAILY_FIRST if index == 0 else _DAILY_LATER
        for key, value in values.items():
            daily[key].append(value)
        daily["time"].append(day)
    hourly_time: list[str] = []
    hourly_humidity: list[float] = []
    for day in dates:
        hourly_time.append(f"{day}T00:00")
        hourly_humidity.append(80.0 if day == dates[0] else 70.0)
        hourly_time.append(f"{day}T01:00")
        hourly_humidity.append(84.0 if day == dates[0] else 72.0)
    return {
        "timezone": "Asia/Shanghai",
        "daily": daily,
        "hourly": {"time": hourly_time, "relative_humidity_2m": hourly_humidity},
    }


def _tool() -> WeatherTool:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport)
    return WeatherTool(provider, today_provider=lambda: _TODAY)


def _router(*, default_location: str = "") -> ContextRouter:
    registry = ToolRegistry()
    register_weather_tool(registry, _tool())
    return ContextRouter(
        registry,
        default_location=default_location,
        today_provider=lambda: _TODAY,
        now_provider=lambda: datetime(2026, 8, 10, 8, 0, 0, tzinfo=timezone.utc),
    )


def test_weather_tool_returns_facts_without_styling_advice() -> None:
    facts = _tool()(WeatherToolInput(location="上海", date="明天"))

    assert facts.status == "available"
    assert facts.forecast_date == "2026-08-11"
    assert facts.temperature_min_c == 25.0
    assert facts.temperature_max_c == 32.0
    assert facts.feels_like_c == 31.0
    assert facts.precipitation_probability_percent == 70
    assert facts.humidity_percent == 82.0
    assert facts.condition == "小雨"
    assert "wear" not in facts.model_dump_json().lower()
    assert "穿" not in facts.model_dump_json()


def test_weather_tool_converts_invalid_date_to_unavailable_fact() -> None:
    facts = _tool()(WeatherToolInput(location="上海", date="下个月"))

    assert facts.status == "unavailable"
    assert facts.error_code == "invalid_date"


def test_weather_tool_returns_range_window_with_daily_facts() -> None:
    facts = _tool()(
        WeatherToolInput(
            location="上海",
            start_date="2026-08-11",
            end_date="2026-08-13",
        )
    )

    assert facts.status == "available"
    assert facts.start_date == "2026-08-11"
    assert facts.end_date == "2026-08-13"
    assert facts.default_applied is False
    assert [day.date for day in facts.days] == [
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
    ]
    assert facts.days[0].condition == "小雨"
    assert facts.days[1].condition == "晴朗"
    # The single-day header fields mirror the first day.
    assert facts.temperature_min_c == facts.days[0].temperature_min_c
    assert facts.condition == facts.days[0].condition


def test_context_router_uses_default_location_and_keeps_trace() -> None:
    registry = ToolRegistry()
    register_weather_tool(registry, _tool())
    router = ContextRouter(
        registry,
        default_location="上海",
        today_provider=lambda: _TODAY,
    )
    requirements = ContextRequirements(
        weather=WeatherContextRequirement(
            needed=True,
            date="明天",
            reason="户外活动受天气影响",
        )
    )

    environment, trace = router.resolve(requirements)

    assert environment["weather"]["status"] == "available"
    assert environment["weather"]["requested_location"] == "上海"
    assert trace[0]["tool"] == "weather.get_weather"
    assert trace[0]["arguments"] == {"location": "上海", "date": "明天"}


def test_context_router_marks_missing_location_without_calling_network() -> None:
    router = ContextRouter(
        ToolRegistry(),
        today_provider=lambda: _TODAY,
    )

    environment, trace = router.resolve(
        {"weather": {"needed": True, "date": "明天"}}
    )

    assert environment["weather"]["status"] == "unavailable"
    assert environment["weather"]["error_code"] == "location_required"
    assert trace[0]["status"] == "unavailable"


def test_context_router_applies_default_3_day_window() -> None:
    router = _router(default_location="上海")

    environment, trace = router.resolve(
        {"weather": {"needed": True, "reason": "查天气"}}
    )

    assert environment["weather"]["status"] == "available"
    assert environment["weather"]["start_date"] == "2026-08-10"
    assert environment["weather"]["end_date"] == "2026-08-12"
    assert environment["weather"]["default_applied"] is True
    assert len(environment["weather"]["days"]) == 3
    time_context = environment["resolved_time_context"]
    assert time_context["status"] == "resolved"
    assert time_context["default_applied"] is True
    assert time_context["precision"] == "near_3_days"
    assert trace[0]["arguments"] == {
        "location": "上海",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
    }


def test_context_router_unsupported_date_expression() -> None:
    router = _router(default_location="上海")

    environment, trace = router.resolve(
        {"weather": {"needed": True, "date": "后天", "reason": "查天气"}}
    )

    assert environment["weather"]["status"] == "unavailable"
    assert environment["weather"]["error_code"] == "unsupported_date_expression"
    assert environment["resolved_time_context"]["status"] == "unsupported"
    assert environment["resolved_time_context"]["expression"] == "后天"
    assert trace[0]["status"] == "unavailable"


def test_device_location_uses_coordinates_and_keeps_privacy() -> None:
    router = _router()
    location_context = {
        "latitude": 31.234567,
        "longitude": 121.474444,
        "accuracy_m": 85.0,
        "captured_at": "2026-08-10T08:00:00+00:00",
        "consent_granted": True,
    }

    environment, trace = router.resolve(
        {"weather": {"needed": True, "date": "明天"}},
        location_context=location_context,
    )

    assert environment["weather"]["status"] == "available"
    assert environment["weather"]["resolved_location"]["source"] == "device"
    resolved = environment["resolved_location_context"]
    assert resolved["source"] == "device"
    assert resolved["accuracy_bucket"] == "high"
    assert "上海市" in resolved["display_name"]
    # Privacy: raw coordinates never leave the resolver in public payloads.
    assert "latitude" not in resolved
    assert "longitude" not in resolved
    assert "latitude" not in trace[0]["arguments"]
    assert "longitude" not in trace[0]["arguments"]
    assert trace[0]["arguments"]["source"] == "device"
    assert "上海市" in trace[0]["arguments"]["location"]
