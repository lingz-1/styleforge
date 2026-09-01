"""Read-only Polyvore Outfits benchmark contracts, baselines and metrics.

The module deliberately keeps public-benchmark evaluation separate from the
StyleForge agent quality harness.  It parses the official Compatibility and
FITB files, resolves ``set_id_index`` tokens through the matching outfit split,
and never writes to the dataset root.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PRIMARY_VARIANTS = {"disjoint", "nondisjoint"}
HARD_NEGATIVE_VARIANT = "maryland_polyvore_hardneg"
SUPPORTED_VARIANTS = PRIMARY_VARIANTS | {HARD_NEGATIVE_VARIANT}
SUPPORTED_SPLITS = {"train", "valid", "test"}


class PolyvoreContractError(ValueError):
    """Raised when the local dataset violates the official file contract."""


@dataclass(frozen=True, slots=True)
class BenchmarkItem:
    token: str
    item_id: str
    semantic_category: str
    category_id: str
    image_path: Path
    embedding_key: str

    @property
    def category_key(self) -> str:
        fine = self.category_id or "unknown"
        return f"{self.semantic_category}:{fine}"


@dataclass(frozen=True, slots=True)
class CompatibilityCase:
    case_id: str
    label: int
    tokens: tuple[str, ...]
    items: tuple[BenchmarkItem, ...] = ()


@dataclass(frozen=True, slots=True)
class FitbCase:
    case_id: str
    question_tokens: tuple[str, ...]
    answer_tokens: tuple[str, ...]
    blank_position: int
    correct_index: int
    question_items: tuple[BenchmarkItem, ...] = ()
    answer_items: tuple[BenchmarkItem, ...] = ()

    @property
    def correct_category(self) -> str:
        if not self.answer_items:
            return "unknown"
        return self.answer_items[self.correct_index].semantic_category


def _require_split(split: str) -> str:
    normalized = split.strip().lower()
    if normalized not in SUPPORTED_SPLITS:
        raise ValueError(f"Unsupported split {split!r}; expected train, valid or test")
    return normalized


def _token_parts(token: str) -> tuple[str, int]:
    raw = token.strip()
    try:
        set_id, index_text = raw.rsplit("_", 1)
        index = int(index_text)
    except (ValueError, TypeError) as exc:
        raise PolyvoreContractError(f"Invalid Polyvore token: {token!r}") from exc
    if not set_id or index < 1:
        raise PolyvoreContractError(f"Invalid Polyvore token: {token!r}")
    return set_id, index


class PolyvoreDataset:
    """Strict read-only adapter for one official Polyvore benchmark variant."""

    def __init__(self, root: Path, variant: str = "disjoint") -> None:
        self.root = Path(root).expanduser().resolve()
        self.variant = variant.strip().lower()
        if self.variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"Unsupported Polyvore variant {variant!r}; "
                f"choose from {sorted(SUPPORTED_VARIANTS)}"
            )
        if not self.root.is_dir():
            raise FileNotFoundError(f"Polyvore dataset root does not exist: {self.root}")
        self.variant_root = self.root / self.variant
        if not self.variant_root.is_dir():
            raise FileNotFoundError(f"Polyvore variant is missing: {self.variant_root}")
        self._metadata: dict[str, dict[str, Any]] | None = None
        self._token_maps: dict[str, dict[str, str]] = {}
        self._resolved_items: dict[tuple[str, str], BenchmarkItem] = {}

    @property
    def supports_item_mapping(self) -> bool:
        return self.variant in PRIMARY_VARIANTS

    def _path(self, name: str) -> Path:
        path = self.variant_root / name
        if not path.is_file():
            raise FileNotFoundError(f"Required Polyvore file is missing: {path}")
        return path

    @staticmethod
    def _split_json_name(split: str) -> str:
        return f"{split}.json"

    @staticmethod
    def _image_split(split: str) -> str:
        return "validation" if split == "valid" else split

    def source_paths(self, split: str) -> dict[str, Path]:
        split = _require_split(split)
        paths = {
            "compatibility": self._path(f"compatibility_{split}.txt"),
            "fitb": self._path(f"fill_in_blank_{split}.json"),
        }
        if self.supports_item_mapping:
            paths["outfits"] = self._path(self._split_json_name(split))
            metadata_path = self.root / "polyvore_item_metadata.json"
            if not metadata_path.is_file():
                raise FileNotFoundError(f"Polyvore metadata is missing: {metadata_path}")
            paths["metadata"] = metadata_path
        return paths

    def _load_metadata(self) -> dict[str, dict[str, Any]]:
        if not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} contains no official token-to-item metadata mapping"
            )
        if self._metadata is None:
            path = self.root / "polyvore_item_metadata.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise PolyvoreContractError(f"Metadata must be a JSON object: {path}")
            self._metadata = payload
        return self._metadata

    def load_token_map(self, split: str) -> Mapping[str, str]:
        split = _require_split(split)
        if not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} has benchmark tokens but no outfit/item mapping files"
            )
        cached = self._token_maps.get(split)
        if cached is not None:
            return cached
        path = self._path(self._split_json_name(split))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise PolyvoreContractError(f"Outfit split must be a JSON array: {path}")
        mapping: dict[str, str] = {}
        for raw_outfit in payload:
            if not isinstance(raw_outfit, dict):
                raise PolyvoreContractError(f"Invalid outfit record in {path}")
            set_id = str(raw_outfit.get("set_id", "")).strip()
            items = raw_outfit.get("items")
            if not set_id or not isinstance(items, list):
                raise PolyvoreContractError(f"Invalid outfit record in {path}: {raw_outfit!r}")
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    raise PolyvoreContractError(f"Invalid item record in {path}")
                item_id = str(raw_item.get("item_id", "")).strip()
                try:
                    index = int(raw_item.get("index"))
                except (TypeError, ValueError) as exc:
                    raise PolyvoreContractError(f"Invalid item index in {path}") from exc
                token = f"{set_id}_{index}"
                previous = mapping.get(token)
                if previous is not None and previous != item_id:
                    raise PolyvoreContractError(
                        f"Token {token!r} maps to both {previous!r} and {item_id!r}"
                    )
                if not item_id:
                    raise PolyvoreContractError(f"Token {token!r} has an empty item_id")
                mapping[token] = item_id
        self._token_maps[split] = mapping
        return mapping

    def resolve_item(self, split: str, token: str) -> BenchmarkItem:
        split = _require_split(split)
        cached = self._resolved_items.get((split, token))
        if cached is not None:
            return cached
        mapping = self.load_token_map(split)
        try:
            item_id = mapping[token]
        except KeyError as exc:
            raise PolyvoreContractError(
                f"Token {token!r} is absent from {self.variant}/{split} outfit mapping"
            ) from exc
        record = self._load_metadata().get(item_id)
        if not isinstance(record, dict):
            raise PolyvoreContractError(f"Item {item_id!r} has no metadata record")
        semantic = str(record.get("semantic_category", "")).strip().lower()
        if not semantic:
            raise PolyvoreContractError(f"Item {item_id!r} has no semantic_category")
        category_id = str(record.get("category_id", "")).strip()
        image_split = self._image_split(split)
        image_path = self.root / "images" / self.variant / image_split / f"{item_id}.jpg"
        if not image_path.is_file():
            raise PolyvoreContractError(f"Item {item_id!r} has no image: {image_path}")
        item = BenchmarkItem(
            token=token,
            item_id=item_id,
            semantic_category=semantic,
            category_id=category_id,
            image_path=image_path,
            embedding_key=f"{self.variant}/{image_split}/{item_id}",
        )
        self._resolved_items[(split, token)] = item
        return item

    def resolve_compatibility_cases(
        self,
        split: str,
        cases: Sequence[CompatibilityCase],
    ) -> tuple[CompatibilityCase, ...]:
        split = _require_split(split)
        if not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} cannot resolve category or image-backed items"
            )
        return tuple(
            CompatibilityCase(
                case_id=case.case_id,
                label=case.label,
                tokens=case.tokens,
                items=tuple(self.resolve_item(split, token) for token in case.tokens),
            )
            for case in cases
        )

    def resolve_fitb_cases(
        self,
        split: str,
        cases: Sequence[FitbCase],
    ) -> tuple[FitbCase, ...]:
        split = _require_split(split)
        if not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} cannot resolve category or image-backed items"
            )
        return tuple(
            FitbCase(
                case_id=case.case_id,
                question_tokens=case.question_tokens,
                answer_tokens=case.answer_tokens,
                blank_position=case.blank_position,
                correct_index=case.correct_index,
                question_items=tuple(
                    self.resolve_item(split, token) for token in case.question_tokens
                ),
                answer_items=tuple(
                    self.resolve_item(split, token) for token in case.answer_tokens
                ),
            )
            for case in cases
        )

    def audit_references(
        self,
        split: str,
        compatibility_cases: Sequence[CompatibilityCase],
        fitb_cases: Sequence[FitbCase],
    ) -> dict[str, int]:
        split = _require_split(split)
        tokens = {
            token
            for case in compatibility_cases
            for token in case.tokens
        }
        tokens.update(
            token
            for case in fitb_cases
            for token in (*case.question_tokens, *case.answer_tokens)
        )
        result = {
            "referenced_token_count": len(tokens),
            "missing_mapping_count": 0,
            "missing_metadata_count": 0,
            "missing_semantic_category_count": 0,
            "missing_image_count": 0,
        }
        if not self.supports_item_mapping:
            result["missing_mapping_count"] = len(tokens)
            return result
        mapping = self.load_token_map(split)
        missing_tokens = tokens - set(mapping)
        result["missing_mapping_count"] = len(missing_tokens)
        metadata = self._load_metadata()
        item_ids = {mapping[token] for token in tokens if token in mapping}
        missing_metadata = {
            item_id for item_id in item_ids if not isinstance(metadata.get(item_id), dict)
        }
        result["missing_metadata_count"] = len(missing_metadata)
        result["missing_semantic_category_count"] = sum(
            1
            for item_id in item_ids - missing_metadata
            if not str(metadata[item_id].get("semantic_category", "")).strip()
        )
        image_root = self.root / "images" / self.variant / self._image_split(split)
        result["missing_image_count"] = sum(
            1 for item_id in item_ids if not (image_root / f"{item_id}.jpg").is_file()
        )
        return result

    def load_compatibility(
        self,
        split: str,
        *,
        require_items: bool = True,
    ) -> tuple[CompatibilityCase, ...]:
        split = _require_split(split)
        if require_items and not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} cannot resolve category or image-backed items"
            )
        path = self._path(f"compatibility_{split}.txt")
        cases: list[CompatibilityCase] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                parts = line.strip().split()
                if not parts:
                    continue
                if len(parts) < 3 or parts[0] not in {"0", "1"}:
                    raise PolyvoreContractError(
                        f"Invalid compatibility row at {path}:{line_number}"
                    )
                tokens = tuple(parts[1:])
                for token in tokens:
                    _token_parts(token)
                items = (
                    tuple(self.resolve_item(split, token) for token in tokens)
                    if require_items
                    else ()
                )
                cases.append(
                    CompatibilityCase(
                        case_id=f"{self.variant}-{split}-compat-{line_number:06d}",
                        label=int(parts[0]),
                        tokens=tokens,
                        items=items,
                    )
                )
        if not cases:
            raise PolyvoreContractError(f"Compatibility split is empty: {path}")
        return tuple(cases)

    def load_fitb(
        self,
        split: str,
        *,
        require_items: bool = True,
    ) -> tuple[FitbCase, ...]:
        split = _require_split(split)
        if require_items and not self.supports_item_mapping:
            raise PolyvoreContractError(
                f"{self.variant} cannot resolve category or image-backed items"
            )
        path = self._path(f"fill_in_blank_{split}.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise PolyvoreContractError(f"FITB split must be a non-empty JSON array: {path}")
        cases: list[FitbCase] = []
        for index, raw in enumerate(payload, start=1):
            if not isinstance(raw, dict):
                raise PolyvoreContractError(f"Invalid FITB record at {path} index {index - 1}")
            question = tuple(str(value).strip() for value in raw.get("question") or ())
            answers = tuple(str(value).strip() for value in raw.get("answers") or ())
            try:
                blank_position = int(raw.get("blank_position"))
            except (TypeError, ValueError) as exc:
                raise PolyvoreContractError(
                    f"Invalid blank_position at {path} index {index - 1}"
                ) from exc
            if not question or len(answers) < 2 or blank_position < 1:
                raise PolyvoreContractError(f"Invalid FITB record at {path} index {index - 1}")
            set_ids = {_token_parts(token)[0] for token in question}
            for token in answers:
                _token_parts(token)
            if len(set_ids) != 1:
                raise PolyvoreContractError(
                    f"FITB question spans multiple source outfits at {path} index {index - 1}"
                )
            correct_token = f"{next(iter(set_ids))}_{blank_position}"
            correct_positions = [
                answer_index
                for answer_index, token in enumerate(answers)
                if token == correct_token
            ]
            if self.variant == "disjoint":
                if len(correct_positions) != 1:
                    raise PolyvoreContractError(
                        f"FITB correct token {correct_token!r} is not unique at "
                        f"{path} index {index - 1}"
                    )
                correct_index = correct_positions[0]
            else:
                # Nondisjoint and Maryland were published with the correct
                # answer first. Maryland additionally contains records whose
                # blank_position does not match the token suffix and records
                # with duplicated answer tokens. Deriving or deduplicating the
                # answer would silently change the published benchmark.
                correct_index = 0
            question_items = (
                tuple(self.resolve_item(split, token) for token in question)
                if require_items
                else ()
            )
            answer_items = (
                tuple(self.resolve_item(split, token) for token in answers)
                if require_items
                else ()
            )
            cases.append(
                FitbCase(
                    case_id=f"{self.variant}-{split}-fitb-{index:06d}",
                    question_tokens=question,
                    answer_tokens=answers,
                    blank_position=blank_position,
                    correct_index=correct_index,
                    question_items=question_items,
                    answer_items=answer_items,
                )
            )
        return tuple(cases)


def stable_unit_interval(seed: int, *parts: str) -> float:
    digest = hashlib.sha256(
        "\x1f".join((str(seed), *parts)).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def sample_compatibility_cases(
    cases: Sequence[CompatibilityCase],
    limit: int | None,
    *,
    seed: int,
) -> tuple[CompatibilityCase, ...]:
    if not limit or limit >= len(cases):
        return tuple(cases)
    if limit < 2:
        raise ValueError("Compatibility sample limit must be at least 2")
    groups: dict[int, list[CompatibilityCase]] = defaultdict(list)
    for case in cases:
        groups[case.label].append(case)
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    labels = sorted(groups)
    allocation = {label: limit // len(labels) for label in labels}
    for label in labels[: limit % len(labels)]:
        allocation[label] += 1
    selected: list[CompatibilityCase] = []
    for label in labels:
        selected.extend(groups[label][: allocation[label]])
    rng.shuffle(selected)
    return tuple(selected)


def sample_fitb_cases(
    cases: Sequence[FitbCase],
    limit: int | None,
    *,
    seed: int,
) -> tuple[FitbCase, ...]:
    if not limit or limit >= len(cases):
        return tuple(cases)
    if limit < 1:
        raise ValueError("FITB sample limit must be positive")
    rng = random.Random(seed)
    return tuple(rng.sample(list(cases), limit))


def roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    if len(labels) != len(scores) or not labels:
        raise ValueError("labels and scores must be non-empty and have equal length")
    positives = sum(label == 1 for label in labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        raise ValueError("ROC-AUC requires both positive and negative labels")
    ordered = sorted(enumerate(scores), key=lambda pair: pair[1])
    ranks = [0.0] * len(scores)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = ((cursor + 1) + end) / 2.0
        for offset in range(cursor, end):
            ranks[ordered[offset][0]] = average_rank
        cursor = end
    positive_rank_sum = sum(rank for rank, label in zip(ranks, labels) if label == 1)
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def select_accuracy_threshold(labels: Sequence[int], scores: Sequence[float]) -> float:
    if len(labels) != len(scores) or not labels:
        raise ValueError("labels and scores must be non-empty and have equal length")
    grouped: dict[float, Counter[int]] = defaultdict(Counter)
    for label, score in zip(labels, scores):
        grouped[float(score)][int(label)] += 1
    ordered_scores = sorted(grouped, reverse=True)
    correct = sum(label == 0 for label in labels)
    best_correct = correct
    threshold = math.nextafter(ordered_scores[0], math.inf)
    for score in ordered_scores:
        counts = grouped[score]
        correct += counts[1] - counts[0]
        if correct > best_correct:
            best_correct = correct
            threshold = score
    return float(threshold)


def _distribution(scores: Sequence[float]) -> dict[str, float]:
    ordered = sorted(float(value) for value in scores)
    if not ordered:
        raise ValueError("score distribution must not be empty")

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "min": ordered[0],
        "p05": percentile(0.05),
        "median": percentile(0.5),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
        "stddev": statistics.pstdev(ordered),
    }


def _wilson_interval(successes: int, total: int) -> list[float]:
    if total < 1:
        return [0.0, 0.0]
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def binary_metrics(
    cases: Sequence[CompatibilityCase],
    scores: Sequence[float],
    *,
    threshold: float,
) -> dict[str, Any]:
    labels = [case.label for case in cases]
    if len(labels) != len(scores) or not labels:
        raise ValueError("Compatibility cases and scores must be non-empty and aligned")
    predictions = [int(score >= threshold) for score in scores]
    correct = sum(prediction == label for prediction, label in zip(predictions, labels))
    positive_scores = [score for score, label in zip(scores, labels) if label == 1]
    negative_scores = [score for score, label in zip(scores, labels) if label == 0]
    return {
        "case_count": len(cases),
        "positive_count": sum(labels),
        "negative_count": len(labels) - sum(labels),
        "roc_auc": roc_auc(labels, scores),
        "accuracy": correct / len(labels),
        "accuracy_ci95": _wilson_interval(correct, len(labels)),
        "threshold": threshold,
        "score_distribution": {
            "positive": _distribution(positive_scores),
            "negative": _distribution(negative_scores),
        },
    }


def evaluate_compatibility(
    validation_cases: Sequence[CompatibilityCase],
    test_cases: Sequence[CompatibilityCase],
    scorer: Callable[[CompatibilityCase], float],
) -> dict[str, Any]:
    started = time.perf_counter()
    validation_scores = [float(scorer(case)) for case in validation_cases]
    threshold = select_accuracy_threshold(
        [case.label for case in validation_cases], validation_scores
    )
    test_scores = [float(scorer(case)) for case in test_cases]
    elapsed = time.perf_counter() - started
    return {
        "validation": binary_metrics(validation_cases, validation_scores, threshold=threshold),
        "test": binary_metrics(test_cases, test_scores, threshold=threshold),
        "scoring_seconds": elapsed,
        "scoring_ms_per_case": elapsed * 1000 / (len(validation_cases) + len(test_cases)),
    }


def _ranked_indices(
    scores: Sequence[float],
    tokens: Sequence[str],
    *,
    seed: int,
    case_id: str,
) -> list[int]:
    if len(scores) != len(tokens) or not scores:
        raise ValueError("FITB scores and tokens must be non-empty and aligned")
    return sorted(
        range(len(scores)),
        key=lambda index: (
            -float(scores[index]),
            stable_unit_interval(seed, "tie", case_id, tokens[index]),
        ),
    )


def evaluate_fitb(
    cases: Sequence[FitbCase],
    scorer: Callable[[FitbCase], Sequence[float]],
    *,
    tie_seed: int,
) -> dict[str, Any]:
    if not cases:
        raise ValueError("FITB cases must not be empty")
    started = time.perf_counter()
    reciprocal_ranks: list[float] = []
    correct_top1 = 0
    buckets: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        scores = tuple(float(value) for value in scorer(case))
        ranked = _ranked_indices(
            scores,
            case.answer_tokens,
            seed=tie_seed,
            case_id=case.case_id,
        )
        rank = ranked.index(case.correct_index) + 1
        is_correct = rank == 1
        correct_top1 += int(is_correct)
        reciprocal_ranks.append(1 / rank)
        buckets[case.correct_category].append(is_correct)
    elapsed = time.perf_counter() - started
    return {
        "case_count": len(cases),
        "top1_accuracy": correct_top1 / len(cases),
        "top1_accuracy_ci95": _wilson_interval(correct_top1, len(cases)),
        "mrr": statistics.fmean(reciprocal_ranks),
        "by_missing_semantic_category": {
            category: {
                "case_count": len(values),
                "top1_accuracy": sum(values) / len(values),
            }
            for category, values in sorted(buckets.items())
        },
        "scoring_seconds": elapsed,
        "scoring_ms_per_case": elapsed * 1000 / len(cases),
    }


class RandomBaseline:
    def __init__(self, seed: int) -> None:
        self.seed = int(seed)

    def score_compatibility(self, case: CompatibilityCase) -> float:
        return stable_unit_interval(self.seed, "compatibility", *case.tokens)

    def score_fitb(self, case: FitbCase) -> tuple[float, ...]:
        return tuple(
            stable_unit_interval(self.seed, "fitb", case.case_id, token)
            for token in case.answer_tokens
        )


class CategoryCooccurrenceBaseline:
    """Train-only fine-category pair-frequency baseline with additive smoothing."""

    def __init__(
        self,
        category_counts: Counter[str],
        pair_counts: Counter[tuple[str, str]],
        *,
        item_total: int,
        pair_total: int,
        alpha: float,
    ) -> None:
        if item_total < 1 or pair_total < 1:
            raise ValueError("Category baseline requires non-empty train statistics")
        self.category_counts = category_counts
        self.pair_counts = pair_counts
        self.item_total = item_total
        self.pair_total = pair_total
        self.alpha = alpha
        self.category_space = max(1, len(category_counts))
        self.pair_space = max(1, self.category_space * (self.category_space + 1) // 2)

    @classmethod
    def fit(
        cls,
        cases: Sequence[CompatibilityCase],
        *,
        alpha: float = 1.0,
    ) -> "CategoryCooccurrenceBaseline":
        if alpha <= 0:
            raise ValueError("alpha must be positive")
        category_counts: Counter[str] = Counter()
        pair_counts: Counter[tuple[str, str]] = Counter()
        item_total = 0
        pair_total = 0
        positive_count = 0
        for case in cases:
            if case.label != 1:
                continue
            if not case.items:
                raise ValueError("Category baseline requires resolved benchmark items")
            positive_count += 1
            categories = [item.category_key for item in case.items]
            category_counts.update(categories)
            item_total += len(categories)
            for left_index, left in enumerate(categories):
                for right in categories[left_index + 1 :]:
                    pair_counts[tuple(sorted((left, right)))] += 1
                    pair_total += 1
        if positive_count == 0:
            raise ValueError("Category baseline requires positive train examples")
        return cls(
            category_counts,
            pair_counts,
            item_total=item_total,
            pair_total=pair_total,
            alpha=alpha,
        )

    def _pair_score(self, left: str, right: str) -> float:
        pair = tuple(sorted((left, right)))
        pair_probability = (self.pair_counts[pair] + self.alpha) / (
            self.pair_total + self.alpha * self.pair_space
        )
        return math.log(pair_probability)

    def score_items(self, items: Sequence[BenchmarkItem]) -> float:
        if len(items) < 2:
            raise ValueError("An outfit score requires at least two items")
        pair_scores = [
            self._pair_score(left.category_key, right.category_key)
            for left_index, left in enumerate(items)
            for right in items[left_index + 1 :]
        ]
        return statistics.fmean(pair_scores)

    def score_compatibility(self, case: CompatibilityCase) -> float:
        return self.score_items(case.items)

    def score_fitb(self, case: FitbCase) -> tuple[float, ...]:
        if not case.question_items or not case.answer_items:
            raise ValueError("Category FITB requires resolved benchmark items")
        return tuple(
            self.score_items((*case.question_items, answer))
            for answer in case.answer_items
        )


def _vector_dot(left: Any, right: Any) -> float:
    return float(left @ right)


class FashionClipBaseline:
    """Non-learning visual baseline over L2-normalized FashionCLIP embeddings."""

    def __init__(self, embeddings: Mapping[str, Any]) -> None:
        self.embeddings = embeddings

    def _vector(self, item: BenchmarkItem) -> Any:
        try:
            return self.embeddings[item.embedding_key]
        except KeyError as exc:
            raise KeyError(f"Missing FashionCLIP embedding for {item.embedding_key}") from exc

    def score_items(self, items: Sequence[BenchmarkItem]) -> float:
        if len(items) < 2:
            raise ValueError("An outfit score requires at least two items")
        cross_category = [
            _vector_dot(self._vector(left), self._vector(right))
            for left_index, left in enumerate(items)
            for right in items[left_index + 1 :]
            if left.semantic_category != right.semantic_category
        ]
        if cross_category:
            return statistics.fmean(cross_category)
        all_pairs = [
            _vector_dot(self._vector(left), self._vector(right))
            for left_index, left in enumerate(items)
            for right in items[left_index + 1 :]
        ]
        return statistics.fmean(all_pairs)

    def score_compatibility(self, case: CompatibilityCase) -> float:
        return self.score_items(case.items)

    def score_fitb(self, case: FitbCase) -> tuple[float, ...]:
        if not case.question_items or not case.answer_items:
            raise ValueError("FashionCLIP FITB requires resolved benchmark items")
        context_vectors = [self._vector(item) for item in case.question_items]
        return tuple(
            statistics.fmean(
                _vector_dot(context, self._vector(answer))
                for context in context_vectors
            )
            for answer in case.answer_items
        )


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
