"""Reusable deterministic wardrobes for recommendation-quality evaluation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import Connection
from styleforge.repositories.wardrobe_repository import add_items


DEFAULT_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "wardrobes.json"
FIXTURE_NAMESPACE = uuid.UUID("27bf3ed4-fb21-5f0e-9324-15023de2f207")


@dataclass(frozen=True)
class WardrobeFixture:
    name: str
    user_id: str
    purpose: str
    items: tuple[CatalogItem, ...]
    item_keys: tuple[str, ...]

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.items)


def fixture_item_id(key: str) -> str:
    return str(uuid.uuid5(FIXTURE_NAMESPACE, key))


def _catalog_item(raw: dict[str, Any]) -> CatalogItem:
    key = str(raw["key"]).strip()
    item_type = str(raw["item_type"]).strip()
    image_filename = str(raw.get("image_filename") or "")
    relative_image_path = str(raw.get("relative_image_path") or "")
    default_image_status = "available" if relative_image_path else "unbound"
    return CatalogItem(
        item_id=fixture_item_id(key),
        dataset_item_id=str(raw.get("dataset_item_id") or f"fixture:{key}"),
        source=str(raw.get("source") or "eval_fixture"),
        gender=str(raw.get("gender") or "women"),
        item_type=item_type,
        main_category=str(raw.get("main_category") or item_type),
        name=str(raw.get("name") or ""),
        color=str(raw.get("color") or ""),
        description=str(raw.get("description") or ""),
        features=tuple(str(value) for value in raw.get("features") or ()),
        image_filename=image_filename,
        relative_image_path=relative_image_path,
        image_status=ImageStatus(str(raw.get("image_status") or default_image_status)),
        embedding_status=EmbeddingStatus(
            str(raw.get("embedding_status") or "pending")
        ),
    )


def load_fixture(name: str, path: Path = DEFAULT_FIXTURE_PATH) -> WardrobeFixture:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "styleforge.wardrobe-fixtures.v1":
        raise ValueError("Unsupported wardrobe fixture schema")
    raw_items = payload.get("items") or []
    by_key: dict[str, CatalogItem] = {}
    for raw in raw_items:
        key = str(raw.get("key") or "").strip()
        if not key or key in by_key:
            raise ValueError(f"Invalid or duplicate fixture item key: {key!r}")
        by_key[key] = _catalog_item(raw)
    profile = (payload.get("profiles") or {}).get(name)
    if profile is None:
        available = ", ".join(sorted((payload.get("profiles") or {}).keys()))
        raise KeyError(f"Unknown wardrobe fixture {name!r}; available: {available}")
    item_keys = tuple(str(value) for value in profile.get("item_keys") or ())
    missing = [key for key in item_keys if key not in by_key]
    if missing:
        raise ValueError(f"Fixture {name!r} references unknown items: {missing}")
    if len(item_keys) != len(set(item_keys)):
        raise ValueError(f"Fixture {name!r} contains duplicate item keys")
    return WardrobeFixture(
        name=name,
        user_id=str(profile["user_id"]),
        purpose=str(profile.get("purpose") or ""),
        items=tuple(by_key[key] for key in item_keys),
        item_keys=item_keys,
    )


def list_fixture_names(path: Path = DEFAULT_FIXTURE_PATH) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(sorted((payload.get("profiles") or {}).keys()))


def seed_fixture(
    connection: Connection,
    name: str,
    path: Path = DEFAULT_FIXTURE_PATH,
) -> WardrobeFixture:
    fixture = load_fixture(name, path)
    upsert_items(connection, list(fixture.items), "wardrobe-fixtures-v1")
    add_items(connection, fixture.user_id, fixture.item_ids)
    return fixture
