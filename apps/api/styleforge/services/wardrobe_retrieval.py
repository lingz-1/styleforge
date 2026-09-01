"""Lazy hybrid retrieval over one user's wardrobe allow-list."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal, Sequence

from styleforge.core.categories import infer_slot
from styleforge.core.slots import base_slot
from styleforge.integrations.embeddings.text_embedder import TextEmbedder
from styleforge.repositories.personal_embedding_repository import PersonalEmbeddingStore
from styleforge.services.catalog_vector_store import CatalogVectorStore, normalize_relevance


_SLOT_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("运动鞋", "sneakers", "footwear"),
    ("乐福鞋", "loafers", "footwear"),
    ("高跟鞋", "heels", "footwear"),
    ("靴", "boots", "footwear"),
    ("鞋", "shoes", "footwear"),
    ("衬衫", "shirt blouse", "top"),
    ("毛衣", "sweater knitwear", "top"),
    ("上衣", "top blouse", "top"),
    ("牛仔裤", "jeans", "bottom"),
    ("裤", "pants trousers", "bottom"),
    ("半身裙", "skirt", "bottom"),
    ("裙子", "skirt", "bottom"),
    ("外套", "coat jacket", "outerwear"),
    ("大衣", "coat", "outerwear"),
    ("风衣", "trench coat", "outerwear"),
    ("连衣裙", "dress", "one_piece"),
    ("包", "handbag bag", "bag"),
    ("帽", "hat", "accessory"),
    ("配饰", "fashion accessory", "accessory"),
    ("sneaker", "sneakers", "footwear"),
    ("shoe", "shoes", "footwear"),
    ("boot", "boots", "footwear"),
    ("shirt", "shirt blouse", "top"),
    ("sweater", "sweater knitwear", "top"),
    ("top", "top blouse", "top"),
    ("jeans", "jeans", "bottom"),
    ("pants", "pants trousers", "bottom"),
    ("trousers", "pants trousers", "bottom"),
    ("skirt", "skirt", "bottom"),
    ("coat", "coat jacket", "outerwear"),
    ("jacket", "coat jacket", "outerwear"),
    ("dress", "dress", "one_piece"),
    ("bag", "handbag bag", "bag"),
)

_ATTRIBUTE_ALIASES: tuple[tuple[str, str], ...] = (
    ("白色", "white"),
    ("黑色", "black"),
    ("灰色", "gray"),
    ("蓝色", "blue"),
    ("红色", "red"),
    ("绿色", "green"),
    ("棕色", "brown"),
    ("米色", "beige"),
    ("正式", "formal tailored"),
    ("休闲", "casual"),
    ("通勤", "business casual workwear"),
    ("复古", "vintage retro"),
    ("简约", "minimalist"),
    ("保暖", "warm insulated"),
    ("防水", "waterproof"),
)

_GENERIC_TERMS = {"a", "an", "the", "fashion", "item", "outfit", "wear"}


@dataclass(frozen=True, slots=True)
class WardrobeRetrievalOutcome:
    item_ids: list[str]
    matched: int
    mode: Literal["keyword", "semantic", "hybrid"]
    semantic_available: bool
    final_scores: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _contains(text: str, alias: str) -> bool:
    if alias.isascii():
        return bool(re.search(rf"\b{re.escape(alias)}s?\b", text))
    return alias in text


def _expanded_query(query: str) -> tuple[str, set[str]]:
    normalized = query.strip().lower()
    expansions: list[str] = []
    slots: set[str] = set()
    for alias, expansion, slot in _SLOT_ALIASES:
        if _contains(normalized, alias):
            expansions.append(expansion)
            slots.add(slot)
    for alias, expansion in _ATTRIBUTE_ALIASES:
        if alias in normalized:
            expansions.append(expansion)
    return " ".join(dict.fromkeys([normalized, *expansions])).strip(), slots


def _keyword_scores(query: str, items: Sequence[Any]) -> dict[str, float]:
    terms = {
        term
        for term in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", query.lower())
        if term not in _GENERIC_TERMS
    }
    if not terms:
        return {}
    scores: dict[str, float] = {}
    for item in items:
        features = " ".join(str(value) for value in getattr(item, "features", ()) or ())
        haystack = " ".join(
            str(value or "")
            for value in (
                getattr(item, "name", ""),
                getattr(item, "item_type", ""),
                getattr(item, "main_category", ""),
                getattr(item, "subtype", ""),
                getattr(item, "color", ""),
                getattr(item, "description", ""),
                features,
            )
        ).lower()
        score = float(sum(term in haystack for term in terms))
        if score > 0:
            scores[str(item.item_id)] = score
    return scores


class WardrobeHybridRetriever:
    """Combine FashionCLIP and keyword scores without leaving the allow-list."""

    def __init__(
        self,
        *,
        embedding_dir: Path,
        model_dir: Path,
        text_embedder: Any | None = None,
        embedder_factory: Callable[[], Any] | None = None,
        catalog_store_factory: Callable[[], Any] | None = None,
        personal_store_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.embedding_dir = embedding_dir.resolve()
        self.model_dir = model_dir.resolve()
        self._embedder = text_embedder
        self._embedder_factory = embedder_factory or self._load_default_embedder
        self._catalog_store_factory = catalog_store_factory or (
            lambda: CatalogVectorStore(self.embedding_dir)
        )
        self._personal_store_factory = personal_store_factory or PersonalEmbeddingStore
        self._catalog_store: Any | None = None
        self._load_attempted = text_embedder is not None
        self._catalog_load_attempted = False
        self._load_error = ""
        self._load_lock = Lock()
        self._inference_lock = Lock()

    @property
    def available(self) -> bool:
        return self._embedder is not None

    @property
    def load_error(self) -> str:
        return self._load_error

    def attach_text_embedder(self, embedder: Any | None) -> bool:
        """Reuse an already-loaded encoder without triggering model loading."""
        if embedder is None:
            return False
        with self._load_lock:
            if self._load_attempted:
                return self._embedder is embedder
            self._embedder = embedder
            self._load_attempted = True
            return True

    def runtime_status(self) -> dict[str, Any]:
        """Return safe process-local state without loading a model or index."""
        with self._load_lock:
            return {
                "strategy": "fashionclip_hybrid_with_keyword_fallback",
                "model_load_attempted": self._load_attempted,
                "model_loaded": self._embedder is not None,
                "catalog_index_load_attempted": self._catalog_load_attempted,
                "catalog_index_loaded": self._catalog_store is not None,
                "degraded": bool(
                    (self._load_attempted and self._embedder is None)
                    or (
                        self._catalog_load_attempted
                        and self._catalog_store is None
                    )
                ),
                "last_error": self._load_error,
            }

    def _load_default_embedder(self) -> Any:
        try:
            return TextEmbedder(self.model_dir, device="cuda", precision="float16")
        except Exception:
            return TextEmbedder(self.model_dir, device="cpu", precision="float32")

    def _ensure_runtime(self) -> tuple[Any | None, Any | None]:
        with self._load_lock:
            if not self._load_attempted:
                self._load_attempted = True
                try:
                    self._embedder = self._embedder_factory()
                except Exception as error:
                    self._load_error = type(error).__name__
                    self._embedder = None
            if self._embedder is not None and not self._catalog_load_attempted:
                self._catalog_load_attempted = True
                try:
                    self._catalog_store = self._catalog_store_factory()
                except Exception as error:
                    # Personal vectors can still provide semantic retrieval.
                    self._load_error = type(error).__name__
            return self._embedder, self._catalog_store

    @staticmethod
    def _encode(embedder: Any, query: str) -> Any:
        if hasattr(embedder, "embed_texts"):
            return embedder.embed_texts([query])[0]
        return embedder.encode_texts([query])[0]

    def search(
        self,
        query: str,
        wardrobe_items: Sequence[Any],
        *,
        connection: Any | None,
        limit: int,
    ) -> WardrobeRetrievalOutcome:
        normalized = (query or "").strip()[:200]
        cap = max(1, limit)
        if not normalized or not wardrobe_items:
            return WardrobeRetrievalOutcome(
                [],
                0,
                "keyword",
                self.available,
                diagnostics={
                    "pool_count": len(wardrobe_items),
                    "keyword_scored": 0,
                    "semantic_scored": 0,
                    "requested_slots": [],
                    "errors": [],
                    "empty_reason": (
                        "empty_query" if not normalized else "empty_wardrobe"
                    ),
                },
            )

        expanded, requested_slots = _expanded_query(normalized)
        pool = list(wardrobe_items)
        if requested_slots:
            pool = [
                item
                for item in pool
                if base_slot(infer_slot(str(item.item_type))) in requested_slots
            ]
        if not pool:
            return WardrobeRetrievalOutcome(
                item_ids=[],
                matched=0,
                mode="keyword",
                semantic_available=self.available,
                diagnostics={
                    "pool_count": 0,
                    "keyword_scored": 0,
                    "semantic_scored": 0,
                    "requested_slots": sorted(requested_slots),
                    "errors": [],
                },
            )
        allowed_ids = [str(item.item_id) for item in pool]
        allowed = set(allowed_ids)
        keyword_raw = _keyword_scores(expanded, pool)
        keyword_high = max(keyword_raw.values(), default=0.0)
        keyword_norm = {
            item_id: score / keyword_high
            for item_id, score in keyword_raw.items()
            if keyword_high > 0
        }

        semantic_raw: dict[str, float] = {}
        errors: list[str] = []
        embedder, catalog_store = self._ensure_runtime()
        if embedder is not None:
            try:
                with self._inference_lock:
                    vector = self._encode(embedder, f"a fashion item {expanded}")
            except Exception as error:
                errors.append(type(error).__name__)
                vector = None
            if vector is not None and catalog_store is not None:
                try:
                    semantic_raw.update(catalog_store.score_items(vector, allowed_ids))
                except Exception as error:
                    errors.append(type(error).__name__)
            if vector is not None and connection is not None:
                try:
                    personal_store = self._personal_store_factory(connection)
                    for item_id, score in personal_store.score_items(
                        vector, allowed_ids
                    ).items():
                        if item_id in allowed:
                            semantic_raw[item_id] = max(
                                semantic_raw.get(item_id, score), score
                            )
                except Exception as error:
                    errors.append(type(error).__name__)
        semantic_raw = {
            item_id: score
            for item_id, score in semantic_raw.items()
            if item_id in allowed
        }
        semantic_norm = {
            item_id: score / 100.0
            for item_id, score in normalize_relevance(semantic_raw).items()
        }

        combined: dict[str, float] = {}
        if semantic_norm:
            for item_id in semantic_norm.keys() | keyword_norm.keys():
                combined[item_id] = (
                    0.85 * semantic_norm.get(item_id, 0.0)
                    + 0.15 * keyword_norm.get(item_id, 0.0)
                )
            mode = "hybrid" if keyword_norm else "semantic"
        else:
            combined = dict(keyword_norm)
            mode = "keyword"

        ranked = sorted(combined, key=lambda item_id: (-combined[item_id], item_id))
        diagnostics = {
            "pool_count": len(pool),
            "keyword_scored": len(keyword_norm),
            "semantic_scored": len(semantic_norm),
            "requested_slots": sorted(requested_slots),
            "errors": sorted(set(filter(None, [self._load_error, *errors]))),
        }
        return WardrobeRetrievalOutcome(
            item_ids=ranked[:cap],
            matched=len(combined),
            mode=mode,
            semantic_available=bool(semantic_norm),
            final_scores=combined,
            diagnostics=diagnostics,
        )
