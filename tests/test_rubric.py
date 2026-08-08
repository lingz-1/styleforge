from styleforge.core.rubric import (
    DEFAULT_EVALUATION_WEIGHTS,
    FIVE_DIMENSIONS,
    normalize_weights,
    rubric_text,
)


def test_default_weights_sum_to_one() -> None:
    assert abs(sum(DEFAULT_EVALUATION_WEIGHTS.values()) - 1.0) < 1e-6


def test_five_dimension_keys_match_weights() -> None:
    keys = {key for key, _, _, _ in FIVE_DIMENSIONS}
    assert keys == set(DEFAULT_EVALUATION_WEIGHTS)


def test_normalize_merges_missing_with_defaults() -> None:
    result = normalize_weights({"wearability": 0.5})
    # All five dimensions present and sum to 1; wearability is boosted above default.
    assert set(result) == set(DEFAULT_EVALUATION_WEIGHTS)
    assert abs(sum(result.values()) - 1.0) < 1e-6
    assert result["wearability"] > DEFAULT_EVALUATION_WEIGHTS["wearability"]


def test_normalize_empty_returns_defaults() -> None:
    assert normalize_weights({}) == DEFAULT_EVALUATION_WEIGHTS


def test_normalize_none_returns_defaults() -> None:
    assert normalize_weights(None) == DEFAULT_EVALUATION_WEIGHTS


def test_normalize_floor_protection_keeps_sum_one() -> None:
    result = normalize_weights({"wearability": 1.0, "freshness": 0.0})
    assert result["freshness"] >= 0.05
    assert abs(sum(result.values()) - 1.0) < 1e-6


def test_rubric_text_contains_dimensions_and_weight() -> None:
    # Default weights render request_relevance at 25%.
    text = rubric_text()
    assert "需求还原度" in text
    assert "搭配协调性" in text
    assert "实穿性" in text
    assert "25%" in text
