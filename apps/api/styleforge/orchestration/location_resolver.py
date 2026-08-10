"""Location priority chain for weather and environment context.

Resolution order: explicit location from Agent 1 -> validated device
coordinates for this request -> the user's profile default city -> the global
demo default. Precise coordinates never leave this module as raw floats: they
are rounded to two decimal places before entering any fact, trace, or
persistence path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from styleforge.tools.weather.schemas import ContextRequirements, DeviceLocationContext


@dataclass(frozen=True, slots=True)
class LocationResolution:
    mode: Literal["named", "coordinates"] = "named"
    query: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = "auto"
    display_name: str = ""
    source: str = ""
    accuracy_bucket: Literal["high", "medium", "low"] | None = None
    error_code: str = ""
    device_error: str = ""


def _accuracy_bucket(accuracy_m: float) -> Literal["high", "medium", "low"]:
    if accuracy_m <= 100:
        return "high"
    if accuracy_m <= 1000:
        return "medium"
    return "low"


def _parse_captured_at(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class LocationResolver:
    def __init__(
        self,
        *,
        now_provider=None,
        max_age_seconds: int = 1800,
        max_accuracy_m: float = 5000.0,
    ) -> None:
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.max_age_seconds = max_age_seconds
        self.max_accuracy_m = max_accuracy_m

    def resolve(
        self,
        requirements: ContextRequirements,
        *,
        device_location: DeviceLocationContext | None = None,
        environment_profile: dict[str, Any] | None = None,
        global_default: str = "",
    ) -> LocationResolution:
        explicit = (
            requirements.location.query.strip()
            or requirements.weather.location.strip()
        )
        if explicit:
            return LocationResolution(mode="named", query=explicit, source="explicit")

        if device_location is not None:
            result = self._resolve_device(device_location)
            if result.error_code == "":
                return result
            # Invalid/stale/inaccurate/denied device location falls through to
            # the profile default; the rejection reason is kept for the trace.
            device_error = result.error_code
        else:
            device_error = ""

        if self._allow_profile_default(requirements) and self._profile_default_city(
            environment_profile
        ):
            return LocationResolution(
                mode="named",
                query=self._profile_default_city(environment_profile),
                source="profile",
                device_error=device_error,
            )

        if global_default.strip():
            return LocationResolution(
                mode="named",
                query=global_default.strip(),
                source="global",
                device_error=device_error,
            )

        return LocationResolution(
            error_code="location_required",
            device_error=device_error,
        )

    def _resolve_device(
        self, device: DeviceLocationContext
    ) -> LocationResolution:
        if not device.consent_granted:
            return LocationResolution(error_code="location_denied")
        if not (-90 <= device.latitude <= 90 and -180 <= device.longitude <= 180):
            return LocationResolution(error_code="location_invalid")
        captured = _parse_captured_at(device.captured_at)
        if captured is None:
            return LocationResolution(error_code="location_invalid")
        age_seconds = (self._now_provider() - captured).total_seconds()
        if age_seconds > self.max_age_seconds:
            return LocationResolution(error_code="location_stale")
        if device.accuracy_m > self.max_accuracy_m:
            return LocationResolution(error_code="location_inaccurate")
        return LocationResolution(
            mode="coordinates",
            latitude=round(device.latitude, 2),
            longitude=round(device.longitude, 2),
            timezone="auto",
            display_name="",
            source="device",
            accuracy_bucket=_accuracy_bucket(device.accuracy_m),
        )

    @staticmethod
    def _allow_profile_default(requirements: ContextRequirements) -> bool:
        if not requirements.location.needed:
            return True
        return requirements.location.allow_profile_default

    @staticmethod
    def _profile_default_city(
        environment_profile: dict[str, Any] | None,
    ) -> str:
        if not environment_profile:
            return ""
        return str(environment_profile.get("default_city", "")).strip()
