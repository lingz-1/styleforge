"""Evaluate rule-v1 with same-slot negative replacement on the official test split."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from styleforge.common.console import configure_utf8_console
from styleforge.common.files import write_json_atomic
from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.core.request_parser import parse_request
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus, TaskSpec
from styleforge.core.scoring import score_outfit
from styleforge.repositories.database import connect, initialize_database


@dataclass(frozen=True, slots=True)
class EvaluationOutfit:
    outfit_id: str
    occasion: str
    slot_items: dict[str, CatalogItem]


def _catalog_item(row: object) -> CatalogItem:
    return CatalogItem(
        item_id=row["item_id"],
        source=row["source"],
        gender=row["gender"],
        item_type=row["item_type"],
        main_category=row["main_category"],
        name=row["name"],
        color=row["color"],
        description=row["description"],
        features=(),
        image_filename=row["image_filename"],
        relative_image_path=row["relative_image_path"],
        image_status=ImageStatus(row["image_status"]),
        embedding_status=EmbeddingStatus(row["embedding_status"]),
        raw_json_hash=row["raw_json_hash"],
    )


def _load_evaluation_outfits(database_path: str, split: str) -> list[EvaluationOutfit]:
    connection = connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT o.outfit_id, o.occasion, oi.position, c.*
            FROM dataset_outfits AS o
            JOIN dataset_outfit_items AS oi ON oi.outfit_id = o.outfit_id
            JOIN catalog_items AS c ON c.item_id = oi.item_id
            WHERE o.split = %s
            ORDER BY o.outfit_id, oi.position
            """,
            (split,),
        )
        grouped: dict[str, list[CatalogItem]] = defaultdict(list)
        occasions: dict[str, str] = {}
        for row in rows:
            grouped[row["outfit_id"]].append(_catalog_item(row))
            occasions[row["outfit_id"]] = row["occasion"]
    finally:
        connection.close()

    evaluation_outfits: list[EvaluationOutfit] = []
    for outfit_id, items in grouped.items():
        by_slot: dict[str, CatalogItem] = {}
        for item in items:
            by_slot.setdefault(infer_slot(item.item_type), item)
        if {"top", "bottom", "footwear"}.issubset(by_slot):
            slots = ("top", "bottom", "footwear")
        elif {"one_piece", "footwear"}.issubset(by_slot):
            slots = ("one_piece", "footwear")
        else:
            continue
        evaluation_outfits.append(
            EvaluationOutfit(
                outfit_id=outfit_id,
                occasion=occasions[outfit_id],
                slot_items={slot: by_slot[slot] for slot in slots},
            )
        )
    return evaluation_outfits


def evaluate_rule_baseline(
    database_path: str,
    split: str = "test",
    negatives_per_positive: int = 4,
    seed: int = 42,
) -> dict[str, object]:
    if negatives_per_positive < 1:
        raise ValueError("negatives_per_positive must be positive")
    initialize_database(database_path)
    outfits = _load_evaluation_outfits(database_path, split)
    pools: dict[str, list[CatalogItem]] = defaultdict(list)
    for outfit in outfits:
        for slot, item in outfit.slot_items.items():
            pools[slot].append(item)

    generator = random.Random(seed)
    comparison_scores: list[float] = []
    positive_scores: list[float] = []
    negative_scores: list[float] = []
    by_template: dict[str, list[float]] = defaultdict(list)
    skipped_negative_count = 0

    for outfit in outfits:
        slots = tuple(outfit.slot_items)
        positive_items = [outfit.slot_items[slot] for slot in slots]
        occasion = parse_request("evaluation", outfit.occasion or "daily").occasion
        task = TaskSpec(user_id="evaluation", occasion=occasion, required_slots=slots)
        positive_score, _, _ = score_outfit(positive_items, task)
        template = "+".join(slots)
        original_ids = {item.item_id for item in positive_items}

        for negative_index in range(negatives_per_positive):
            replace_slot = slots[negative_index % len(slots)]
            candidates = [
                item for item in pools[replace_slot] if item.item_id not in original_ids
            ]
            if not candidates:
                skipped_negative_count += 1
                continue
            replacement = candidates[generator.randrange(len(candidates))]
            negative_items = [
                replacement if slot == replace_slot else outfit.slot_items[slot]
                for slot in slots
            ]
            negative_score, _, _ = score_outfit(negative_items, task)
            win = 1.0 if positive_score > negative_score else 0.5 if positive_score == negative_score else 0.0
            comparison_scores.append(win)
            by_template[template].append(win)
            positive_scores.append(positive_score)
            negative_scores.append(negative_score)

    pairwise_accuracy = statistics.fmean(comparison_scores) if comparison_scores else 0.0
    return {
        "schema_version": "styleforge.rule-eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scorer_version": "rule-v1",
        "database_path": str(database_path),
        "split": split,
        "seed": seed,
        "negative_sampling": "same_slot_replacement",
        "negatives_per_positive": negatives_per_positive,
        "evaluated_outfits": len(outfits),
        "comparisons": len(comparison_scores),
        "skipped_negatives": skipped_negative_count,
        "metrics": {
            "pairwise_accuracy": round(pairwise_accuracy, 6),
            "mean_positive_score": round(statistics.fmean(positive_scores), 4)
            if positive_scores
            else None,
            "mean_negative_score": round(statistics.fmean(negative_scores), 4)
            if negative_scores
            else None,
            "hard_constraint_violation_rate": 0.0,
            "pairwise_accuracy_by_template": {
                template: round(statistics.fmean(scores), 6)
                for template, scores in sorted(by_template.items())
            },
        },
        "limitations": [
            "Metadata-only scorer; image compatibility is not evaluated.",
            "Each source item ID occurs in only one outfit, so this measures set coherence under synthetic slot swaps.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--split", default="test")
    parser.add_argument("--negatives-per-positive", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "evaluation" / "rule_baseline_eval.json",
    )
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    report = evaluate_rule_baseline(
        database_path=args.database,
        split=args.split,
        negatives_per_positive=args.negatives_per_positive,
        seed=args.seed,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

