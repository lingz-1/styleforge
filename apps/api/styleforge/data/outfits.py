"""Garments2Look-Polyvore outfit record normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class DatasetOutfit:
    outfit_id: str
    source: str
    split: str
    gender: str
    description: str
    style: str
    season: str
    occasion: str
    theme: str
    color_palette: tuple[str, ...]
    is_official_outfit: bool
    is_official_look: bool
    items: tuple[tuple[str, str], ...]


def normalize_outfit(outfit_id: str, record: dict[str, Any]) -> DatasetOutfit:
    if not isinstance(record, dict):
        raise TypeError(f"Outfit {outfit_id!r} must be an object")
    info = record.get("outfit_info")
    info = info if isinstance(info, dict) else {}
    raw_palette = info.get("color_palette")
    palette = (
        tuple(_text(value) for value in raw_palette if _text(value))
        if isinstance(raw_palette, list)
        else ()
    )
    raw_items = record.get("outfit")
    if not isinstance(raw_items, dict) or not raw_items:
        raise ValueError(f"Outfit {outfit_id!r} has no item relationships")
    items = tuple((item_id, _text(description)) for item_id, description in raw_items.items())
    return DatasetOutfit(
        outfit_id=outfit_id,
        source=_text(record.get("source")) or "polyvore",
        split=_text(record.get("section")),
        gender=_text(record.get("gender")),
        description=_text(info.get("outfit_description")),
        style=_text(info.get("style")),
        season=_text(info.get("season")),
        occasion=_text(info.get("occasion")),
        theme=_text(info.get("theme")),
        color_palette=palette,
        is_official_outfit=bool(record.get("is_official_outfit")),
        is_official_look=bool(record.get("is_official_look")),
        items=items,
    )

