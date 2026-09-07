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
DEFAULT_P_OUTFIT_CASES_PATH = Path(__file__).with_name("cases") / "p_outfit.json"
FIXTURE_NAMESPACE = uuid.UUID("27bf3ed4-fb21-5f0e-9324-15023de2f207")

_COLOR_WORDS = (
    "black",
    "white",
    "grey",
    "gray",
    "beige",
    "brown",
    "navy",
    "blue",
    "green",
    "red",
    "orange",
    "pink",
    "purple",
    "yellow",
    "gold",
    "silver",
    "khaki",
    "cream",
    "ivory",
    "rust",
)


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


def _raw_p_outfit_color(item: dict[str, Any]) -> str:
    """Extract only an explicitly mentioned color from frozen raw item text."""

    text = " ".join(
        str(item.get(field) or "")
        for field in ("title", "url_name", "description")
    ).lower()
    return next((color for color in _COLOR_WORDS if color in text), "unknown")


def _load_p_outfit_items(
    path: Path = DEFAULT_P_OUTFIT_CASES_PATH,
) -> tuple[dict[str, CatalogItem], dict[str, tuple[str, ...]]]:
    """Load raw Polyvore cards without outfit-title or occasion leakage.

    ``p_outfit.json`` is a self-contained, frozen copy of public Polyvore
    metadata.  Outfit titles, generated requests and golden relationships are
    deliberately ignored here: they belong to the evaluator, not the wardrobe
    facts visible to the recommendation system.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "styleforge.p-outfit-cases.v1":
        raise ValueError("Unsupported p-outfit fixture source schema")
    by_key: dict[str, CatalogItem] = {}
    keys_by_set: dict[str, tuple[str, ...]] = {}
    for case in payload.get("cases") or ():
        set_id = str(case.get("source_set_id") or "").strip()
        keys: list[str] = []
        for item in case.get("items") or ():
            dataset_item_id = str(item.get("item_id") or "").strip()
            if not dataset_item_id:
                continue
            key = f"polyvore_raw_{dataset_item_id}"
            keys.append(key)
            if key in by_key:
                continue
            name = str(item.get("title") or item.get("url_name") or "").strip()
            raw = {
                "key": key,
                "source": "polyvore",
                "dataset_item_id": dataset_item_id,
                "item_type": str(item.get("item_type") or "").strip(),
                "main_category": str(item.get("semantic_category") or "").strip(),
                "name": name,
                "color": _raw_p_outfit_color(item),
                "description": str(item.get("description") or "").strip(),
                "features": [],
                "image_filename": f"{dataset_item_id}.jpg",
                "relative_image_path": f"nondisjoint/train/{dataset_item_id}.jpg",
            }
            by_key[key] = _catalog_item(raw)
        if set_id:
            keys_by_set[set_id] = tuple(keys)
    return by_key, keys_by_set


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
    raw_p_outfit, p_outfit_keys_by_set = _load_p_outfit_items()
    by_key.update(raw_p_outfit)
    profile = (payload.get("profiles") or {}).get(name)
    if profile is None:
        available = ", ".join(sorted((payload.get("profiles") or {}).keys()))
        raise KeyError(f"Unknown wardrobe fixture {name!r}; available: {available}")
    item_keys_list = [str(value) for value in profile.get("item_keys") or ()]
    for set_id in profile.get("p_outfit_set_ids") or ():
        normalized_set_id = str(set_id)
        if normalized_set_id not in p_outfit_keys_by_set:
            raise ValueError(
                f"Fixture {name!r} references unknown p-outfit set: {normalized_set_id}"
            )
        item_keys_list.extend(p_outfit_keys_by_set[normalized_set_id])
    excluded = {str(value) for value in profile.get("exclude_item_keys") or ()}
    item_keys = tuple(key for key in dict.fromkeys(item_keys_list) if key not in excluded)
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
    *,
    user_id: str | None = None,
) -> WardrobeFixture:
    fixture = load_fixture(name, path)
    if user_id is not None:
        fixture = WardrobeFixture(
            name=fixture.name,
            user_id=user_id,
            purpose=fixture.purpose,
            items=fixture.items,
            item_keys=fixture.item_keys,
        )
    upsert_items(connection, list(fixture.items), "wardrobe-fixtures-v1")
    add_items(connection, fixture.user_id, fixture.item_ids)
    return fixture
