"""Audit Garments2Look-Polyvore item metadata without requiring image files."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.garments2look import REQUIRED_FIELDS, ImagePathResolver
from styleforge.data.json_stream import iter_json_object


def _has_product_image(record: dict[str, Any]) -> bool:
    images = record.get("images")
    product = images.get("product") if isinstance(images, dict) else None
    full = product.get("full") if isinstance(product, dict) else None
    return isinstance(full, list) and any(isinstance(value, str) and value.strip() for value in full)


def audit_metadata(metadata_path: Path, image_root: Path | None = None) -> dict[str, Any]:
    resolver = ImagePathResolver(image_root)
    by_gender: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_main_category: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()
    image_binding: Counter[str] = Counter()
    invalid_record_count = 0
    declared_product_image_count = 0
    fallback_filename_count = 0
    duplicate_ids: list[str] = []
    seen_ids: set[str] = set()
    total = 0

    for item_id, value in iter_json_object(metadata_path):
        total += 1
        if item_id in seen_ids and len(duplicate_ids) < 20:
            duplicate_ids.append(item_id)
        seen_ids.add(item_id)
        if not isinstance(value, dict):
            invalid_record_count += 1
            continue

        for field in REQUIRED_FIELDS:
            if not isinstance(value.get(field), str) or not value[field].strip():
                missing_fields[field] += 1
        gender = value.get("gender") if isinstance(value.get("gender"), str) else "<missing>"
        item_type = value.get("type") if isinstance(value.get("type"), str) else "<missing>"
        main_category = (
            value.get("main_category")
            if isinstance(value.get("main_category"), str)
            else "<missing>"
        )
        by_gender[gender] += 1
        by_type[item_type] += 1
        by_main_category[main_category] += 1

        if _has_product_image(value):
            declared_product_image_count += 1
        else:
            fallback_filename_count += 1
        relative_path = resolver.relative_path(item_id, value)
        image_binding[resolver.bind(relative_path).status.value] += 1

    stat = metadata_path.stat()
    source_hash = hashlib.sha256()
    with metadata_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            source_hash.update(chunk)

    return {
        "schema_version": "styleforge.dataset-audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(metadata_path.resolve()),
            "size_bytes": stat.st_size,
            "sha256": source_hash.hexdigest(),
        },
        "image_root": str(image_root.resolve()) if image_root is not None else None,
        "image_audit_scope": (
            "filesystem_presence" if image_root is not None else "metadata_declarations_only"
        ),
        "counts": {
            "records_total": total,
            "records_valid_object": total - invalid_record_count,
            "records_invalid_object": invalid_record_count,
            "declared_product_image": declared_product_image_count,
            "fallback_image_filename": fallback_filename_count,
            "duplicate_item_ids_detected": len(duplicate_ids),
        },
        "missing_required_fields": dict(missing_fields.most_common()),
        "by_gender": dict(by_gender.most_common()),
        "by_type": dict(by_type.most_common()),
        "by_main_category": dict(by_main_category.most_common()),
        "image_binding_status": dict(image_binding.most_common()),
        "duplicate_item_id_samples": duplicate_ids,
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=settings.metadata_path)
    parser.add_argument("--image-root", type=Path, default=settings.image_root)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "data_audit" / "polyvore_metadata_audit.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.metadata.is_file():
        raise SystemExit(f"Metadata file not found: {args.metadata}")
    report = audit_metadata(args.metadata, args.image_root)
    write_json_atomic(args.report, report)
    summary = {
        "report": str(args.report.resolve()),
        "records_total": report["counts"]["records_total"],
        "by_gender": report["by_gender"],
        "image_audit_scope": report["image_audit_scope"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

