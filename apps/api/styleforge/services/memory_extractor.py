"""LLM-based extraction of long-term preference memories.

Extraction is best-effort: the LLM distills ``{category, content, meta}``
extracts from a request; when no LLM is configured or the call fails, the
extractor returns an empty list so memory distillation never fails a task run.
Entries that fail validation individually are dropped (tolerant parsing).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from styleforge.llm.memory_prompts import (
    MEMORY_PROMPT_VERSION,
    build_memory_extraction_prompt,
)
from styleforge.llm.memory_schema import MemoryExtract, MemoryExtraction
from styleforge.repositories.memory_repository import normalize_content


def extract_memories(
    llm: Any,
    request: str,
) -> list[dict[str, Any]]:
    """Return ``{"category", "content", "meta"}`` preference extracts, or ``[]``.

    ``llm`` must expose ``chat_json(system, user, json_schema, temperature)``.
    A missing client, a failed call, or a fully-invalid response yields ``[]``.
    """
    if llm is None or not request.strip():
        return []
    try:
        system, user = build_memory_extraction_prompt(request)
        payload, _diagnostics = llm.chat_json(
            system=system,
            user=user,
            json_schema=MemoryExtraction.model_json_schema(),
        )
        raw_items = MemoryExtraction.model_validate(payload).memories
    except Exception:
        # Provider failure, invalid JSON, or schema failure: skip distillation.
        return []

    extracts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items:
        try:
            item = MemoryExtract.model_validate(raw)
        except ValidationError:
            continue
        content = normalize_content(item.content)
        if not content:
            continue
        key = (item.category, content)
        if key in seen:
            continue
        seen.add(key)
        extracts.append(
            {
                "category": item.category,
                "content": content,
                "meta": dict(item.meta or {}),
            }
        )
    return extracts


__all__ = [
    "MEMORY_PROMPT_VERSION",
    "extract_memories",
]
