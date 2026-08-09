"""LangGraph orchestration for the StyleForge agent workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from styleforge.workflow.graph import StyleForgeWorkflow

__all__ = ["StyleForgeWorkflow"]


def __getattr__(name: str):
    """Avoid importing the executable graph module during package initialization."""
    if name == "StyleForgeWorkflow":
        from styleforge.workflow.graph import StyleForgeWorkflow

        return StyleForgeWorkflow
    raise AttributeError(name)
