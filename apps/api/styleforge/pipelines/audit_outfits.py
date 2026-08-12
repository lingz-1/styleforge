"""Audit outfit completeness and catalog coverage before importing relationships."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.data.json_stream import iter_json_object
from styleforge.repositories.database import connect, initialize_database


def _catalog_types(database_path: str) -> dict[str, str]:
    connection = connect(database_path)
    try:
        return {
            row["item_id"]: row["item_type"]
            for row in connection.execute("SELECT item_id, item_type FROM catalog_items")
        }
    finally:
        connection.close()


def audit_outfits(outfit_path: Path, database_path: str) -> dict[str, Any]:
    initialize_database(database_path)
    item_types = _catalog_types(database_path)
    by_split: Counter[str] = Counter()
    by_size: Counter[int] = Counter()
    invalid_record_count = 0
    relation_count = 0
    official_outfit_count = 0
    official_look_count = 0
    description_present = 0
    style_present = 0
    orphan_ids: set[str] = set()
    orphan_relation_count = 0
    affected_outfit_count = 0
    fully_covered_outfit_count = 0
    outfit_sizes: list[int] = []
    templates: Counter[str] = Counter()
    fully_covered_templates: Counter[str] = Counter()
    item_outfit_usage: Counter[str] = Counter()
    total = 0

    for _, record in iter_json_object(outfit_path):
        total += 1
        if not isinstance(record, dict):
            invalid_record_count += 1
            continue
        by_split[str(record.get("section") or "<missing>")] += 1
        official_outfit_count += int(bool(record.get("is_official_outfit")))
        official_look_count += int(bool(record.get("is_official_look")))
        info = record.get("outfit_info")
        if isinstance(info, dict):
            description_present += int(bool(str(info.get("outfit_description") or "").strip()))
            style_present += int(bool(str(info.get("style") or "").strip()))
        raw_outfit = record.get("outfit")
        if not isinstance(raw_outfit, dict) or not raw_outfit:
            invalid_record_count += 1
            continue
        item_ids = list(raw_outfit)
        item_outfit_usage.update(set(item_ids))
        size = len(item_ids)
        relation_count += size
        outfit_sizes.append(size)
        by_size[size] += 1
        slots = set()
        outfit_orphan_count = 0
        for item_id in item_ids:
            item_type = item_types.get(item_id)
            if item_type is None:
                orphan_ids.add(item_id)
                orphan_relation_count += 1
                outfit_orphan_count += 1
            else:
                slots.add(infer_slot(item_type))
        matched_templates = []
        if {"top", "bottom"}.issubset(slots):
            matched_templates.append("top+bottom")
        if {"top", "bottom", "footwear"}.issubset(slots):
            matched_templates.append("top+bottom+footwear")
        if "one_piece" in slots:
            matched_templates.append("one_piece")
        if {"one_piece", "footwear"}.issubset(slots):
            matched_templates.append("one_piece+footwear")
        if "outerwear" in slots:
            matched_templates.append("contains_outerwear")
        templates.update(matched_templates)
        if outfit_orphan_count:
            affected_outfit_count += 1
        else:
            fully_covered_outfit_count += 1
            fully_covered_templates.update(matched_templates)

    source_hash = hashlib.sha256()
    with outfit_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            source_hash.update(chunk)
    stat = outfit_path.stat()
    return {
        "schema_version": "styleforge.outfit-audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(outfit_path.resolve()),
            "size_bytes": stat.st_size,
            "sha256": source_hash.hexdigest(),
        },
        "catalog_path": str(database_path),
        "counts": {
            "outfits_total": total,
            "invalid_records": invalid_record_count,
            "outfit_item_relations": relation_count,
            "catalog_items": len(item_types),
            "orphan_item_ids": len(orphan_ids),
            "orphan_item_relations": orphan_relation_count,
            "outfits_affected_by_orphans": affected_outfit_count,
            "outfits_fully_covered_by_catalog": fully_covered_outfit_count,
            "official_outfits": official_outfit_count,
            "official_looks": official_look_count,
            "description_present": description_present,
            "style_present": style_present,
            "unique_item_ids_in_outfits": len(item_outfit_usage),
            "items_reused_across_outfits": sum(
                1 for count in item_outfit_usage.values() if count > 1
            ),
            "max_outfits_per_item": max(item_outfit_usage.values(), default=0),
        },
        "outfit_size": {
            "minimum": min(outfit_sizes) if outfit_sizes else None,
            "maximum": max(outfit_sizes) if outfit_sizes else None,
            "mean": round(statistics.fmean(outfit_sizes), 3) if outfit_sizes else None,
            "median": statistics.median(outfit_sizes) if outfit_sizes else None,
            "distribution": {str(size): count for size, count in sorted(by_size.items())},
        },
        "by_split": dict(by_split.most_common()),
        "complete_template_counts": dict(templates.most_common()),
        "fully_covered_template_counts": dict(fully_covered_templates.most_common()),
        "orphan_item_id_samples": sorted(orphan_ids)[:30],
        "quality_gate": {
            "full_dataset_catalog_coverage_pass": not orphan_ids,
            "valid_records_pass": invalid_record_count == 0,
            "strict_subset_available": fully_covered_outfit_count > 0,
            "strict_subset_has_complete_separates": (
                fully_covered_templates["top+bottom+footwear"] > 0
            ),
            "strict_subset_has_complete_one_piece": (
                fully_covered_templates["one_piece+footwear"] > 0
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outfits", type=Path, default=settings.outfit_path)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "data_audit" / "polyvore_outfit_audit.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.outfits.is_file():
        raise SystemExit(f"Outfit file not found: {args.outfits}")
    report = audit_outfits(args.outfits, args.database)
    write_json_atomic(args.report, report)
    print(
        json.dumps(
            {
                "report": str(args.report.resolve()),
                "counts": report["counts"],
                "complete_template_counts": report["complete_template_counts"],
                "fully_covered_template_counts": report["fully_covered_template_counts"],
                "quality_gate": report["quality_gate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
