import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from open_webui.utils.task import get_memories_engine

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

ALLOWED_STATUS = {STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED}


@dataclass
class ScheduledTask:
    id: int
    prompt: str
    run_at: datetime
    notify: bool
    status: str
    created_at: datetime
    executed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_response_preview: Optional[str] = None


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    prompt TEXT NOT NULL,
    run_at TIMESTAMPTZ NOT NULL,
    notify BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    last_error TEXT,
    last_response_preview TEXT
);
"""

CREATE_STATUS_CONSTRAINT = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'scheduled_tasks_status_check'
    ) THEN
        ALTER TABLE scheduled_tasks
        ADD CONSTRAINT scheduled_tasks_status_check
        CHECK (status IN ('pending', 'running', 'done', 'failed'));
    END IF;
END
$$;
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS scheduled_tasks_pending_idx
    ON scheduled_tasks (status, run_at);
"""


def _get_engine() -> Engine:
    engine = get_memories_engine()
    if engine is None:
        raise RuntimeError("External memories database engine is not configured.")
    return engine


def _ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE))
        conn.execute(text(CREATE_STATUS_CONSTRAINT))
        conn.execute(text(CREATE_INDEX))


def _row_to_task(row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        prompt=row["prompt"],
        run_at=row["run_at"],
        notify=bool(row["notify"]),
        status=row["status"],
        created_at=row["created_at"],
        executed_at=row.get("executed_at"),
        last_error=row.get("last_error"),
        last_response_preview=row.get("last_response_preview"),
    )


def create_task(prompt: str, run_at: datetime, notify: bool) -> ScheduledTask:
    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "INSERT INTO scheduled_tasks (prompt, run_at, notify, status) "
                    "VALUES (:prompt, :run_at, :notify, :status) "
                    "RETURNING id, prompt, run_at, notify, status, created_at, executed_at, last_error, last_response_preview"
                ),
                {
                    "prompt": prompt,
                    "run_at": run_at,
                    "notify": notify,
                    "status": STATUS_PENDING,
                },
            )
            .mappings()
            .first()
        )

    return _row_to_task(row)


def get_due_tasks(now: datetime) -> List[ScheduledTask]:
    engine = _get_engine()
    _ensure_table(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, prompt, run_at, notify, status, created_at, executed_at, last_error, last_response_preview "
                "FROM scheduled_tasks "
                "WHERE status = :status AND run_at <= :now "
                "ORDER BY run_at ASC, id ASC"
            ),
            {"status": STATUS_PENDING, "now": now},
        ).mappings()

        return [_row_to_task(row) for row in rows]


def update_task_status(task_id: int, status: str, executed_at: Optional[datetime] = None) -> None:
    if status not in ALLOWED_STATUS:
        raise ValueError(f"Invalid status '{status}'. Allowed: {sorted(ALLOWED_STATUS)}")

    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scheduled_tasks "
                "SET status = :status, executed_at = COALESCE(:executed_at, executed_at) "
                "WHERE id = :id"
            ),
            {"status": status, "executed_at": executed_at, "id": task_id},
        )


def update_last_error(task_id: int, last_error: str, executed_at: Optional[datetime] = None) -> None:
    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scheduled_tasks "
                "SET last_error = :last_error, executed_at = COALESCE(:executed_at, executed_at) "
                "WHERE id = :id"
            ),
            {"last_error": last_error, "executed_at": executed_at, "id": task_id},
        )


def update_last_response_preview(task_id: int, preview: Optional[str]) -> None:
    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scheduled_tasks "
                "SET last_response_preview = :preview "
                "WHERE id = :id"
            ),
            {"preview": preview, "id": task_id},
        )
