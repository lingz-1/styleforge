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
        {"weather": {"needed": True, "date": "上周", "reason": "查天气"}}
    )

    assert environment["weather"]["status"] == "unavailable"
    assert environment["weather"]["error_code"] == "unsupported_date_expression"
    assert environment["resolved_time_context"]["status"] == "unsupported"
    assert environment["resolved_time_context"]["expression"] == "上周"
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


def _transport_province(url: str, timeout: float) -> dict:
    """Transport asserting that province names are mapped to their capital."""
    assert timeout == 3.0
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "geocoding-api" in parsed.netloc:
        assert query["name"] == ["乌鲁木齐"]
        return {
            "results": [
                {
                    "name": "乌鲁木齐",
                    "country": "中国",
                    "admin1": "新疆",
                    "latitude": 43.8,
                    "longitude": 87.6,
                    "timezone": "Asia/Shanghai",
                }
            ]
        }
    raise AssertionError("unexpected provider call in _transport_province")


def test_province_name_maps_to_representative_city() -> None:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport_province)
    location = provider.resolve_location("新疆")
    assert location is not None
    assert location.name == "乌鲁木齐"
    assert location.latitude == 43.8


def test_province_suffix_is_stripped_before_mapping() -> None:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport_province)
    location = provider.resolve_location("新疆维吾尔自治区")
    assert location is not None
    assert location.name == "乌鲁木齐"


def _transport_hourly(url: str, timeout: float) -> dict:
    """V2.2 hourly transport: full daily UV/sun series plus hourly weather series."""
    assert timeout == 3.0
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "geocoding-api" in parsed.netloc:
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
    start = query["start_date"][0]
    end = query["end_date"][0]
    dates = _range_dates(start, end)
    daily = {
        "temperature_2m_max": [],
        "temperature_2m_min": [],
        "apparent_temperature_max": [],
        "apparent_temperature_min": [],
        "precipitation_probability_max": [],
        "weather_code": [],
        "wind_speed_10m_max": [],
        "uv_index_max": [],
        "sunrise": [],
        "sunset": [],
        "time": [],
    }
    for day in dates:
        daily["temperature_2m_max"].append(32.0)
        daily["temperature_2m_min"].append(25.0)
        daily["apparent_temperature_max"].append(35.0)
        daily["apparent_temperature_min"].append(27.0)
        daily["precipitation_probability_max"].append(70)
        daily["weather_code"].append(61)
        daily["wind_speed_10m_max"].append(18.0)
        daily["uv_index_max"].append(7.0)
        daily["sunrise"].append(f"{day}T05:30")
        daily["sunset"].append(f"{day}T18:45")
        daily["time"].append(day)
    hourly_time: list[str] = []
    for day in dates:
        for hour in range(24):
            hourly_time.append(f"{day}T{hour:02d}:00")
    hourly = {
        "time": hourly_time,
        "relative_humidity_2m": [50.0 + (i % 24) for i in range(len(hourly_time))],
        "temperature_2m": [20.0 + (i % 24) for i in range(len(hourly_time))],
        "apparent_temperature": [21.0 + (i % 24) for i in range(len(hourly_time))],
        "precipitation_probability": [float(i % 24) for i in range(len(hourly_time))],
        "weather_code": [0] * len(hourly_time),
        "wind_speed_10m": [5.0 + (i % 24) for i in range(len(hourly_time))],
    }
    return {"timezone": "Asia/Shanghai", "daily": daily, "hourly": hourly}


def _tool_hourly() -> WeatherTool:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport_hourly)
    return WeatherTool(provider, today_provider=lambda: _TODAY)


def _router_hourly(*, default_location: str = "上海") -> ContextRouter:
    registry = ToolRegistry()
    register_weather_tool(registry, _tool_hourly())
    return ContextRouter(
        registry,
        default_location=default_location,
        today_provider=lambda: _TODAY,
        now_provider=lambda: datetime(2026, 8, 10, 8, 0, 0, tzinfo=timezone.utc),
    )


def test_provider_hourly_aggregates_morning_window() -> None:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport_hourly)
    location = provider.resolve_location("上海")
    assert location is not None

    facts = provider.forecast_range(
        location, date(2026, 8, 11), date(2026, 8, 11),
        granularity="hourly", period="morning",
    )

    assert facts.status == "available"
    day = facts.days[0]
    assert day.uv_index_max == 7.0
    assert day.sunrise == "2026-08-11T05:30"
    assert day.sunset == "2026-08-11T18:45"
    assert len(day.key_periods) == 1
    period = day.key_periods[0]
    assert period.label == "早晨"
    assert period.start_at == "2026-08-11T06:00:00"
    assert period.end_at == "2026-08-11T09:59:59"
    # Hours 6..9: feels mean = (27+28+29+30)/4, precip/wind = window max.
    assert period.feels_like_c == 28.5
    assert period.precipitation_probability_percent == 9.0
    assert period.wind_speed_kmh == 14.0
    assert period.uv_index_max == 7.0


def test_provider_hourly_without_period_returns_five_windows() -> None:
    provider = OpenMeteoProvider(timeout=3.0, transport=_transport_hourly)
    location = provider.resolve_location("上海")
    assert location is not None

    facts = provider.forecast_range(
        location, date(2026, 8, 11), date(2026, 8, 11),
        granularity="hourly",
    )

    labels = [period.label for period in facts.days[0].key_periods]
    assert labels == ["早晨", "下午", "傍晚/晚上", "深夜", "凌晨"]
    night = facts.days[0].key_periods[3]
    assert night.start_at == "2026-08-11T22:00:00"
    assert night.end_at == "2026-08-11T23:59:59"
    dawn = facts.days[0].key_periods[4]
    assert dawn.start_at == "2026-08-11T00:00:00"
    assert dawn.end_at == "2026-08-11T05:59:59"


def test_context_router_morning_full_chain() -> None:
    router = _router_hourly()

    environment, trace = router.resolve(
        {"weather": {"needed": True, "date": "明早", "reason": "查天气"}}
    )

    assert environment["weather"]["status"] == "available"
    time_context = environment["resolved_time_context"]
    assert time_context["status"] == "resolved"
    assert time_context["precision"] == "period_day"
    assert time_context["granularity"] == "hourly"
    assert time_context["period"] == "morning"
    assert time_context["period_label"] == "早晨"
    assert time_context["start_date"] == "2026-08-11"
    assert time_context["start_at"] == "2026-08-11T06:00:00"
    assert time_context["end_at"] == "2026-08-11T09:59:59"
    # The tool input carries granularity/period; the trace keeps them too.
    assert trace[0]["arguments"] == {
        "location": "上海",
        "date": "2026-08-11",
        "granularity": "hourly",
        "period": "morning",
    }
    key_period = environment["weather"]["days"][0]["key_periods"][0]
    assert key_period["label"] == "早晨"


def test_context_router_weekend_resolves_to_daily_range() -> None:
    router = _router(default_location="上海")

    environment, trace = router.resolve(
        {"weather": {"needed": True, "date": "周末", "reason": "查天气"}}
    )

    assert environment["weather"]["status"] == "available"
    time_context = environment["resolved_time_context"]
    assert time_context["status"] == "resolved"
    assert time_context["precision"] == "weekend"
    assert time_context["granularity"] == "daily"
    assert time_context["start_date"] == "2026-08-15"
    assert time_context["end_date"] == "2026-08-16"
    assert trace[0]["arguments"] == {
        "location": "上海",
        "start_date": "2026-08-15",
        "end_date": "2026-08-16",
    }
    assert len(environment["weather"]["days"]) == 2
