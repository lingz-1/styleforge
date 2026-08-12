"""Chroma RAG: chunking, ingestion, vector merge, degradation.

Keyword-only tests against the real ``knowledge/`` tree live in
``test_knowledge_retriever.py``; here a fake embedder and a fake Chroma store
drive the merge logic without loading the model or hitting ChromaDB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from styleforge.knowledge.chunking import chunk_markdown, split_long_text
from styleforge.knowledge.ingestion import load_knowledge_documents
from styleforge.knowledge.retriever import KEYWORD_SOURCE, VECTOR_SOURCE, KnowledgeRetriever


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def knowledge_root(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    (root / "styles").mkdir(parents=True)
    (root / "items").mkdir()
    (root / "index.json").write_text(
        json.dumps(
            [
                {
                    "id": "style-alpha",
                    "kind": "style",
                    "path": "styles/alpha.md",
                    "title": "Alpha Style",
                    "aliases": ["alpha style"],
                },
                {
                    "id": "item-gamma",
                    "kind": "item",
                    "path": "items/gamma.md",
                    "title": "Gamma Item",
                    "aliases": ["gamma item"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "styles" / "alpha.md").write_text(
        "# Alpha Style\n## 风格识别\nAlpha style is bold and minimal.\n## 搭配原则\nWear it with flat shoes.\n",
        encoding="utf-8",
    )
    (root / "items" / "gamma.md").write_text(
        "# Gamma Item\n## 选购\nPick the lightest color.\n## 保养\nAir dry only.\n",
        encoding="utf-8",
    )
    return root


class FakeEmbedder:
    dimension = 8

    def embed_texts(self, texts):
        import numpy as np

        return np.array(
            [[float(index + 1) for index in range(self.dimension)] for _ in texts],
            dtype=np.float32,
        )


class FakeChroma:
    def __init__(self, matches=None) -> None:
        self.records: list[dict] = []
        self.matches: list[dict] = matches or []

    def upsert(self, records) -> None:
        self.records.extend(records)

    def query(self, embedding, *, kind, top_k):  # noqa: ARG002
        return self.matches[:top_k]

    def count(self) -> int:
        return len(self.records)


# --- chunking -----------------------------------------------------------------


def test_chunk_markdown_splits_sections() -> None:
    chunks = chunk_markdown("# Title\n## A\nline one\n- item two\n## B\nlast\n")
    assert chunks == [("A", "line one item two"), ("B", "last")]


def test_split_long_text_chunks_over_max() -> None:
    text = "字" * 600
    parts = split_long_text(text)
    assert len(parts) > 1
    assert all(len(part) <= split_long_text.__globals__["MAX_SUBCHUNK_CHARS"] for part in parts)
    # Overlap keeps continuity between neighbouring parts.
    assert any(part != "字" * 220 for part in parts)


def test_split_long_text_short_is_unchanged() -> None:
    assert split_long_text("短文本") == ["短文本"]
    assert split_long_text("") == []


def test_load_knowledge_documents(knowledge_root: Path) -> None:
    docs = load_knowledge_documents(knowledge_root)
    assert {doc.id for doc in docs} == {"style-alpha", "item-gamma"}
    assert docs[0].sections[0][0] == "风格识别"


# --- vector merge -------------------------------------------------------------


def _vector_match(source_id: str, section: str, score: float) -> dict:
    return {"source_id": source_id, "section": section, "content": "…", "score": score}


def test_vector_boost_upgrades_keyword_evidence_source(knowledge_root: Path) -> None:
    retriever = KnowledgeRetriever(
        knowledge_root,
        chroma_store=FakeChroma([_vector_match("style-alpha", "搭配原则", 0.9)]),
        text_embedder=FakeEmbedder(),
    )
    evidence, entries = retriever.search("alpha style", kind="style", limit=6)
    assert entries[0]["id"] == "style-alpha"
    upgraded = [item for item in evidence if item.source == VECTOR_SOURCE]
    assert upgraded
    # The vector hit is merged into the keyword hit for the same section.
    assert any(item.section == "搭配原则" and item.source == VECTOR_SOURCE for item in evidence)


def test_vector_fills_gap_when_keyword_misses(knowledge_root: Path) -> None:
    retriever = KnowledgeRetriever(
        knowledge_root,
        chroma_store=FakeChroma([_vector_match("item-gamma", "选购", 0.9)]),
        text_embedder=FakeEmbedder(),
    )
    evidence, entries = retriever.search("totally unknown phrasing", kind="item", limit=4)
    assert entries == []
    assert evidence
    assert all(item.source == VECTOR_SOURCE for item in evidence)
    assert evidence[0].source_id == "item-gamma"


def test_keyword_only_without_vector(knowledge_root: Path) -> None:
    retriever = KnowledgeRetriever(knowledge_root)
    evidence, entries = retriever.search("alpha style", kind="style")
    assert entries[0]["id"] == "style-alpha"
    assert evidence
    assert all(item.source == KEYWORD_SOURCE for item in evidence)


def test_vector_failure_degrades_to_keyword(knowledge_root: Path) -> None:
    class BrokenEmbedder(FakeEmbedder):
        def embed_texts(self, texts):
            raise RuntimeError("model missing")

    retriever = KnowledgeRetriever(
        knowledge_root,
        chroma_store=FakeChroma([_vector_match("style-alpha", "搭配原则", 0.9)]),
        text_embedder=BrokenEmbedder(),
    )
    evidence, entries = retriever.search("alpha style", kind="style")
    assert entries[0]["id"] == "style-alpha"
    assert evidence
    assert all(item.source == KEYWORD_SOURCE for item in evidence)


def test_chroma_store_upsert_and_query_round_trip(tmp_path: Path) -> None:
    import numpy as np

    from styleforge.integrations.vectorstores.chroma_store import ChromaStore

    store = ChromaStore(tmp_path / "chroma")
    store.upsert(
        [
            {
                "id": "a:0:0",
                "kind": "style",
                "source_id": "style-alpha",
                "section": "风格识别",
                "content": "alpha content",
                "embedding": [1.0, 0.0, 0.0],
            },
            {
                "id": "b:0:0",
                "kind": "item",
                "source_id": "item-gamma",
                "section": "选购",
                "content": "gamma content",
                "embedding": [0.0, 1.0, 0.0],
            },
        ]
    )
    assert store.count() == 2
    matches = store.query(np.array([1.0, 0.0, 0.0], dtype=np.float32), kind="style", top_k=1)
    assert len(matches) == 1
    assert matches[0]["source_id"] == "style-alpha"
    assert matches[0]["section"] == "风格识别"
    assert matches[0]["content"] == "alpha content"
    assert 0.0 < matches[0]["score"] <= 1.0
