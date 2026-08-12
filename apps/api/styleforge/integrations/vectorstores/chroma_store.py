"""Persistent Chroma vector store for knowledge-chunk embeddings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb


class ChromaStore:
    """Cosine-similarity store over knowledge chunks, backed by ChromaDB.

    Each record carries metadata ``kind`` (``style``/``item``), ``source_id``
    (the knowledge entry id), ``section`` and the chunk ``content`` so search
    results map straight back to ``KnowledgeEvidence``.
    """

    def __init__(self, persist_dir: Path, *, collection_name: str = "knowledge_chunks") -> None:
        self.persist_dir = Path(persist_dir).resolve()
        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, records: list[dict[str, Any]]) -> None:
        """Upsert chunk records; each record carries an ``embedding`` vector."""
        if not records:
            return
        self._collection.upsert(
            ids=[str(record["id"]) for record in records],
            embeddings=[record["embedding"] for record in records],
            metadatas=[
                {
                    "kind": str(record["kind"]),
                    "source_id": str(record["source_id"]),
                    "section": str(record["section"]),
                }
                for record in records
            ],
            documents=[str(record["content"]) for record in records],
        )

    def query(
        self,
        embedding: Any,
        *,
        kind: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Return the top-k matching chunks as ``{source_id, section, content, score}``.

        Scores are cosine similarities in ``[0, 1]`` (converted from distance).
        """
        result = self._collection.query(
            query_embeddings=[embedding],
            where={"kind": kind},
            n_results=top_k,
            include=["metadatas", "distances", "documents"],
        )
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        matches: list[dict[str, Any]] = []
        for metadata, distance, content in zip(metadatas, distances, documents):
            matches.append(
                {
                    "source_id": metadata.get("source_id", ""),
                    "section": metadata.get("section", ""),
                    "content": content or "",
                    "score": 1.0 - float(distance),
                }
            )
        return matches

    def count(self) -> int:
        return self._collection.count()
