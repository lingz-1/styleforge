"""HTTP tests for the photo-recognition endpoint and attributes persistence.

The ``/wardrobes/{user_id}/items/analyze`` endpoint is tested with a fake
vision client (monkeypatched onto the api module) so no provider is hit.
Photo creation asserts the recognized attributes round-trip into the database,
and the order-import endpoints are re-exercised to prove they are unaffected.
"""

from __future__ import annotations

import base64
import importlib
import io
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from styleforge.core.config import Settings
from styleforge.vision.vision_client import (
    VisionInvalidJson,
    VisionUnavailable,
    vision_client_from_settings,
)


class FakeVisionClient:
    """Records the image bytes it is given and returns a preset analysis."""

    name = "vision.analyze_clothing"

    def __init__(self, response: dict[str, Any], error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[bytes] = []

    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        self.calls.append(image_bytes)
        if self.error is not None:
            raise self.error
        return self.response


def _photo_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), (40, 60, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _import_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STYLEFORGE_DATABASE_PATH", str(tmp_path / "vision-api.db"))
    monkeypatch.delenv("STYLEFORGE_DEFAULT_LOCATION", raising=False)
    sys.modules.pop("styleforge.api", None)
    return importlib.import_module("styleforge.api")


def test_analyze_returns_mapped_fields(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = FakeVisionClient(
        {
            "type": "t-shirt",
            "subtype": "",
            "primary_color": "black",
            "colors": ["black"],
            "pattern": "solid",
            "material": "cotton",
            "formality": "casual",
            "style": ["streetwear"],
            "season": ["summer", "fall"],
            "fit": "",
            "occasion": ["daily"],
            "cultural_origin": ["none"],
            "silhouette": "",
            "neckline": "round",
            "collar": "",
            "sleeve_length": "short",
            "length": "",
            "brand": "",
            "condition": "",
            "features": [],
            "description": "一件黑色纯棉短袖T恤",
            "confidence": 0.92,
        }
    )
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": _b64(_photo_bytes())},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["recognized"] is True
    assert payload["item_type"] == "top"
    assert payload["subtype"] == "t_shirt"
    assert payload["color"] == "black"
    assert payload["confidence"] == 0.92
    assert payload["attributes"]["season"] == ["summer", "fall"]
    assert payload["attributes"]["cultural_origin"] == ["none"]
    assert len(fake.calls) == 1
    assert fake.calls[0] == _photo_bytes()


def test_analyze_flags_low_confidence_as_not_recognized(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = FakeVisionClient({"type": "shirt", "confidence": 0.05})
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": _b64(_photo_bytes())},
        )

    assert response.status_code == 200
    assert response.json()["recognized"] is False


def test_analyze_returns_503_when_vision_disabled(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: None)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": _b64(_photo_bytes())},
        )

    assert response.status_code == 503


def test_analyze_returns_503_on_provider_failure(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = FakeVisionClient({}, error=VisionUnavailable("proxy down"))
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": _b64(_photo_bytes())},
        )

    assert response.status_code == 503
    assert "proxy down" in response.json()["detail"]


def test_analyze_returns_502_on_invalid_json(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = FakeVisionClient({}, error=VisionInvalidJson("no json"))
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": _b64(_photo_bytes())},
        )

    assert response.status_code == 502


def test_analyze_returns_422_on_invalid_base64(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = FakeVisionClient({"type": "shirt"})
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/analyze",
            json={"filename": "tee.png", "content_base64": "!!!not-base64!!!"},
        )

    assert response.status_code == 422


def test_create_photo_item_persists_attributes(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    attributes = {
        "item_type": "dress",
        "subtype": "qipao",
        "primary_color": "red",
        "season": ["summer"],
        "material": "silk",
        "cultural_origin": ["qipao-cheongsam"],
        "description": "一件红色旗袍",
        "confidence": 0.9,
    }

    with TestClient(api.app) as client:
        created = client.post(
            "/wardrobes/u1/items/photo",
            json={
                "filename": "qipao.png",
                "content_base64": _b64(_photo_bytes()),
                "item_type": "dress",
                "subtype": "qipao",
                "name": "旗袍",
                "color": "red",
                "attributes": attributes,
            },
        )

    assert created.status_code == 201
    item_id = created.json()["item"]["item_id"]

    with TestClient(api.app) as client:
        fetched = client.get("/wardrobes/u1")

    assert fetched.status_code == 200
    item = next(i for i in fetched.json()["items"] if i["item_id"] == item_id)
    assert item["attributes"]["season"] == ["summer"]
    assert item["attributes"]["cultural_origin"] == ["qipao-cheongsam"]
    assert item["description"] == "一件红色旗袍"


def test_create_photo_item_defaults_attributes_to_empty(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)

    with TestClient(api.app) as client:
        created = client.post(
            "/wardrobes/u1/items/photo",
            json={
                "filename": "tee.png",
                "content_base64": _b64(_photo_bytes()),
                "item_type": "top",
                "subtype": "t_shirt",
                "name": "T恤",
                "color": "black",
            },
        )

    assert created.status_code == 201
    item_id = created.json()["item"]["item_id"]
    with TestClient(api.app) as client:
        fetched = client.get("/wardrobes/u1")
    item = next(i for i in fetched.json()["items"] if i["item_id"] == item_id)
    assert item["attributes"] == {}


def test_vision_client_factory_respects_enabled_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STYLEFORGE_VISION_ENABLED", "false")
    assert vision_client_from_settings(Settings.from_env()) is None
    monkeypatch.setenv("STYLEFORGE_VISION_ENABLED", "true")
    assert vision_client_from_settings(Settings.from_env()) is not None


def test_order_import_flow_still_works(tmp_path, monkeypatch) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "订单数据"
    sheet.append(
        (
            "订单号", "订单提交时间", "订单状态", "店铺名称", "商品名称",
            "商品链接", "型号款式", "商品数量", "商品金额", "实付金额",
        )
    )
    sheet.append(
        (
            "order-1", "2026-01-01 12:00:00", "交易成功", "服装店",
            "蓝色格纹女衬衫", "https://item.taobao.com/item.htm?id=1",
            "蓝色;M", 1, "¥99.00", "¥89.00",
        )
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    api = _import_api(tmp_path, monkeypatch)

    with TestClient(api.app) as client:
        preview = client.post(
            "/wardrobes/u1/imports",
            json={
                "filename": "orders.xlsx",
                "content_base64": _b64(buffer.getvalue()),
                "default_audience": "women",
            },
        )
    assert preview.status_code == 201
    batch_id = preview.json()["batch"]["batch_id"]
    assert preview.json()["count"] == 1
    rows = preview.json()["rows"]
    candidate = [r for r in rows if r["decision"] == "candidate"]
    assert candidate

    with TestClient(api.app) as client:
        commit = client.post(
            f"/wardrobes/u1/imports/{batch_id}/commit",
            json={
                "selections": [
                    {
                        "row_id": candidate[0]["row_id"],
                        "item_type": "top",
                        "subtype": "shirt",
                        "color": "蓝色",
                        "size": "M",
                        "audience": "women",
                    }
                ],
                "auto_embed": False,
            },
        )
    assert commit.status_code == 200
    assert commit.json()["committed_item_count"] == 1
