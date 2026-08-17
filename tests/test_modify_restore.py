"""PR4B regression: locked/replaced are Agent 1 facts, restored by the composer.

Regression for the reported 422 on follow-up "不要运动鞋" edits.  Which current
items are locked vs replaced is a deterministic partition computed by Agent 1's
candidate pool -- the LLM never decides it.  The composer restores both fields
from the facts after validation, so a wrong value can no longer trip the hard
validator (which compares set equality against ``agent1.facts``).

  * a structured modify draft with wrong locked/replaced is corrected in place,
  * the corrected draft passes ``validate_extension_draft``,
  * the completion rule tells the LLM the fields are filled by the system.
"""

from __future__ import annotations

from styleforge.agents.composer import ComposerAgent
from styleforge.core.candidate_service import build_candidate_pool
from styleforge.core.request_spec import (
    Change,
    ChangeAction,
    ConstraintStrength,
    EntityRef,
    Lock,
    Operation,
    RequestSpec,
    interpret_request,
)
from styleforge.core.schemas import CatalogItem, ImageStatus
from styleforge.llm.extension_prompts import completion_rule_for
from styleforge.models.context import ContextPack, RequestContext, UserContext
from styleforge.orchestration.task_router import TaskType
from styleforge.tools.extension_analysis import _structured_modify
from styleforge.tools.extension_validation import validate_extension_draft

from tests.extension_llm import ScriptedExtensionLlm


def _item(
    item_id: str,
    item_type: str,
    *,
    name: str = "",
    color: str = "",
    description: str = "",
    features: tuple[str, ...] = (),
) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        source="test",
        gender="",
        item_type=item_type,
        main_category=item_type,
        name=name,
        color=color,
        description=description,
        features=features,
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
    )


def _route() -> object:
    from styleforge.orchestration.task_router import (
        TASK_CAPABILITIES,
        TASK_SUBGRAPHS,
        TaskRoute,
    )

    return TaskRoute(
        task_type=TaskType.OUTFIT_MODIFY,
        subgraph=TASK_SUBGRAPHS[TaskType.OUTFIT_MODIFY],
        confidence=1.0,
        reason="test",
        required_capabilities=TASK_CAPABILITIES[TaskType.OUTFIT_MODIFY],
    )


def _modify_agent1(
    spec: RequestSpec,
    wardrobe: list[CatalogItem],
    current: list[str],
) -> object:
    pool = build_candidate_pool(spec, wardrobe, current)
    return _structured_modify("outfit-1", current, wardrobe, pool, spec, _route())


def _context(original_request: str) -> ContextPack:
    return ContextPack(
        request_context=RequestContext(
            original_request=original_request,
            task_type="outfit_modify",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(user_id="u"),
    )


def _modify_draft(
    *,
    locked: list[str],
    replaced: list[str],
    alternatives: list[dict[str, object]],
) -> dict[str, object]:
    used = sorted({uid for alt in alternatives for uid in alt["item_ids"]})
    return {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "局部修改方案",
        "result": {
            "status": "completed",
            "current_outfit_id": "outfit-1",
            "target_slot": "",
            "replaced_item_ids": replaced,
            "locked_item_ids": locked,
            "alternatives": alternatives,
            "message": "已按需调整",
        },
        "used_item_ids": used,
        "evidence_source_ids": [],
    }


def _wardrobe_ids(wardrobe: list[CatalogItem]) -> set[str]:
    return {item.item_id for item in wardrobe}


def test_composer_restores_locked_and_replaced_for_structured_modify() -> None:
    """Acceptance scene: nothing is replaced, the whole current outfit is locked.

    The LLM guesses wrong (treats the shoes as replaced, forgets the clip is
    locked); the composer restores Agent 1's facts so the draft passes the hard
    validator -- the 422 path is closed by construction.
    """
    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("purple_clip", "other", name="Purple hair clip", color="purple",
              description="", features=("hair clip",)),
        _item("hat1", "hats", name="Fedora", color="brown"),
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
        _item("necklace", "necklace", name="Gold necklace", color="gold"),
    ]
    current = ["dress", "shoes", "purple_clip"]
    agent1 = _modify_agent1(interpret_request("不要帽子，要粉色系发夹"), wardrobe, current)
    assert agent1.facts["locked_item_ids"] == current
    assert agent1.facts["replaced_item_ids"] == []

    draft = _modify_draft(
        locked=["dress"],
        replaced=["shoes"],
        alternatives=[
            {
                "outfit_id": "mod-1",
                "item_ids": ["dress", "shoes", "purple_clip", "gold_clip"],
                "reasoning": "用金色发夹满足发饰。",
            }
        ],
    )
    output, _info, _diag = ComposerAgent().run_extension(
        user_query="不要帽子，要粉色系发夹",
        context_pack=_context("不要帽子，要粉色系发夹"),
        agent1_output=agent1,
        llm=ScriptedExtensionLlm([draft]),
    )

    assert output.result["locked_item_ids"] == current
    assert output.result["replaced_item_ids"] == []
    checks = validate_extension_draft(
        agent1=agent1, agent2=output, wardrobe_ids=_wardrobe_ids(wardrobe)
    )
    assert checks


def test_composer_restores_nonempty_replaced_from_facts() -> None:
    """REPLACE scene: the current shoes really are the replaced set, the dress is
    locked.  The LLM echoes neither; both are restored from the facts."""
    spec = RequestSpec(
        operation=Operation.MODIFY,
        changes=[
            Change(
                action=ChangeAction.REPLACE,
                target=EntityRef(item_type="footwear"),
                strength=ConstraintStrength.MUST,
                subject=EntityRef(item_type="shoes"),
                preferences=[],
            )
        ],
        locks=[Lock(target=EntityRef(item_type="dress"))],
        excluded_colors=[],
        unresolved_fields=[],
    )
    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("boots", "boots", name="Boots", color="brown"),
    ]
    current = ["dress", "shoes"]
    agent1 = _modify_agent1(spec, wardrobe, current)
    assert agent1.facts["locked_item_ids"] == ["dress"]
    assert agent1.facts["replaced_item_ids"] == ["shoes"]

    draft = _modify_draft(
        locked=[],
        replaced=[],
        alternatives=[
            {
                "outfit_id": "mod-1",
                "item_ids": ["dress", "boots"],
                "reasoning": "换上靴子，锁定连衣裙。",
            }
        ],
    )
    output, _info, _diag = ComposerAgent().run_extension(
        user_query="把鞋换成靴子",
        context_pack=_context("把鞋换成靴子"),
        agent1_output=agent1,
        llm=ScriptedExtensionLlm([draft]),
    )

    assert output.result["locked_item_ids"] == ["dress"]
    assert output.result["replaced_item_ids"] == ["shoes"]
    checks = validate_extension_draft(
        agent1=agent1, agent2=output, wardrobe_ids=_wardrobe_ids(wardrobe)
    )
    assert checks


def test_modify_completion_rule_announces_system_restore() -> None:
    """The structured-modify completion rule tells the LLM the locked/replaced
    fields are filled by the system, so the model stops guessing them."""
    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
    ]
    agent1 = _modify_agent1(interpret_request("不要帽子，要发夹"), wardrobe, ["dress", "shoes"])
    rule = completion_rule_for(agent1)
    assert "自动回填" in rule
    assert "无需填写" in rule
    assert "锁定单品绝不丢弃或替换" in rule
