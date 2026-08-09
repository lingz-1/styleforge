from __future__ import annotations

from pathlib import Path

from styleforge.knowledge.retriever import KnowledgeRetriever


KNOWLEDGE_ROOT = Path("knowledge")


def test_style_knowledge_returns_traceable_markdown_sections() -> None:
    evidence, entries = KnowledgeRetriever(KNOWLEDGE_ROOT).search(
        "American Vintage 风格应该怎么穿？",
        kind="style",
    )

    assert entries[0]["id"] == "style-american-vintage"
    assert evidence
    assert {item.section for item in evidence} >= {"搭配原则", "避免"}
    assert all(item.source == "local_knowledge" for item in evidence)


def test_item_knowledge_does_not_cross_into_style_documents() -> None:
    evidence, entries = KnowledgeRetriever(KNOWLEDGE_ROOT).search(
        "Cowboy Boots 怎么搭？",
        kind="item",
    )

    assert entries[0]["id"] == "item-cowboy-boots"
    assert evidence
    assert all(item.source_id.startswith("item-") for item in evidence)


def test_unknown_style_does_not_match_on_generic_words_only() -> None:
    evidence, entries = KnowledgeRetriever(KNOWLEDGE_ROOT).search(
        "完全未知的量子泡泡风格怎么穿？",
        kind="style",
    )

    assert evidence == []
    assert entries == []
