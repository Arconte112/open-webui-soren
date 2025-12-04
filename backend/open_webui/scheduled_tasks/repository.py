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
STATUS_COMPLETED = "completed"

ALLOWED_STATUS = {STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED, STATUS_COMPLETED}


@dataclass
class ScheduledTask:
    id: int
    prompt: str
    run_at: datetime
    recurrence: Optional[str]
    recurrence_end: Optional[datetime]
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
    recurrence TEXT,
    recurrence_end TIMESTAMPTZ,
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
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'completed'));
    END IF;
END
$$;
"""

MIGRATE_STATUS_CONSTRAINT = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'scheduled_tasks_status_check'
          AND convalidated
    ) THEN
        BEGIN
            ALTER TABLE scheduled_tasks DROP CONSTRAINT scheduled_tasks_status_check;
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END;
    END IF;
    BEGIN
        ALTER TABLE scheduled_tasks
        ADD CONSTRAINT scheduled_tasks_status_check
        CHECK (status IN ('pending','running','done','failed','completed'));
    EXCEPTION WHEN duplicate_object THEN
        NULL;
    END;
END
$$;
"""

ALTER_COLUMNS = """
ALTER TABLE scheduled_tasks
    ADD COLUMN IF NOT EXISTS recurrence TEXT,
    ADD COLUMN IF NOT EXISTS recurrence_end TIMESTAMPTZ;
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
        conn.execute(text(MIGRATE_STATUS_CONSTRAINT))
        conn.execute(text(ALTER_COLUMNS))
        conn.execute(text(CREATE_INDEX))


def _row_to_task(row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        prompt=row["prompt"],
        run_at=row["run_at"],
        recurrence=row.get("recurrence"),
        recurrence_end=row.get("recurrence_end"),
        notify=bool(row["notify"]),
        status=row["status"],
        created_at=row["created_at"],
        executed_at=row.get("executed_at"),
        last_error=row.get("last_error"),
        last_response_preview=row.get("last_response_preview"),
    )


def create_task(
    prompt: str,
    run_at: datetime,
    notify: bool,
    recurrence: Optional[str] = None,
    recurrence_end: Optional[datetime] = None,
) -> ScheduledTask:
    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "INSERT INTO scheduled_tasks (prompt, run_at, notify, status, recurrence, recurrence_end) "
                    "VALUES (:prompt, :run_at, :notify, :status, :recurrence, :recurrence_end) "
                    "RETURNING id, prompt, run_at, notify, status, created_at, executed_at, last_error, last_response_preview, recurrence, recurrence_end"
                ),
                {
                    "prompt": prompt,
                    "run_at": run_at,
                    "notify": notify,
                    "status": STATUS_PENDING,
                    "recurrence": recurrence,
                    "recurrence_end": recurrence_end,
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
                "SELECT id, prompt, run_at, notify, status, created_at, executed_at, last_error, last_response_preview, recurrence, recurrence_end "
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


def reschedule_task(
    task_id: int,
    next_run_at: datetime,
    status: str,
    executed_at: Optional[datetime] = None,
) -> None:
    if status not in ALLOWED_STATUS:
        raise ValueError(f"Invalid status '{status}'. Allowed: {sorted(ALLOWED_STATUS)}")

    engine = _get_engine()
    _ensure_table(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scheduled_tasks "
                "SET run_at = :run_at, status = :status, executed_at = :executed_at "
                "WHERE id = :id"
            ),
            {
                "run_at": next_run_at,
                "status": status,
                "executed_at": executed_at,
                "id": task_id,
            },
        )
