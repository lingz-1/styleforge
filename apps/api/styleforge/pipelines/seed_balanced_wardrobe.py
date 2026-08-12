"""Seed an image-backed demo wardrobe with broad style coverage."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone

from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus
from styleforge.core.scoring import item_formality_score
from styleforge.repositories.database import database_session, initialize_database


BALANCED_QUOTAS = {
    "top": 12,
    "pants": 6,
    "skirt": 6,
    "shoes": 12,
    "dress": 6,
    "jumpsuit": 3,
    "outwear": 8,
    "bag": 8,
    "neckwear": 4,
    "eyewear": 4,
    "earrings": 4,
}

MIXED_LARGE_QUOTAS = {
    "top": 32,
    "pants": 16,
    "skirt": 16,
    "shoes": 32,
    "dress": 20,
    "jumpsuit": 8,
    "outwear": 20,
    "bag": 16,
    "neckwear": 6,
    "eyewear": 6,
    "earrings": 6,
    "necklace": 6,
    "bracelet": 6,
    "hats": 6,
    "legwear": 4,
    "watches": 4,
}

PROFILES = {
    "balanced": BALANCED_QUOTAS,
    "mixed-large": MIXED_LARGE_QUOTAS,
}

STYLE_KEYWORDS = {
    "business": (
        "tailored",
        "button-up",
        "button up",
        "blazer",
        "trouser",
        "pencil skirt",
        "loafer",
        "pump",
        "structured",
    ),
    "formal": (
        "formal",
        "elegant",
        "evening",
        "cocktail",
        "satin",
        "silk",
        "gown",
        "embellished",
    ),
    "minimalist": (
        "minimal",
        "simple",
        "clean",
        "solid",
        "monochrome",
        "classic",
        "sleek",
    ),
    "casual": (
        "casual",
        "denim",
        "jeans",
        "tee",
        "t-shirt",
        "knit",
        "relaxed",
        "flat",
    ),
    "sporty": (
        "sport",
        "athletic",
        "running",
        "sneaker",
        "active",
        "jogger",
        "performance",
    ),
    "romantic": (
        "romantic",
        "floral",
        "lace",
        "ruffle",
        "bow",
        "pastel",
        "pleated",
    ),
    "street": (
        "street",
        "graphic",
        "oversized",
        "combat",
        "platform",
        "distressed",
        "moto",
    ),
    "vintage": (
        "vintage",
        "retro",
        "plaid",
        "tweed",
        "mary jane",
        "victorian",
        "heritage",
    ),
}

STYLE_ORDER = tuple(STYLE_KEYWORDS)


def _catalog_item(row) -> CatalogItem:
    return CatalogItem(
        item_id=row["item_id"],
        source=row["source"],
        gender=row["gender"],
        item_type=row["item_type"],
        main_category=row["main_category"],
        name=row["name"],
        color=row["color"],
        description=row["description"],
        features=tuple(json.loads(row["features_json"])),
        image_filename=row["image_filename"],
        relative_image_path=row["relative_image_path"],
        image_status=ImageStatus(row["image_status"]),
        embedding_status=EmbeddingStatus(row["embedding_status"]),
        raw_json_hash=row["raw_json_hash"],
    )


def _style_score(item: CatalogItem, style: str) -> float:
    text = f"{item.name} {item.description} {' '.join(item.features)}".lower()
    keyword_score = sum(1.0 for keyword in STYLE_KEYWORDS[style] if keyword in text)
    if style == "business":
        return keyword_score * 12.0 + item_formality_score(item, "business")
    if style == "formal":
        return keyword_score * 14.0 + 0.5 * item_formality_score(item, "formal")
    return keyword_score * 20.0


def _load_candidates(
    connection,
    item_type: str,
    audience: str | None,
) -> list[CatalogItem]:
    sql = (
        "SELECT * FROM catalog_items "
        "WHERE item_type = %s "
        "  AND image_status = 'available' "
        "  AND embedding_status = 'ready'"
    )
    parameters: list[str] = [item_type]
    if audience is not None:
        sql += " AND gender = %s"
        parameters.append(audience)
    sql += " ORDER BY item_id"
    rows = connection.execute(sql, parameters).fetchall()
    return [_catalog_item(row) for row in rows]


def _select_mixed_items(
    candidates: list[CatalogItem],
    limit: int,
) -> list[tuple[CatalogItem, str]]:
    selected: list[tuple[CatalogItem, str]] = []
    selected_ids: set[str] = set()
    color_counts: Counter[str] = Counter()
    style_targets = Counter(STYLE_ORDER[index % len(STYLE_ORDER)] for index in range(limit))

    for style in STYLE_ORDER:
        for _ in range(style_targets[style]):
            remaining = [item for item in candidates if item.item_id not in selected_ids]
            if not remaining:
                break
            item = min(
                remaining,
                key=lambda value: (
                    -_style_score(value, style),
                    color_counts[value.color.strip().lower()],
                    value.item_id,
                ),
            )
            selected.append((item, style))
            selected_ids.add(item.item_id)
            color_counts[item.color.strip().lower()] += 1
    return selected


def seed_balanced_wardrobe(
    database_path: str,
    *,
    user_id: str,
    profile: str = "mixed-large",
    audience: str | None = None,
    replace: bool = False,
) -> dict[str, object]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    initialize_database(database_path)
    selected: list[tuple[CatalogItem, str]] = []
    with database_session(database_path) as connection:
        available_audiences = {
            row["gender"]
            for row in connection.execute(
                "SELECT DISTINCT gender FROM catalog_items "
                "WHERE image_status = 'available' AND embedding_status = 'ready'"
            )
        }
        if audience is None and len(available_audiences) > 1:
            raise ValueError(
                "audience is required when the catalog contains multiple audiences"
            )
        if replace:
            connection.execute("DELETE FROM wardrobe_items WHERE user_id = %s", (user_id,))
        for item_type, quota in PROFILES[profile].items():
            candidates = _load_candidates(connection, item_type, audience)
            selected.extend(_select_mixed_items(candidates, quota))
        added_at = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT INTO wardrobe_items("
            "  user_id, item_id, active, favorite, notes, added_at"
            ") VALUES (%s, %s, 1, 0, %s, %s) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1",
            (
                (
                    user_id,
                    item.item_id,
                    f"demo_seed:{profile}:{style}",
                    added_at,
                )
                for item, style in selected
            ),
        )
        active_total = connection.execute(
            "SELECT COUNT(*) FROM wardrobe_items WHERE user_id = %s AND active = 1",
            (user_id,),
        ).fetchone()[0]

    by_type = Counter(item.item_type for item, _ in selected)
    by_slot = Counter(infer_slot(item.item_type) for item, _ in selected)
    by_style = Counter(style for _, style in selected)
    return {
        "status": "completed",
        "user_id": user_id,
        "profile": profile,
        "audience": audience,
        "selected_items": len(selected),
        "active_wardrobe_items": active_total,
        "by_type": dict(sorted(by_type.items())),
        "by_slot": dict(sorted(by_slot.items())),
        "by_style": dict(sorted(by_style.items())),
        "replace": replace,
    }


def _build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="mixed-large")
    parser.add_argument(
        "--audience",
        choices=("women", "men", "girls", "boys", "baby", "life"),
    )
    parser.add_argument("--replace", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    report = seed_balanced_wardrobe(
        args.database,
        user_id=args.user_id,
        profile=args.profile,
        audience=args.audience,
        replace=args.replace,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
