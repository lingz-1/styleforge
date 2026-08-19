"""Deterministic Environment for the Agentic loop (Stage 2 minimal loop).

The Environment supplies facts and executes tools; it never decides *what the
user wants*. It owns:
  - building ``EnvironmentFacts`` from a pre-legacy snapshot (the input state
    is frozen before the legacy graph runs, so an A/B comparison is fair),
  - the four loop tools (inspect / search / modify / check), where
    ``modify_outfit`` applies a plan to the Agent's *draft*, validates the
    complete resulting state physically, and only then updates the draft.

The draft never touches real session state — the whole Stage 2 loop is shadow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from styleforge.models.agentic_contract import (
    CheckEnvironmentResult,
    EnvironmentFacts,
    GarmentLayer,
    InteractionContext,
    ItemSnapshot,
    ModifyOp,
    ModifyOutcome,
    ModifyPlan,
    OutfitSnapshot,
    Placement,
    SkillResult,
    WardrobeSearchResult,
    WebSearchResult,
)
from styleforge.agentic.context.wardrobe_index import build_wardrobe_index
from styleforge.tools.weather.schemas import WeatherFacts
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import fetch_items_by_ids

from styleforge.agentic.structure import (
    PlacedItem,
    check_structure,
    effective_layer,
    placement_error,
    structure_for,
)


@dataclass
class Draft:
    """The Agent's working copy of the outfit under edit.

    ``outfit.items`` carries each item's physical structure; ``layers`` records
    the layer each added/replaced item was placed on (the Agent's decision).
    Both live inside the shadow — nothing here is ever committed.
    """

    outfit: OutfitSnapshot
    layers: dict[str, GarmentLayer] = field(default_factory=dict)


def _item_snapshot_from_row(row: Any) -> ItemSnapshot:
    return ItemSnapshot(
        item_id=row["item_id"],
        name=row["name"] or "",
        item_type=row["item_type"] or "",
        color=row["color"] or "",
        structure=structure_for(row["item_type"] or ""),
    )


def _item_snapshot_from_catalog(item: Any) -> ItemSnapshot:
    return ItemSnapshot(
        item_id=item.item_id,
        name=item.name or "",
        item_type=item.item_type or "",
        color=item.color or "",
        structure=structure_for(item.item_type or ""),
    )


def _load_items(connection: Any, item_ids: list[str]) -> list[ItemSnapshot]:
    if not item_ids:
        return []
    rows = fetch_items_by_ids(connection, item_ids)
    by_id = {row["item_id"]: row for row in rows}
    return [_item_snapshot_from_row(by_id[item_id]) for item_id in item_ids if item_id in by_id]


def _wardrobe_summary(items: list[Any], request: str = "") -> dict[str, Any]:
    """Compact wardrobe capability index (see ``wardrobe_index``).

    The old full item dump (~262 KB for 2080 items) blew ContextGuard's 40 K
    budget and the Stylist got a truncated wardrobe. Concrete item ids are only
    ever fetched through ``search_wardrobe``; the prompt carries a count-level
    index instead.
    """
    return build_wardrobe_index(items, request=request)


def resolve_active_outfit(
    connection: Any,
    task_input: TaskExecutionInput,
    context_pack: ContextPack | None,
) -> OutfitSnapshot | None:
    """Resolve the outfit the user is editing from task input + session context.

    ``current_item_ids`` travels with the request (the frontend session keeps
    it); when only an ``outfit_id`` is given, the environment falls back to the
    latest stored candidate outfit with that id.
    """
    outfit_id = task_input.current_outfit_id or (
        (context_pack.outfit_context.current_outfit_id if context_pack else "")
    )
    item_ids = list(task_input.current_item_ids) or list(
        (context_pack.outfit_context.current_item_ids if context_pack else [])
    )
    if not item_ids and outfit_id:
        row = connection.execute(
            """
            SELECT c.item_ids_json
            FROM candidate_outfits AS c
            JOIN styling_runs AS s ON s.run_id = c.run_id
            WHERE c.outfit_id = %s AND s.user_id = %s
            ORDER BY s.created_at DESC
            LIMIT 1
            """,
            (outfit_id, task_input.user_id),
        ).fetchone()
        if row is not None:
            item_ids = json.loads(row["item_ids_json"] or "[]")
    if not outfit_id and not item_ids:
        return None
    return OutfitSnapshot(
        outfit_id=outfit_id or "active_outfit",
        item_ids=item_ids,
        items=_load_items(connection, item_ids),
    )


def recent_visible_outfits(connection: Any, user_id: str, limit: int = 4) -> list[OutfitSnapshot]:
    """The user's most recently generated candidate outfits (conversation view)."""
    rows = connection.execute(
        """
        SELECT c.outfit_id, c.item_ids_json
        FROM candidate_outfits AS c
        JOIN styling_runs AS s ON s.run_id = c.run_id
        WHERE s.user_id = %s AND s.status = 'completed'
        ORDER BY s.created_at DESC, c.rank NULLS LAST
        LIMIT %s
        """,
        (user_id, limit),
    ).fetchall()
    outfits: list[OutfitSnapshot] = []
    for row in rows:
        item_ids = json.loads(row["item_ids_json"] or "[]")
        outfits.append(
            OutfitSnapshot(
                outfit_id=row["outfit_id"],
                item_ids=item_ids,
                items=_load_items(connection, item_ids),
            )
        )
    return outfits


def build_facts(
    connection: Any,
    task_input: TaskExecutionInput,
    context_pack: ContextPack | None,
    wardrobe_items: list[Any],
) -> EnvironmentFacts:
    """Freeze the EnvironmentFacts the Agent may rely on.

    ``context_pack`` must be the *pre-legacy* snapshot (built before the legacy
    graph runs) so the shadow sees the same world the legacy saw.
    """
    interaction = InteractionContext(
        active_outfit_id=task_input.current_outfit_id or None,
        selected_item_id=task_input.selected_item_id or None,
    )
    active_outfit = resolve_active_outfit(connection, task_input, context_pack)
    selected_item = None
    if task_input.selected_item_id:
        row = connection.execute(
            "SELECT item_id, name, item_type, color FROM catalog_items WHERE item_id = %s",
            (task_input.selected_item_id,),
        ).fetchone()
        if row is not None:
            selected_item = _item_snapshot_from_row(row)
    weather = None
    memory_profile: dict[str, Any] = {}
    if context_pack is not None:
        weather = context_pack.environment_context.weather
        preferences = context_pack.user_context.preferences or {}
        raw_memory = preferences.get("memory_profile")
        # The legacy context pack stores preference evidence as a *list* of
        # records (ContextPackBuilder -> list_preferences); EnvironmentFacts
        # expects a dict snapshot. Normalise at the program boundary so a real
        # user with memories does not trip the contract validation.
        if isinstance(raw_memory, dict):
            memory_profile = raw_memory
        elif isinstance(raw_memory, list) and raw_memory:
            memory_profile = {"preferences": raw_memory}
    return EnvironmentFacts(
        interaction=interaction,
        visible_outfits=recent_visible_outfits(connection, task_input.user_id),
        active_outfit=active_outfit,
        selected_item=selected_item,
        wardrobe_summary=_wardrobe_summary(wardrobe_items, request=task_input.request),
        weather=weather,
        memory_profile=memory_profile,
    )


class Environment:
    """Deterministic tools over the shadow draft. Facts only, no decisions."""

    def __init__(
        self,
        connection: Any,
        wardrobe_items: list[Any],
        facts: EnvironmentFacts,
        *,
        search_limit: int = 12,
        web_search_provider: Any | None = None,
        weather_provider: Any | None = None,
        skills_root: Path | None = None,
    ) -> None:
        self.connection = connection
        self.wardrobe_items = wardrobe_items
        self.facts = facts
        self.search_limit = search_limit
        self.web_search_provider = web_search_provider
        self.weather_provider = weather_provider
        self.skills_root = skills_root
        self._wardrobe_by_id = {item.item_id: item for item in wardrobe_items}
        # Last ``get_weather`` fact (if any) — surfaced in the recommend payload
        # so the frontend weather block can render what the Agent actually saw.
        self.last_weather_facts: WeatherFacts | None = None

    # --- helpers ---------------------------------------------------------

    def _snapshot_for(self, item_id: str) -> ItemSnapshot | None:
        item = self._wardrobe_by_id.get(item_id)
        if item is not None:
            return _item_snapshot_from_catalog(item)
        row = self.connection.execute(
            "SELECT item_id, name, item_type, color FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
        if row is not None:
            return _item_snapshot_from_row(row)
        return None

    def _placed(self, draft: Draft) -> list[PlacedItem]:
        return [
            PlacedItem(
                item_id=item.item_id,
                structure=item.structure,
                assigned_layer=draft.layers.get(item.item_id),
            )
            for item in draft.outfit.items
        ]

    # --- tools -----------------------------------------------------------

    def inspect_outfit(self, outfit_id: str) -> OutfitSnapshot | None:
        """Read a snapshot; only facts that are already grounded."""
        candidates = []
        if self.facts.active_outfit is not None and self.facts.active_outfit.outfit_id == outfit_id:
            candidates.append(self.facts.active_outfit)
        candidates.extend(
            outfit for outfit in self.facts.visible_outfits if outfit.outfit_id == outfit_id
        )
        return candidates[0] if candidates else None

    def search_wardrobe(self, query: str, limit: int | None = None) -> WardrobeSearchResult:
        """Keyword search over the user's wardrobe, always capped at top-K.

        An empty result is a fact — the Agent decides what it means. The user
        may never demand "give me everything": the result is hard-capped.
        """
        cap = self.search_limit
        if limit is not None and limit > 0:
            cap = min(limit, cap)
        tokens = [token for token in query.lower().split() if token]
        scored: list[tuple[int, Any]] = []
        for item in self.wardrobe_items:
            hay = " ".join(
                [
                    item.name or "",
                    item.item_type or "",
                    item.main_category or "",
                    item.color or "",
                    item.description or "",
                ]
            ).lower()
            score = sum(1 for token in tokens if token in hay)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].item_id))
        results = [
            _item_snapshot_from_catalog(item)
            for _, item in scored[:cap]
        ]
        return WardrobeSearchResult(
            results=results,
            matched=len(scored),
            query=query,
        )

    def search_web(self, query: str) -> WebSearchResult:
        """Web search for outfit / occasion / activity knowledge beyond the
        wardrobe. Results are knowledge references only — never item ids.

        An unconfigured provider degrades to an ``available=False`` fact; a
        failed call carries an ``error``. Either way the Agent keeps working.
        """
        if self.web_search_provider is None:
            return WebSearchResult(query=query, error="联网搜索未配置", available=False)
        return self.web_search_provider.search((query or "").strip()[:200])

    def load_skill(self, skill_name: str) -> SkillResult:
        """Load procedural task knowledge (e.g. event_outfit_planning).

        A skill is a SKILL.md under ``skills_root/tasks/{name}/``; the content
        teaches *how to investigate and decide* for a class of tasks, never
        concrete outfit choices. Missing / unconfigured degrades to an
        ``available=False`` fact — the Agent decides on its own.
        """
        name = (skill_name or "").strip().replace(".", "/")
        if not name or self.skills_root is None:
            return SkillResult(name=skill_name, error="技能未配置", available=False)
        candidates = [
            self.skills_root / "tasks" / name / "SKILL.md",
            self.skills_root / "tasks" / f"{name}.md",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                except OSError as error:
                    return SkillResult(
                        name=skill_name,
                        error=f"技能读取失败：{type(error).__name__}",
                        available=False,
                    )
                if not content:
                    return SkillResult(name=skill_name, error="技能内容为空", available=False)
                return SkillResult(name=name, content=content, available=True)
        return SkillResult(name=skill_name, error=f"未找到技能 {skill_name}", available=False)

    def get_weather(self, location: str, date_expression: str = "") -> WeatherFacts:
        """Resolve a named location and fetch its near-term forecast facts.

        ``date_expression`` is honoured when it is a plain ISO date (single day);
        otherwise the near-3-day default window is used. An unconfigured or
        unresolvable provider returns an ``unavailable`` fact, never raises.
        """
        provider = self.weather_provider
        query = (location or "").strip()
        if provider is None or not query:
            return WeatherFacts.unavailable(
                requested_location=query or (self.facts.weather or {}).get("requested_location", ""),
                requested_date=date_expression,
                error_code="tool_disabled",
                error_message="天气工具未配置或未提供地点",
            )
        resolved = provider.resolve_location(query)
        if resolved is None:
            return WeatherFacts.unavailable(
                requested_location=query,
                requested_date=date_expression,
                error_code="location_unresolved",
                error_message=f"无法解析地点「{query}」",
            )
        today = date.today()
        start = today
        end = today + timedelta(days=2)
        expr = date_expression.strip()
        try:
            if expr and len(expr) >= 8 and expr.replace("-", "").isdigit():
                start = end = date.fromisoformat(expr[:10])
        except ValueError:
            pass  # fall back to the near-3-day window
        result = provider.forecast_range(resolved, start, end)
        if result.status == "available":
            self.last_weather_facts = result
        return result

    def _resolve_placement(
        self,
        snapshot: ItemSnapshot,
        placement: Placement | None,
    ) -> tuple[Placement | None, str | None]:
        """Fill missing region/layer from the garment's structure facts.

        The region is a structural fact (the garment's allowed region) and the
        layer falls back to the garment's effective (lowest) layer; the Agent's
        explicit values win when present. A *wrong* explicit value is still a
        physical error. Returns (placement, None) when resolved, else
        (None, issue).
        """
        if snapshot.structure is None:
            if placement is None or placement.region is None:
                return None, "未知物理结构且未显式指定 placement"
            # UNKNOWN type with an explicit placement: verify it verbatim.
            error = placement_error(None, placement)
            return (placement, None) if error is None else (None, error)
        region = (
            placement.region
            if placement is not None and placement.region is not None
            else snapshot.structure.allowed_region
        )
        layer = (
            placement.layer
            if placement is not None and placement.layer is not None
            else effective_layer(snapshot.structure)
        )
        resolved = Placement(region=region, layer=layer)
        error = placement_error(snapshot.structure, resolved)
        if error is not None:
            return None, error
        return resolved, None

    def modify_outfit(
        self,
        draft: Draft,
        plan: ModifyPlan,
    ) -> tuple[Draft | None, list[str]]:
        """Apply a plan to the draft; on physical failure the draft is untouched.

        Validation is on the *complete resulting state* (never step-wise), so a
        replace (remove+add) is not falsely killed mid-way. Returns the new
        draft when legal, otherwise ``None`` plus the issues for the Agent to
        replan on.
        """
        if not plan.ops:
            return Draft(outfit=draft.outfit, layers=dict(draft.layers)), []

        item_ids = list(draft.outfit.item_ids)
        items = list(draft.outfit.items)
        layers = dict(draft.layers)
        for op in plan.ops:
            if op.action == "remove":
                if op.item_id not in item_ids:
                    return None, [f"无法移除 {op.item_id}：不在当前搭配中"]
                item_ids = [item_id for item_id in item_ids if item_id != op.item_id]
                items = [item for item in items if item.item_id != op.item_id]
                layers.pop(op.item_id, None)
            elif op.action == "add":
                if op.item_id in item_ids:
                    return None, [f"无法添加 {op.item_id}：已在搭配中"]
                snapshot = self._snapshot_for(op.item_id)
                if snapshot is None:
                    return None, [f"无法添加 {op.item_id}：衣橱中不存在该单品"]
                resolved, issue = self._resolve_placement(snapshot, op.placement)
                if issue is not None:
                    return None, [f"单品 {op.item_id}：{issue}"]
                assert resolved is not None
                layers[op.item_id] = resolved.layer
                item_ids.append(op.item_id)
                items.append(snapshot)
            elif op.action == "replace":
                if op.item_id not in item_ids:
                    return None, [f"无法替换 {op.item_id}：不在当前搭配中"]
                replacement = op.replacement_item_id or ""
                if not replacement:
                    return None, ["replace 需要 replacement_item_id"]
                snapshot = self._snapshot_for(replacement)
                if snapshot is None:
                    return None, [f"无法替换为 {replacement}：衣橱中不存在该单品"]
                resolved, issue = self._resolve_placement(snapshot, op.placement)
                if issue is not None:
                    return None, [f"替换单品 {replacement}：{issue}"]
                assert resolved is not None
                layers[replacement] = resolved.layer
                item_ids = [replacement if item_id == op.item_id else item_id for item_id in item_ids]
                items = [item for item in items if item.item_id != op.item_id]
                items.append(snapshot)
        item_ids = list(dict.fromkeys(item_ids))

        check = check_structure(
            [
                PlacedItem(
                    item_id=item.item_id,
                    structure=item.structure,
                    assigned_layer=layers.get(item.item_id),
                )
                for item in items
            ]
        )
        if not check.valid:
            return None, check.issues
        next_draft = Draft(
            outfit=OutfitSnapshot(
                outfit_id=draft.outfit.outfit_id,
                item_ids=item_ids,
                items=items,
                score=draft.outfit.score,
                reasoning=plan.reasoning or draft.outfit.reasoning,
            ),
            layers=layers,
        )
        return next_draft, []

    def check_environment(self, draft: Draft) -> CheckEnvironmentResult:
        """Pure physical legality of the draft's complete resulting state."""
        check = check_structure(self._placed(draft))
        issues = list(check.issues)
        if check.unknown_item_ids:
            issues.append(
                f"以下单品物理结构未知：{'、'.join(check.unknown_item_ids)}"
            )
        return CheckEnvironmentResult(valid=check.valid and not issues, issues=issues)
