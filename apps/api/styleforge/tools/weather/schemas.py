"""Pydantic contracts for weather requirements, inputs, and factual output."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WeatherContextRequirement(BaseModel):
    """Agent 1's declaration that weather context is or is not required."""

    needed: bool = False
    location: str = Field(default="", max_length=160)
    date: str = Field(default="", max_length=32)
    granularity: Literal["daily", "hourly"] = "daily"
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _normalize_optional_fields(self) -> "WeatherContextRequirement":
        self.location = self.location.strip()
        self.date = self.date.strip()
        self.reason = self.reason.strip()
        return self


class TemporalContextRequirement(BaseModel):
    """Agent 1's declaration that a time window must be resolved."""

    needed: bool = False
    expression: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _normalize_optional_fields(self) -> "TemporalContextRequirement":
        self.expression = self.expression.strip()
        self.reason = self.reason.strip()
        return self


class LocationContextRequirement(BaseModel):
    """Agent 1's declaration that a location must be resolved."""

    needed: bool = False
    query: str = Field(default="", max_length=160)
    allow_profile_default: bool = True
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _normalize_optional_fields(self) -> "LocationContextRequirement":
        self.query = self.query.strip()
        self.reason = self.reason.strip()
        return self


class ContextRequirements(BaseModel):
    """External factual context requested by Agent 1."""

    temporal: TemporalContextRequirement = Field(
        default_factory=TemporalContextRequirement
    )
    location: LocationContextRequirement = Field(
        default_factory=LocationContextRequirement
    )
    weather: WeatherContextRequirement = Field(
        default_factory=WeatherContextRequirement
    )


class DeviceLocationContext(BaseModel):
    """A location authorized by the client for this request only.

    Precise coordinates are consumed by the Location Resolver and rounded to
    two decimal places before they ever reach facts, traces, or persistence.
    """

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(default=100.0, ge=0)
    captured_at: str = Field(default="", max_length=64)
    source: Literal["device"] = "device"
    consent_granted: bool = True


class WeatherToolInput(BaseModel):
    """Resolved tool input: either a named location or device coordinates.

    ``date`` and ``start_date/end_date`` are mutually exclusive; a window is
    always required (the Context Router decides single-day vs. the near-3-day
    default). Coordinates bypass city-name resolution entirely.
    """

    location: str = Field(default="", max_length=160)
    date: str = Field(default="", max_length=32)
    start_date: str = Field(default="", max_length=32)
    end_date: str = Field(default="", max_length=32)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "auto"
    display_name: str = Field(default="", max_length=160)
    accuracy_bucket: Literal["high", "medium", "low"] | None = None

    @model_validator(mode="after")
    def _validate_windows(self) -> "WeatherToolInput":
        has_date = bool(self.date.strip())
        has_start = bool(self.start_date.strip())
        has_end = bool(self.end_date.strip())
        if has_date and (has_start or has_end):
            raise ValueError("date is mutually exclusive with start_date/end_date")
        if has_start != has_end:
            raise ValueError("start_date and end_date must be provided together")
        if not has_date and not has_start:
            raise ValueError("a date or a start/end window is required")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.latitude is not None and self.location.strip():
            raise ValueError("named location and coordinates are mutually exclusive")
        return self


class ResolvedLocation(BaseModel):
    name: str
    country: str = ""
    admin1: str = ""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = "auto"
    source: str = "resolved"
    accuracy_bucket: Literal["high", "medium", "low"] | None = None

    @property
    def display_name(self) -> str:
        parts = [self.name, self.admin1, self.country]
        return ", ".join(dict.fromkeys(part for part in parts if part))


class WeatherDay(BaseModel):
    """One calendar day of facts inside a multi-day window."""

    date: str
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    feels_like_c: float | None = None
    precipitation_probability_percent: float | None = Field(
        default=None, ge=0, le=100
    )
    humidity_percent: float | None = Field(default=None, ge=0, le=100)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    weather_code: int | None = None
    condition: str = ""


class WeatherFacts(BaseModel):
    """Weather observations only; this model must never contain styling advice.

    The single-day headline fields mirror the first day of ``days`` so that
    V1 consumers (Web card, persistence) keep working unchanged.
    """

    status: Literal["available", "unavailable"]
    source: str = "open-meteo"
    requested_location: str = ""
    resolved_location: ResolvedLocation | None = None
    requested_date: str = ""
    forecast_date: str = ""
    start_date: str = ""
    end_date: str = ""
    default_applied: bool = False
    days: list[WeatherDay] = Field(default_factory=list)
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    feels_like_c: float | None = None
    precipitation_probability_percent: float | None = Field(
        default=None, ge=0, le=100
    )
    humidity_percent: float | None = Field(default=None, ge=0, le=100)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    weather_code: int | None = None
    condition: str = ""
    retrieved_at: str = Field(default_factory=_now)
    error_code: str = ""
    error_message: str = ""

    @classmethod
    def unavailable(
        cls,
        *,
        requested_location: str,
        requested_date: str,
        error_code: str,
        error_message: str,
    ) -> "WeatherFacts":
        return cls(
            status="unavailable",
            requested_location=requested_location,
            requested_date=requested_date,
            error_code=error_code,
            error_message=error_message,
        )
