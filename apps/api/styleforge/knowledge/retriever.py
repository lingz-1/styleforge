"""Dependency-free retrieval over curated Markdown knowledge assets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

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


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        tokens.add(segment)
    return {token for token in tokens if token}


def _chunks(markdown: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    section = "概述"
    body: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if body:
                chunks.append((section, " ".join(body)))
            section = line.removeprefix("## ").strip()
            body = []
        elif line:
            body.append(line.lstrip("- "))
    if body:
        chunks.append((section, " ".join(body)))
    return chunks


class KnowledgeRetriever:
    def __init__(self, knowledge_root: Path) -> None:
        self.knowledge_root = knowledge_root.resolve()
        index_path = self.knowledge_root / "index.json"
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("knowledge/index.json must contain a list")
        self.entries: list[dict[str, Any]] = entries

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
        selected_entries = [entry for _, entry in ranked_entries[:2]]
        if not selected_entries:
            return [], []

        evidence: list[KnowledgeEvidence] = []
        for entry_score, entry in ranked_entries[:2]:
            path = (self.knowledge_root / entry["path"]).resolve()
            if self.knowledge_root not in path.parents or not path.is_file():
                continue
            for section, content in _chunks(path.read_text(encoding="utf-8")):
                chunk_score = entry_score + len(query_tokens & _tokens(f"{section} {content}"))
                evidence.append(
                    KnowledgeEvidence(
                        source="local_knowledge",
                        source_id=entry["id"],
                        section=section,
                        content=content,
                        score=round(chunk_score, 3),
                    )
                )
        evidence.sort(key=lambda item: (-item.score, item.source_id, item.section))
        return evidence[:limit], selected_entries
