"""Evaluate FashionCLIP image and text retrieval against catalog categories."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.repositories.database import connect
from styleforge.services.visual_search import FashionIndex
from styleforge.vision.fashion_clip import FashionClipEncoder


PROMPT_OVERRIDES = {
    "bag": "a fashion handbag or bag",
    "bracelet": "a fashion bracelet",
    "belts": "a fashion belt",
    "bodysuit": "a fashion bodysuit",
    "brooch": "a brooch pin",
    "dress": "a fashion dress",
    "earrings": "fashion earrings",
    "eyewear": "fashion sunglasses or eyewear",
    "gloves": "fashion gloves",
    "hairwear": "a hair accessory",
    "hats": "a fashion hat",
    "jewellery": "fashion jewelry",
    "jumpsuit": "a fashion jumpsuit",
    "legwear": "fashion tights or socks",
    "leggings": "fashion leggings",
    "necklace": "a fashion necklace",
    "neckwear": "a scarf or neck accessory",
    "outwear": "an outerwear coat or jacket",
    "pants": "fashion pants or trousers",
    "rings": "fashion finger rings",
    "scarves": "a fashion scarf",
    "shoes": "fashion shoes",
    "shorts": "fashion shorts",
    "skirt": "a fashion skirt",
    "suit": "a tailored fashion suit",
    "top": "a fashion top or blouse",
    "watches": "a fashion wristwatch",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_by_id(database_path: str) -> dict[str, tuple[str, str]]:
    connection = connect(database_path)
    try:
        return {
            row["item_id"]: (row["item_type"], row["main_category"])
            for row in connection.execute(
                "SELECT item_id, item_type, main_category FROM catalog_items"
            )
        }
    finally:
        connection.close()


def _chance_agreement(labels: list[str]) -> float:
    counts = Counter(labels)
    total = len(labels)
    if total <= 1:
        return 0.0
    return sum(count * (count - 1) for count in counts.values()) / (total * (total - 1))


def evaluate_visual_retrieval(
    *,
    database_path: str,
    embedding_dir: Path,
    index_dir: Path,
    model_dir: Path,
    output_path: Path,
    sample_size: int = 2000,
    top_k: int = 10,
    seed: int = 20260722,
    device: str = "cuda",
) -> dict[str, Any]:
    """Measure neighbor category agreement and category-text precision."""
    import numpy as np

    if sample_size <= 0 or top_k <= 0:
        raise ValueError("sample_size and top_k must be positive")
    embedding_dir = embedding_dir.resolve()
    index_dir = index_dir.resolve()
    model_dir = model_dir.resolve()
    embeddings = np.load(embedding_dir / "embeddings.npy", mmap_mode="r")
    item_ids: list[str] = json.loads(
        (embedding_dir / "item_ids.json").read_text(encoding="utf-8")
    )
    metadata = _metadata_by_id(database_path)
    if len(item_ids) != embeddings.shape[0]:
        raise RuntimeError("Embedding rows and item IDs do not match")
    if any(item_id not in metadata for item_id in item_ids):
        raise RuntimeError("The embedding item map contains IDs missing from the database")

    index = FashionIndex(index_dir)
    rng = random.Random(seed)
    probe_positions = sorted(rng.sample(range(len(item_ids)), min(sample_size, len(item_ids))))
    probe_vectors = np.ascontiguousarray(embeddings[probe_positions], dtype=np.float32)
    scores, neighbor_positions = index.index.search(probe_vectors, min(top_k + 1, len(item_ids)))

    type_precisions: list[float] = []
    main_category_precisions: list[float] = []
    top1_type_matches = 0
    top1_main_category_matches = 0
    top1_exact_item_matches = 0
    self_in_returned_neighbors = 0
    top_similarity_below_one = 0
    per_type_hits: Counter[str] = Counter()
    per_type_total: Counter[str] = Counter()
    for probe_position, row_positions, row_scores in zip(probe_positions, neighbor_positions, scores):
        probe_id = item_ids[probe_position]
        probe_type, probe_main_category = metadata[probe_id]
        ranked = [
            (int(position), float(score))
            for position, score in zip(row_positions, row_scores)
            if position >= 0
        ]
        if ranked:
            top1_exact_item_matches += int(ranked[0][0] == probe_position)
            self_in_returned_neighbors += int(any(position == probe_position for position, _ in ranked))
            top_similarity_below_one += int(ranked[0][1] < 0.999)
        neighbors = [position for position, _ in ranked if position != probe_position][:top_k]
        if not neighbors:
            continue
        neighbor_metadata = [metadata[item_ids[position]] for position in neighbors]
        type_hits = sum(item_type == probe_type for item_type, _ in neighbor_metadata)
        main_hits = sum(
            main_category == probe_main_category for _, main_category in neighbor_metadata
        )
        type_precisions.append(type_hits / len(neighbors))
        main_category_precisions.append(main_hits / len(neighbors))
        top1_type_matches += int(neighbor_metadata[0][0] == probe_type)
        top1_main_category_matches += int(neighbor_metadata[0][1] == probe_main_category)
        per_type_hits[probe_type] += type_hits
        per_type_total[probe_type] += len(neighbors)

    catalog_types = [metadata[item_id][0] for item_id in item_ids]
    type_chance = _chance_agreement(catalog_types)
    image_type_precision = float(np.mean(type_precisions))
    image_metrics = {
        "sample_size": len(probe_positions),
        "top_k_excluding_self": top_k,
        "top_similarity_below_0_999_count": top_similarity_below_one,
        "exact_item_top1_rate": round(top1_exact_item_matches / len(probe_positions), 6),
        "exact_item_in_returned_neighbors_rate": round(
            self_in_returned_neighbors / len(probe_positions), 6
        ),
        "same_item_type_precision_at_k": round(image_type_precision, 6),
        "same_main_category_precision_at_k": round(
            float(np.mean(main_category_precisions)), 6
        ),
        "same_item_type_top1_rate": round(top1_type_matches / len(type_precisions), 6),
        "same_main_category_top1_rate": round(
            top1_main_category_matches / len(main_category_precisions), 6
        ),
        "random_item_type_agreement": round(type_chance, 6),
        "item_type_lift_over_random": round(image_type_precision / max(type_chance, 1e-12), 3),
        "per_item_type_precision_at_k": {
            item_type: round(per_type_hits[item_type] / per_type_total[item_type], 6)
            for item_type in sorted(per_type_total)
        },
    }

    item_types = sorted(set(catalog_types))
    prompts = [
        PROMPT_OVERRIDES.get(item_type, f"a {item_type.replace('_', ' ')} fashion item")
        for item_type in item_types
    ]
    encoder = FashionClipEncoder(model_dir, device=device, precision="float16")
    text_results = index.search_texts(encoder, prompts, top_k=top_k)
    text_details: dict[str, Any] = {}
    text_precisions: list[float] = []
    for item_type, prompt, hits in zip(item_types, prompts, text_results):
        retrieved_types = [metadata[hit.item_id][0] for hit in hits]
        precision = sum(value == item_type for value in retrieved_types) / max(len(hits), 1)
        text_precisions.append(precision)
        text_details[item_type] = {
            "prompt": prompt,
            "precision_at_k": round(precision, 6),
            "top_item_ids": [hit.item_id for hit in hits],
            "top_scores": [round(hit.score, 6) for hit in hits],
        }

    report = {
        "schema_version": 1,
        "status": "completed",
        "seed": seed,
        "catalog_items": len(item_ids),
        "embedding_dimension": int(embeddings.shape[1]),
        "image_to_image": image_metrics,
        "text_to_image": {
            "category_count": len(item_types),
            "top_k": top_k,
            "macro_item_type_precision_at_k": round(float(np.mean(text_precisions)), 6),
            "details": text_details,
        },
        "completed_at": _now(),
    }
    write_json_atomic(output_path.resolve(), report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument(
        "--embedding-dir",
        type=Path,
        default=settings.embedding_dir,
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=settings.index_dir,
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=settings.artifact_root / "models",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=settings.artifact_root / "evaluation" / "fashionclip_retrieval.json",
    )
    parser.add_argument("--sample-size", type=int, default=2000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--device", default="cuda")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    report = evaluate_visual_retrieval(
        database_path=args.database,
        embedding_dir=args.embedding_dir,
        index_dir=args.index_dir,
        model_dir=args.model_dir,
        output_path=args.output,
        sample_size=args.sample_size,
        top_k=args.top_k,
        seed=args.seed,
        device=args.device,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
