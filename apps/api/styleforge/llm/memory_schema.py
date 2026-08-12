"""Structured schema for LLM-based long-term memory extraction.

Extraction is best-effort: when the LLM is unavailable or returns content that
fails every entry, the extractor returns an empty list so memory distillation
never affects the task run.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MEMORY_CATEGORIES = (
    "category",
    "color",
    "style",
    "formality",
    "occasion",
    "habit",
    "general",
)

MemoryCategory = Literal[
    "category", "color", "style", "formality", "occasion", "habit", "general"
]


class MemoryExtract(BaseModel):
    """One distilled preference: a category, a short natural content, and metadata."""

    category: MemoryCategory
    content: str = Field(min_length=1, max_length=64)
    meta: dict[str, Any] = Field(default_factory=dict)


class MemoryExtraction(BaseModel):
    """The LLM returns a list of extracts; each entry is validated individually."""

    memories: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
