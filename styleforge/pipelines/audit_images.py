"""Audit image presence and full decodability without modifying the source directory."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    from PIL import Image, ImageFile, UnidentifiedImageError
except ImportError as error:  # pragma: no cover
    raise RuntimeError("Pillow is required for image auditing.") from error

from styleforge.common.console import configure_utf8_console
from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.garments2look import ImagePathResolver
from styleforge.data.json_stream import iter_json_object


ImageFile.LOAD_TRUNCATED_IMAGES = False


@dataclass(frozen=True, slots=True)
class ImageTask:
    item_id: str
    relative_path: str
    absolute_path: Path


@dataclass(frozen=True, slots=True)
class ImageCheck:
    item_id: str
    relative_path: str
    status: str
    file_size: int = 0
    image_format: str = ""
    mode: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


def inspect_image(task: ImageTask) -> ImageCheck:
    if not task.absolute_path.is_file():
        return ImageCheck(task.item_id, task.relative_path, "missing")
    try:
        file_size = task.absolute_path.stat().st_size
        with Image.open(task.absolute_path) as image:
            image.load()
            image_format = image.format or "<unknown>"
            mode = image.mode
            width, height = image.size
        if width < 1 or height < 1:
            raise ValueError(f"Invalid dimensions: {width}x{height}")
        return ImageCheck(
            item_id=task.item_id,
            relative_path=task.relative_path,
            status="decoded",
            file_size=file_size,
            image_format=image_format,
            mode=mode,
            width=width,
            height=height,
        )
    except (OSError, ValueError, UnidentifiedImageError) as error:
        return ImageCheck(
            item_id=task.item_id,
            relative_path=task.relative_path,
            status="decode_error",
            error=f"{type(error).__name__}: {error}"[:500],
        )


def iter_tasks(
    metadata_path: Path,
    resolver: ImagePathResolver,
    expected_paths: set[str],
) -> Iterator[ImageTask]:
    if resolver.image_root is None:
        raise ValueError("image_root is required for image auditing")
    for item_id, record in iter_json_object(metadata_path):
        relative_path = resolver.relative_path(item_id, record)
        expected_paths.add(relative_path)
        yield ImageTask(
            item_id=item_id,
            relative_path=relative_path,
            absolute_path=resolver.image_root.joinpath(*PurePosixPath(relative_path).parts),
        )


def bounded_checks(tasks: Iterator[ImageTask], workers: int) -> Iterator[ImageCheck]:
    pending_limit = max(workers * 4, 8)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image-audit") as executor:
        pending: set[Future[ImageCheck]] = set()
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < pending_limit:
                try:
                    task = next(tasks)
                except StopIteration:
                    exhausted = True
                    break
                pending.add(executor.submit(inspect_image, task))
            if not pending:
                continue
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()


def filesystem_extras(image_root: Path, expected_paths: set[str]) -> dict[str, Any]:
    unexpected_images: list[str] = []
    auxiliary_files: list[str] = []
    unexpected_image_count = 0
    auxiliary_file_count = 0
    for path in image_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(image_root).as_posix()
        if relative_path in expected_paths:
            continue
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            unexpected_image_count += 1
            if len(unexpected_images) < 50:
                unexpected_images.append(relative_path)
        else:
            auxiliary_file_count += 1
            if len(auxiliary_files) < 50:
                auxiliary_files.append(relative_path)
    return {
        "unexpected_image_count": unexpected_image_count,
        "unexpected_image_samples": unexpected_images,
        "auxiliary_file_count": auxiliary_file_count,
        "auxiliary_file_samples": auxiliary_files,
    }


def audit_images(
    metadata_path: Path,
    image_root: Path,
    workers: int = 8,
    progress_every: int = 10_000,
) -> dict[str, Any]:
    if workers < 1 or workers > 64:
        raise ValueError("workers must be between 1 and 64")
    resolver = ImagePathResolver(image_root)
    expected_paths: set[str] = set()
    status_counts: Counter[str] = Counter()
    format_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    dimension_counts: Counter[str] = Counter()
    widths: list[int] = []
    heights: list[int] = []
    file_sizes: list[int] = []
    failures: list[dict[str, str]] = []
    processed = 0

    tasks = iter_tasks(metadata_path, resolver, expected_paths)
    for check in bounded_checks(tasks, workers):
        processed += 1
        status_counts[check.status] += 1
        if check.status == "decoded":
            format_counts[check.image_format] += 1
            mode_counts[check.mode] += 1
            widths.append(check.width)
            heights.append(check.height)
            file_sizes.append(check.file_size)
            if min(check.width, check.height) < 32:
                dimension_counts["min_side_lt_32"] += 1
            elif min(check.width, check.height) < 128:
                dimension_counts["min_side_32_to_127"] += 1
            else:
                dimension_counts["min_side_ge_128"] += 1
        elif len(failures) < 100:
            failures.append(
                {
                    "item_id": check.item_id,
                    "relative_path": check.relative_path,
                    "status": check.status,
                    "error": check.error,
                }
            )
        if progress_every and processed % progress_every == 0:
            print(
                f"image_audit_progress={processed} decoded={status_counts['decoded']} "
                f"missing={status_counts['missing']} errors={status_counts['decode_error']}",
                flush=True,
            )

    extras = filesystem_extras(image_root, expected_paths)
    decoded = status_counts["decoded"]
    missing = status_counts["missing"]
    decode_errors = status_counts["decode_error"]
    return {
        "schema_version": "styleforge.image-audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "metadata_path": str(metadata_path.resolve()),
            "image_root": str(image_root.resolve()),
        },
        "configuration": {
            "workers": workers,
            "full_pixel_decode": True,
            "load_truncated_images": False,
        },
        "counts": {
            "expected_images": processed,
            "decoded_images": decoded,
            "missing_images": missing,
            "decode_errors": decode_errors,
        },
        "coverage_rate": round((processed - missing) / processed, 8) if processed else 0.0,
        "decode_success_rate": round(decoded / processed, 8) if processed else 0.0,
        "formats": dict(format_counts.most_common()),
        "modes": dict(mode_counts.most_common()),
        "dimension_buckets": dict(dimension_counts),
        "dimensions": {
            "width_min": min(widths) if widths else None,
            "width_median": statistics.median(widths) if widths else None,
            "width_max": max(widths) if widths else None,
            "height_min": min(heights) if heights else None,
            "height_median": statistics.median(heights) if heights else None,
            "height_max": max(heights) if heights else None,
        },
        "file_sizes": {
            "total_bytes": sum(file_sizes),
            "minimum_bytes": min(file_sizes) if file_sizes else None,
            "median_bytes": statistics.median(file_sizes) if file_sizes else None,
            "maximum_bytes": max(file_sizes) if file_sizes else None,
        },
        "filesystem_extras": extras,
        "failure_samples": failures,
        "quality_gate": {
            "all_paths_present": missing == 0,
            "all_images_decodable": decode_errors == 0,
            "all_images_pass": missing == 0 and decode_errors == 0 and decoded == processed,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=settings.metadata_path)
    parser.add_argument("--image-root", type=Path, default=settings.image_root)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "data_audit" / "polyvore_image_audit.json",
    )
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    if not args.metadata.is_file():
        raise SystemExit(f"Metadata file not found: {args.metadata}")
    if args.image_root is None or not args.image_root.is_dir():
        raise SystemExit(f"Image root not found: {args.image_root}")
    report = audit_images(
        metadata_path=args.metadata,
        image_root=args.image_root,
        workers=args.workers,
        progress_every=args.progress_every,
    )
    write_json_atomic(args.report, report)
    print(
        json.dumps(
            {
                "report": str(args.report.resolve()),
                "counts": report["counts"],
                "coverage_rate": report["coverage_rate"],
                "decode_success_rate": report["decode_success_rate"],
                "quality_gate": report["quality_gate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

