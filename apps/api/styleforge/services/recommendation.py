"""End-to-end deterministic recommendation service."""

from __future__ import annotations


from styleforge.core.schemas import RecommendationResult, TaskSpec
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.run_repository import (
    fail_run,
    finish_run,
    save_candidates,
    start_run,
)
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.tools.candidate_generation import generate_candidates, select_diverse_candidates


def recommend_for_user(database_path: str, task: TaskSpec) -> RecommendationResult:
    initialize_database(database_path)
    with database_session(database_path) as connection:
        run_id = start_run(connection, task)

    try:
        with database_session(database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        candidates, diagnostics = generate_candidates(wardrobe_items, task)
        selected = select_diverse_candidates(candidates, task.max_results)
        status = "completed" if selected else "infeasible"
        result = RecommendationResult(
            run_id=run_id,
            status=status,
            recommendations=tuple(selected),
            diagnostics=diagnostics,
        )
        with database_session(database_path) as connection:
            save_candidates(connection, run_id, selected)
            finish_run(connection, result)
        return result
    except BaseException as error:
        with database_session(database_path) as connection:
            fail_run(connection, run_id, error)
        raise

