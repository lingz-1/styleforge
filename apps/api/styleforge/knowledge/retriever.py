"""Retrieval over curated Markdown knowledge assets.

Keyword matching is dependency-free and always available. When a Chroma vector
store and a text embedder are injected, ``search`` also runs a vector pass and
merges the two result sets: a vector hit boosts a keyword hit and is labelled
``chroma_rag``. Any vector failure degrades to keyword-only retrieval.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from styleforge.knowledge.chunking import chunk_markdown
from styleforge.models.context import KnowledgeEvidence

GENERIC_TOKENS = {
    "advice",
    "item",
    "outfit",
    "style",
    "wear",
    "单品",
    "如何",
    "怎么",
    "搭配",
    "穿搭",
    "风格",
}

VECTOR_SOURCE = "chroma_rag"
KEYWORD_SOURCE = "local_knowledge"


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for segment in re.findall(r"[一-鿿]+", normalized):
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        tokens.add(segment)
    return {token for token in tokens if token}


class KnowledgeRetriever:
    def __init__(
        self,
        knowledge_root: Path,
        *,
        chroma_store: Any | None = None,
        text_embedder: Any | None = None,
    ) -> None:
        self.knowledge_root = knowledge_root.resolve()
        index_path = self.knowledge_root / "index.json"
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("knowledge/index.json must contain a list")
        self.entries: list[dict[str, Any]] = entries
        self.chroma_store = chroma_store
        self.text_embedder = text_embedder

    def search(
        self,
        query: str,
        *,
        kind: Literal["style", "item"],
        limit: int = 4,
    ) -> tuple[list[KnowledgeEvidence], list[dict[str, Any]]]:
        if limit < 1:
            raise ValueError("knowledge retrieval limit must be positive")
        normalized = query.strip().lower()
        query_tokens = _tokens(normalized)
        ranked_entries = self._rank_entries(normalized, query_tokens, kind)
        selected_entries = [entry for _, entry in ranked_entries[:2]]

        # Vector retrieval always runs so a semantic query that misses on
        # keywords can still surface evidence; it merges with keyword hits.
        keyword_evidence = self._keyword_evidence(ranked_entries, query_tokens)
        vector_evidence = self._vector_evidence(query, kind)
        merged = self._merge_evidence(keyword_evidence, vector_evidence, limit)
        if not merged:
            return [], selected_entries
        return merged, selected_entries

    def _rank_entries(
        self,
        normalized: str,
        query_tokens: set[str],
        kind: str,
    ) -> list[tuple[float, dict[str, Any]]]:
        ranked_entries: list[tuple[float, dict[str, Any]]] = []
        for entry in self.entries:
            if entry.get("kind") != kind:
                continue
            aliases = [str(value).lower() for value in entry.get("aliases", [])]
            exact_score = max((100.0 for alias in aliases if alias in normalized), default=0.0)
            entry_tokens = _tokens(" ".join([entry.get("title", ""), *aliases]))
            overlap_tokens = (query_tokens & entry_tokens) - GENERIC_TOKENS
            overlap = len(overlap_tokens)
            score = exact_score + 4.0 * overlap
            if exact_score == 0 and overlap < 2:
                continue
            if score > 0:
                ranked_entries.append((score, entry))
        ranked_entries.sort(key=lambda value: (-value[0], value[1]["id"]))
        return ranked_entries

    def _keyword_evidence(
        self,
        ranked_entries: list[tuple[float, dict[str, Any]]],
        query_tokens: set[str],
    ) -> list[KnowledgeEvidence]:
        evidence: list[KnowledgeEvidence] = []
        for entry_score, entry in ranked_entries[:2]:
            path = (self.knowledge_root / entry["path"]).resolve()
            if self.knowledge_root not in path.parents or not path.is_file():
                continue
            for section, content in chunk_markdown(path.read_text(encoding="utf-8")):
                chunk_score = entry_score + len(query_tokens & _tokens(f"{section} {content}"))
                evidence.append(
                    KnowledgeEvidence(
                        source=KEYWORD_SOURCE,
                        source_id=entry["id"],
                        section=section,
                        content=content,
                        score=round(chunk_score, 3),
                    )
                )
        evidence.sort(key=lambda item: (-item.score, item.source_id, item.section))
        return evidence

    def _vector_evidence(self, query: str, kind: str) -> list[KnowledgeEvidence]:
        """Chroma cosine top-k mapped back to section evidence (best effort)."""
        if self.chroma_store is None or self.text_embedder is None:
            return []
        try:
            embedding = self.text_embedder.embed_texts([query])[0]
            matches = self.chroma_store.query(embedding, kind=kind, top_k=8)
        except BaseException:
            # Vector retrieval is additive; a failure must not break a task.
            return []
        evidence: list[KnowledgeEvidence] = []
        for match in matches:
            if not match.get("content"):
                continue
            evidence.append(
                KnowledgeEvidence(
                    source=VECTOR_SOURCE,
                    source_id=str(match.get("source_id", "")),
                    section=str(match.get("section", "")),
                    content=str(match["content"]),
                    score=round(float(match.get("score", 0.0)), 3),
                )
            )
        return evidence

    @staticmethod
    def _merge_evidence(
        keyword_evidence: list[KnowledgeEvidence],
        vector_evidence: list[KnowledgeEvidence],
        limit: int,
    ) -> list[KnowledgeEvidence]:
        """Combine keyword and vector hits keyed by ``(source_id, section)``."""
        merged: dict[tuple[str, str], KnowledgeEvidence] = {}
        for item in [*keyword_evidence, *vector_evidence]:
            key = (item.source_id, item.section)
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
            else:
                # A vector hit strengthens a keyword hit and marks it as RAG.
                existing.score += item.score
                existing.source = VECTOR_SOURCE
        ranked = sorted(
            merged.values(), key=lambda item: (-item.score, item.source_id, item.section)
        )
        return ranked[:limit]
