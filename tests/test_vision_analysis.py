"""Offline unit tests for the structured clothing-analysis module."""

from __future__ import annotations

import io

from PIL import Image

from styleforge.vision.clothing_analysis import (
    ClothingAttributes,
    build_analysis_prompt,
    is_reliable_analysis,
    map_ai_type_to_item_fields,
    parse_attributes,
)
from styleforge.vision.vision_client import (
    OpenAiVisionClient,
    preprocess_image,
)


def test_build_analysis_prompt_lists_vocabulary_and_json_shape() -> None:
    prompt = build_analysis_prompt()
    assert "OUTPUT ONLY JSON" in prompt
    assert '"type":"TYPE"' in prompt
    assert '"cultural_origin":["none"]' in prompt
    assert '"season":["SEASON1"]' in prompt
    for token in ("t-shirt", "qipao-cheongsam", "ma-mian-skirt", "all-season"):
        assert token in prompt


def test_map_ai_type_to_item_fields_maps_common_garments() -> None:
    assert map_ai_type_to_item_fields("shirt") == ("top", "shirt")
    assert map_ai_type_to_item_fields("t-shirt") == ("top", "t_shirt")
    assert map_ai_type_to_item_fields("jeans") == ("pants", "jeans")
    assert map_ai_type_to_item_fields("sneakers") == ("shoes", "sneakers")
    assert map_ai_type_to_item_fields("ankle-boots") == ("shoes", "ankle_boots")
    assert map_ai_type_to_item_fields("blazer") == ("outwear", "blazer")
    assert map_ai_type_to_item_fields("scarf") == ("accessory", "scarf")


def test_map_ai_type_to_item_fields_aliases_and_free_text() -> None:
    assert map_ai_type_to_item_fields("denim jacket") == ("outwear", "denim_jacket")
    assert map_ai_type_to_item_fields("shirt with cuff") == ("top", "shirt")
    assert map_ai_type_to_item_fields("tanktop") == ("top", "tank_top")
    assert map_ai_type_to_item_fields("unknown garment") == ("other", "")


def test_map_ai_type_to_item_fields_uses_ai_subtype_when_provided() -> None:
    assert map_ai_type_to_item_fields("shirt", "overshirt") == ("top", "overshirt")
    assert map_ai_type_to_item_fields("unknown", "hoodie") == ("top", "hoodie")


def test_clothing_attributes_accepts_full_payload() -> None:
    attributes = ClothingAttributes.model_validate(
        {
            "type": "qipao-cheongsam",
            "subtype": "qipao",
            "primary_color": "red",
            "colors": ["red", "gold"],
            "pattern": "floral",
            "material": "silk",
            "formality": "formal",
            "style": ["elegant", "vintage"],
            "season": ["summer"],
            "fit": "tailored",
            "occasion": ["ceremony"],
            "cultural_origin": ["qipao-cheongsam"],
            "silhouette": "A-line",
            "neckline": "mandarin",
            "collar": "mandarin",
            "sleeve_length": "short",
            "length": "midi",
            "brand": "",
            "condition": "new",
            "features": ["embroidery"],
            "description": "一件红色盘扣旗袍",
            "confidence": 0.95,
        }
    )
    assert attributes.type == "qipao-cheongsam"
    assert attributes.cultural_origin == ["qipao-cheongsam"]
    assert attributes.confidence == 0.95


def test_clothing_attributes_fills_missing_fields() -> None:
    attributes = ClothingAttributes.model_validate({"type": "jeans"})
    assert attributes.primary_color == ""
    assert attributes.colors == []
    assert attributes.cultural_origin == []
    assert attributes.confidence == 0.0


def test_parse_attributes_accepts_dict_and_raw_text() -> None:
    payload = {"type": "jeans", "primary_color": "blue", "confidence": 0.8}
    assert parse_attributes(payload).primary_color == "blue"
    assert parse_attributes('{"type":"jeans","confidence":0.8}').type == "jeans"


def test_parse_attributes_degrades_on_bad_input() -> None:
    broken = parse_attributes("not json at all")
    assert broken.confidence == 0.0
    assert broken.type == "other"
    assert parse_attributes("[1, 2, 3]").type == "other"


def test_parse_attributes_tolerates_null_confidence_and_string_lists() -> None:
    attributes = parse_attributes(
        {
            "type": "jeans",
            "season": "summer",
            "colors": None,
            "cultural_origin": ["none"],
            "confidence": None,
        }
    )
    assert attributes.type == "jeans"
    assert attributes.season == ["summer"]
    assert attributes.colors == []
    assert attributes.confidence == 0.0


def test_parse_attributes_tolerates_string_confidence_and_non_numeric() -> None:
    attributes = parse_attributes(
        {"type": "hoodie", "confidence": "0.9", "style": "streetwear"}
    )
    assert attributes.confidence == 0.9
    assert attributes.style == ["streetwear"]
    out_of_range = parse_attributes({"type": "hoodie", "confidence": 5})
    assert out_of_range.confidence == 1.0
    garbage = parse_attributes({"type": "hoodie", "confidence": "high"})
    assert garbage.confidence == 0.0


def test_is_reliable_analysis_requires_real_type_and_confidence() -> None:
    assert is_reliable_analysis(ClothingAttributes(type="jeans", confidence=0.8))
    assert not is_reliable_analysis(ClothingAttributes(type="other", confidence=0.9))
    assert not is_reliable_analysis(ClothingAttributes(type="jeans", confidence=0.1))
    assert not is_reliable_analysis(ClothingAttributes(confidence=0.0))


def test_preprocess_image_returns_jpeg_data_url() -> None:
    image = Image.new("RGB", (64, 64), (200, 30, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data_url = preprocess_image(buffer.getvalue())
    assert data_url.startswith("data:image/jpeg;base64,")
    assert len(data_url) > 64


def test_extract_json_object_strips_markdown_fence() -> None:
    extract = OpenAiVisionClient._extract_json_object
    assert extract('```json\n{"type": "t-shirt"}\n```') == '{"type": "t-shirt"}'
    assert extract('{"type": "t-shirt"}') == '{"type": "t-shirt"}'
    assert extract('Here is the answer: {"season": ["summer"]}.') == '{"season": ["summer"]}'
    assert extract("no json here") == "no json here"
