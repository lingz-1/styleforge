import numpy as np
import pytest

from tests.helpers import make_item

from styleforge.core.schemas import TaskSpec
from styleforge.services.semantic_retrieval import (
    novelty_scores,
    preference_scores,
    score_multi_query,
)


class _FakeEncoder:
    def __init__(self, vectors) -> None:
        self.vectors = np.asarray(vectors, dtype=np.float32)

    def encode_texts(self, texts):
        return self.vectors[: len(texts)]


class _FakeStore:
    """score_items looks up the first vector element to pick a score row."""

    def __init__(self, table) -> None:
        self.table = table

    def score_items(self, query_vector, allowed_ids):
        row = self.table[float(np.asarray(query_vector).flat[0])]
        return {item_id: score for item_id, score in row.items() if item_id in allowed_ids}


PLANS = [
    {"type": "core", "query": "dark theater outfit", "score_weight": 0.40},
    {"type": "distinctive", "query": "structured burgundy piece", "score_weight": 0.30},
    {"type": "supporting", "query": "comfortable semi-formal", "score_weight": 0.10},
]

ITEM_A = make_item("A", "top", name="A top", color="Black")
ITEM_B = make_item("B", "top", name="B top", color="Red")
ITEM_C = make_item("C", "top", name="C top", color="Blue")


def _basic_setup():
    encoder = _FakeEncoder([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    catalog = _FakeStore(
        {
            0.0: {"A": 0.9, "B": 0.8, "C": 0.7},
            1.0: {"A": 0.5, "B": 0.4},
            2.0: {"A": 0.2, "B": 0.1},
        }
    )
    return encoder, catalog


def test_score_multi_query_weights_and_combines() -> None:
    encoder, catalog = _basic_setup()
    preference = {"A": 1.0, "B": 0.0, "C": 0.5}
    novelty = {"A": 0.5, "B": 0.5, "C": 0.5}

    outcome = score_multi_query(
        plans=PLANS,
        wardrobe_items=[ITEM_A, ITEM_B, ITEM_C],
        by_slot={"top": ["A", "B", "C"]},
        encoder=encoder,
        catalog_store=catalog,
        personal_store=None,
        preference=preference,
        novelty=novelty,
    )

    # weighted: A=0.4*0.9+0.3*0.5+0.1*0.2=0.53, B=0.4*0.8+0.3*0.4+0.1*0.1=0.45, C=0.4*0.7=0.28
    # norm (min-max over 0.28..0.53): A=1.0, B=0.68, C=0.0
    # final A=0.8*1+0.1*1+0.1*0.5=0.95
    assert outcome.final_scores["A"] == pytest.approx(0.95, abs=1e-6)
    # final B=0.8*0.68+0.0+0.05=0.594
    assert outcome.final_scores["B"] == pytest.approx(0.594, abs=1e-6)
    # final C=0.0+0.1*0.5+0.1*0.5=0.1
    assert outcome.final_scores["C"] == pytest.approx(0.1, abs=1e-6)
    assert outcome.per_plan_raw["core"]["A"] == pytest.approx(0.9, abs=1e-6)
    assert len(outcome.prompts) == 3


def test_score_multi_query_respects_allow_list() -> None:
    encoder, catalog = _basic_setup()
    outcome = score_multi_query(
        plans=PLANS,
        wardrobe_items=[ITEM_A, ITEM_B, ITEM_C],
        by_slot={"top": ["A"]},
        encoder=encoder,
        catalog_store=catalog,
        personal_store=None,
        preference={"A": 0.5},
        novelty={"A": 0.5},
    )
    assert set(outcome.final_scores) == {"A"}


def test_score_multi_query_flat_scores_give_midpoint() -> None:
    encoder = _FakeEncoder([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    catalog = _FakeStore(
        {
            0.0: {"A": 0.5, "B": 0.5},
            1.0: {"A": 0.5, "B": 0.5},
            2.0: {"A": 0.5, "B": 0.5},
        }
    )
    outcome = score_multi_query(
        plans=PLANS,
        wardrobe_items=[ITEM_A, ITEM_B],
        by_slot={"top": ["A", "B"]},
        encoder=encoder,
        catalog_store=catalog,
        personal_store=None,
        preference={"A": 0.5, "B": 0.5},
        novelty={"A": 0.5, "B": 0.5},
    )
    assert outcome.final_scores["A"] == pytest.approx(outcome.final_scores["B"], abs=1e-6)


def test_preference_scores_neutral_when_no_preferred_colors() -> None:
    task = TaskSpec(user_id="u")
    scores = preference_scores([ITEM_A, ITEM_B], task)
    assert scores == {"A": 0.5, "B": 0.5}


def test_preference_scores_matches_color() -> None:
    task = TaskSpec(user_id="u", preferred_colors=("red",))
    scores = preference_scores([ITEM_A, ITEM_B], task)
    assert scores == {"A": 0.0, "B": 1.0}


def test_novelty_scores_defaults_to_midpoint_without_memory() -> None:
    scores = novelty_scores([ITEM_A, ITEM_B], [])
    assert scores == {"A": 0.5, "B": 0.5}


def test_novelty_scores_penalizes_matching_facets() -> None:
    recent = [
        {
            "category_structure": ["top"],
            "dominant_color_family": ["black"],
            "style_mix": ["basic"],
        }
    ]
    item = make_item("blacktop", "top", name="Basic cotton top", color="Black")
    scores = novelty_scores([item], recent)
    # facets: slot(top) hit, color(black) hit, style(basic) hit -> exposure 1.0
    assert scores["blacktop"] == pytest.approx(0.0, abs=1e-6)
