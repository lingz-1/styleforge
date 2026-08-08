from tests.helpers import make_item

from styleforge.core.schemas import TaskSpec
from styleforge.tools.candidate_pool import (
    build_candidate_pool,
    pool_manifest,
)

TASK = TaskSpec(user_id="u", occasion="formal")


def _wardrobe():
    tops = [make_item(f"top-{i}", "top", name=f"Top {i}", color="Black") for i in range(10)]
    bottoms = [make_item(f"bottom-{i}", "pants", name=f"Pant {i}", color="Navy") for i in range(10)]
    shoes = [make_item(f"shoe-{i}", "shoes", name=f"Shoe {i}", color="Black") for i in range(10)]
    outerwear = [make_item(f"coat-{i}", "outwear", name=f"Coat {i}", color="Gray") for i in range(3)]
    return tops + bottoms + shoes + outerwear


def _scores(items):
    return {item.item_id: 1.0 - i * 0.01 for i, item in enumerate(items)}


def test_build_candidate_pool_respects_quotas() -> None:
    items = _wardrobe()
    requirements = {"tops": 4, "bottoms": 4, "shoes": 4, "outerwear": 2, "dresses": 0, "accessories": 0}
    outcome = build_candidate_pool(
        wardrobe_items=items,
        final_scores=_scores(items),
        requirements=requirements,
        task=TASK,
    )
    assert len(outcome.pool_items) == 14
    assert outcome.quota_used["tops"] == 4
    assert outcome.quota_used["outerwear"] == 2
    assert outcome.quota_transfer_log == []


def test_build_candidate_pool_transfers_deficit() -> None:
    items = _wardrobe()
    # Demand 20 coats but only 3 exist; deficit must be filled elsewhere.
    requirements = {"tops": 4, "bottoms": 4, "shoes": 4, "outerwear": 20, "dresses": 0, "accessories": 0}
    outcome = build_candidate_pool(
        wardrobe_items=items,
        final_scores=_scores(items),
        requirements=requirements,
        task=TASK,
    )
    assert outcome.quota_used["outerwear"] == 3
    assert outcome.quota_used["tops"] >= 4
    assert outcome.quota_transfer_log
    total = sum(outcome.quota_used.values())
    assert total == min(len(items), sum(requirements.values()))
    assert total == len(outcome.pool_items)


def test_build_candidate_pool_truncates_to_50() -> None:
    many_items = [make_item(f"item-{i}", "top", name=f"Top {i}", color="Black") for i in range(80)]
    requirements = {"tops": 60, "bottoms": 0, "dresses": 0, "outerwear": 0, "shoes": 0, "accessories": 0}
    outcome = build_candidate_pool(
        wardrobe_items=many_items,
        final_scores=_scores(many_items),
        requirements=requirements,
        task=TASK,
    )
    assert len(outcome.pool_items) == 50
    assert outcome.diagnostics["pool_truncated"] is True


def test_build_candidate_pool_rule_fallback_when_no_scores() -> None:
    items = _wardrobe()
    outcome = build_candidate_pool(
        wardrobe_items=items,
        final_scores={},
        requirements={"tops": 4, "bottoms": 4, "shoes": 4, "outerwear": 0, "dresses": 0, "accessories": 0},
        task=TASK,
    )
    assert outcome.diagnostics["retrieval_degraded_to_rule"] is True
    assert len(outcome.pool_items) == 12


def test_pool_manifest_fields() -> None:
    items = [make_item("top-1", "top", name="Tailored blazer top", color="Black")]
    outcome = build_candidate_pool(
        wardrobe_items=items,
        final_scores={"top-1": 0.9},
        requirements={"tops": 1, "bottoms": 0, "dresses": 0, "outerwear": 0, "shoes": 0, "accessories": 0},
        task=TASK,
    )
    manifest = pool_manifest(outcome)
    assert manifest[0]["item_id"] == "top-1"
    assert manifest[0]["category"] == "tops"
    assert manifest[0]["color"] == "Black"
    assert "formal" in manifest[0]["style_tags"]
    assert isinstance(manifest[0]["score"], float)
