"""Persistence for all v3.3 task executions."""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection
import uuid
from datetime import datetime, timezone
from typing import Any

from styleforge.orchestration.task_router import TaskType


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_task_run(
    connection: Connection,
    *,
    user_id: str,
    task_type: TaskType,
    request: str,
) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO task_runs(run_id, user_id, task_type, request, status, created_at)
        VALUES (%s, %s, %s, %s, 'running', %s)
        """,
        (run_id, user_id, task_type.value, request, _now()),
    )
    return run_id


def finish_task_run(
    connection: Connection,
    *,
    run_id: str,
    status: str,
    context_pack: dict[str, Any],
    result: dict[str, Any],
) -> None:
    connection.execute(
        """
        UPDATE task_runs
        SET status = %s, context_pack_json = %s, result_json = %s, finished_at = %s
        WHERE run_id = %s
        """,
        (
            status,
            json.dumps(context_pack, ensure_ascii=False, sort_keys=True),
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            _now(),
            run_id,
        ),
    )


def fail_task_run(
    connection: Connection,
    *,
    run_id: str,
    error: BaseException,
    context_pack: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        UPDATE task_runs
        SET status = 'failed', context_pack_json = %s, error_message = %s, finished_at = %s
        WHERE run_id = %s
        """,
        (
            json.dumps(context_pack or {}, ensure_ascii=False, sort_keys=True),
            str(error)[:2000],
            _now(),
            run_id,
        ),
    )


def get_task_run(
    connection: Connection,
    *,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM task_runs WHERE run_id = %s AND user_id = %s",
        (run_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "task_type": row["task_type"],
        "request": row["request"],
        "status": row["status"],
        "context_pack": json.loads(row["context_pack_json"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }
