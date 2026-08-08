"""Structural fingerprints for cross-request outfit diversity memory.

``CatalogItem.color`` is free text (e.g. "Charcoal Gray"); ``color_family``
normalizes it to one of a small set of families. ``structure_signature`` then
summarizes an outfit by category structure, dominant color families, style
tags, and layer count — the four facets the composer avoids repeating.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import CatalogItem, StructureSignature
from styleforge.core.scoring import normalize_color
from styleforge.core.slots import base_slot

# Ordered rules: earlier families win on overlapping tokens, so
# "charcoal gray" maps to dark_neutral rather than gray.
COLOR_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("black", ("black",)),
    ("dark_neutral", ("charcoal", "dark gray", "dark grey", "anthracite", "graphite")),
    ("gray", ("gray", "grey")),
    ("white", ("white", "ivory")),
    ("light_neutral", ("cream", "beige", "sand", "off-white", "tan", "nude")),
    ("navy", ("navy", "midnight")),
    ("blue", ("blue", "azure", "cobalt")),
    ("burgundy", ("burgundy", "wine", "maroon", "bordeaux", "oxblood")),
    ("red", ("red", "scarlet", "crimson")),
    ("pink", ("pink", "rose", "blush", "magenta")),
    ("purple", ("purple", "violet", "lavender", "plum")),
    ("brown", ("brown", "camel", "chocolate", "espresso", "coffee")),
    ("olive", ("olive", "khaki", "moss")),
    ("green", ("green", "emerald", "forest", "sage")),
    ("yellow", ("yellow", "mustard", "citron")),
    ("orange", ("orange", "coral", "amber", "terracotta")),
    ("metallic", ("gold", "silver", "bronze", "metallic")),
    ("multicolor", ("multi", "printed", "pattern", "floral", "stripe")),
)

COLOR_FAMILIES: dict[str, tuple[str, ...]] = dict(COLOR_FAMILY_RULES)

# (tag, keywords). Keywords are matched as substrings against the lower-cased
# name + description + features; keep tokens >= 4 chars to avoid noise like
# "tee" matching "meet".
STYLE_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("formal", ("blazer", "suit", "tailored", "oxford", "trousers", "tuxedo")),
    ("business", ("shirt", "collar", "blouse", "pump", "tie")),
    ("casual", ("denim", "jeans", "t-shirt", "t shirt", "tees", "chino")),
    ("street", ("hoodie", "sweatshirt", "sneaker", "cargo", "street")),
    ("vintage", ("vintage", "retro", "check", "plaid", "tweed", "gingham")),
    ("romantic", ("silk", "satin", "lace", "floral", "ruffle")),
    ("minimalist", ("minimal", "clean", "monochrome", "plain")),
    ("sporty", ("sport", "athletic", "running", "gym", "active")),
    ("cozy", ("knit", "sweater", "wool", "cashmere", "cardigan")),
    ("evening", ("evening", "gown", "heels", "sequin")),
)

_LAYER_SLOTS = {"top", "outerwear", "one_piece", "base_layer_top"}


def color_family(color: str) -> str:
    """Map a free-text color to a family name, or ``other``."""
    normalized = normalize_color(color)
    if not normalized:
        return "other"
    for family, tokens in COLOR_FAMILY_RULES:
        for token in tokens:
            if token in normalized:
                return family
    return "other"


def style_tags(item: CatalogItem) -> tuple[str, ...]:
    """Infer style tags from name, description, and features."""
    haystack = f"{item.name} {item.description} {' '.join(item.features)}".lower()
    tags: list[str] = []
    for tag, keywords in STYLE_TAG_RULES:
        if any(keyword in haystack for keyword in keywords):
            tags.append(tag)
            if len(tags) >= 3:
                break
    return tuple(tags) or ("basic",)


def structure_signature(items: Sequence[CatalogItem]) -> StructureSignature:
    """Summarize an outfit into a deterministic cross-request fingerprint."""
    if not items:
        return StructureSignature()
    category_structure = sorted(
        {base_slot(infer_slot(item.item_type)) for item in items}
    )
    family_counts: Counter[str] = Counter(color_family(item.color) for item in items)
    dominant = [family for family, _ in family_counts.most_common(2) if family != "other"]
    if not dominant and family_counts:
        dominant = ["other"]
    style_mix: list[str] = []
    for item in items:
        for tag in style_tags(item):
            if tag not in style_mix:
                style_mix.append(tag)
    layer_count = sum(
        1 for item in items if infer_slot(item.item_type) in _LAYER_SLOTS
    )
    return StructureSignature(
        category_structure=tuple(category_structure),
        dominant_color_family=tuple(dominant),
        style_mix=tuple(style_mix[:3]) or ("basic",),
        layer_count=layer_count,
    )
