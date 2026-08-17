"""Real DeepSeek smoke for the Stage 2 Agentic loop (4 acceptance cases).

Each case runs the real provider through the bounded ReAct loop inside a
throwaway test schema. Nothing is committed — the whole loop is shadow. The
output is the outcome + full trace per case (action / args / observation),
so a human can judge whether the Loop *mechanism* and the Agent's *semantics*
both work against a real model before we build the Stage 3 A/B framework.

Run (style env, from repo root):
    "D:/anaconda/envs/style/python.exe" tools/smoke_agentic.py > smoke_out.txt
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Make `styleforge` importable from apps/api.
API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

# The terminal is GBK on this machine; writing UTF-8 to the file lets Read
# decode the trace (which contains Chinese) instead of mojibake.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from styleforge.agentic.agent import AgentLoop, LoopConfig  # noqa: E402
from styleforge.agentic.environment import Environment, build_facts  # noqa: E402
from styleforge.core.config import Settings  # noqa: E402
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus  # noqa: E402
from styleforge.llm.client import llm_client_from_settings  # noqa: E402
from styleforge.models.context import (  # noqa: E402
    ContextPack,
    EnvironmentContext,
    OutfitContext,
    RequestContext,
    UserContext,
)
from styleforge.models.task import TaskExecutionInput  # noqa: E402
from styleforge.repositories.catalog_repository import upsert_items  # noqa: E402
from styleforge.repositories.database import connect, database_session, initialize_database  # noqa: E402
from styleforge.repositories.wardrobe_repository import add_items, list_items  # noqa: E402

USER_ID = "smoke-user"
SCHEMA = "smoke_agentic"


# ── seed ─────────────────────────────────────────────────────────────


def _item(item_id: str, item_type: str, name: str, color: str) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        source="smoke",
        gender="women",
        item_type=item_type,
        main_category=item_type,
        name=name,
        color=color,
        description="",
        features=(),
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
        embedding_status=EmbeddingStatus.PENDING,
    )


WARDROBE = [
    _item("top-1", "top", "白衬衫", "white"),
    _item("top-2", "top", "蓝色针织衫", "blue"),      # the only non-white top
    _item("pants-1", "pants", "黑色西裤", "black"),
    _item("pants-2", "pants", "卡其裤", "khaki"),
    _item("coat-1", "outwear", "灰色大衣", "gray"),
    _item("blazer-1", "outwear", "深蓝西装外套", "navy"),
    _item("shoes-1", "shoes", "黑色皮鞋", "black"),
    _item("boots-1", "shoes", "棕色短靴", "brown"),
    _item("sneakers-1", "shoes", "白色运动鞋", "white"),
    _item("bag-1", "bag", "黑色手袋", "black"),
    # Deliberately NO pink item anywhere — case 3 must face an empty search.
]


def _scoped_dsn(base_dsn: str, schema: str) -> str:
    separator = "&" if "?" in base_dsn else "?"
    return f"{base_dsn}{separator}options=-csearch_path%3D{schema}"


def _prepare_schema() -> str:
    base_dsn = os.environ.get("STYLEFORGE_TEST_DATABASE_DSN", "").strip()
    if not base_dsn:
        raise SystemExit("STYLEFORGE_TEST_DATABASE_DSN is not set in .env")
    admin = connect(base_dsn)
    try:
        admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        admin.execute(f'CREATE SCHEMA "{SCHEMA}"')
        admin.commit()
    finally:
        admin.close()
    dsn = _scoped_dsn(base_dsn, SCHEMA)
    initialize_database(dsn)
    with database_session(dsn) as connection:
        upsert_items(connection, WARDROBE, "smoke")
        add_items(connection, USER_ID, [item.item_id for item in WARDROBE])
    return dsn


def _context_pack() -> ContextPack:
    return ContextPack(
        request_context=RequestContext(
            original_request="",
            task_type="OUTFIT_MODIFY",
            route_reason="smoke",
            route_confidence=1.0,
        ),
        user_context=UserContext(user_id=USER_ID),
        environment_context=EnvironmentContext(
            weather={"summary": "晴", "temp": 22}
        ),
    )


# ── harness ──────────────────────────────────────────────────────────


def _run_smoke(dsn: str, llm: Any, cases: list[tuple[str, TaskExecutionInput]]) -> None:
    for title, task_input in cases:
        print(f"\n=== {title} ===")
        with database_session(dsn) as connection:
            wardrobe_items = list_items(connection, task_input.user_id)
            facts = build_facts(connection, task_input, _context_pack(), wardrobe_items)
            environment = Environment(connection, wardrobe_items, facts, search_limit=12)
            loop = AgentLoop(
                llm,
                environment,
                task_input.request,
                config=LoopConfig(max_steps=8, search_limit=12),
            )
            outcome = loop.run()
        _print_trace(outcome)


def _print_trace(outcome: dict) -> None:
    print(f"  status         : {outcome['status']}")
    print(f"  llm_call_count : {outcome['llm_call_count']}  steps: {len(outcome['steps'])}")
    candidate = outcome.get("candidate") or {}
    print(f"  candidate      : {candidate.get('item_ids')}")
    if outcome.get("ask_user"):
        print(f"  ask_user       : {outcome['ask_user']['question']}")
    review = outcome.get("review")
    if review:
        print(f"  review         : approved={review.get('approved')} issues={review.get('issues')}")
    for step in outcome["steps"]:
        args = json.dumps(step.get("args", {}), ensure_ascii=False)
        print(f"    step {step['step']:>2} [{step['action']}] {args}")
        obs = str(step.get("observation", ""))
        print(f"        obs: {obs[:200]}")


# ── the four acceptance cases ────────────────────────────────────────


def main() -> int:
    dsn = _prepare_schema()
    llm = llm_client_from_settings(Settings.from_env())
    if llm is None:
        raise SystemExit("DEEPSEEK_API_KEY is not set in .env")
    cases = [
        (
            "C1 第二套挺好但鞋不喜欢（grounding + replace）",
            TaskExecutionInput(
                user_id=USER_ID,
                request="第二套挺好但鞋不喜欢，帮我换一下",
                current_outfit_id="outfit-2",
                current_item_ids=["top-2", "pants-2", "boots-1"],
            ),
        ),
        (
            "C2 上衣留着其他随便（守住 requirement）",
            TaskExecutionInput(
                user_id=USER_ID,
                request="这套里上衣留着，其他你看着办",
                current_outfit_id="outfit-1",
                current_item_ids=["top-1", "pants-1", "coat-1", "shoes-1"],
            ),
        ),
        (
            "C3 想要粉色上衣，没有的话你看着办（search empty 自主放宽）",
            TaskExecutionInput(
                user_id=USER_ID,
                request="想加一件粉色上衣，衣柜里没有的话你看着办",
                current_outfit_id="outfit-1",
                current_item_ids=["top-1", "pants-1", "shoes-1"],
            ),
        ),
        (
            "C4 鞋换一下（无 active/selected → ask_user）",
            TaskExecutionInput(
                user_id=USER_ID,
                request="鞋换一下",
            ),
        ),
    ]
    _run_smoke(dsn, llm, cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
