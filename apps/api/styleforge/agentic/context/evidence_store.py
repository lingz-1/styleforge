"""EvidenceStore: append-only store of ResearchEvidence products (frozen #6).

H2b scope is intentionally thin: one Research pass runs once and its
``ResearchEvidence`` crosses to the Main Graph through the shared channel —
three candidate runs share the same evidence with no re-search. This store is
the harness-side audit/journal of those products and their sources; H3 replaces
the backing implementation (compaction/eviction) without changing the API.

Like every Runtime Dependency it lives on the harness instance, never in the
serializable Execution State.
"""

from __future__ import annotations

from typing import Any

from styleforge.agentic.agentic_contract import EvidenceSource, ResearchEvidence


class EvidenceStore:
    """Append-only journal of ResearchEvidence products + their sources."""

    def __init__(self) -> None:
        self._evidence: list[ResearchEvidence] = []
        self._sources: dict[str, list[EvidenceSource]] = {}

    def save(self, evidence: ResearchEvidence, run_id: str = "") -> None:
        """Record one evidence product; sources are indexed by run for tracing."""
        self._evidence.append(evidence)
        self._sources.setdefault(run_id, []).extend(evidence.sources)

    def latest(self) -> ResearchEvidence | None:
        return self._evidence[-1] if self._evidence else None

    def sources_for(self, run_id: str = "") -> list[EvidenceSource]:
        return list(self._sources.get(run_id, []))

    def count(self) -> int:
        return len(self._evidence)

    def __len__(self) -> int:
        return len(self._evidence)

    # -- store protocol parity (H3 replaces internals, keeps these methods) ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": [e.model_dump() for e in self._evidence],
            "sources": {run: [s.model_dump() for s in src] for run, src in self._sources.items()},
        }
