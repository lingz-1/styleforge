"""Offline tests for the home-card ``GET /weather/now`` endpoint.

The endpoint reuses the workflow's ``weather_tool``; each case monkeypatches
``api.get_workflow`` with a fake workflow whose tool records the resolved
``WeatherToolInput`` and returns deterministic facts, so no provider is hit.
"""

from __future__ import annotations

import importlib
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

from styleforge.tools.weather.schemas import (
    ResolvedLocation,
    WeatherDay,
    WeatherFacts,
    WeatherToolInput,
)


class RecordingWeatherTool:
    """Records every resolved ``WeatherToolInput`` and returns preset facts."""

    def __init__(self) -> None:
        self.calls: list[WeatherToolInput] = []

    def __call__(self, tool_input: WeatherToolInput) -> WeatherFacts:
        self.calls.append(tool_input)
        return _available_facts(tool_input)


def _available_facts(tool_input: WeatherToolInput) -> WeatherFacts:
    name = tool_input.location.strip() or "设备定位"
    return WeatherFacts(
        status="available",
        requested_location=name,
        requested_date=tool_input.date,
        resolved_location=ResolvedLocation(
            name=name,
            latitude=tool_input.latitude or 0.0,
            longitude=tool_input.longitude or 0.0,
        ),
        start_date=tool_input.date,
        end_date=tool_input.date,
        forecast_date=tool_input.date,
        days=[
            WeatherDay(
                date=tool_input.date,
                temperature_min_c=24.0,
                temperature_max_c=31.0,
                feels_like_c=28.0,
                precipitation_probability_percent=20.0,
                wind_speed_kmh=12.0,
                weather_code=1,
                condition="局部多云",
                uv_index_max=6.0,
            )
        ],
    )


def _import_api(
    monkeypatch: pytest.MonkeyPatch,
    db_dsn: str,
    *,
    default_location: str | None,
):
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    if default_location is None:
        monkeypatch.delenv("STYLEFORGE_DEFAULT_LOCATION", raising=False)
    else:
        monkeypatch.setenv("STYLEFORGE_DEFAULT_LOCATION", default_location)
    sys.modules.pop("styleforge.api", None)
    return importlib.import_module("styleforge.api")


def _fake_workflow(tool) -> object:
    import types

    return types.SimpleNamespace(weather_tool=tool)


def test_location_param_resolves_named_city(db_dsn, monkeypatch) -> None:
    tool = RecordingWeatherTool()
    api = _import_api(monkeypatch, db_dsn, default_location="上海")
    api.get_workflow = lambda: _fake_workflow(tool)
    today = date.today().isoformat()

    with TestClient(api.app) as client:
        response = client.get("/weather/now", params={"location": "北京"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["requested_location"] == "北京"
    assert tool.calls[0].location == "北京"
    assert tool.calls[0].date == today


def test_coordinates_bypass_city_name(db_dsn, monkeypatch) -> None:
    tool = RecordingWeatherTool()
    api = _import_api(monkeypatch, db_dsn, default_location="上海")
    api.get_workflow = lambda: _fake_workflow(tool)

    with TestClient(api.app) as client:
        response = client.get(
            "/weather/now",
            params={"latitude": 39.9, "longitude": 116.4},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert tool.calls[0].latitude == 39.9
    assert tool.calls[0].longitude == 116.4
    assert tool.calls[0].location == ""


def test_location_and_coordinates_are_mutually_exclusive(
    db_dsn, monkeypatch
) -> None:
    api = _import_api(monkeypatch, db_dsn, default_location="上海")
    api.get_workflow = lambda: _fake_workflow(RecordingWeatherTool())

    with TestClient(api.app) as client:
        both = client.get(
            "/weather/now",
            params={"location": "北京", "latitude": 39.9, "longitude": 116.4},
        )
        lone = client.get("/weather/now", params={"location": "北京", "latitude": 39.9})

    assert both.status_code == 422
    assert lone.status_code == 422


def test_no_params_uses_configured_default_city(db_dsn, monkeypatch) -> None:
    tool = RecordingWeatherTool()
    api = _import_api(monkeypatch, db_dsn, default_location="上海")
    api.get_workflow = lambda: _fake_workflow(tool)
    today = date.today().isoformat()

    with TestClient(api.app) as client:
        response = client.get("/weather/now")

    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert tool.calls[0].location == "上海"
    assert tool.calls[0].date == today


def test_no_default_location_returns_honest_unavailable(db_dsn, monkeypatch) -> None:
    api = _import_api(monkeypatch, db_dsn, default_location=None)
    api.get_workflow = lambda: _fake_workflow(RecordingWeatherTool())

    with TestClient(api.app) as client:
        response = client.get("/weather/now")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "location_required"


def test_disabled_weather_tool_returns_unavailable(db_dsn, monkeypatch) -> None:
    api = _import_api(monkeypatch, db_dsn, default_location="上海")
    api.get_workflow = lambda: _fake_workflow(None)

    with TestClient(api.app) as client:
        response = client.get("/weather/now")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "weather_disabled"
