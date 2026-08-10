"""Execute Agent 1's factual context requirements through typed tools."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from styleforge.orchestration.location_resolver import (
    LocationResolution,
    LocationResolver,
)
from styleforge.tools.calendar import resolve_temporal_expression
from styleforge.tools.registry import ToolRegistry
from styleforge.tools.weather.schemas import (
    ContextRequirements,
    DeviceLocationContext,
    WeatherContextRequirement,
    WeatherFacts,
    WeatherToolInput,
)

_SIMPLE_DATE_EXPRESSIONS = {
    "today",
    "今天",
    "今日",
    "tomorrow",
    "明天",
    "明日",
}

_DEFAULT_WINDOW_DAYS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_time_window(
    weather: WeatherContextRequirement,
    temporal,
    *,
    today: date,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Turn a weather/temporal declaration into tool window arguments.

    Returns ``(window_args, resolved_time_context)``. With no date expression
    the near-3-day default window (today..today+2) is applied and flagged with
    ``default_applied``. Unsupported explicit expressions resolve to status
    ``unsupported`` so the caller can return an honest ``unavailable`` fact.
    """
    expression = weather.date.strip() or temporal.expression.strip()
    if not expression:
        start = today
        end = today + timedelta(days=_DEFAULT_WINDOW_DAYS - 1)
        payload = {
            "status": "resolved",
            "expression": "",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "precision": "near_3_days",
            "default_applied": True,
            "resolution_basis": "default_near_3_days",
        }
        return (
            {"start_date": start.isoformat(), "end_date": end.isoformat()},
            payload,
        )
    normalized = expression.strip().lower()
    if normalized in _SIMPLE_DATE_EXPRESSIONS or _is_iso_date(normalized):
        # Today/tomorrow/ISO stay verbatim so the trace keeps the user's own
        # wording; the calendar resolver handles every other expression.
        target = _resolve_simple_date(normalized, today)
        payload = {
            "status": "resolved",
            "expression": expression,
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
            "precision": "day",
            "default_applied": False,
            "resolution_basis": "relative_to_request_time",
        }
        return {"date": expression}, payload

    resolution = resolve_temporal_expression(expression, today=today)
    if resolution.status == "unsupported":
        payload = {
            "status": "unsupported",
            "expression": expression,
            "start_date": "",
            "end_date": "",
            "precision": "",
            "default_applied": False,
            "resolution_basis": "unsupported_expression",
        }
        return {}, payload

    window_args: dict[str, str] = {}
    if resolution.precision in {"day", "period_day"}:
        # A single-day window (incl. period windows) rides the ``date`` channel.
        window_args["date"] = resolution.start_date
    else:
        window_args["start_date"] = resolution.start_date
        window_args["end_date"] = resolution.end_date
    if resolution.granularity == "hourly":
        window_args["granularity"] = "hourly"
        if resolution.period:
            window_args["period"] = resolution.period
    payload = {
        "status": "resolved",
        "expression": expression,
        "start_date": resolution.start_date,
        "end_date": resolution.end_date,
        "precision": resolution.precision,
        "default_applied": resolution.default_applied,
        "resolution_basis": resolution.resolution_basis,
        "granularity": resolution.granularity,
        "period": resolution.period,
        "period_label": resolution.period_label,
        "start_at": resolution.start_at,
        "end_at": resolution.end_at,
        "timezone": resolution.timezone,
        "approximate": resolution.approximate,
    }
    return window_args, payload


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _resolve_simple_date(normalized: str, today: date) -> date:
    if normalized in {"today", "今天", "今日"}:
        return today
    if normalized in {"tomorrow", "明天", "明日"}:
        return today + timedelta(days=1)
    return date.fromisoformat(normalized)


def _resolved_location_payload(
    location: LocationResolution,
    facts: WeatherFacts,
) -> dict[str, Any] | None:
    """Public location context: display name, timezone, source, accuracy bucket.

    Never contains raw coordinates.
    """
    if location.error_code or not location.source:
        return None
    if location.mode == "coordinates":
        display = (
            facts.resolved_location.display_name
            if facts.resolved_location
            else location.display_name or "设备定位"
        )
        timezone = (
            facts.resolved_location.timezone
            if facts.resolved_location
            else location.timezone
        )
        accuracy_bucket = location.accuracy_bucket
    else:
        display = location.query
        timezone = facts.resolved_location.timezone if facts.resolved_location else "auto"
        accuracy_bucket = None
    return {
        "display_name": display,
        "timezone": timezone,
        "source": location.source,
        "accuracy_bucket": accuracy_bucket,
    }


