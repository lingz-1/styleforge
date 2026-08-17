"""ShadowRunner: run the Agentic loop alongside the legacy path (Stage 2).

The runner freezes ``EnvironmentFacts`` from the *pre-legacy* context pack, so
the shadow sees the same world the legacy saw — execution may be serial, but
the input state is logically parallel. Nothing is ever committed: the outcome
is a trace for Stage 3 A/B, never a real session/Database mutation.

Gated by the ``AGENTIC_SHADOW`` env flag (or an injected ``enabled``). The
result is only exposed on the API payload when ``AGENTIC_SHADOW_EXPOSE`` is
set (tests / development); production responses stay contract-clean. Steps
carry action/args/observation/result only — never the Agent's raw ``thought``.
"""

from __future__ import annotations

import os
from typing import Any

from styleforge.agentic.agent import AgentLoop, LoopConfig
from styleforge.agentic.environment import Environment, build_facts
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.database import database_session
from styleforge.repositories.wardrobe_repository import list_items


def shadow_enabled() -> bool:
    return os.environ.get("AGENTIC_SHADOW", "").strip().lower() == "true"


def shadow_exposed() -> bool:
    return os.environ.get("AGENTIC_SHADOW_EXPOSE", "").strip().lower() == "true"


class AgenticShadowRunner:
    def __init__(
        self,
        database_path: str,
        llm_client: Any,
        *,
        enabled: bool | None = None,
        search_limit: int = 12,
    ) -> None:
        self.database_path = database_path
        self.llm_client = llm_client
        self.enabled = enabled if enabled is not None else shadow_enabled()
        self.search_limit = search_limit

    def run(
        self,
        task_input: TaskExecutionInput,
        context_pack: ContextPack | None,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Run the shadow loop. Returns the outcome dict, or None when off/error."""
        if not self.enabled or self.llm_client is None:
            return None
        try:
            with database_session(self.database_path) as connection:
                wardrobe_items = list_items(connection, task_input.user_id)
                facts = build_facts(connection, task_input, context_pack, wardrobe_items)
                environment = Environment(
                    connection,
                    wardrobe_items,
                    facts,
                    search_limit=self.search_limit,
                )
                loop = AgentLoop(
                    self.llm_client,
                    environment,
                    task_input.request,
                    config=LoopConfig(search_limit=self.search_limit),
                )
                outcome = loop.run()
            return outcome
        except BaseException:
            # The shadow must never fail the request it rides along with.
            return None
