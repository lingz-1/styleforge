"""Safe weather tool facade that converts provider failures into traceable facts."""

from __future__ import annotations

from datetime import date, timedelta

from styleforge.tools.weather.provider import WeatherProvider
from styleforge.tools.weather.schemas import (
    ResolvedLocation,
    WeatherFacts,
    WeatherToolInput,
)


class WeatherTool:
    """Resolve a location/window and return facts without any clothing decision.

    Supports both a named location and device coordinates. Coordinates bypass
    city-name guessing entirely and are rounded to two decimal places before
    being passed to the provider.
    """

    name = "weather.get_weather"

    def __init__(
        self,
        provider: WeatherProvider,
        *,
        today_provider=None,
        max_forecast_days: int = 16,
    ) -> None:
        self.provider = provider
        self._today_provider = today_provider or date.today
        self.max_forecast_days = max_forecast_days

    def __call__(self, tool_input: WeatherToolInput) -> WeatherFacts:
        requested_location = tool_input.location.strip() or tool_input.display_name.strip()
        requested_date = tool_input.date.strip()
        try:
            start, end = self._resolve_window(tool_input)
        except ValueError as error:
            return WeatherFacts.unavailable(
                requested_location=requested_location,
                requested_date=requested_date,
                error_code="invalid_date",
                error_message=str(error),
            )
        try:
            if tool_input.latitude is not None:
                location = self._resolve_device_location(tool_input)
            else:
                location = self.provider.resolve_location(tool_input.location.strip())
                if location is None:
                    return WeatherFacts.unavailable(
                        requested_location=requested_location,
                        requested_date=requested_date,
                        error_code="location_not_found",
                        error_message=f"无法解析地点：{tool_input.location}",
                    )
            facts = self.provider.forecast_range(location, start, end)
            return facts.model_copy(
                update={
                    "requested_location": requested_location
                    or location.display_name,
                    "requested_date": requested_date,
                }
            )
        except Exception as error:
            return WeatherFacts.unavailable(
                requested_location=requested_location,
                requested_date=requested_date,
                error_code="provider_unavailable",
                error_message=f"{type(error).__name__}: {error}",
            )

    def _resolve_window(self, tool_input: WeatherToolInput) -> tuple[date, date]:
        today = self._today_provider()
        if tool_input.date.strip():
            target = self._resolve_date(tool_input.date.strip())
            return target, target
        start = date.fromisoformat(tool_input.start_date)
        end = date.fromisoformat(tool_input.end_date)
        if start > end:
            raise ValueError("start_date 不能晚于 end_date")
        if start < today:
            raise ValueError("天气工具不查询历史日期")
        if (end - today).days >= self.max_forecast_days:
            raise ValueError(f"天气工具最多查询未来 {self.max_forecast_days - 1} 天")
        return start, end

    def _resolve_date(self, value: str) -> date:
        today = self._today_provider()
        normalized = value.strip().lower()
        if normalized in {"", "today", "今天", "今日"}:
            target = today
        elif normalized in {"tomorrow", "明天", "明日"}:
            target = today + timedelta(days=1)
        else:
            try:
                target = date.fromisoformat(normalized)
            except ValueError as error:
                raise ValueError("日期必须是今天、明天或 YYYY-MM-DD") from error
        delta = (target - today).days
        if delta < 0:
            raise ValueError("天气工具不查询历史日期")
        if delta >= self.max_forecast_days:
            raise ValueError(f"天气工具最多查询未来 {self.max_forecast_days - 1} 天")
        return target

    def _resolve_device_location(self, tool_input: WeatherToolInput) -> ResolvedLocation:
        latitude = round(tool_input.latitude, 2)
        longitude = round(tool_input.longitude, 2)
        location = None
        reverse = getattr(self.provider, "reverse_geocode", None)
        if callable(reverse):
            try:
                location = reverse(latitude, longitude)
            except Exception:
                location = None
        if location is None:
            location = ResolvedLocation(
                name=tool_input.display_name.strip() or "设备定位",
                latitude=latitude,
                longitude=longitude,
                timezone=tool_input.timezone or "auto",
                source="device",
                accuracy_bucket=tool_input.accuracy_bucket,
            )
        else:
            location = location.model_copy(
                update={"accuracy_bucket": tool_input.accuracy_bucket}
            )
        return location
