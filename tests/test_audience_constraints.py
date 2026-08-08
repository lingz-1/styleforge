from styleforge.agents.planner import PlannerAgent
from styleforge.core.request_parser import parse_request
from styleforge.core.schemas import CatalogItem, ImageStatus, TaskSpec
from styleforge.tools.candidate_generation import generate_candidates


def _item(item_id: str, gender: str, item_type: str) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        source="test",
        gender=gender,
        item_type=item_type,
        main_category=item_type,
        name=item_id,
        color="navy",
        description="",
        features=(),
        image_filename=f"{item_id}.jpg",
        relative_image_path=f"{item_id}.jpg",
        image_status=ImageStatus.AVAILABLE,
    )


def test_parser_extracts_specific_audiences_without_substring_leakage() -> None:
    assert parse_request("u", "男装面试穿搭").target_audiences == ("men",)
    assert parse_request("u", "women business outfit").target_audiences == ("women",)
    assert parse_request("u", "男童周末穿搭").target_audiences == ("boys",)


def test_planner_uses_neutral_or_requested_audience_prompt() -> None:
    planner = PlannerAgent()
    neutral = planner.retrieval_prompt(TaskSpec(user_id="u"), "top")
    menswear = planner.retrieval_prompt(
        TaskSpec(user_id="u", target_audiences=("men",)),
        "top",
    )

    assert "women's" not in neutral
    assert "men's top" in menswear


def test_candidate_generation_filters_a_mixed_wardrobe_by_audience() -> None:
    wardrobe = [
        _item("women-top", "women", "top"),
        _item("women-pants", "women", "pants"),
        _item("men-top", "men", "top"),
        _item("men-pants", "men", "pants"),
    ]
    task = TaskSpec(user_id="u", target_audiences=("men",))

    candidates, diagnostics = generate_candidates(wardrobe, task)

    assert candidates
    assert all(item_id.startswith("men-") for item_id in candidates[0].item_ids)
    assert diagnostics["target_audiences"] == ["men"]
