"""Build a clean simulated wardrobe database from Mytheresa + Polyvore subsets.

Selection (deterministic, seeded):
- ``my_outfits`` complete Mytheresa outfits, every item present with a real image
- ``pv_outfits`` complete Polyvore outfits, every item present with a real image
- one random sample per representative adult Mytheresa category (level-2 type)

Only the selected items are written to the catalog, so ``catalog_items`` equals
the wardrobe contents exactly (no extra garment data). The previous database is
backed up before being replaced.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.garments2look import ImagePathResolver, normalize_catalog_item
from styleforge.data.json_stream import iter_json_object
from styleforge.data.mytheresa import (
    MytheresaImagePathResolver,
    normalize_mytheresa_item,
)
from styleforge.data.outfits import normalize_outfit
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import (
    database_session,
    initialize_database,
)
from styleforge.repositories.dataset_source_repository import register_dataset_source
from styleforge.repositories.item_image_repository import replace_item_images
from styleforge.repositories.outfit_repository import upsert_outfits

DEMO_USER_ID = "demo-user"
ADULT_SKIP_TOKENS = ("baby", "girls'", "boys'", "diaper")
DEFAULT_REVISION = "simulated-wardrobe-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mytheresa_primary_filename(record: dict[str, Any]) -> str:
    """Return the primary product filename, or '' when none is usable."""
    images = record.get("images")
    if not isinstance(images, dict):
        return ""
    product = images.get("product")
    if not isinstance(product, dict):
        return ""
    for key in ("full", "partial"):
        group = product.get(key)
        if isinstance(group, list):
            for raw in group:
                name = str(raw).replace("\\", "/").strip()
                if name and not name.startswith("http"):
                    return Path(name).name
    return ""


def _is_adult_category(type_value: str) -> bool:
    lowered = type_value.lower()
    return not any(token in lowered for token in ADULT_SKIP_TOKENS)


def _level2_type(type_value: str) -> str:
    parts = type_value.split("::")
    return "::".join(parts[:2])


def _select_mytheresa_ids(
    metadata_path: Path,
    outfit_path: Path,
    image_root: Path,
    rng: random.Random,
    *,
    outfits_to_pick: int,
    items_per_category: int,
    category_count: int,
) -> tuple[set[str], dict[str, object], set[str]]:
    """Pick Mytheresa outfit items + representative-category items.

    Returns ``(selected_item_ids, stats, selected_outfit_ids)``.
    """
    resolver = MytheresaImagePathResolver(image_root)
    has_image: set[str] = set()
    category_groups: dict[str, list[str]] = defaultdict(list)

    for item_id, record in iter_json_object(metadata_path):
        filename = _mytheresa_primary_filename(record)
        if not filename:
            continue
        relative = resolver.relative_path(item_id, filename)
        absolute = image_root.joinpath(*Path(relative).parts)
        if not absolute.is_file():
            continue
        has_image.add(item_id)
        type_value = record.get("type", "")
        if _is_adult_category(type_value):
            category_groups[_level2_type(type_value)].append(item_id)

    # 1) Representative categories: keep the N largest adult categories.
    top_categories = [
        category
        for category, _ in sorted(
            category_groups.items(), key=lambda kv: len(kv[1]), reverse=True
        )[:category_count]
    ]
    category_picked: list[str] = []
    for category in top_categories:
        pool = category_groups[category]
        category_picked.extend(rng.sample(pool, min(items_per_category, len(pool))))

    # 2) Complete outfits: every item must have a real image.
    outfit_records: list[tuple[str, dict[str, Any]]] = []
    for outfit_id, record in iter_json_object(outfit_path):
        outfit = record.get("outfit")
        if not isinstance(outfit, dict) or not outfit:
            continue
        if all(item_id in has_image for item_id in outfit):
            outfit_records.append((outfit_id, record))
    rng.shuffle(outfit_records)
    selected_outfits = outfit_records[:outfits_to_pick]
    selected_outfit_ids = {outfit_id for outfit_id, _record in selected_outfits}
    outfit_picked = [
        item_id
        for _oid, record in selected_outfits
        for item_id in record.get("outfit", {})
    ]

    selected_ids = set(category_picked) | set(outfit_picked)
    category_stats = {
        "categories_selected": top_categories,
        "category_item_count": len(category_picked),
        "outfit_count": len(selected_outfits),
        "outfit_item_count": len(set(outfit_picked)),
        "selected_item_count": len(selected_ids),
    }
    return selected_ids, category_stats, selected_outfit_ids


def _select_polyvore_ids(
    metadata_path: Path,
    outfit_path: Path,
    image_root: Path,
    rng: random.Random,
    *,
    outfits_to_pick: int,
) -> tuple[set[str], dict[str, object], set[str]]:
    """Pick Polyvore outfit items whose images exist under the image root.

    Returns ``(selected_item_ids, stats, selected_outfit_ids)``.
    """
    resolver = ImagePathResolver(image_root)
    has_image: set[str] = set()
    for item_id, record in iter_json_object(metadata_path):
        relative = resolver.relative_path(item_id, record)
        absolute = image_root.joinpath(*Path(relative).parts)
        if absolute.is_file():
            has_image.add(item_id)

    outfit_records: list[tuple[str, dict[str, Any]]] = []
    for outfit_id, record in iter_json_object(outfit_path):
        outfit = record.get("outfit")
        if not isinstance(outfit, dict) or not outfit:
            continue
        if all(item_id in has_image for item_id in outfit):
            outfit_records.append((outfit_id, record))
    rng.shuffle(outfit_records)
    selected_outfits = outfit_records[:outfits_to_pick]
    selected_outfit_ids = {outfit_id for outfit_id, _record in selected_outfits}
    selected_ids = {
        item_id
        for _oid, record in selected_outfits
        for item_id in record.get("outfit", {})
    }
    stats = {
        "outfit_count": len(selected_outfits),
        "outfit_item_count": len(selected_ids),
    }
    return selected_ids, stats, selected_outfit_ids


def _insert_wardrobe_items(
    connection,
    user_id: str,
    item_ids: list[str],
) -> None:
    stamp = _now()
    connection.executemany(
        """
        INSERT INTO wardrobe_items(user_id, item_id, active, favorite, notes, added_at)
        VALUES (%s, %s, 1, 0, '', %s)
        ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1
        """,
        ((user_id, item_id, stamp) for item_id in item_ids),
    )


def build_simulated_wardrobe(
    *,
    mytheresa_metadata: Path,
    mytheresa_outfits: Path,
    mytheresa_image_root: Path,
    polyvore_metadata: Path,
    polyvore_outfits: Path,
    polyvore_image_root: Path,
    database_path: str,
    seed: int,
    my_outfits: int,
    pv_outfits: int,
    items_per_category: int,
    category_count: int,
) -> dict[str, object]:
    for label, path in (
        ("mytheresa metadata", mytheresa_metadata),
        ("mytheresa outfits", mytheresa_outfits),
        ("polyvore metadata", polyvore_metadata),
        ("polyvore outfits", polyvore_outfits),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    for label, root in (
        ("mytheresa image root", mytheresa_image_root),
        ("polyvore image root", polyvore_image_root),
    ):
        if not root.is_dir():
            raise FileNotFoundError(f"{label} not found: {root}")

    rng = random.Random(seed)

    print("Selecting Mytheresa subset...", flush=True)
    my_ids, my_stats, my_outfit_ids = _select_mytheresa_ids(
        mytheresa_metadata,
        mytheresa_outfits,
        mytheresa_image_root,
        rng,
        outfits_to_pick=my_outfits,
        items_per_category=items_per_category,
        category_count=category_count,
    )
    print("Selecting Polyvore subset...", flush=True)
    pv_ids, pv_stats, pv_outfit_ids = _select_polyvore_ids(
        polyvore_metadata,
        polyvore_outfits,
        polyvore_image_root,
        rng,
        outfits_to_pick=pv_outfits,
    )
    picked_outfit_ids = my_outfit_ids | pv_outfit_ids
    my_ids_list = sorted(my_ids)
    pv_ids_list = sorted(pv_ids)
    print(
        f"Selected: mytheresa={len(my_ids_list)} polyvore={len(pv_ids_list)}",
        flush=True,
    )

    # PostgreSQL-backed: the schema already lives on the server, so the old
    # SQLite file-reset dance (backup, unlink .db/-wal/-shm, rebuild) is gone.
    initialize_database(database_path)

    my_imported = 0
    my_images = 0
    pv_imported = 0
    my_resolver = MytheresaImagePathResolver(mytheresa_image_root)
    pv_resolver = ImagePathResolver(polyvore_image_root)

    with database_session(database_path) as connection:
        register_dataset_source(
            connection,
            source="mytheresa",
            image_root=mytheresa_image_root,
            metadata_path=mytheresa_metadata,
            source_revision=DEFAULT_REVISION,
        )
        register_dataset_source(
            connection,
            source="polyvore",
            image_root=polyvore_image_root,
            metadata_path=polyvore_metadata,
            source_revision=DEFAULT_REVISION,
        )

    my_item_batch: list[Any] = []
    my_image_batch: list[Any] = []
    my_id_set = set(my_ids_list)
    for item_id, record in iter_json_object(mytheresa_metadata):
        if item_id not in my_id_set:
            continue
        item, images = normalize_mytheresa_item(item_id, record, my_resolver)
        my_item_batch.append(item)
        my_image_batch.extend(images)
        if len(my_item_batch) >= 500:
            with database_session(database_path) as connection:
                my_imported += upsert_items(connection, my_item_batch, DEFAULT_REVISION)
                my_images += replace_item_images(connection, my_image_batch)
            my_item_batch.clear()
            my_image_batch.clear()
    if my_item_batch:
        with database_session(database_path) as connection:
            my_imported += upsert_items(connection, my_item_batch, DEFAULT_REVISION)
            my_images += replace_item_images(connection, my_image_batch)

    pv_id_set = set(pv_ids_list)
    pv_item_batch: list[Any] = []
    for item_id, record in iter_json_object(polyvore_metadata):
        if item_id not in pv_id_set:
            continue
        pv_item_batch.append(normalize_catalog_item(item_id, record, pv_resolver))
        if len(pv_item_batch) >= 500:
            with database_session(database_path) as connection:
                pv_imported += upsert_items(connection, pv_item_batch, DEFAULT_REVISION)
            pv_item_batch.clear()
    if pv_item_batch:
        with database_session(database_path) as connection:
            pv_imported += upsert_items(connection, pv_item_batch, DEFAULT_REVISION)

    # Import exactly the picked outfits (only the selected outfit ids).
    outfit_count = 0
    relation_count = 0
    outfit_batch: list[Any] = []
    for source_path in (mytheresa_outfits, polyvore_outfits):
        for outfit_id, record in iter_json_object(source_path):
            if outfit_id not in picked_outfit_ids:
                continue
            outfit_batch.append(normalize_outfit(outfit_id, record))
            if len(outfit_batch) >= 500:
                with database_session(database_path) as connection:
                    imported, relations = upsert_outfits(
                        connection, outfit_batch, DEFAULT_REVISION
                    )
                    outfit_count += imported
                    relation_count += relations
                outfit_batch.clear()
    if outfit_batch:
        with database_session(database_path) as connection:
            imported, relations = upsert_outfits(
                connection, outfit_batch, DEFAULT_REVISION
            )
            outfit_count += imported
            relation_count += relations

    with database_session(database_path) as connection:
        _insert_wardrobe_items(
            connection,
            DEMO_USER_ID,
            sorted(my_id_set | pv_id_set),
        )
        image_stats = {
            row["image_status"]: row["count"]
            for row in connection.execute(
                "SELECT image_status, COUNT(*) AS count FROM catalog_items "
                "GROUP BY image_status"
            )
        }
        source_counts = {
            row["source"]: row["count"]
            for row in connection.execute(
                "SELECT source, COUNT(*) AS count FROM catalog_items "
                "GROUP BY source"
            )
        }
        wardrobe_total = connection.execute(
            "SELECT COUNT(*) FROM wardrobe_items WHERE active = 1"
        ).fetchone()[0]

    report: dict[str, object] = {
        "generated_at": _now(),
        "seed": seed,
        "database_path": str(database_path),
        "backup_path": None,
        "selection": {"mytheresa": my_stats, "polyvore": pv_stats},
        "imported": {
            "mytheresa_items": my_imported,
            "mytheresa_images": my_images,
            "polyvore_items": pv_imported,
        },
        "outfits": {"imported": outfit_count, "relations": relation_count},
        "catalog": {
            "by_source": source_counts,
            "by_image_status": image_stats,
            "total": sum(source_counts.values()),
        },
        "wardrobe": {"user_id": DEMO_USER_ID, "total": wardrobe_total},
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mytheresa-metadata", type=Path, required=True)
    parser.add_argument("--mytheresa-outfits", type=Path, required=True)
    parser.add_argument("--mytheresa-image-root", type=Path, required=True)
    parser.add_argument("--polyvore-metadata", type=Path, required=True)
    parser.add_argument("--polyvore-outfits", type=Path, required=True)
    parser.add_argument("--polyvore-image-root", type=Path, required=True)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--my-outfits", type=int, default=150)
    parser.add_argument("--pv-outfits", type=int, default=50)
    parser.add_argument("--items-per-category", type=int, default=20)
    parser.add_argument("--category-count", type=int, default=60)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "imports" / "build_simulated_wardrobe.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.my_outfits < 0 or args.pv_outfits < 0:
        raise SystemExit("outfit counts must be non-negative")
    if args.items_per_category < 1 or args.category_count < 1:
        raise SystemExit("items-per-category and category-count must be positive")
    report = build_simulated_wardrobe(
        mytheresa_metadata=args.mytheresa_metadata,
        mytheresa_outfits=args.mytheresa_outfits,
        mytheresa_image_root=args.mytheresa_image_root,
        polyvore_metadata=args.polyvore_metadata,
        polyvore_outfits=args.polyvore_outfits,
        polyvore_image_root=args.polyvore_image_root,
        database_path=args.database,
        seed=args.seed,
        my_outfits=args.my_outfits,
        pv_outfits=args.pv_outfits,
        items_per_category=args.items_per_category,
        category_count=args.category_count,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
