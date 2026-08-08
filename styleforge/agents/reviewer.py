"""Reviewer agent: verify ownership and hard constraints before acceptance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from styleforge.core.schemas import OutfitCandidate, TaskSpec


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    accepted: bool
    notes: tuple[str, ...]


class ReviewerAgent:
    """Final critic that cannot override hard constraints or scoring facts."""

    backend = "deterministic"

    def review(
        self,
        selected: Sequence[OutfitCandidate],
        task: TaskSpec,
        wardrobe_item_ids: set[str],
    ) -> ReviewDecision:
        if not selected:
            return ReviewDecision(False, ("没有满足全部硬约束的候选搭配。",))
        notes: list[str] = []
        for candidate in selected:
            if not candidate.hard_valid:
                return ReviewDecision(False, (f"候选 {candidate.outfit_id} 未通过硬约束。",))
            if not set(candidate.item_ids) <= wardrobe_item_ids:
                return ReviewDecision(False, (f"候选 {candidate.outfit_id} 含非用户衣柜单品。",))
            missing_slots = set(task.required_slots) - set(candidate.slot_items)
            if missing_slots:
                return ReviewDecision(
                    False,
                    (f"候选 {candidate.outfit_id} 缺少槽位：{sorted(missing_slots)}。",),
                )
        notes.append(f"已复核 {len(selected)} 套搭配，全部来自当前用户衣柜。")
        notes.append("全部推荐均通过必需槽位、排除颜色和重复单品检查。")
        return ReviewDecision(True, tuple(notes))
