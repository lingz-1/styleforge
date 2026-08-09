"""Audit Mytheresa metadata and canonical category coverage before import."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.data.json_stream import iter_json_object
from styleforge.data.mytheresa import IMAGE_GROUPS, canonical_item_type


def _image_references(record: dict[str, Any]) -> list[str]:
    images = record.get("images")
    if not isinstance(images, dict):
        return []
    result: list[str] = []
    for _, group_name, size_name in IMAGE_GROUPS:
        group = images.get(group_name)
        filenames = group.get(size_name) if isinstance(group, dict) else None
        if isinstance(filenames, list):
            result.extend(
                PurePosixPath(filename.replace("\\", "/")).name
                for filename in filenames
                if isinstance(filename, str) and filename.strip()
            )
    return result


def audit_mytheresa(
    metadata_path: Path,
    image_root: Path | None = None,
) -> dict[str, Any]:
    by_audience: Counter[str] = Counter()
    by_item_type: Counter[str] = Counter()
    by_slot: Counter[str] = Counter()
    unknown_raw_types: Counter[str] = Counter()
    total_items = 0
    total_image_references = 0
    unique_image_references = 0
    present_image_references = 0

    for item_id, record in iter_json_object(metadata_path):
        if not isinstance(record, dict):
            raise TypeError(f"Record {item_id!r} must be an object")
        total_items += 1
        audience = str(record.get("gender") or "unknown")
        item_type = canonical_item_type(record)
        by_audience[audience] += 1
        by_item_type[item_type] += 1
        by_slot[infer_slot(item_type)] += 1
        if item_type == "other":
            unknown_raw_types[str(record.get("type") or "unknown")] += 1

        filenames = _image_references(record)
        total_image_references += len(filenames)
        unique_image_references += len(set(filenames))
        if image_root is not None:
            present_image_references += sum(
                (image_root / item_id / filename).is_file()
                for filename in filenames
            )

    return {
        "schema_version": "styleforge.mytheresa-audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata_path": str(metadata_path.resolve()),
        "image_root": str(image_root.resolve()) if image_root is not None else None,
        "total_items": total_items,
        "total_image_references": total_image_references,
        "unique_image_references": unique_image_references,
        "present_image_references": (
            present_image_references if image_root is not None else None
        ),
        "missing_image_references": (
            total_image_references - present_image_references
            if image_root is not None
            else None
        ),
        "by_audience": dict(by_audience.most_common()),
        "by_item_type": dict(by_item_type.most_common()),
        "by_slot": dict(by_slot.most_common()),
        "unmapped_item_count": by_item_type["other"],
        "unmapped_raw_types": dict(unknown_raw_types.most_common()),
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--image-root", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "data_audit" / "mytheresa_audit.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.metadata.is_file():
        raise SystemExit(f"Metadata file not found: {args.metadata}")
    if args.image_root is not None and not args.image_root.is_dir():
        raise SystemExit(f"Image root not found: {args.image_root}")
    if args.image_root is not None:
        report_path = args.report.resolve()
        image_root = args.image_root.resolve()
        if report_path == image_root or report_path.is_relative_to(image_root):
            raise SystemExit("Report path must not be inside the external image root")
    report = audit_mytheresa(args.metadata, args.image_root)
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
