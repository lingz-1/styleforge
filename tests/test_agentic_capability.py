"""H1a-2/3/4: CapabilityRegistry three-layer split + ToolRuntime hook chain.

Covers the frozen #12 API separation (registered_for_agent stable set /
runtime_available capability filter / check_precondition temporary state),
the 8 local tools' catalog isolation, and the ToolRuntime gate chain
(schema validate → PreToolUse → precondition → handler → PostToolUse/
OnToolError → normalized ToolCallResult) with stateful write-backs.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from styleforge.agentic.environment import Draft, Environment
from styleforge.agentic.hooks.manager import (
    HookManager,
    ON_TOOL_ERROR,
    POST_TOOL_USE,
    PRE_TOOL_USE,
)
from styleforge.agentic.runtime.capability_registry import (
    CapabilityRegistry,
    ToolCapability,
)
from styleforge.agentic.runtime.tool_runtime import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PRECONDITION_FAILED,
    ToolRuntime,
)
from styleforge.agentic.tools.local_tools import (
    AGENT_COORDINATOR,
    AGENT_RESEARCH,
    AGENT_STYLIST,
    CAP_KNOWLEDGE,
    CAP_SKILLS,
    CAP_WEB_SEARCH,
    CAP_WEATHER,
    register_local_tools,
)
from styleforge.models.agentic_contract import EnvironmentFacts, OutfitSnapshot
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item

_ALL_CAPS = frozenset({CAP_WEB_SEARCH, CAP_WEATHER, CAP_KNOWLEDGE, CAP_SKILLS})


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    return registry


# ── Layer 1: registered_for_agent ──────────────────────────────────────────

def test_registered_for_agent_isolation_and_deterministic_order() -> None:
    registry = _registry()

    stylist = [t.name for t in registry.registered_for_agent(AGENT_STYLIST)]
    research = [t.name for t in registry.registered_for_agent(AGENT_RESEARCH)]
    coordinator = [t.name for t in registry.registered_for_agent(AGENT_COORDINATOR)]

    # Stylist sees the wardrobe + knowledge tools, in registration order.
    assert stylist == [
        "inspect_outfit",
        "search_wardrobe",
        "search_web",
        "get_weather",
        "search_knowledge",
        "load_skill",
        "modify_outfit",
    ]
    # Research never sees wardrobe editing; coordinator is a manager-agent.
    assert research == ["search_web", "get_weather", "search_knowledge", "load_skill"]
    assert "modify_outfit" not in research
    assert "inspect_outfit" not in research
    assert coordinator == ["update_plan"]

    # Deterministic ordering across calls — stable prefix fingerprint needs it.
    assert [t.name for t in registry.registered_for_agent(AGENT_STYLIST)] == stylist


def test_registered_tools_carry_full_schema_not_manifest_only() -> None:
    registry = _registry()
    definition = next(
        t for t in registry.registered_for_agent(AGENT_STYLIST) if t.name == "search_wardrobe"
    )
    assert definition.input_schema["type"] == "object"
    assert "query" in definition.input_schema["properties"]


# ── Layer 2: runtime_available ────────────────────────────────────────────

def test_runtime_available_drops_capability_gated_tools() -> None:
    registry = _registry()

    empty = {t.name for t in registry.runtime_available(AGENT_STYLIST, frozenset())}
    assert empty == {"inspect_outfit", "search_wardrobe", "modify_outfit"}

    full = {t.name for t in registry.runtime_available(AGENT_STYLIST, _ALL_CAPS)}
    assert full == {
        "inspect_outfit",
        "search_wardrobe",
        "search_web",
        "get_weather",
        "search_knowledge",
        "load_skill",
        "modify_outfit",
    }


def test_runtime_available_no_tavily_drops_only_search_web() -> None:
    registry = _registry()
    caps = _ALL_CAPS - {CAP_WEB_SEARCH}
    names = {t.name for t in registry.runtime_available(AGENT_STYLIST, caps)}

    assert "search_web" not in names
    assert "get_weather" in names
    assert "load_skill" in names


def test_runtime_available_research_never_gets_wardrobe_tools() -> None:
    registry = _registry()
    names = {t.name for t in registry.runtime_available(AGENT_RESEARCH, _ALL_CAPS)}

    assert names == {"search_web", "get_weather", "search_knowledge", "load_skill"}


def test_workflow_does_not_advertise_unavailable_web_search_provider() -> None:
    workflow = object.__new__(MultiTaskWorkflow)
    workflow.web_search_provider = SimpleNamespace(available=False)
    workflow.weather_provider = None
    workflow.knowledge_retriever = None
    workflow.skills_root = None

    assert CAP_WEB_SEARCH not in workflow._runtime_capabilities()


def test_workflow_advertises_available_web_search_provider() -> None:
    workflow = object.__new__(MultiTaskWorkflow)
    workflow.web_search_provider = SimpleNamespace(available=True)
    workflow.weather_provider = None
    workflow.knowledge_retriever = None
    workflow.skills_root = None

    assert CAP_WEB_SEARCH in workflow._runtime_capabilities()


# ── Layer 3: check_precondition ────────────────────────────────────────────

def test_check_precondition_modify_outfit_requires_working_draft() -> None:
    registry = _registry()

    assert registry.check_precondition("modify_outfit", {}) is not None
    assert registry.check_precondition("modify_outfit", {"working_draft": object()}) is None
    # Stateless tools never carry a precondition.
    assert registry.check_precondition("search_wardrobe", {}) is None
    assert registry.check_precondition("update_plan", {}) is None


# ── ToolRuntime gate chain ─────────────────────────────────────────────────

def _environment() -> Environment:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("bottom-1", "pants", "黑西裤", "black"),
        make_item("shoes-1", "shoes", "黑皮鞋", "black"),
    ]
    return Environment(connection=None, wardrobe_items=items, facts=EnvironmentFacts())


def _setup() -> tuple[ToolRuntime, HookManager, CapabilityRegistry]:
    registry = CapabilityRegistry()
    register_local_tools(registry, _environment())
    hooks = HookManager()
    return ToolRuntime(registry, hooks), hooks, registry


def test_execute_stateless_tool_normalizes_observation() -> None:
    runtime, _, _ = _setup()
    result = runtime.execute("search_wardrobe", {"query": "white"}, state={})

    assert result.status == STATUS_OK
    assert result.state_updates == {}
    assert "top-1" in result.observation


def test_execute_modify_outfit_roundtrips_working_draft() -> None:
    runtime, _, _ = _setup()
    draft = Draft(outfit=OutfitSnapshot(outfit_id="draft", item_ids=[], items=[]), layers={})
    result = runtime.execute(
        "modify_outfit",
        {"plan": {"ops": [{"action": "add", "item_id": "top-1", "placement": {"region": "upper_body"}}]}},
        state={"working_draft": draft},
    )

    assert result.status == STATUS_OK
    assert "top-1" in result.observation
    new_draft = result.state_updates["working_draft"]
    assert new_draft is not draft
    assert new_draft.outfit.item_ids == ["top-1"]


def test_execute_modify_outfit_precondition_failed_when_no_draft() -> None:
    runtime, hooks, _ = _setup()
    result = runtime.execute(
        "modify_outfit",
        {"plan": {"ops": [{"action": "add", "item_id": "top-1", "placement": {"region": "upper_body"}}]}},
        state={},
    )

    assert result.status == STATUS_PRECONDITION_FAILED
    assert "working_draft" in result.observation
    assert result.state_updates == {}
    # The precondition failure still runs the PostToolUse hook with its status.
    post_payload: dict = {}
    hooks.register_hook(POST_TOOL_USE, lambda p: post_payload.update(p))
    runtime.execute("modify_outfit", {"plan": {"ops": []}}, state={})
    assert post_payload["status"] == STATUS_PRECONDITION_FAILED


def test_execute_schema_violation_normalizes_error_and_fires_on_error() -> None:
    runtime, hooks, _ = _setup()
    fired: list[str] = []
    hooks.register_hook(ON_TOOL_ERROR, lambda p: fired.append(p["tool"]))

    result = runtime.execute("search_wardrobe", {"query": {"nested": True}}, state={})

    assert result.status == STATUS_ERROR
    assert "不合法" in result.observation
    assert fired == ["search_wardrobe"]


def test_execute_handler_error_normalized_never_raises() -> None:
    registry = CapabilityRegistry()

    class _In(BaseModel):
        q: str

    def _boom(inp: _In, ctx) -> ToolCapability:
        raise RuntimeError("boom")

    registry.register_tool(
        ToolCapability(name="bad_tool", description="x", input_model=_In, handler=_boom)
    )
    runtime = ToolRuntime(registry)

    result = runtime.execute("bad_tool", {"q": "hi"}, state={})

    assert result.status == STATUS_ERROR
    assert "boom" in result.observation


def test_hook_chain_order_pre_then_post() -> None:
    runtime, hooks, _ = _setup()
    events: list[tuple] = []
    hooks.register_hook(PRE_TOOL_USE, lambda p: events.append(("pre", p["tool"])))
    hooks.register_hook(POST_TOOL_USE, lambda p: events.append(("post", p["tool"], p["status"])))

    result = runtime.execute("search_wardrobe", {"query": "white"}, state={})

    assert events[0] == ("pre", "search_wardrobe")
    assert events[1] == ("post", "search_wardrobe", STATUS_OK)
    assert result.status == STATUS_OK


def test_hook_failure_recorded_never_fatal() -> None:
    runtime, hooks, _ = _setup()

    def _bad_hook(payload: dict) -> None:
        raise ValueError("observer bug")

    hooks.register_hook(PRE_TOOL_USE, _bad_hook)

    result = runtime.execute("search_wardrobe", {"query": "white"}, state={})

    assert result.status == STATUS_OK
    assert len(hooks.errors) == 1
    assert hooks.errors[0][0] == PRE_TOOL_USE


def test_execute_update_plan_writes_state() -> None:
    runtime, _, _ = _setup()
    result = runtime.execute(
        "update_plan",
        {
            "plan": {
                "objective": "看音乐剧",
                "missing_information": ["时间"],
                "next_steps": ["查天气"],
                "completed_steps": [],
                "remaining_steps": ["搭配"],
            }
        },
        state={},
    )

    assert result.status == STATUS_OK
    plan = result.state_updates["plan"]
    assert plan.objective == "看音乐剧"
    assert plan.next_steps == ["查天气"]
