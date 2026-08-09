"""Stylist agent: rank legal candidates and enforce result-set diversity."""

from __future__ import annotations

from collections.abc import Sequence

from styleforge.core.schemas import OutfitCandidate, TaskSpec
from styleforge.tools.candidate_generation import select_diverse_candidates


class StylistAgent:
    """Select only from deterministic candidates; never invent item IDs."""

    backend = "deterministic"

    def select(
        self,
        candidates: Sequence[OutfitCandidate],
        task: TaskSpec,
    ) -> list[OutfitCandidate]:
        legal = [candidate for candidate in candidates if candidate.hard_valid]
        # Three-item outfits sharing one item have Jaccard similarity 0.2.
        # A 0.19 threshold therefore prefers completely disjoint results.
        # The selector still falls back to the best deferred candidates when
        # the wardrobe cannot provide enough disjoint outfits.
        return select_diverse_candidates(
            legal,
            task.max_results,
            max_jaccard_similarity=0.19,
        )
