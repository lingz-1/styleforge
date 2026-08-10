"""Typed weather facts for the recommendation context router."""

from styleforge.tools.weather.client import WeatherTool
from styleforge.tools.weather.provider import OpenMeteoProvider, WeatherProvider
from styleforge.tools.weather.schemas import (
    ContextRequirements,
    DeviceLocationContext,
    LocationContextRequirement,
    ResolvedLocation,
    TemporalContextRequirement,
    WeatherContextRequirement,
    WeatherDay,
    WeatherFacts,
    WeatherToolInput,
)

__all__ = [
    "ContextRequirements",
    "DeviceLocationContext",
    "LocationContextRequirement",
    "OpenMeteoProvider",
    "ResolvedLocation",
    "TemporalContextRequirement",
    "WeatherContextRequirement",
    "WeatherDay",
    "WeatherFacts",
    "WeatherProvider",
    "WeatherTool",
    "WeatherToolInput",
]
