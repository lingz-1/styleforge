"""Structured schema for LLM-based preference-evidence extraction.

Extraction is best-effort: when the LLM is unavailable or returns content that
fails every entry, the extractor returns an empty list so memory distillation
never affects the task run.

The LLM only decides *what the request means*: a normalized
``(dimension, attribute, value, polarity, strength, scope)`` claim. Memory
management (lifecycle, aggregation, decay) is deterministic and lives in the
repositories / services layer, per the scheme's principle "LLM understands,
deterministic code manages memory".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# The four preference dimensions fixed by the scheme; attribute/value stay
# free-form text so the vocabulary is not frozen into an ENUM.
MEMORY_DIMENSIONS = ("style", "garment", "appearance", "shopping")

MemoryDimension = Literal["style", "garment", "appearance", "shopping"]

POLARITIES = ("positive", "negative")


class EvidenceScope(BaseModel):
    """Where a preference applies: everywhere (global) or only some contexts.

    ``occasions`` is a list of occasion/context tags (通勤 / 约会 / 面试 ...).
    ``formality`` optionally narrows it further (正式 / 休闲 / 商务).
    """

    type: Literal["global", "contextual"] = "global"
    occasions: list[str] = Field(default_factory=list, max_length=6)
    formality: str = Field(default="", max_length=16)


class MemoryEvidence(BaseModel):
    """One standardized preference claim extracted from a request."""

    dimension: MemoryDimension
    attribute: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=64)
    polarity: Literal["positive", "negative"] = "positive"
    strength: float = Field(default=0.5, ge=0.05, le=1.0)
    scope: EvidenceScope = Field(default_factory=EvidenceScope)


class MemoryEvidenceList(BaseModel):
    """The LLM returns a list of claims; each entry is validated individually."""

    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


__all__ = [
    "MEMORY_DIMENSIONS",
    "MemoryDimension",
    "POLARITIES",
    "EvidenceScope",
    "MemoryEvidence",
    "MemoryEvidenceList",
]
