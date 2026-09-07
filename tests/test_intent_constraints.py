from __future__ import annotations

from styleforge.agentic.intent_constraints import (
    desired_features_for_request,
    explicit_feature_requirements,
    item_feature_evidence,
    missing_feature_requirements,
    unavailable_feature_requirements,
)
from styleforge.agentic.graph.main import _modify_target_slots

from tests.helpers import make_item


def test_raw_public_metadata_supplies_auditable_feature_evidence() -> None:
    heels = make_item(
        "heels",
        "shoes",
        "Closed Pointed Toe Stiletto High Heels",
        description="4 inch heel wedding pumps",
    )
    loafers = make_item(
        "loafers",
        "shoes",
        "Faux Suede Loafers",
        description="Lightly padded insole and flat loafer styling",
    )
    leggings = make_item(
        "leggings",
        "pants",
        "Space Dye Leggings",
        description="Very stretchy sports leggings for training",
    )

    assert "high_heels" in item_feature_evidence(heels)
    assert "comfortable" in item_feature_evidence(loafers)
    assert "sport" in item_feature_evidence(leggings)


def test_numeric_heel_measurements_are_recognized_as_high_heels() -> None:
    millimetres = make_item(
        "heel-mm",
        "shoes",
        "Leather sandals",
        description="Wooden heel measures approximately 120mm / 5 inches.",
    )
    inches = make_item(
        "heel-in",
        "shoes",
        "Flower sandals",
        description="Leather sole. 4'' Heel.",
    )

    assert "high_heels" in item_feature_evidence(millimetres)
    assert "high_heels" in item_feature_evidence(inches)


def test_long_standing_and_no_heels_reject_literal_stiletto_metadata() -> None:
    heels = make_item("heels", "shoes", "Pointed Toe Stiletto High Heels")
    loafers = make_item(
        "loafers",
        "shoes",
        "Padded Flat Loafers",
        description="comfortable padded insole",
    )

    missing = missing_feature_requirements(
        "见客户，要适合久站而且不要高跟鞋",
        [heels],
        available_items=[heels, loafers],
    )

    assert {requirement.feature for requirement in missing} >= {
        "comfortable",
        "high_heels",
    }


def test_basketball_is_infeasible_without_literal_sport_items() -> None:
    formal = [
        make_item("top", "top", "Lace top"),
        make_item("pants", "pants", "Tailored trousers"),
        make_item("heels", "shoes", "Stiletto High Heels"),
    ]

    missing = missing_feature_requirements(
        "只用当前衣柜进行篮球训练",
        formal,
        available_items=formal,
    )

    assert {requirement.feature for requirement in missing} >= {"sport", "cushioned"}
    unavailable = unavailable_feature_requirements("进行篮球训练", formal)
    assert {requirement.feature for requirement in unavailable} == {"sport", "cushioned"}


def test_yoga_requires_activity_suitable_top_and_bottom() -> None:
    selected = [
        make_item("denim", "top", "Ruffled denim top"),
        make_item("shorts", "shorts", "Cargo shorts"),
        make_item("air", "shoes", "Running sneaker", features=("sport",)),
    ]
    available = selected + [
        make_item("bra", "top", "Sports bra", features=("sport",)),
        make_item("leggings", "pants", "Sports leggings", features=("sport",)),
    ]

    missing = missing_feature_requirements(
        "低强度瑜伽和热身训练",
        selected,
        available_items=available,
    )

    assert {(requirement.feature, requirement.slot) for requirement in missing} >= {
        ("sport", "top"),
        ("sport", "bottom"),
    }


def test_exclusive_modify_clause_does_not_target_kept_slots() -> None:
    request = "保留绿色真丝吊带裙和黑色 Marni 外套，只把黑色细高跟鞋换成另一双正式鞋。"

    assert _modify_target_slots(request) == ["footwear"]
    assert _modify_target_slots("再换一件外套，其他都保留。") == ["outerwear"]


def test_casual_modify_request_prefers_comfort_evidence_without_hard_constraint() -> None:
    assert desired_features_for_request("整体调整得更休闲一点") == {
        "comfortable",
        "casual",
        "practical",
    }
    assert explicit_feature_requirements("整体调整得更休闲一点") == ()


def test_business_request_and_english_metadata_supply_formal_evidence() -> None:
    shirt = make_item("shirt", "top", "Striped office shirt")

    assert {"formal"} <= item_feature_evidence(shirt)
    assert {"formal", "minimal", "practical"} <= desired_features_for_request(
        "夏天下午做项目汇报，想穿得利落"
    )
    assert any(
        requirement.feature == "formal"
        for requirement in explicit_feature_requirements("做项目汇报，穿得利落")
    )
