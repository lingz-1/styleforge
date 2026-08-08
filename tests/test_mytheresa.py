import json

import pytest

from styleforge.core.categories import infer_slot
from styleforge.data.mytheresa import (
    MytheresaImagePathResolver,
    canonical_item_type,
    normalize_mytheresa_item,
)
from styleforge.pipelines.audit_mytheresa import audit_mytheresa
from styleforge.pipelines.import_mytheresa import import_mytheresa
from styleforge.repositories.database import database_session


def _record(
    item_type: str = "clothing::tops::long-sleeved tops",
    item_id: str = "P00000001",
) -> dict:
    return {
        "source": "mytheresa",
        "gender": "men",
        "type": item_type,
        "name": "Cotton shirt",
        "images": {
            "product": {"full": [f"{item_id}.jpg"], "partial": [f"{item_id}_d1.jpg"]},
            "model": {"full": [f"{item_id}_b1.jpg"], "partial": []},
        },
        "color": "blue",
        "features": ["Material: 100% cotton"],
        "official_description": "A formal cotton shirt.",
        "description": "A blue long-sleeved shirt.",
        "main_category": "clothing::top/full",
    }


def test_mytheresa_category_mapping_covers_major_components() -> None:
    cases = {
        "clothing::dresses::midi dresses": "dress",
        "clothing::skirts::midi skirts": "skirt",
        "clothing::pants::wide-leg pants": "pants",
        "clothing::jackets::blazers": "outwear",
        "shoes::loafers": "shoes",
        "bags::shoulder bags": "bag",
        "jewelry::fine jewelry::earrings": "earrings",
        "jewelry::fine jewelry::necklaces": "necklace",
        "clothing::beachwear::swimsuits": "swimwear",
        "clothing::beachwear::bikinis bottoms": "swim_bottom",
        "clothing::beachwear::cover-ups": "swim_coverup",
        "clothing::girls' swimwear": "swimwear",
        "clothing::swimwear": "swimwear",
        "baby outfits": "outfit_set",
        "clothing::beachwear::bikinis sets": "swimwear",
        "clothing::boys' swimwear": "swimwear",
        "clothing::girls' skiwear": "skiwear",
        "clothing::skiwear::base layers upper": "base_layer_top",
        "clothing::activewear::bras": "activewear_bra",
        "clothing::boys' skiwear": "skiwear",
        "clothing::girls' outfits": "outfit_set",
        "clothing::tailoring::business": "suit",
        "clothing::boys' outfits": "outfit_set",
        "clothing::tailoring::tuxedo": "suit",
        "clothing::skiwear::base layers lower": "base_layer_bottom",
        "clothing::underwear & sleepwear::boxers": "underwear",
        "clothing::boys' nightwear": "sleepwear",
        "clothing::underwear & sleepwear::briefs": "underwear",
        "clothing::lingerie::bralette": "underwear",
        "baby swimwear": "swimwear",
        "clothing::tailoring::casual": "suit",
        "clothing::girls' nightwear": "sleepwear",
        "clothing::girls' bathtime": "bathwear",
        "clothing::lingerie::briefs": "underwear",
        "clothing::girls' underwear": "underwear",
        "clothing::underwear & sleepwear::pyjama set": "sleepwear",
        "clothing::boys' bathtime": "bathwear",
        "clothing::underwear & sleepwear::legwarmers": "legwear",
        "clothing::lingerie::bikinis": "underwear",
        "clothing::skiwear::ski masks": "hats",
    }
    for raw_type, expected in cases.items():
        record = _record(raw_type)
        record["main_category"] = (
            "shoes"
            if raw_type.startswith("shoes")
            else "bag"
            if raw_type.startswith("bags")
            else "accessory::ear"
            if "earrings" in raw_type
            else "accessory::neck"
            if "necklaces" in raw_type
            else "clothing::top/full"
        )
        assert canonical_item_type(record) == expected


def test_special_use_types_have_isolated_slots() -> None:
    assert infer_slot("swimwear") == "swimwear"
    assert infer_slot("swim_bottom") == "swim_bottom"
    assert infer_slot("skiwear") == "skiwear"
    assert infer_slot("activewear_bra") == "activewear_bra"
    assert infer_slot("underwear") == "underwear"
    assert infer_slot("outfit_set") == "one_piece"


