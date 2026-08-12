"""LLM-based extraction of structured preference evidence.

Extraction is best-effort: the LLM distills ``MemoryEvidence`` claims from a
request; when no LLM is configured or the call fails, the extractor returns an
empty list so memory distillation never fails a task run. Entries that fail
validation individually are dropped (tolerant parsing).

Each returned dict is ready to persist via
``preference_evidence_repository.add_evidence`` (source is pinned to
``llm_request``). ``extract_language_evidence`` is the same function exposed
under the name used by the evidence pipeline in ``services.memory_evidence``.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from styleforge.llm.memory_prompts import (
    MEMORY_PROMPT_VERSION,
    build_memory_extraction_prompt,
)
from styleforge.llm.memory_schema import MemoryEvidence, MemoryEvidenceList

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Strip, collapse inner whitespace and lower-case a claim term."""
    return _WHITESPACE_RE.sub(" ", text.strip()).lower()


def extract_language_evidence(
    llm: Any,
    request: str,
) -> list[dict[str, Any]]:
    """Return ``MemoryEvidence`` dicts for a request, or ``[]`` on any failure.

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
            json_schema=MemoryEvidenceList.model_json_schema(),
        )
        raw_items = MemoryEvidenceList.model_validate(payload).evidence
    except Exception:
        # Provider failure, invalid JSON, or schema failure: skip distillation.
        return []

    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_items:
        try:
            item = MemoryEvidence.model_validate(raw)
        except ValidationError:
            continue
        attribute = _normalize(item.attribute)
        value = _normalize(item.value)
        if not attribute or not value:
            continue
        key = (item.dimension, attribute, value)
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "dimension": item.dimension,
                "attribute": attribute,
                "value": value,
                "polarity": item.polarity,
                "strength": item.strength,
                "scope": item.scope.model_dump(),
                "source": "llm_request",
            }
        )
    return evidence


# Backwards-compatible alias for the old function name.
extract_memories = extract_language_evidence


__all__ = [
    "MEMORY_PROMPT_VERSION",
    "extract_language_evidence",
    "extract_memories",
]
