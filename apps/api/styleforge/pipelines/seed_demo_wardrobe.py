"""Create a deterministic metadata-only demo wardrobe from the local catalog."""

from __future__ import annotations

import argparse
import json

from styleforge.core.config import Settings
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import (
    add_items,
    choose_demo_items,
    choose_demo_outfit_items,
    deactivate_all_items,
)


DEFAULT_ITEM_TYPES = ("top", "pants", "skirt", "shoes", "outwear", "bag")


def seed_demo_wardrobe(
    database_path: str,
    user_id: str,
    per_type: int,
    outfit_count: int = 8,
) -> dict[str, object]:
    initialize_database(database_path)
    with database_session(database_path) as connection:
        item_ids, source_outfit_ids = choose_demo_outfit_items(connection, outfit_count)
        strategy = "complete_train_outfits"
        if not item_ids:
            item_ids = choose_demo_items(connection, DEFAULT_ITEM_TYPES, per_type)
            source_outfit_ids = []
            strategy = "balanced_catalog_fallback"
        deactivated_count = deactivate_all_items(connection, user_id)
        added_count = add_items(connection, user_id, item_ids)
    return {
        "user_id": user_id,
        "selected_item_count": added_count,
        "item_types": list(DEFAULT_ITEM_TYPES),
        "per_type": per_type,
        "requested_outfit_count": outfit_count,
        "source_outfit_ids": source_outfit_ids,
        "selection_strategy": strategy,
        "deactivated_previous_items": deactivated_count,
        "image_requirement": "not_required_for_metadata_baseline",
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--per-type", type=int, default=6)
    parser.add_argument("--outfit-count", type=int, default=8)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = seed_demo_wardrobe(
        args.database,
        args.user_id,
        args.per_type,
        outfit_count=args.outfit_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
