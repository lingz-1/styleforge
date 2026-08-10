"""Open-Meteo provider adapter with an injectable JSON transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from styleforge.tools.calendar.periods import PERIOD_WINDOWS
from styleforge.tools.weather.schemas import (
    ResolvedLocation,
    WeatherDay,
    WeatherFacts,
    WeatherHour,
)

JsonTransport = Callable[[str, float], dict[str, Any]]


class WeatherProvider(Protocol):
    name: str

    def resolve_location(self, query: str) -> ResolvedLocation | None: ...

    def forecast(self, location: ResolvedLocation, target_date: date) -> WeatherFacts: ...

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: date,
        end_date: date,
        *,
        granularity: str = "daily",
        period: str | None = None,
    ) -> WeatherFacts: ...

    def reverse_geocode(
        self, latitude: float, longitude: float
    ) -> ResolvedLocation | None: ...


def _default_transport(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "StyleForge/0.4"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


_WEATHER_CODES = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "轻微毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "轻微冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


def _value_at(payload: dict[str, Any], field: str, index: int) -> Any:
    values = payload.get(field, [])
    return values[index] if isinstance(values, list) and index < len(values) else None


# Open-Meteo geocoding only covers city-level names; province-level queries
# (新疆/西藏/内蒙古...) return no results. Map them to a representative city
# (their capital) as a best-effort weather approximation.
_PROVINCE_CAPITALS: dict[str, str] = {
    "新疆": "乌鲁木齐",
    "西藏": "拉萨",
    "内蒙古": "呼和浩特",
    "广西": "南宁",
    "宁夏": "银川",
    "香港": "香港",
    "澳门": "澳门",
    "台湾": "台北",
    "黑龙江": "哈尔滨",
    "吉林": "长春",
    "辽宁": "沈阳",
    "河北": "石家庄",
    "山西": "太原",
    "陕西": "西安",
    "甘肃": "兰州",
    "青海": "西宁",
    "山东": "济南",
    "河南": "郑州",
    "湖北": "武汉",
    "湖南": "长沙",
    "江苏": "南京",
    "安徽": "合肥",
    "浙江": "杭州",
    "江西": "南昌",
    "福建": "福州",
    "广东": "广州",
    "海南": "海口",
    "四川": "成都",
    "贵州": "贵阳",
    "云南": "昆明",
}

_PROVINCE_SUFFIXES = (
    "特别行政区",
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "藏族自治区",
    "自治区",
    "省",
    "市",
)


def _normalize_province_query(query: str) -> str:
    """Strip administrative suffixes and map province names to a geocodable
    representative city (their capital) as a weather approximation."""
    name = query.strip()
    for suffix in _PROVINCE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    return _PROVINCE_CAPITALS.get(name, query.strip())


class OpenMeteoProvider:
    """Fetch geocoding, reverse geocoding and daily facts from Open-Meteo."""

    name = "open-meteo"
    geocoding_endpoint = "https://geocoding-api.open-meteo.com/v1/search"
    forecast_endpoint = "https://api.open-meteo.com/v1/forecast"
    reverse_endpoint = "https://api.bigdatacloud.net/data/reverse-geocode-client"

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        transport: JsonTransport | None = None,
        reverse_transport: JsonTransport | None = None,
        reverse_endpoint: str = "",
    ) -> None:
        self.timeout = timeout
        self._transport = transport or _default_transport
        self._reverse_transport = reverse_transport or self._transport
        if reverse_endpoint.strip():
            self.reverse_endpoint = reverse_endpoint.strip()

    def resolve_location(self, query: str) -> ResolvedLocation | None:
        params = urlencode(
            {
                "name": _normalize_province_query(query),
                "count": 1,
                "language": "zh",
                "format": "json",
            }
        )
        payload = self._transport(f"{self.geocoding_endpoint}?{params}", self.timeout)
        results = payload.get("results", [])
        if not results:
            return None
        result = results[0]
        return ResolvedLocation(
            name=str(result.get("name", query)),
            country=str(result.get("country", "")),
            admin1=str(result.get("admin1", "")),
            latitude=float(result["latitude"]),
            longitude=float(result["longitude"]),
            timezone=str(result.get("timezone", "auto")),
            source="resolved",
        )

    def reverse_geocode(
        self, latitude: float, longitude: float
    ) -> ResolvedLocation | None:
        """Best-effort city name for device coordinates; None if unavailable.

        The weather query never depends on this call succeeding.
        """
        params = urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "localityLanguage": "zh",
                "format": "json",
            }
        )
        try:
            payload = self._reverse_transport(
                f"{self.reverse_endpoint}?{params}", self.timeout
            )
        except Exception:
            return None
        name = str(
            payload.get("city")
            or payload.get("locality")
            or payload.get("principalSubdivision")
            or ""
        ).strip()
        if not name:
            return None
        return ResolvedLocation(
            name=name,
            country=str(payload.get("countryName", "")),
            admin1=str(payload.get("principalSubdivision", "")),
            latitude=round(float(latitude), 2),
            longitude=round(float(longitude), 2),
            timezone=str(payload.get("timezone") or "auto").strip(),
            source="device",
        )

    def forecast(self, location: ResolvedLocation, target_date: date) -> WeatherFacts:
        return self.forecast_range(location, target_date, target_date)

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: date,
        end_date: date,
        *,
        granularity: str = "daily",
        period: str | None = None,
    ) -> WeatherFacts:
        start = start_date.isoformat()
        end = end_date.isoformat()
        daily_variables = [
            "temperature_2m_max",
            "temperature_2m_min",
            "apparent_temperature_max",
            "apparent_temperature_min",
            "precipitation_probability_max",
            "weather_code",
            "wind_speed_10m_max",
        ]
        hourly_variables = ["relative_humidity_2m"]
        if granularity == "hourly":
            # UV / sun timing belong to the day; the key-period aggregation
            # reuses them together with the extended hourly series.
            daily_variables += ["uv_index_max", "sunrise", "sunset"]
            hourly_variables += [
                "temperature_2m",
                "apparent_temperature",
                "precipitation_probability",
                "weather_code",
                "wind_speed_10m",
            ]
        params = urlencode(
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "daily": ",".join(daily_variables),
                "hourly": ",".join(hourly_variables),
                "timezone": location.timezone or "auto",
                "start_date": start,
                "end_date": end,
            }
        )
        payload = self._transport(f"{self.forecast_endpoint}?{params}", self.timeout)
        daily = payload.get("daily", {})
        dates = [str(value) for value in daily.get("time", [])]
        if start not in dates or end not in dates:
            raise ValueError(f"forecast does not cover requested window: {start}..{end}")
        hourly = payload.get("hourly", {})
        humidity_by_date = _humidity_by_date(hourly, start=start, end=end)
        response_timezone = str(payload.get("timezone") or location.timezone or "auto")
        resolved = (
            location.model_copy(update={"timezone": response_timezone})
            if location.timezone in {"", "auto"}
            else location
        )
        days: list[WeatherDay] = []
        for index, day_str in enumerate(dates):
            if day_str < start or day_str > end:
                continue
            weather_code = _value_at(daily, "weather_code", index)
            days.append(
                WeatherDay(
                    date=day_str,
                    temperature_min_c=_value_at(daily, "temperature_2m_min", index),
                    temperature_max_c=_value_at(daily, "temperature_2m_max", index),
                    feels_like_c=_mean_feels_like(daily, index),
                    precipitation_probability_percent=_value_at(
                        daily, "precipitation_probability_max", index
                    ),
                    humidity_percent=humidity_by_date.get(day_str),
                    wind_speed_kmh=_value_at(daily, "wind_speed_10m_max", index),
                    weather_code=weather_code,
                    condition=_WEATHER_CODES.get(weather_code, "未知天气"),
                    uv_index_max=_value_at(daily, "uv_index_max", index),
                    sunrise=str(_value_at(daily, "sunrise", index) or ""),
                    sunset=str(_value_at(daily, "sunset", index) or ""),
                    key_periods=(
                        _key_periods_for_day(
                            hourly, daily, day_str, index, period=period
                        )
                        if granularity == "hourly"
                        else []
                    ),
                )
            )
        if not days:
            raise ValueError(f"forecast window is empty: {start}..{end}")
        first = days[0]
        return WeatherFacts(
            status="available",
            source=self.name,
            resolved_location=resolved,
            forecast_date=start,
            start_date=start,
            end_date=end,
            days=days,
            temperature_min_c=first.temperature_min_c,
            temperature_max_c=first.temperature_max_c,
            feels_like_c=first.feels_like_c,
            precipitation_probability_percent=first.precipitation_probability_percent,
            humidity_percent=first.humidity_percent,
            wind_speed_kmh=first.wind_speed_kmh,
            weather_code=first.weather_code,
            condition=first.condition,
        )


def _key_periods_for_day(
    hourly: dict[str, Any],
    daily: dict[str, Any],
    day: str,
    index: int,
    *,
    period: str | None = None,
) -> list[WeatherHour]:
    """Aggregate hourly series into canonical time-of-day windows.

    ``period`` restricts the output to that single window; otherwise all five
    standard windows are produced. ``feels_like_c`` is the window mean,
    ``precipitation_probability_percent``/``wind_speed_kmh`` are the window
    maxima, and ``uv_index_max`` mirrors the day-level value. Windows never
    cross midnight; missing values fall back to None.
    """
    buckets: dict[str, dict[str, list[float]]] = {}
    times = hourly.get("time", [])
    for hour_index, timestamp in enumerate(times):
        timestamp = str(timestamp)
        if timestamp[:10] != day:
            continue
        hour = int(timestamp[11:13]) if len(timestamp) >= 13 else None
        if hour is None:
            continue
        for label, (start_hour, end_hour, _) in PERIOD_WINDOWS.items():
            if start_hour <= hour < end_hour:
                bucket = buckets.setdefault(
                    label, {"feels": [], "precip": [], "wind": []}
                )
                _append_hour(bucket, hourly, hour_index)
                break
    labels = [period] if period is not None else list(PERIOD_WINDOWS)
    result: list[WeatherHour] = []
    for label in labels:
        start_hour, end_hour, label_cn = PERIOD_WINDOWS[label]
        start_at = f"{day}T{start_hour:02d}:00:00"
        if end_hour == 24:
            end_at = f"{day}T23:59:59"
        else:
            end_at = f"{day}T{end_hour - 1:02d}:59:59"
        bucket = buckets.get(label)
        if bucket is None:
            result.append(
                WeatherHour(
                    label=label_cn,
                    start_at=start_at,
                    end_at=end_at,
                    uv_index_max=_value_at(daily, "uv_index_max", index),
                )
            )
            continue
        feels = _window_mean(bucket["feels"])
        precip = max(bucket["precip"]) if bucket["precip"] else None
        wind = max(bucket["wind"]) if bucket["wind"] else None
        result.append(
            WeatherHour(
                label=label_cn,
                start_at=start_at,
                end_at=end_at,
                feels_like_c=feels,
                precipitation_probability_percent=precip,
                wind_speed_kmh=wind,
                uv_index_max=_value_at(daily, "uv_index_max", index),
            )
        )
    return result


def _append_hour(
    bucket: dict[str, list[float]],
    hourly: dict[str, Any],
    hour_index: int,
) -> None:
    for key, field in (
        ("feels", "apparent_temperature"),
        ("precip", "precipitation_probability"),
        ("wind", "wind_speed_10m"),
    ):
        value = _value_at(hourly, field, hour_index)
        if value is not None:
            bucket[key].append(float(value))


def _window_mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _mean_feels_like(daily: dict[str, Any], index: int) -> float | None:
    apparent_min = _value_at(daily, "apparent_temperature_min", index)
    apparent_max = _value_at(daily, "apparent_temperature_max", index)
    if apparent_min is None or apparent_max is None:
        return None
    return round((float(apparent_min) + float(apparent_max)) / 2, 1)


def _humidity_by_date(
    hourly: dict[str, Any], *, start: str, end: str
) -> dict[str, float | None]:
    grouped: dict[str, list[float]] = {}
    for timestamp, value in zip(
        hourly.get("time", []),
        hourly.get("relative_humidity_2m", []),
        strict=False,
    ):
        day = str(timestamp)[:10]
        if start <= day <= end and value is not None:
            grouped.setdefault(day, []).append(float(value))
    return {
        day: round(sum(values) / len(values), 1) for day, values in grouped.items()
    }
