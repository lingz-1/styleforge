"""Load curated knowledge assets into typed documents for indexing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from styleforge.knowledge.chunking import chunk_markdown


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    kind: str
    title: str
    aliases: list[str] = field(default_factory=list)
    path: str = ""
    sections: list[tuple[str, str]] = field(default_factory=list)


def load_knowledge_documents(knowledge_root: Path) -> list[KnowledgeDocument]:
    """Read ``index.json`` and chunk every referenced Markdown file."""
    knowledge_root = knowledge_root.resolve()
    index_path = knowledge_root / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("knowledge/index.json must contain a list")

    documents: list[KnowledgeDocument] = []
    for entry in entries:
        relative_path = entry.get("path", "")
        if not isinstance(relative_path, str):
            continue
        path = (knowledge_root / relative_path).resolve()
        if knowledge_root not in path.parents or not path.is_file():
            continue
        sections = chunk_markdown(path.read_text(encoding="utf-8"))
        if not sections:
            continue
        documents.append(
            KnowledgeDocument(
                id=str(entry.get("id", "")),
                kind=str(entry.get("kind", "")),
                title=str(entry.get("title", "")),
                aliases=[str(value) for value in entry.get("aliases", [])],
                path=relative_path,
                sections=sections,
            )
        )
    return documents
