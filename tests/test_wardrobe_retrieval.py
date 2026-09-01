"""Hybrid wardrobe retrieval stays scoped, lazy, and safely degradable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.agentic.environment import Environment
from styleforge.models.agentic_contract import EnvironmentFacts
from styleforge.services.wardrobe_retrieval import (
    WardrobeHybridRetriever,
    WardrobeRetrievalOutcome,
)
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item


class _Embedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        assert len(texts) == 1
        return [[1.0, 0.0]]


class _ScoreStore:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.allowed_ids: list[str] = []

    def score_items(
        self,
        query_vector: Any,
        allowed_item_ids: list[str],
    ) -> dict[str, float]:
        assert query_vector == [1.0, 0.0]
        self.allowed_ids = list(allowed_item_ids)
        return dict(self.scores)


def _retriever(
    catalog_store: Any,
    *,
    personal_store_factory: Any | None = None,
) -> WardrobeHybridRetriever:
    return WardrobeHybridRetriever(
        embedding_dir=Path("embeddings"),
        model_dir=Path("models"),
        text_embedder=_Embedder(),
        catalog_store_factory=lambda: catalog_store,
        personal_store_factory=personal_store_factory or (lambda connection: _ScoreStore({})),
    )


def test_hybrid_search_expands_chinese_and_enforces_slot_allow_list() -> None:
    items = [
        make_item("shoe_white", "shoes", "White sneakers", "white"),
        make_item("shoe_black", "shoes", "Black sneakers", "black"),
        make_item("top_red", "top", "Red blouse", "red"),
    ]
    catalog = _ScoreStore(
        {
            "shoe_white": 0.90,
            "shoe_black": 0.70,
            "top_red": 0.99,
            "another_users_item": 1.0,
        }
    )

    outcome = _retriever(catalog).search(
        "白色运动鞋",
        items,
        connection=None,
        limit=5,
    )

    assert catalog.allowed_ids == ["shoe_white", "shoe_black"]
    assert outcome.item_ids == ["shoe_white", "shoe_black"]
    assert "top_red" not in outcome.item_ids
    assert "another_users_item" not in outcome.item_ids
    assert outcome.mode == "hybrid"
    assert outcome.semantic_available is True
    assert outcome.diagnostics["requested_slots"] == ["footwear"]


def test_semantic_only_query_ranks_by_vector_similarity() -> None:
    items = [
        make_item("minimal", "top", "Plain shirt", "white"),
        make_item("statement", "top", "Structured blouse", "black"),
    ]
    catalog = _ScoreStore({"minimal": 0.1, "statement": 0.9})

    outcome = _retriever(catalog).search(
        "avant garde",
        items,
        connection=None,
        limit=2,
    )

    assert outcome.item_ids == ["statement", "minimal"]
    assert outcome.mode == "semantic"


def test_explicit_slot_with_no_items_does_not_return_another_category() -> None:
    catalog = _ScoreStore({"top": 0.99})

    outcome = _retriever(catalog).search(
        "白色运动鞋",
        [make_item("top", "top", "White blouse", "white")],
        connection=None,
        limit=2,
    )

    assert outcome.item_ids == []
    assert outcome.diagnostics["pool_count"] == 0
    assert catalog.allowed_ids == []


def test_personal_embedding_store_participates_in_semantic_ranking() -> None:
    item = make_item("personal_photo", "top", "Uploaded item", "navy", source="personal")
    personal = _ScoreStore({"personal_photo": 0.8})

    outcome = _retriever(
        _ScoreStore({}),
        personal_store_factory=lambda connection: personal,
    ).search("architectural style", [item], connection=object(), limit=3)

    assert outcome.item_ids == ["personal_photo"]
    assert outcome.mode == "semantic"
    assert personal.allowed_ids == ["personal_photo"]


def test_model_load_failure_is_attempted_once_and_falls_back_to_keyword() -> None:
    attempts = 0

    def fail_to_load() -> Any:
        nonlocal attempts
        attempts += 1
        raise FileNotFoundError("model unavailable")

    retriever = WardrobeHybridRetriever(
        embedding_dir=Path("embeddings"),
        model_dir=Path("models"),
        embedder_factory=fail_to_load,
    )
    items = [make_item("shoe", "shoes", "White sneakers", "white")]

    first = retriever.search("sneakers", items, connection=None, limit=3)
    second = retriever.search("sneakers", items, connection=None, limit=3)

    assert attempts == 1
    assert first.item_ids == ["shoe"]
    assert first.mode == "keyword"
    assert first.semantic_available is False
    assert first.diagnostics["errors"] == ["FileNotFoundError"]
    assert second.item_ids == ["shoe"]
    assert retriever.runtime_status()["degraded"] is True
    assert retriever.runtime_status()["last_error"] == "FileNotFoundError"


def test_runtime_status_does_not_trigger_model_loading() -> None:
    attempts = 0

    def load() -> _Embedder:
        nonlocal attempts
        attempts += 1
        return _Embedder()

    retriever = WardrobeHybridRetriever(
        embedding_dir=Path("embeddings"),
        model_dir=Path("models"),
        embedder_factory=load,
    )

    status = retriever.runtime_status()

    assert attempts == 0
    assert status["model_load_attempted"] is False
    assert status["model_loaded"] is False


def test_catalog_load_failure_is_attempted_once() -> None:
    attempts = 0

    def fail_to_load() -> Any:
        nonlocal attempts
        attempts += 1
        raise FileNotFoundError("index unavailable")

    retriever = WardrobeHybridRetriever(
        embedding_dir=Path("embeddings"),
        model_dir=Path("models"),
        text_embedder=_Embedder(),
        catalog_store_factory=fail_to_load,
    )
    items = [make_item("shoe", "shoes", "White sneakers", "white")]

    retriever.search("sneakers", items, connection=None, limit=3)
    retriever.search("sneakers", items, connection=None, limit=3)

    assert attempts == 1


def test_environment_uses_injected_retriever_and_filters_unknown_ids() -> None:
    class _Retriever:
        def search(self, query: str, items: list[Any], **kwargs: Any) -> Any:
            assert query == "通勤上衣"
            assert [item.item_id for item in items] == ["owned"]
            assert kwargs["limit"] == 2
            return WardrobeRetrievalOutcome(
                item_ids=["owned", "not_owned"],
                matched=2,
                mode="semantic",
                semantic_available=True,
            )

    owned = make_item("owned", "top", "Work blouse", "blue")
    environment = Environment(
        connection=None,
        wardrobe_items=[owned],
        facts=EnvironmentFacts(),
        search_limit=2,
        wardrobe_retriever=_Retriever(),
    )

    result = environment.search_wardrobe("通勤上衣", limit=10)

    assert [item.item_id for item in result.results] == ["owned"]
    assert result.retrieval_mode == "semantic"
    assert result.matched == 2
    assert len(environment.wardrobe_search_diagnostics) == 1
    assert environment.wardrobe_search_diagnostics[0]["mode"] == "semantic"
    assert environment.wardrobe_search_diagnostics[0]["duration_ms"] >= 0


def test_environment_caches_identical_wardrobe_searches_and_uses_short_sessions() -> None:
    calls = 0
    opened = 0

    class _Session:
        def __enter__(self) -> str:
            nonlocal opened
            opened += 1
            return "short-lived-connection"

        def __exit__(self, *args: Any) -> None:
            return None

    class _Retriever:
        def search(self, query: str, items: list[Any], **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            assert kwargs["connection"] == "short-lived-connection"
            return WardrobeRetrievalOutcome(
                item_ids=["owned"],
                matched=1,
                mode="hybrid",
                semantic_available=True,
            )

    owned = make_item("owned", "top", "Work blouse", "blue")
    environment = Environment(
        connection=None,
        wardrobe_items=[owned],
        facts=EnvironmentFacts(),
        wardrobe_retriever=_Retriever(),
        connection_factory=_Session,
    )

    first = environment.search_wardrobe("  通勤上衣 ", limit=4)
    second = environment.search_wardrobe("通勤上衣", limit=4)

    assert calls == 1
    assert opened == 1
    assert first.diagnostics["cache_hit"] is False
    assert second.diagnostics["cache_hit"] is True
    assert environment.wardrobe_search_diagnostics[-1]["cache_hit"] is True


def test_task_diagnostics_aggregates_calls_without_vectors() -> None:
    diagnostics = MultiTaskWorkflow._task_diagnostics(
        [
            {
                "wardrobe_retrievals": [
                    {
                        "query": "白色运动鞋",
                        "vector": [0.1, 0.2],
                        "mode": "hybrid",
                        "duration_ms": 12.25,
                        "degraded": False,
                        "errors": [],
                    }
                ]
            },
            {
                "wardrobe_retrievals": [
                    {
                        "query": "正式外套",
                        "mode": "keyword",
                        "duration_ms": 1.5,
                        "degraded": True,
                        "errors": ["FileNotFoundError"],
                    }
                ]
            },
        ]
    )["wardrobe_retrieval"]

    assert diagnostics["calls"] == 2
    assert diagnostics["modes"] == ["hybrid", "keyword"]
    assert diagnostics["semantic_used"] is True
    assert diagnostics["keyword_fallback_calls"] == 1
    assert diagnostics["degraded_calls"] == 1
    assert diagnostics["total_duration_ms"] == 13.75
    assert diagnostics["errors"] == ["FileNotFoundError"]
    assert "vector" not in str(diagnostics).lower()
    assert "白色运动鞋" not in str(diagnostics)