def test_normalizer_preserves_all_views_and_uses_product_main_image(tmp_path) -> None:
    item_root = tmp_path / "P00000001"
    item_root.mkdir()
    for filename in ("P00000001.jpg", "P00000001_d1.jpg", "P00000001_b1.jpg"):
        (item_root / filename).write_bytes(b"image")

    item, images = normalize_mytheresa_item(
        "P00000001",
        _record(),
        MytheresaImagePathResolver(tmp_path),
    )

    assert item.source == "mytheresa"
    assert item.gender == "men"
    assert item.item_type == "top"
    assert item.relative_image_path == "P00000001/P00000001.jpg"
    assert len(images) == 3
    assert images[0].is_primary is True
    assert {image.image_role for image in images} == {
        "product_full",
        "product_partial",
        "model_full",
    }


def test_small_import_registers_external_root_and_multi_view_images(tmp_path) -> None:
    image_root = tmp_path / "images"
    item_root = image_root / "P00000001"
    item_root.mkdir(parents=True)
    for filename in ("P00000001.jpg", "P00000001_d1.jpg", "P00000001_b1.jpg"):
        (item_root / filename).write_bytes(b"image")
    metadata_path = tmp_path / "mytheresa.json"
    metadata_path.write_text(
        json.dumps({"P00000001": _record()}),
        encoding="utf-8",
    )
    database_path = tmp_path / "styleforge.db"

    report = import_mytheresa(
        metadata_path=metadata_path,
        database_path=database_path,
        image_root=image_root,
        source_revision="test",
        batch_size=1,
    )

    assert report["processed_count"] == 1
    assert report["stored_image_records"] == 3
    with database_session(database_path) as connection:
        source = connection.execute(
            "SELECT image_root FROM dataset_sources WHERE source = 'mytheresa'"
        ).fetchone()
        assert source is not None
        assert connection.execute("SELECT COUNT(*) FROM catalog_item_images").fetchone()[0] == 3


def test_metadata_audit_reports_mapping_coverage(tmp_path) -> None:
    metadata_path = tmp_path / "mytheresa.json"
    metadata_path.write_text(
        json.dumps({"P00000001": _record()}),
        encoding="utf-8",
    )

    report = audit_mytheresa(metadata_path)

    assert report["total_items"] == 1
    assert report["total_image_references"] == 3
    assert report["unique_image_references"] == 3
    assert report["by_audience"] == {"men": 1}
    assert report["by_item_type"] == {"top": 1}
    assert report["unmapped_item_count"] == 0


def test_import_rejects_database_inside_external_image_root(tmp_path) -> None:
    image_root = tmp_path / "images"
    image_root.mkdir()
    metadata_path = tmp_path / "mytheresa.json"
    metadata_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must not be inside"):
        import_mytheresa(
            metadata_path=metadata_path,
            database_path=image_root / "styleforge.db",
            image_root=image_root,
            source_revision="test",
        )


def test_interrupted_import_replay_converges_on_a_disposable_database(tmp_path) -> None:
    image_root = tmp_path / "images"
    records = {}
    for item_id in ("P00000001", "P00000002"):
        item_root = image_root / item_id
        item_root.mkdir(parents=True)
        for suffix in (".jpg", "_d1.jpg", "_b1.jpg"):
            (item_root / f"{item_id}{suffix}").write_bytes(b"image")
        records[item_id] = _record(item_id=item_id)
    metadata_path = tmp_path / "mytheresa.json"
    metadata_path.write_text(json.dumps(records), encoding="utf-8")
    database_path = tmp_path / "styleforge.db"

    with pytest.raises(RuntimeError, match="Simulated import failure"):
        import_mytheresa(
            metadata_path=metadata_path,
            database_path=database_path,
            image_root=image_root,
            source_revision="test",
            batch_size=1,
            simulate_failure_after=1,
        )

    first_replay = import_mytheresa(
        metadata_path=metadata_path,
        database_path=database_path,
        image_root=image_root,
        source_revision="test",
        batch_size=1,
    )
    second_replay = import_mytheresa(
        metadata_path=metadata_path,
        database_path=database_path,
        image_root=image_root,
        source_revision="test",
        batch_size=1,
    )

    assert first_replay["mytheresa_catalog_items"] == 2
    assert second_replay["mytheresa_catalog_items"] == 2
    with database_session(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalog_item_images").fetchone()[0] == 6
