"""Persistence for styling runs and candidate snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from styleforge.core.schemas import OutfitCandidate, RecommendationResult, TaskSpec


def start_run(connection: sqlite3.Connection, task: TaskSpec) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO styling_runs(run_id, user_id, status, task_spec_json, created_at)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (
            run_id,
            task.user_id,
            json.dumps(task.to_dict(), ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return run_id


def save_candidates(
    connection: sqlite3.Connection,
    run_id: str,
    candidates: Sequence[OutfitCandidate],
) -> None:
    connection.executemany(
        """
        INSERT INTO candidate_outfits(
            run_id, outfit_id, rank, score, hard_valid, item_ids_json, score_details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, outfit_id) DO UPDATE SET
            rank = excluded.rank,
            score = excluded.score,
            hard_valid = excluded.hard_valid,
            item_ids_json = excluded.item_ids_json,
            score_details_json = excluded.score_details_json
        """,
        (
            (
                run_id,
                candidate.outfit_id,
                rank,
                candidate.score,
                int(candidate.hard_valid),
                json.dumps(candidate.item_ids, ensure_ascii=False),
                json.dumps(candidate.score_details, ensure_ascii=False, sort_keys=True),
            )
            for rank, candidate in enumerate(candidates, start=1)
        ),
    )


def finish_run(connection: sqlite3.Connection, result: RecommendationResult) -> None:
    connection.execute(
        """
        UPDATE styling_runs
        SET status = ?, result_json = ?, finished_at = ?
        WHERE run_id = ?
        """,
        (
            result.status,
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
            result.run_id,
        ),
    )


def save_semantic_detail(
    connection: sqlite3.Connection,
    run_id: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        """
        UPDATE styling_runs
        SET semantic_detail_json = ?
        WHERE run_id = ?
        """,
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            run_id,
        ),
    )


def fail_run(connection: sqlite3.Connection, run_id: str, error: BaseException) -> None:
    connection.execute(
        """
        UPDATE styling_runs
        SET status = 'failed', error_message = ?, finished_at = ?
        WHERE run_id = ?
        """,
        (str(error)[:2000], datetime.now(timezone.utc).isoformat(), run_id),
    )

