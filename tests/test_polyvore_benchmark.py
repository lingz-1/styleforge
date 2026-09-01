from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from evals.polyvore_benchmark import (
    BenchmarkItem,
    CategoryCooccurrenceBaseline,
    CompatibilityCase,
    FashionClipBaseline,
    FitbCase,
    PolyvoreContractError,
    PolyvoreDataset,
    RandomBaseline,
    binary_metrics,
    evaluate_fitb,
    roc_auc,
    sample_compatibility_cases,
    select_accuracy_threshold,
)
from evals.runners import evaluate_polyvore_baselines as runner


def _outfit(set_id: str, item_ids: tuple[str, ...]) -> dict:
    return {
        "set_id": set_id,
        "items": [
            {"item_id": item_id, "index": index}
            for index, item_id in enumerate(item_ids, start=1)
        ],
    }


def _build_dataset(root: Path) -> Path:
    variant_root = root / "disjoint"
    variant_root.mkdir(parents=True)
    outfits = [
        _outfit("set-a", ("a-top", "a-bottom", "a-shoes")),
        _outfit("set-b", ("b-top", "b-bottom", "b-shoes")),
        _outfit("set-c", ("c-top", "c-bottom", "c-shoes")),
        _outfit("set-d", ("d-top", "d-bottom", "d-shoes")),
    ]
    categories = {
        "top": ("tops", "11"),
        "bottom": ("bottoms", "21"),
        "shoes": ("shoes", "31"),
    }
    metadata = {}
    for outfit in outfits:
        for item in outfit["items"]:
            suffix = item["item_id"].split("-", 1)[1]
            semantic, category_id = categories[suffix]
            metadata[item["item_id"]] = {
                "semantic_category": semantic,
                "category_id": category_id,
                "url_name": item["item_id"],
            }
    (root / "polyvore_item_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    compatibility = "\n".join(
        (
            "1 set-a_1 set-a_2 set-a_3",
            "1 set-b_1 set-b_2 set-b_3",
            "0 set-a_1 set-c_2 set-b_3",
            "0 set-d_1 set-b_2 set-c_3",
        )
    )
    fitb = [
        {
            "question": ["set-a_1", "set-a_2"],
            "blank_position": 3,
            "answers": ["set-c_3", "set-a_3", "set-b_3", "set-d_3"],
        },
        {
            "question": ["set-b_1", "set-b_3"],
            "blank_position": 2,
            "answers": ["set-b_2", "set-d_2", "set-a_2", "set-c_2"],
        },
    ]
    for split, image_split in (("train", "train"), ("valid", "validation"), ("test", "test")):
        (variant_root / f"{split}.json").write_text(
            json.dumps(outfits), encoding="utf-8"
        )
        (variant_root / f"compatibility_{split}.txt").write_text(
            compatibility + "\n", encoding="utf-8"
        )
        (variant_root / f"fill_in_blank_{split}.json").write_text(
            json.dumps(fitb), encoding="utf-8"
        )
        image_root = root / "images" / "disjoint" / image_split
        image_root.mkdir(parents=True)
        for item_id in metadata:
            (image_root / f"{item_id}.jpg").write_bytes(b"fixture")
    return root


def _item(
    token: str,
    category: str,
    category_id: str,
    key: str,
) -> BenchmarkItem:
    return BenchmarkItem(
        token=token,
        item_id=token,
        semantic_category=category,
        category_id=category_id,
        image_path=Path(f"{token}.jpg"),
        embedding_key=key,
    )


def test_adapter_resolves_shuffled_disjoint_fitb_answer(tmp_path: Path) -> None:
    dataset = PolyvoreDataset(_build_dataset(tmp_path / "polyvore"), "disjoint")

    cases = dataset.load_fitb("test")

    assert cases[0].correct_index == 1
    assert cases[0].answer_items[1].item_id == "a-shoes"
    assert cases[0].correct_category == "shoes"
    assert cases[0].answer_items[1].image_path.name == "a-shoes.jpg"


def test_adapter_full_reference_audit_has_zero_missing(tmp_path: Path) -> None:
    dataset = PolyvoreDataset(_build_dataset(tmp_path / "polyvore"), "disjoint")
    compatibility = dataset.load_compatibility("valid", require_items=False)
    fitb = dataset.load_fitb("valid", require_items=False)

    audit = dataset.audit_references("valid", compatibility, fitb)

    assert audit == {
        "referenced_token_count": 11,
        "missing_mapping_count": 0,
        "missing_metadata_count": 0,
        "missing_semantic_category_count": 0,
        "missing_image_count": 0,
    }


def test_adapter_rejects_unknown_token_when_materialized(tmp_path: Path) -> None:
    root = _build_dataset(tmp_path / "polyvore")
    path = root / "disjoint" / "compatibility_test.txt"
    path.write_text("1 missing_1 set-a_2 set-a_3\n", encoding="utf-8")
    dataset = PolyvoreDataset(root, "disjoint")
    raw = dataset.load_compatibility("test", require_items=False)

    assert dataset.audit_references("test", raw, ())["missing_mapping_count"] == 1
    with pytest.raises(PolyvoreContractError, match="absent"):
        dataset.resolve_compatibility_cases("test", raw)


def test_fitb_correct_token_must_match_blank_position(tmp_path: Path) -> None:
    root = _build_dataset(tmp_path / "polyvore")
    broken = [{
        "question": ["set-a_1", "set-a_2"],
        "blank_position": 3,
        "answers": ["set-b_3", "set-c_3", "set-d_3", "set-b_2"],
    }]
    (root / "disjoint" / "fill_in_blank_test.json").write_text(
        json.dumps(broken), encoding="utf-8"
    )

    with pytest.raises(PolyvoreContractError, match="is not unique"):
        PolyvoreDataset(root, "disjoint").load_fitb("test", require_items=False)


