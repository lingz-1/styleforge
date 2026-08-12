"""Deterministic task routing for StyleForge v3.3.

The router is a system capability, not a fourth Agent.  It selects a task
subgraph before any of the three reasoning Agents run.  The rule-based
baseline is intentionally auditable and becomes the P2.5 routing baseline.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    OUTFIT_RECOMMEND = "outfit_recommend"
    OUTFIT_MODIFY = "outfit_modify"
    STYLE_ADVICE = "style_advice"
    ITEM_ADVICE = "item_advice"
    WARDROBE_COMPATIBILITY = "wardrobe_compatibility"
    WARDROBE_GAP = "wardrobe_gap"


TASK_SUBGRAPHS: dict[TaskType, str] = {
    TaskType.OUTFIT_RECOMMEND: "outfit_recommend_subgraph",
    TaskType.OUTFIT_MODIFY: "outfit_modify_subgraph",
    TaskType.STYLE_ADVICE: "style_advice_subgraph",
    TaskType.ITEM_ADVICE: "item_advice_subgraph",
    TaskType.WARDROBE_COMPATIBILITY: "wardrobe_compatibility_subgraph",
    TaskType.WARDROBE_GAP: "wardrobe_gap_subgraph",
}


TASK_CAPABILITIES: dict[TaskType, tuple[str, ...]] = {
    TaskType.OUTFIT_RECOMMEND: (
        "semantic_retrieval",
        "wardrobe_retrieval",
        "outfit_composition",
        "outfit_validation",
        "outfit_critique",
    ),
    TaskType.OUTFIT_MODIFY: (
        "modification_parser",
        "wardrobe_retrieval",
        "outfit_composition",
        "locked_item_validation",
        "outfit_critique",
    ),
    TaskType.STYLE_ADVICE: (
        "semantic_retrieval",
        "style_knowledge",
        "optional_wardrobe_retrieval",
    ),
    TaskType.ITEM_ADVICE: (
        "semantic_retrieval",
        "item_knowledge",
        "wardrobe_retrieval",
        "outfit_composition",
    ),
    TaskType.WARDROBE_COMPATIBILITY: (
        "item_attribute_extraction",
        "wardrobe_retrieval",
        "outfit_composition",
        "outfit_critique",
        "compatibility_report",
    ),
    TaskType.WARDROBE_GAP: (
        "wardrobe_analysis",
        "gap_detection",
        "gap_recommendation",
    ),
}


@dataclass(frozen=True, slots=True)
class TaskRoute:
    task_type: TaskType
    subgraph: str
    confidence: float
    reason: str
    required_capabilities: tuple[str, ...]
    extracted: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_type"] = self.task_type.value
        payload["required_capabilities"] = list(self.required_capabilities)
        return payload


_COMPATIBILITY_PATTERNS = (
    re.compile(r"(?:适合|值得|建议)(?:买|入手)|要不要买|该不该买"),
    re.compile(r"(?:衣柜|衣橱).{0,12}(?:兼容|搭吗|搭不搭|好搭)"),
    re.compile(r"(?:这件|这双|新品|新衣).{0,16}(?:兼容|搭配率|值得买吗|适合买吗)"),
    re.compile(
        r"compatib(?:le|ility)|worth buying|should i buy|"
        r"(?:work|fit).{0,12}(?:my )?(?:wardrobe|closet)",
        re.IGNORECASE,
    ),
)

_GAP_PATTERNS = (
    re.compile(r"(?:衣柜|衣橱).{0,12}(?:还?缺|缺少|缺口|补充|补齐)"),
    re.compile(r"(?:还缺什么|缺少什么|该补(?:点)?什么|应该补(?:点)?什么)"),
    re.compile(r"wardrobe\s+gap|(?:wardrobe|closet).{0,20}missing", re.IGNORECASE),
    re.compile(
        r"what(?:'s| is).{0,20}missing.{0,20}(?:wardrobe|closet)|"
        r"what.{0,20}(?:wardrobe|closet).{0,20}need",
        re.IGNORECASE,
    ),
)

_MODIFY_PATTERNS = (
    re.compile(
        r"(?:只|帮我)?换(?:一|个|双|件|条|掉|成|到|为|鞋|靴|外套|上衣|下装|裤子|裙子|包|配饰)|"
        r"替换(?:掉|成)?|改成"
    ),
    re.compile(
        r"(?:鞋|靴|外套|上衣|下装|裤子|裙子|包|配饰|衬衫|毛衣|卫衣|大衣|风衣|夹克|西装|牛仔裤)"
        r".{0,8}(?:不好看|不合适|不要了)"
    ),
    re.compile(r"(?:保留|锁定).{0,12}(?:其他|其余|剩下)"),
    re.compile(r"swap|replace|change\s+(?:the\s+)?(?:shoes|top|bottom|coat|bag)", re.IGNORECASE),
)

_STYLE_ADVICE_PATTERNS = (
    re.compile(
        r"(?:风格|美学|american\s+vintage|gorpcore|cottagecore|business\s+casual|"
        r"美拉德|法式|复古风|极简风).{0,18}(?:怎么穿|如何穿|怎么搭|指南|特点|是什么)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:怎么穿|如何穿|怎么搭).{0,12}(?:风格|美学)"),
    re.compile(
        r"(?:how (?:do i|should i|to) (?:wear|style)|what is).{0,24}"
        r"(?:american vintage|gorpcore|cottagecore|business casual)(?: style)?",
        re.IGNORECASE,
    ),
)

_ITEM_TERMS = (
    "鞋",
    "靴",
    "大衣",
    "风衣",
    "夹克",
    "西装",
    "衬衫",
    "毛衣",
    "卫衣",
    "马甲",
    "针织背心",
    "西装背心",
    "裙",
    "裤",
    "包",
    "mary jane",
    "cowboy boots",
    "trench coat",
    "leather jacket",
    "pleated skirt",
    "cargo pants",
    "vest",
    "waistcoat",
)
_ITEM_ADVICE_PATTERN = re.compile(r"怎么搭|如何搭配|怎么穿|如何穿|搭配建议|how\s+to\s+(?:style|wear)", re.IGNORECASE)

_SLOT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("footwear", ("鞋", "靴", "sneaker", "loafer", "heels", "boots")),
    ("outerwear", ("外套", "大衣", "风衣", "夹克", "coat", "jacket")),
    ("top", ("上衣", "衬衫", "毛衣", "卫衣", "top", "shirt", "sweater")),
    ("bottom", ("下装", "裤", "裙", "bottom", "pants", "trousers", "skirt")),
    ("bag", ("包", "bag")),
    ("accessory", ("配饰", "项链", "耳环", "帽", "accessory")),
)


class TaskRouter:
    """Classify a request into exactly one v3.3 task subgraph."""

    def route(
        self,
        request: str,
        *,
        current_outfit_id: str = "",
        has_candidate_item: bool = False,
        requested_task_type: TaskType | str | None = None,
    ) -> TaskRoute:
        normalized = self._normalize(request)
        if not normalized:
            raise ValueError("request cannot be empty")

        if requested_task_type is not None:
            task_type = TaskType(requested_task_type)
            return self._build_route(
                task_type,
                confidence=1.0,
                reason="explicit_task_type",
                request=normalized,
            )

        if current_outfit_id:
            return self._build_route(
                TaskType.OUTFIT_MODIFY,
                confidence=1.0,
                reason="current_outfit_context",
                request=normalized,
            )
        if has_candidate_item:
            return self._build_route(
                TaskType.WARDROBE_COMPATIBILITY,
                confidence=1.0,
                reason="candidate_item_context",
                request=normalized,
            )

        rules: tuple[tuple[TaskType, tuple[re.Pattern[str], ...]], ...] = (
            (TaskType.WARDROBE_COMPATIBILITY, _COMPATIBILITY_PATTERNS),
            (TaskType.WARDROBE_GAP, _GAP_PATTERNS),
            (TaskType.OUTFIT_MODIFY, _MODIFY_PATTERNS),
            (TaskType.STYLE_ADVICE, _STYLE_ADVICE_PATTERNS),
        )
        for task_type, patterns in rules:
            for pattern_index, pattern in enumerate(patterns, start=1):
                if not pattern.search(normalized):
                    continue
                return self._build_route(
                    task_type,
                    confidence=0.95,
                    reason=f"matched_{task_type.value}_rule_{pattern_index}",
                    request=normalized,
                )

        if _ITEM_ADVICE_PATTERN.search(normalized) and any(
            term in normalized for term in _ITEM_TERMS
        ):
            return self._build_route(
                TaskType.ITEM_ADVICE,
                confidence=0.92,
                reason="matched_item_advice_rule",
                request=normalized,
            )

        return self._build_route(
            TaskType.OUTFIT_RECOMMEND,
            confidence=0.75,
            reason="default_recommendation_route",
            request=normalized,
        )

    @staticmethod
    def _normalize(request: str) -> str:
        return re.sub(r"\s+", " ", request.strip().lower())

    def _build_route(
        self,
        task_type: TaskType,
        *,
        confidence: float,
        reason: str,
        request: str,
    ) -> TaskRoute:
        extracted: dict[str, Any] = {}
        if task_type is TaskType.OUTFIT_MODIFY:
            target_slot = self._extract_target_slot(request)
            if target_slot:
                extracted["target_slot"] = target_slot
        return TaskRoute(
            task_type=task_type,
            subgraph=TASK_SUBGRAPHS[task_type],
            confidence=confidence,
            reason=reason,
            required_capabilities=TASK_CAPABILITIES[task_type],
            extracted=extracted,
        )

    @staticmethod
    def _extract_target_slot(request: str) -> str:
        for slot, terms in _SLOT_TERMS:
            if any(term in request for term in terms):
                return slot
        return ""
