"""Offline tests for the location priority chain and privacy guarantees."""

from __future__ import annotations

from datetime import datetime, timezone

from styleforge.orchestration.location_resolver import LocationResolver
from styleforge.tools.weather.schemas import ContextRequirements, DeviceLocationContext

_NOW = datetime(2026, 8, 10, 8, 0, 0, tzinfo=timezone.utc)


def _requirements(*, location: str = "", weather_location: str = "") -> ContextRequirements:
    return ContextRequirements.model_validate(
        {
            "location": {"needed": True, "query": location, "allow_profile_default": True},
            "weather": {"needed": True, "location": weather_location},
        }
    )


def _device(
    *,
    latitude: float = 31.234567,
    longitude: float = 121.474444,
    accuracy_m: float = 85.0,
    captured_at: str = "2026-08-10T08:00:00+00:00",
    consent_granted: bool = True,
) -> DeviceLocationContext:
    return DeviceLocationContext(
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        captured_at=captured_at,
        consent_granted=consent_granted,
    )


def _resolver(**kwargs) -> LocationResolver:
    return LocationResolver(now_provider=lambda: _NOW, **kwargs)


def test_explicit_location_beats_device_and_profile() -> None:
    result = _resolver().resolve(
        _requirements(location="北京"),
        device_location=_device(),
        environment_profile={"default_city": "上海"},
        global_default="广州",
    )

    assert result.mode == "named"
    assert result.query == "北京"
    assert result.source == "explicit"


def test_weather_location_also_counts_as_explicit() -> None:
    result = _resolver().resolve(
        _requirements(weather_location="成都"),
        device_location=_device(),
        environment_profile={"default_city": "上海"},
        global_default="广州",
    )

    assert result.mode == "named"
    assert result.query == "成都"
    assert result.source == "explicit"


def test_device_location_used_when_no_explicit_location() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(),
        environment_profile={"default_city": "上海"},
        global_default="广州",
    )

    assert result.mode == "coordinates"
    assert result.source == "device"
    assert result.latitude == 31.23  # rounded to two decimals
    assert result.longitude == 121.47
    assert result.accuracy_bucket == "high"


def test_device_location_falls_back_to_profile_city() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(captured_at="2026-08-09T08:00:00+00:00"),
        environment_profile={"default_city": "上海"},
        global_default="广州",
    )

    assert result.mode == "named"
    assert result.query == "上海"
    assert result.source == "profile"
    assert result.device_error == "location_stale"


def test_device_location_falls_back_to_global_default() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(accuracy_m=9000.0),
        environment_profile={},
        global_default="广州",
    )

    assert result.mode == "named"
    assert result.query == "广州"
    assert result.source == "global"
    assert result.device_error == "location_inaccurate"


def test_no_location_source_yields_location_required() -> None:
    result = _resolver().resolve(_requirements(), global_default="")

    assert result.error_code == "location_required"
    assert result.mode == "named"


def test_accuracy_buckets() -> None:
    resolver = _resolver()
    assert resolver.resolve(
        _requirements(), device_location=_device(accuracy_m=50.0), global_default=""
    ).accuracy_bucket == "high"
    assert resolver.resolve(
        _requirements(), device_location=_device(accuracy_m=500.0), global_default=""
    ).accuracy_bucket == "medium"
    assert resolver.resolve(
        _requirements(), device_location=_device(accuracy_m=3000.0), global_default=""
    ).accuracy_bucket == "low"


def test_consent_denied_falls_through_to_default() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(consent_granted=False),
        environment_profile={"default_city": "上海"},
    )

    assert result.source == "profile"
    assert result.device_error == "location_denied"


def test_invalid_coordinates_rejected_without_rounding() -> None:
    # model_construct bypasses pydantic's ge/le field checks so the resolver's
    # own range validation is what rejects the input.
    device = DeviceLocationContext.model_construct(
        latitude=123.0, longitude=121.47, accuracy_m=85.0, captured_at="2026-08-10T08:00:00+00:00"
    )
    result = _resolver().resolve(_requirements(), device_location=device, global_default="")

    assert result.error_code == "location_required"
    assert result.device_error == "location_invalid"


def test_captured_at_is_required() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(captured_at=""),
        global_default="",
    )

    assert result.error_code == "location_required"
    assert result.device_error == "location_invalid"


def test_precise_coordinates_never_exposed_on_resolution() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(),
        global_default="",
    )

    # The rounded values are the only ones that ever leave the resolver.
    assert result.latitude == round(31.234567, 2)
    assert result.longitude == round(121.474444, 2)
    assert result.latitude != 31.234567
    assert result.longitude != 121.474444


def test_captured_at_with_trailing_z_accepted() -> None:
    # ``new Date().toISOString()`` ends with "Z"; Python 3.10 fromisoformat
    # rejects it, so the resolver must normalize before parsing.
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(captured_at="2026-08-10T08:00:00.000Z"),
        global_default="",
    )

    assert result.mode == "coordinates"
    assert result.source == "device"
    assert result.latitude == 31.23


def test_captured_at_with_trailing_lowercase_z_accepted() -> None:
    result = _resolver().resolve(
        _requirements(),
        device_location=_device(captured_at="2026-08-10T08:00:00z"),
        global_default="",
    )

    assert result.source == "device"
    assert result.error_code == ""