def test_maryland_uses_published_first_answer_contract(tmp_path: Path) -> None:
    root = tmp_path / "polyvore"
    variant = root / "maryland_polyvore_hardneg"
    variant.mkdir(parents=True)
    payload = [{
        "question": ["set-a_1", "set-a_2", "set-a_3"],
        "blank_position": 4,
        "answers": ["set-a_5", "set-b_5", "set-c_5", "set-a_5"],
    }]
    (variant / "fill_in_blank_test.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    cases = PolyvoreDataset(root, "maryland_polyvore_hardneg").load_fitb(
        "test", require_items=False
    )

    assert cases[0].correct_index == 0


def test_binary_metrics_and_threshold_are_exact() -> None:
    cases = tuple(
        CompatibilityCase(str(index), label, (str(index), "x"))
        for index, label in enumerate((0, 0, 1, 1))
    )
    scores = (0.1, 0.2, 0.8, 0.9)
    threshold = select_accuracy_threshold([case.label for case in cases], scores)

    metrics = binary_metrics(cases, scores, threshold=threshold)

    assert threshold == 0.8
    assert metrics["roc_auc"] == 1.0
    assert metrics["accuracy"] == 1.0
    assert roc_auc((0, 1, 0, 1), (0.5, 0.5, 0.5, 0.5)) == 0.5


def test_compatibility_sampling_is_balanced_and_reproducible() -> None:
    cases = tuple(
        CompatibilityCase(f"case-{index}", index % 2, (str(index), "x"))
        for index in range(20)
    )

    first = sample_compatibility_cases(cases, 8, seed=17)
    second = sample_compatibility_cases(cases, 8, seed=17)

    assert first == second
    assert sum(case.label for case in first) == 4


def test_category_cooccurrence_uses_train_pair_statistics() -> None:
    top = _item("top", "tops", "11", "top")
    bottom = _item("bottom", "bottoms", "21", "bottom")
    unseen = _item("unseen", "bags", "41", "unseen")
    train = tuple(
        CompatibilityCase(f"positive-{index}", 1, ("a", "b"), (top, bottom))
        for index in range(8)
    ) + (CompatibilityCase("negative", 0, ("a", "c"), (top, unseen)),)
    model = CategoryCooccurrenceBaseline.fit(train)

    assert model.score_items((top, bottom)) > model.score_items((top, unseen))
    assert model.pair_counts[("bottoms:21", "tops:11")] == 8


def test_fashionclip_baseline_scores_compatibility_and_fitb() -> None:
    top = _item("set_1", "tops", "11", "top")
    bottom = _item("set_2", "bottoms", "21", "bottom")
    good = _item("set_3", "shoes", "31", "good")
    bad = _item("other_3", "shoes", "31", "bad")
    embeddings = {
        "top": np.asarray([1.0, 0.0], dtype=np.float32),
        "bottom": np.asarray([0.8, 0.2], dtype=np.float32),
        "good": np.asarray([0.9, 0.1], dtype=np.float32),
        "bad": np.asarray([-1.0, 0.0], dtype=np.float32),
    }
    model = FashionClipBaseline(embeddings)
    fitb = FitbCase(
        case_id="fitb",
        question_tokens=(top.token, bottom.token),
        answer_tokens=(bad.token, good.token),
        blank_position=3,
        correct_index=1,
        question_items=(top, bottom),
        answer_items=(bad, good),
    )

    assert model.score_fitb(fitb)[1] > model.score_fitb(fitb)[0]
    assert model.score_items((top, bottom, good)) > model.score_items((top, bottom, bad))


def test_random_fitb_is_reproducible_and_reports_mrr() -> None:
    case = FitbCase(
        case_id="case",
        question_tokens=("set_1", "set_2"),
        answer_tokens=("a", "b", "set_3", "d"),
        blank_position=3,
        correct_index=2,
    )
    first = RandomBaseline(9).score_fitb(case)
    second = RandomBaseline(9).score_fitb(case)
    result = evaluate_fitb((case,), RandomBaseline(9).score_fitb, tie_seed=9)

    assert first == second
    assert result["case_count"] == 1
    assert result["mrr"] in {1.0, 0.5, 1 / 3, 0.25}
    assert result["by_missing_semantic_category"]["unknown"]["case_count"] == 1


def test_runner_writes_reproducible_random_and_category_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    dataset_root = _build_dataset(tmp_path / "polyvore")
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)
    report_path = workspace / "artifacts" / "report.json"

    report = runner.run_evaluation(
        dataset_root=dataset_root,
        report_path=report_path,
        cache_path=workspace / "artifacts" / "cache.sqlite3",
        model_dir=workspace / "models",
        variants=("disjoint",),
        baselines=("random", "category_cooccurrence"),
        seed=42,
        max_train_compatibility=None,
        max_compatibility=None,
        max_fitb=None,
        device="cpu",
        precision="float32",
        embedding_batch_size=2,
    )

    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert written["schema_version"] == "styleforge.polyvore-baselines.v1"
    assert written["variants"]["disjoint"]["mapping_audit"]["test"][
        "missing_mapping_count"
    ] == 0
    assert set(written["variants"]["disjoint"]["baselines"]) == {
        "random",
        "category_cooccurrence",
    }
    assert math.isfinite(
        written["variants"]["disjoint"]["baselines"]["random"][
            "compatibility"
        ]["test"]["roc_auc"]
    )


def test_runner_rejects_output_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)

    with pytest.raises(ValueError, match="must stay inside"):
        runner._workspace_output(tmp_path / "outside.json")
