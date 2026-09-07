from __future__ import annotations

import uuid

import pytest

from styleforge.core.categories import infer_slot
from evals.wardrobe_fixtures import (
    fixture_item_id,
    list_fixture_names,
    load_fixture,
    seed_fixture,
)
from styleforge.repositories.wardrobe_repository import list_items


EXPECTED_FIXTURES = {
    "commute",
    "limited",
    "no_solution",
    "one_piece",
    "polyvore_one_piece",
    "rain",
    "real_balanced",
    "real_formal_separates",
    "real_one_piece",
    "real_sparse",
    "real_sport_casual",
    "real_weekend",
    "real_winter",
    "sport",
}


def test_fixture_catalog_is_complete_and_ids_are_stable() -> None:
    assert set(list_fixture_names()) == EXPECTED_FIXTURES
    seen: dict[str, str] = {}
    for name in EXPECTED_FIXTURES:
        fixture = load_fixture(name)
        assert fixture.items
        assert len(fixture.item_ids) == len(set(fixture.item_ids))
        for key, item in zip(fixture.item_keys, fixture.items):
            assert item.item_id == fixture_item_id(key)
            assert uuid.UUID(item.item_id).version == 5
            assert item.name and item.item_type and item.color
            previous = seen.setdefault(key, item.item_id)
            assert previous == item.item_id


def test_profiles_lock_the_intended_quality_scenarios() -> None:
    one_piece = load_fixture("one_piece")
    types = {item.item_type for item in one_piece.items}
    assert {"dress", "shoes", "outwear", "bag", "earrings"} <= types

    limited = load_fixture("limited")
    assert {item.item_type for item in limited.items} == {"top", "pants", "shoes"}

    no_solution = load_fixture("no_solution")
    assert {item.item_type for item in no_solution.items} == {"bag", "earrings"}

    real = load_fixture("polyvore_one_piece")
    assert len(real.items) == 11
    assert {item.source for item in real.items} == {"polyvore"}
    assert {item.dataset_item_id for item in real.items} == {
        "151616863",
        "189174949",
        "192792274",
        "193047150",
        "196030328",
        "196144884",
        "201181532",
        "202458704",
        "207483977",
        "209557897",
        "95456824",
    }
    assert all(item.relative_image_path and item.image_filename for item in real.items)

    formal = load_fixture("real_formal_separates")
    assert {"top", "bottom", "footwear", "outerwear"} <= {
        infer_slot(item.item_type) for item in formal.items
    }
    assert any("loafer" in item.name.lower() for item in formal.items)
    assert any("4'' heel" in item.description.lower() for item in formal.items)
    assert any(
        item.dataset_item_id == "203215884"
        and "cotton and linen" in item.description.lower()
        for item in formal.items
    )

    one_piece_real = load_fixture("real_one_piece")
    assert any(
        item.dataset_item_id == "194059150"
        and "loafer" in item.name.lower()
        and "padded insole" in item.description.lower()
        for item in one_piece_real.items
    )

    sport = load_fixture("real_sport_casual")
    assert {"top", "bottom", "footwear"} <= {
        infer_slot(item.item_type) for item in sport.items
    }
    assert any("low impact" in item.description.lower() for item in sport.items)

    winter = load_fixture("real_winter")
    assert any("wool and cashmere" in item.description.lower() for item in winter.items)

    sparse = load_fixture("real_sparse")
    assert {infer_slot(item.item_type) for item in sparse.items} == {"accessory", "bag"}

    # New formal-evaluation fixtures expose only raw item metadata. Outfit
    # titles and reverse-drafted occasion labels never enter model-visible
    # features or descriptions.
    for fixture_name in (
        "real_balanced",
        "real_formal_separates",
        "real_one_piece",
        "real_sparse",
        "real_sport_casual",
        "real_weekend",
        "real_winter",
    ):
        fixture = load_fixture(fixture_name)
        assert {item.source for item in fixture.items} == {"polyvore"}
        assert all(not item.features for item in fixture.items)
        assert all("official polyvore" not in item.description.lower() for item in fixture.items)


def test_seed_fixture_is_idempotent_and_user_scoped(db_conn) -> None:
    commute = seed_fixture(db_conn, "commute")
    seeded_again = seed_fixture(db_conn, "commute")
    rain = seed_fixture(db_conn, "rain")
    db_conn.commit()

    assert commute.item_ids == seeded_again.item_ids
    assert {item.item_id for item in list_items(db_conn, commute.user_id)} == set(
        commute.item_ids
    )
    assert {item.item_id for item in list_items(db_conn, rain.user_id)} == set(rain.item_ids)
    assert commute.user_id != rain.user_id


def test_unknown_fixture_fails_with_available_names() -> None:
    with pytest.raises(KeyError, match="available: commute"):
        load_fixture("missing")
