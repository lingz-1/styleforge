"""Run official Polyvore Compatibility/FITB baselines and write a v2 report.

Examples::

    D:\\anaconda\\envs\\style\\python.exe -m evals.runners.evaluate_polyvore_baselines \\
      --variants disjoint,nondisjoint,maryland_polyvore_hardneg

    D:\\anaconda\\envs\\style\\python.exe -m evals.runners.evaluate_polyvore_baselines \\
      --variants disjoint --max-train-compatibility 200 \\
      --max-compatibility 100 --max-fitb 100

All reports and FashionCLIP caches are constrained to this workspace.  The
external dataset is opened read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(APPS_API_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_API_ROOT))

from evals.polyvore_benchmark import (  # noqa: E402
    HARD_NEGATIVE_VARIANT,
    PRIMARY_VARIANTS,
    SUPPORTED_VARIANTS,
    CategoryCooccurrenceBaseline,
    CompatibilityCase,
    FashionClipBaseline,
    FitbCase,
    PolyvoreDataset,
    RandomBaseline,
    evaluate_compatibility,
    evaluate_fitb,
    file_sha256,
    sample_compatibility_cases,
    sample_fitb_cases,
)
from styleforge.common.console import configure_utf8_console  # noqa: E402
from styleforge.common.files import write_json_atomic  # noqa: E402
from styleforge.vision.fashion_clip import shared_fashion_clip_encoder  # noqa: E402


DEFAULT_DATASET_ROOT = Path(r"E:\01-style-dataset\p-outfit")
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "v2" / "polyvore_baselines.json"
DEFAULT_CACHE = (
    WORKSPACE_ROOT
    / "artifacts"
    / "evaluation"
    / "v2"
    / "fashionclip_embeddings.sqlite3"
)
DEFAULT_MODEL_DIR = WORKSPACE_ROOT / "artifacts" / "models"
BASELINE_NAMES = {"random", "category_cooccurrence", "fashionclip"}


def _workspace_output(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_relative_to(WORKSPACE_ROOT):
        raise ValueError(f"Evaluation outputs must stay inside {WORKSPACE_ROOT}: {resolved}")
    return resolved


def _csv_values(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip().lower() for value in raw.split(",") if value.strip()))


def _source_manifest(dataset: PolyvoreDataset, splits: Sequence[str]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    seen: set[Path] = set()
    for split in splits:
        for kind, path in dataset.source_paths(split).items():
            if path in seen:
                continue
            seen.add(path)
            relative = str(path.relative_to(dataset.root)).replace("\\", "/")
            files[relative] = {
                "kind": kind,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
    return files


class SQLiteEmbeddingCache:
    """Small resumable float32 embedding cache scoped by model revision."""

    def __init__(self, path: Path, *, model_revision: str, dimension: int) -> None:
        self.path = _workspace_output(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_revision = model_revision
        self.dimension = int(dimension)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS cache_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                embedding_key TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                dimension INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing = dict(self.connection.execute("SELECT key, value FROM cache_metadata"))
        expected = {
            "model_revision": self.model_revision,
            "dimension": str(self.dimension),
            "dtype": "float32",
        }
        if existing and any(existing.get(key) != value for key, value in expected.items()):
            raise RuntimeError(
                f"FashionCLIP cache metadata does not match the current model: {self.path}"
            )
        self.connection.executemany(
            "INSERT OR REPLACE INTO cache_metadata(key, value) VALUES (?, ?)",
            expected.items(),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def fetch(self, keys: Sequence[str]) -> dict[str, Any]:
        import numpy as np

        result: dict[str, Any] = {}
        unique = tuple(dict.fromkeys(keys))
        for start in range(0, len(unique), 800):
            chunk = unique[start : start + 800]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT embedding_key, vector, dimension FROM embeddings "
                f"WHERE embedding_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for key, payload, dimension in rows:
                if int(dimension) != self.dimension:
                    raise RuntimeError(f"Invalid cached embedding dimension for {key}")
                vector = np.frombuffer(payload, dtype="<f4").copy()
                if vector.shape != (self.dimension,):
                    raise RuntimeError(f"Invalid cached embedding payload for {key}")
                result[str(key)] = vector
        return result

    def upsert(self, rows: Iterable[tuple[str, Any]]) -> None:
        import numpy as np

        timestamp = datetime.now(timezone.utc).isoformat()
        payloads = []
        for key, vector in rows:
            normalized = np.asarray(vector, dtype="<f4")
            if normalized.shape != (self.dimension,):
                raise ValueError(
                    f"Embedding {key!r} has shape {normalized.shape}; "
                    f"expected {(self.dimension,)}"
                )
            payloads.append((key, normalized.tobytes(), self.dimension, timestamp))
        self.connection.executemany(
            """
            INSERT INTO embeddings(embedding_key, vector, dimension, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(embedding_key) DO UPDATE SET
              vector = excluded.vector,
              dimension = excluded.dimension,
              updated_at = excluded.updated_at
            """,
            payloads,
        )
        self.connection.commit()


def _model_revision(model_dir: Path) -> str:
    config_path = model_dir / "config.json"
    weight_path = model_dir / "model.safetensors"
    if not config_path.is_file() or not weight_path.is_file():
        raise FileNotFoundError(f"FashionCLIP model files are missing under {model_dir}")
    digest = hashlib.sha256(config_path.read_bytes())
    weight_stat = weight_path.stat()
    digest.update(str(weight_stat.st_size).encode("ascii"))
    digest.update(str(weight_stat.st_mtime_ns).encode("ascii"))
    return f"local-fashionclip-{digest.hexdigest()[:16]}"


def _benchmark_items(
    compatibility_sets: Sequence[Sequence[CompatibilityCase]],
    fitb_sets: Sequence[Sequence[FitbCase]],
) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for cases in compatibility_sets:
        for case in cases:
            for item in case.items:
                items.setdefault(item.embedding_key, item)
    for cases in fitb_sets:
        for case in cases:
            for item in (*case.question_items, *case.answer_items):
                items.setdefault(item.embedding_key, item)
    return items


def _ensure_fashionclip_embeddings(
    items: dict[str, Any],
    *,
    encoder: Any,
    cache: SQLiteEmbeddingCache,
    batch_size: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from PIL import Image

    started = time.perf_counter()
    keys = sorted(items)
    embeddings = cache.fetch(keys)
    cached_count = len(embeddings)
    missing = [items[key] for key in keys if key not in embeddings]
    encoded_count = 0
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        images = []
        try:
            for item in batch:
                with Image.open(item.image_path) as source:
                    images.append(source.convert("RGB"))
            vectors = encoder.encode_images(images, batch_size=batch_size)
        finally:
            for image in images:
                image.close()
        cache.upsert(
            (item.embedding_key, vector) for item, vector in zip(batch, vectors)
        )
        for item, vector in zip(batch, vectors):
            embeddings[item.embedding_key] = vector
        encoded_count += len(batch)
        print(
            f"FashionCLIP encoded {encoded_count}/{len(missing)} missing images",
            flush=True,
        )
    elapsed = time.perf_counter() - started
    return embeddings, {
        "required_embedding_count": len(keys),
        "cache_hit_count": cached_count,
        "encoded_count": len(missing),
        "encoding_seconds": elapsed,
        "encoding_ms_per_new_item": elapsed * 1000 / len(missing) if missing else 0.0,
    }


def _evaluate_model(
    model: Any,
    *,
    validation_compatibility: Sequence[CompatibilityCase],
    test_compatibility: Sequence[CompatibilityCase],
    validation_fitb: Sequence[FitbCase],
    test_fitb: Sequence[FitbCase],
    tie_seed: int,
) -> dict[str, Any]:
    return {
        "status": "complete",
        "compatibility": evaluate_compatibility(
            validation_compatibility,
            test_compatibility,
            model.score_compatibility,
        ),
        "fitb": {
            "validation": evaluate_fitb(
                validation_fitb,
                model.score_fitb,
                tie_seed=tie_seed,
            ),
            "test": evaluate_fitb(
                test_fitb,
                model.score_fitb,
                tie_seed=tie_seed,
            ),
        },
    }


def _failure_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(limit=12),
    }


def evaluate_variant(
    dataset: PolyvoreDataset,
    *,
    baselines: Sequence[str],
    seed: int,
    max_train_compatibility: int | None,
    max_compatibility: int | None,
    max_fitb: int | None,
    encoder: Any | None,
    embedding_cache: SQLiteEmbeddingCache | None,
    embedding_batch_size: int,
) -> dict[str, Any]:
    variant_started = time.perf_counter()
    require_items = dataset.supports_item_mapping and any(
        name in baselines for name in ("category_cooccurrence", "fashionclip")
    )
    raw: dict[str, dict[str, Any]] = {}
    audits: dict[str, Any] = {}
    selected: dict[str, dict[str, Any]] = {}
    for split, split_seed in (("valid", seed + 11), ("test", seed + 29)):
        compatibility = dataset.load_compatibility(split, require_items=False)
        fitb = dataset.load_fitb(split, require_items=False)
        audits[split] = dataset.audit_references(split, compatibility, fitb)
        chosen_compatibility = sample_compatibility_cases(
            compatibility,
            max_compatibility,
            seed=split_seed,
        )
        chosen_fitb = sample_fitb_cases(fitb, max_fitb, seed=split_seed + 1)
        if require_items:
            chosen_compatibility = dataset.resolve_compatibility_cases(
                split, chosen_compatibility
            )
            chosen_fitb = dataset.resolve_fitb_cases(split, chosen_fitb)
        raw[split] = {"compatibility": compatibility, "fitb": fitb}
        selected[split] = {
            "compatibility": chosen_compatibility,
            "fitb": chosen_fitb,
        }

    train_compatibility: tuple[CompatibilityCase, ...] = ()
    if "category_cooccurrence" in baselines and dataset.supports_item_mapping:
        raw_train = dataset.load_compatibility("train", require_items=False)
        chosen_train = sample_compatibility_cases(
            raw_train,
            max_train_compatibility,
            seed=seed + 3,
        )
        train_compatibility = dataset.resolve_compatibility_cases("train", chosen_train)
        train_audit = dataset.audit_references("train", raw_train, ())
        raw["train"] = {"compatibility": raw_train}
        selected["train"] = {"compatibility": train_compatibility}
        audits["train"] = train_audit

    result: dict[str, Any] = {
        "status": "complete",
        "supports_item_mapping": dataset.supports_item_mapping,
        "source_counts": {
            split: {kind: len(cases) for kind, cases in tasks.items()}
            for split, tasks in raw.items()
        },
        "evaluated_counts": {
            split: {kind: len(cases) for kind, cases in tasks.items()}
            for split, tasks in selected.items()
        },
        "mapping_audit": audits,
        "source_files": _source_manifest(dataset, tuple(raw)),
        "baselines": {},
    }

    evaluation_cases = {
        "validation_compatibility": selected["valid"]["compatibility"],
        "test_compatibility": selected["test"]["compatibility"],
        "validation_fitb": selected["valid"]["fitb"],
        "test_fitb": selected["test"]["fitb"],
    }
    if "random" in baselines:
        try:
            result["baselines"]["random"] = _evaluate_model(
                RandomBaseline(seed),
                **evaluation_cases,
                tie_seed=seed,
            )
        except BaseException as exc:
            result["baselines"]["random"] = _failure_payload(exc)

    if "category_cooccurrence" in baselines:
        if not dataset.supports_item_mapping:
            result["baselines"]["category_cooccurrence"] = {
                "status": "unavailable",
                "reason": (
                    "The bundled Maryland hard-negative files contain benchmark tokens "
                    "but no official token-to-item/category mapping."
                ),
            }
        else:
            try:
                category_model = CategoryCooccurrenceBaseline.fit(train_compatibility)
                category_result = _evaluate_model(
                    category_model,
                    **evaluation_cases,
                    tie_seed=seed + 101,
                )
                category_result["training"] = {
                    "selected_case_count": len(train_compatibility),
                    "positive_case_count": sum(
                        case.label == 1 for case in train_compatibility
                    ),
                    "category_count": category_model.category_space,
                    "observed_pair_count": len(category_model.pair_counts),
                    "alpha": category_model.alpha,
                }
                result["baselines"]["category_cooccurrence"] = category_result
            except BaseException as exc:
                result["baselines"]["category_cooccurrence"] = _failure_payload(exc)

    if "fashionclip" in baselines:
        if not dataset.supports_item_mapping:
            result["baselines"]["fashionclip"] = {
                "status": "unavailable",
                "reason": (
                    "The bundled Maryland hard-negative files contain no official "
                    "token-to-image mapping, so visual scores cannot be reproduced."
                ),
            }
        elif encoder is None or embedding_cache is None:
            result["baselines"]["fashionclip"] = {
                "status": "failed",
                "error_type": "RuntimeError",
                "error": "FashionCLIP runtime or embedding cache was not initialized",
            }
        else:
            try:
                items = _benchmark_items(
                    (
                        selected["valid"]["compatibility"],
                        selected["test"]["compatibility"],
                    ),
                    (selected["valid"]["fitb"], selected["test"]["fitb"]),
                )
                embeddings, encoding = _ensure_fashionclip_embeddings(
                    items,
                    encoder=encoder,
                    cache=embedding_cache,
                    batch_size=embedding_batch_size,
                )
                fashion_result = _evaluate_model(
                    FashionClipBaseline(embeddings),
                    **evaluation_cases,
                    tie_seed=seed + 211,
                )
                fashion_result["encoding"] = encoding
                result["baselines"]["fashionclip"] = fashion_result
            except BaseException as exc:
                result["baselines"]["fashionclip"] = _failure_payload(exc)

    result["elapsed_seconds"] = time.perf_counter() - variant_started
    if any(
        baseline.get("status") == "failed"
        for baseline in result["baselines"].values()
    ):
        result["status"] = "failed"
    return result


def _hard_negative_comparisons(variants: dict[str, Any]) -> dict[str, Any]:
    ordinary = variants.get("nondisjoint", {}).get("baselines", {})
    hard = variants.get(HARD_NEGATIVE_VARIANT, {}).get("baselines", {})
    comparisons: dict[str, Any] = {}
    for name in sorted(set(ordinary) & set(hard)):
        ordinary_result = ordinary[name]
        hard_result = hard[name]
        if ordinary_result.get("status") != "complete" or hard_result.get("status") != "complete":
            continue
        ordinary_accuracy = ordinary_result["fitb"]["test"]["top1_accuracy"]
        hard_accuracy = hard_result["fitb"]["test"]["top1_accuracy"]
        comparisons[name] = {
            "ordinary_nondisjoint_top1": ordinary_accuracy,
            "maryland_hard_negative_top1": hard_accuracy,
            "absolute_change": hard_accuracy - ordinary_accuracy,
        }
    return comparisons


def run_evaluation(
    *,
    dataset_root: Path,
    report_path: Path,
    cache_path: Path,
    model_dir: Path,
    variants: Sequence[str],
    baselines: Sequence[str],
    seed: int,
    max_train_compatibility: int | None,
    max_compatibility: int | None,
    max_fitb: int | None,
    device: str,
    precision: str,
    embedding_batch_size: int,
) -> dict[str, Any]:
    report_path = _workspace_output(report_path)
    cache_path = _workspace_output(cache_path)
    started = time.perf_counter()
    encoder = None
    cache = None
    model_revision = None
    if "fashionclip" in baselines and any(variant in PRIMARY_VARIANTS for variant in variants):
        model_dir = Path(model_dir).expanduser().resolve()
        model_revision = _model_revision(model_dir)
        encoder = shared_fashion_clip_encoder(
            model_dir,
            device=device,
            precision=precision,
        )
        cache = SQLiteEmbeddingCache(
            cache_path,
            model_revision=model_revision,
            dimension=encoder.dimension,
        )

    report: dict[str, Any] = {
        "schema_version": "styleforge.polyvore-baselines.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "dataset_root": str(Path(dataset_root).expanduser().resolve()),
            "dataset_access": "read_only",
            "workspace_output": str(report_path),
            "variants": list(variants),
            "baselines": list(baselines),
            "seed": seed,
            "max_train_compatibility": max_train_compatibility,
            "max_compatibility": max_compatibility,
            "max_fitb": max_fitb,
            "fashionclip": {
                "model_dir": str(Path(model_dir).expanduser().resolve()),
                "model_revision": model_revision,
                "device": device,
                "precision": precision,
                "embedding_batch_size": embedding_batch_size,
                "cache_path": str(cache_path),
            },
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "variants": {},
        "limitations": [],
    }
    try:
        for variant in variants:
            print(f"Evaluating Polyvore variant: {variant}", flush=True)
            try:
                report["variants"][variant] = evaluate_variant(
                    PolyvoreDataset(dataset_root, variant),
                    baselines=baselines,
                    seed=seed,
                    max_train_compatibility=max_train_compatibility,
                    max_compatibility=max_compatibility,
                    max_fitb=max_fitb,
                    encoder=encoder,
                    embedding_cache=cache,
                    embedding_batch_size=embedding_batch_size,
                )
            except BaseException as exc:
                report["variants"][variant] = _failure_payload(exc)
        if HARD_NEGATIVE_VARIANT in variants:
            report["limitations"].append(
                "The bundled Maryland hard-negative package has no token-to-item, "
                "semantic-category or image mapping; only token-only Random is reproducible."
            )
        report["hard_negative_comparisons"] = _hard_negative_comparisons(report["variants"])
        report["elapsed_seconds"] = time.perf_counter() - started
        report["status"] = (
            "failed"
            if any(value.get("status") == "failed" for value in report["variants"].values())
            else "complete"
        )
        write_json_atomic(report_path, report)
        return report
    finally:
        if cache is not None:
            cache.close()


def _positive_limit(value: int) -> int | None:
    return value if value > 0 else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--variants",
        default="disjoint,nondisjoint,maryland_polyvore_hardneg",
    )
    parser.add_argument(
        "--baselines",
        default="random,category_cooccurrence,fashionclip",
    )
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--max-train-compatibility", type=int, default=0)
    parser.add_argument("--max-compatibility", type=int, default=0)
    parser.add_argument("--max-fitb", type=int, default=0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--precision",
        choices=("float32", "float16", "bfloat16"),
        default="float16",
    )
    parser.add_argument("--embedding-batch-size", type=int, default=64)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_console()
    args = build_parser().parse_args(argv)
    variants = _csv_values(args.variants)
    baselines = _csv_values(args.baselines)
    unknown_variants = set(variants) - SUPPORTED_VARIANTS
    unknown_baselines = set(baselines) - BASELINE_NAMES
    if not variants or unknown_variants:
        raise SystemExit(f"Invalid variants: {sorted(unknown_variants)}")
    if not baselines or unknown_baselines:
        raise SystemExit(f"Invalid baselines: {sorted(unknown_baselines)}")
    if args.embedding_batch_size < 1:
        raise SystemExit("--embedding-batch-size must be positive")
    report = run_evaluation(
        dataset_root=args.dataset_root,
        report_path=args.report,
        cache_path=args.cache,
        model_dir=args.model_dir,
        variants=variants,
        baselines=baselines,
        seed=args.seed,
        max_train_compatibility=_positive_limit(args.max_train_compatibility),
        max_compatibility=_positive_limit(args.max_compatibility),
        max_fitb=_positive_limit(args.max_fitb),
        device=args.device,
        precision=args.precision,
        embedding_batch_size=args.embedding_batch_size,
    )
    print(json.dumps({
        "status": report["status"],
        "report": str(Path(args.report).expanduser().resolve()),
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
