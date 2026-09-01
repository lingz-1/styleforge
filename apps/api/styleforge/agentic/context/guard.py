"""ContextGuard: budget the assembled prompt, never fake completion (frozen).

Runs AFTER PromptAssembler, immediately before the model call:

    ContextVisibilityPolicy → ContextAssembler → PromptAssembler → ContextGuard → LLM

Status semantics:
    OK            within the soft char budget → the bundle passes through untouched.
    OVER_BUDGET   over the soft budget → reclaim from the dynamic C-layer only
                  (the stable prefix stays byte-identical for cache reuse, and
                  the current user message / goal in layer D is never touched),
                  set CONTEXT_BUDGET_WARNING, and STILL run the Agent on the
                  truncated bundle.
    CONTEXT_LIMIT still over budget with no room to reclaim (even the stable
                  prefix + user message cannot fit) → explicit failure: no
                  bundle is returned, the model call is skipped, and the task
                  reports a hard context limit.

The guard NEVER fabricates a completion: it only shrinks input, and if that is
not enough it fails loudly instead of pretending the agent is done. H3 replaces
the head-truncation below with real section-aware compaction (CompactNode), but
the status contract stays.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from styleforge.agentic.context.prompt_assembler import ContextStats, PromptBundle
from styleforge.agentic.context.prompt_security import secure_prompt_payload

CONTEXT_OK = "ok"
CONTEXT_OVER_BUDGET = "over_budget"
CONTEXT_LIMIT = "context_limit"

DEFAULT_CHAR_BUDGET = 40_000
DEFAULT_HARD_LIMIT = 80_000


@dataclass(frozen=True)
class GuardResult:
    status: str
    warnings: list[str] = field(default_factory=list)
    bundle: PromptBundle | None = None
    reclaimed_chars: int = 0

    @property
    def passes(self) -> bool:
        """True when the Agent may still run on ``bundle``."""
        return self.bundle is not None


class ContextGuard:
    def __init__(
        self,
        *,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        hard_limit: int | None = DEFAULT_HARD_LIMIT,
    ) -> None:
        self.char_budget = char_budget
        self.hard_limit = hard_limit if hard_limit is not None else 2 * char_budget

    def check(self, bundle: PromptBundle) -> GuardResult:
        total = _total_chars(bundle)
        if total <= self.char_budget:
            return GuardResult(status=CONTEXT_OK, bundle=bundle)

        # Over the soft budget. The stable prefix (A+B) must stay intact for
        # cache reuse; the current turn (D) must never be dropped. Reclaim
        # from the dynamic C-layer only.
        stable_chars = len(bundle.system_text)
        wrapper_overhead = (
            len(bundle.model_user_message)
            - len(bundle.runtime_context)
            - len(bundle.user_message)
        )
        keep_for_runtime = (
            self.char_budget
            - stable_chars
            - len(bundle.user_message)
            - wrapper_overhead
        )
        if keep_for_runtime <= 0:
            # No room in the dynamic section — the stable prefix + user message
            # alone exceed the budget. We refuse to cut the stable prefix, so
            # only the hard limit decides between pass-through and hard failure.
            return self._hard_verdict(total, bundle)

        if len(bundle.runtime_context) > keep_for_runtime:
            truncated = bundle.runtime_context[:keep_for_runtime]
            # Avoid handing the model a torn section heading: cut back to the
            # previous section boundary when one exists.
            cut = truncated.rfind("\n\n")
            if cut > 0:
                truncated = truncated[:cut]
            reclaimed = len(bundle.runtime_context) - len(truncated)
            secured_user_message, security_report = secure_prompt_payload(
                truncated, bundle.user_message
            )
            stats = ContextStats(
                total_chars=stable_chars + len(secured_user_message),
                stable_chars=stable_chars,
                dynamic_chars=len(secured_user_message),
                tools=len(bundle.tools),
            )
            new_bundle = bundle.model_copy(
                update={
                    "runtime_context": truncated,
                    "context_stats": stats,
                    "secured_user_message": secured_user_message,
                    "security_report": security_report,
                }
            )
            return GuardResult(
                status=CONTEXT_OVER_BUDGET,
                warnings=[
                    "context over budget: dynamic runtime section truncated "
                    f"(reclaimed {reclaimed} chars); latest task/plan/draft and "
                    "the current request are preserved"
                ],
                bundle=new_bundle,
                reclaimed_chars=reclaimed,
            )

        # Runtime fits within its budget share, yet the total is over — the
        # overflow lives in the stable prefix or capability manifest, which we
        # refuse to cut. Fall back to the hard limit.
        return self._hard_verdict(total, bundle)

    def _hard_verdict(self, total: int, bundle: PromptBundle) -> GuardResult:
        if total <= self.hard_limit:
            return GuardResult(
                status=CONTEXT_OVER_BUDGET,
                warnings=[
                    "context over budget but under the hard limit; passing the "
                    "bundle through untouched"
                ],
                bundle=bundle,
            )
        return GuardResult(
            status=CONTEXT_LIMIT,
            warnings=[
                "context exceeds the hard limit; refusing to call the model "
                "with an unbounded prompt"
            ],
            bundle=None,
        )


def _total_chars(bundle: PromptBundle) -> int:
    return len(bundle.system_text) + len(bundle.model_user_message)