class ContextRouter:
    """Validate requirements, resolve location/window, invoke tools, and trace."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        default_location: str = "",
        location_max_age_seconds: int = 1800,
        location_max_accuracy_m: float = 5000.0,
        today_provider=None,
        now_provider=None,
    ) -> None:
        self.registry = registry
        self.default_location = default_location.strip()
        self._today_provider = today_provider or date.today
        self.location_resolver = LocationResolver(
            max_age_seconds=location_max_age_seconds,
            max_accuracy_m=location_max_accuracy_m,
            now_provider=now_provider,
        )

    def resolve(
        self,
        requirements: ContextRequirements | dict[str, Any],
        *,
        location_context: DeviceLocationContext | dict[str, Any] | None = None,
        environment_profile: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        resolved = ContextRequirements.model_validate(requirements)
        weather = resolved.weather
        if not weather.needed:
            return (
                {
                    "weather": None,
                    "resolved_location_context": None,
                    "resolved_time_context": None,
                },
                [],
            )

        device = None
        if location_context is not None:
            device = DeviceLocationContext.model_validate(location_context)

        location = self.location_resolver.resolve(
            resolved,
            device_location=device,
            environment_profile=environment_profile,
            global_default=self.default_location,
        )
        window_args, time_context = _resolve_time_window(
            resolved.weather,
            resolved.temporal,
            today=self._today_provider(),
        )
        requested_location = (
            location.query
            if location.mode == "named"
            else location.display_name or "设备定位"
        )

        if time_context["status"] == "unsupported":
            facts = WeatherFacts.unavailable(
                requested_location=requested_location,
                requested_date=time_context["expression"],
                error_code="unsupported_date_expression",
                error_message=(
                    f"暂不支持该日期表达式：{time_context['expression']}"
                ),
            )
            return (
                self._build_result(facts, location, time_context),
                self._trace(facts, {}, location),
            )

        if location.error_code:
            facts = WeatherFacts.unavailable(
                requested_location=requested_location,
                requested_date=weather.date,
                error_code=location.error_code,
                error_message=self._location_error_message(location),
            )
            return (
                self._build_result(facts, location, time_context),
                self._trace(facts, {}, location),
            )

        arguments = self._tool_arguments(location, window_args)
        if not self.registry.contains("weather.get_weather"):
            facts = WeatherFacts.unavailable(
                requested_location=requested_location,
                requested_date=weather.date,
                error_code="tool_disabled",
                error_message="天气工具未启用",
            )
        else:
            result = self.registry.invoke("weather.get_weather", arguments)
            facts = WeatherFacts.model_validate(result)
        # The router decides whether the window was a system default; reflect
        # that decision on the facts so every consumer sees the same value.
        facts = facts.model_copy(
            update={"default_applied": bool(time_context.get("default_applied"))}
        )
        return (
            self._build_result(facts, location, time_context),
            self._trace(facts, arguments, location),
        )

    def _build_result(
        self,
        facts: WeatherFacts,
        location: LocationResolution,
        time_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "weather": facts.model_dump(mode="json"),
            "resolved_location_context": _resolved_location_payload(location, facts),
            "resolved_time_context": time_context,
        }

    def _tool_arguments(
        self,
        location: LocationResolution,
        window_args: dict[str, str],
    ) -> dict[str, Any]:
        if location.mode == "coordinates":
            return {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "timezone": location.timezone,
                "display_name": location.display_name,
                "accuracy_bucket": location.accuracy_bucket,
                **window_args,
            }
        return {"location": location.query, **window_args}

    def _trace(
        self,
        facts: WeatherFacts,
        arguments: dict[str, Any],
        location: LocationResolution,
    ) -> list[dict[str, Any]]:
        trace_arguments = self._sanitize_trace_arguments(arguments, location, facts)
        return [
            {
                "tool": "weather.get_weather",
                "timestamp": _now(),
                "arguments": trace_arguments,
                "status": facts.status,
                "source": facts.source,
                "error_code": facts.error_code,
            }
        ]

    @staticmethod
    def _sanitize_trace_arguments(
        arguments: dict[str, Any],
        location: LocationResolution,
        facts: WeatherFacts | None = None,
    ) -> dict[str, Any]:
        """Never persist raw device coordinates in the audit trace."""
        if location.mode != "coordinates":
            return dict(arguments)
        window_fields = {
            key: value
            for key, value in arguments.items()
            if key in {"date", "start_date", "end_date", "granularity", "period"}
        }
        display = ""
        if facts is not None and facts.resolved_location is not None:
            display = facts.resolved_location.display_name
        return {
            "location": display or location.display_name or "设备定位",
            "source": "device",
            "accuracy_bucket": location.accuracy_bucket,
            **window_fields,
        }

    def _location_error_message(self, location: LocationResolution) -> str:
        if location.device_error:
            return (
                f"设备定位不可用（{location.device_error}），"
                "且无用户默认城市或全局默认城市"
            )
        return "请求需要天气信息，但没有明确地点且未配置默认城市"


def register_weather_tool(registry: ToolRegistry, weather_tool: Any) -> None:
    registry.register(
        "weather.get_weather",
        input_model=WeatherToolInput,
        handler=weather_tool,
    )
