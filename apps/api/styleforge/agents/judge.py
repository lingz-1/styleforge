"""Independent offline judge: five-dimension quality scoring, detached from the
runtime critic (Agent 3).

The runtime critic decides (accept / recompose / ...) and grounds reasoning
against item evidence; the judge only scores an outfit against a user request
using the shared rubric. A failure never produces a neutral score -- it returns
``(None, diag, None)`` so the evaluation can count it in ``judge_failure_rate``
instead of silently blending into the mean.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from styleforge.llm.judge_prompts import JUDGE_PROMPT_VERSION, build_judge_prompt
from styleforge.llm.schema import DimensionScores, parse_llm_json


class JudgeOutput(BaseModel):
    """Strict judge output: one outfit's score plus a short justification.

    A strict subset of the critic's output -- no explanation assessment, no
    environment assessment, no decision, no missing items.
    """

    outfit_id: str = Field(min_length=1, max_length=64)
    dimension_scores: DimensionScores
    reasoning: str = Field(default="", max_length=600)


class IndependentJudge:
    """Text-only DeepSeek judge over the shared five-dimension rubric."""

    backend = "llm"
    prompt_version = JUDGE_PROMPT_VERSION

    def score_outfit(
        self,
        *,
        user_request: str,
        item_texts: list[str],
        llm: Any,
        weights: dict[str, float] | None = None,
        outfit_id: str = "generated",
    ) -> tuple[DimensionScores | None, dict[str, Any], Any]:
        """Score one outfit. Returns ``(scores, diag, diagnostics)``.

        ``scores`` is ``None`` on any failure (missing client, network, bad
        JSON, schema violation); ``diag["degraded"]`` is then ``True`` with the
        reason, so callers can exclude the case from score averages.
        """
        diag: dict[str, Any] = {
            "outfit_id": outfit_id,
            "prompt_version": self.prompt_version,
            "degraded": False,
        }
        if llm is None:
            diag.update({"degraded": True, "reason": "no llm client"})
            return None, diag, None
        system, user = build_judge_prompt(
            user_request=user_request,
            item_texts=item_texts,
            weights=weights,
        )
        try:
            raw, diagnostics = llm.chat_json(
                system=system,
                user=user,
                json_schema=JudgeOutput.model_json_schema(),
                temperature=0.2,
            )
            parsed = parse_llm_json(json.dumps(raw, ensure_ascii=False), JudgeOutput)
        except BaseException as error:
            diag.update({"degraded": True, "reason": f"{type(error).__name__}: {error}"})
            return None, diag, None
        diag["reasoning"] = parsed.reasoning
        return parsed.dimension_scores, diag, diagnostics
