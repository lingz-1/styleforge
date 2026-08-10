"""Open-Meteo provider adapter with an injectable JSON transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from styleforge.tools.weather.schemas import ResolvedLocation, WeatherDay, WeatherFacts

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
                "name": query,
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
    ) -> WeatherFacts:
        start = start_date.isoformat()
        end = end_date.isoformat()
        params = urlencode(
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "daily": ",".join(
                    [
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "apparent_temperature_max",
                        "apparent_temperature_min",
                        "precipitation_probability_max",
                        "weather_code",
                        "wind_speed_10m_max",
                    ]
                ),
                "hourly": "relative_humidity_2m",
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
        humidity_by_date = _humidity_by_date(
            payload.get("hourly", {}), start=start, end=end
        )
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
