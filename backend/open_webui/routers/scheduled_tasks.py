import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from open_webui.scheduled_tasks.repository import create_task
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


class ScheduleTaskBody(BaseModel):
    prompt: str = Field(..., min_length=1, description="Mensaje a programar.")
    run_at: str = Field(
        ...,
        description="Fecha/hora de ejecución en ISO8601 (UTC). Se aceptan valores con sufijo 'Z'.",
    )
    notify: bool = Field(
        default=False,
        description="Si es true, además de ejecutar el prompt se enviará una notificación externa.",
    )


class ScheduledTaskResponse(BaseModel):
    id: int
    prompt: str
    run_at: datetime
    notify: bool
    status: str
    created_at: datetime
    executed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_response_preview: Optional[str] = None


def _parse_run_at(value: str) -> datetime:
    run_at_raw = (value or "").strip()
    if not run_at_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="run_at es obligatorio y debe venir en formato ISO8601.",
        )

    normalized = run_at_raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="run_at debe tener formato ISO8601 válido (ej. 2025-12-05T12:00:00Z).",
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


@router.post(
    "/",
    response_model=ScheduledTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Programa una tarea para Soren",
)
def schedule_task(
    form_data: ScheduleTaskBody,
    user=Depends(get_verified_user),
):
    prompt = form_data.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="prompt no puede estar vacío.",
        )

    run_at = _parse_run_at(form_data.run_at)

    try:
        task = create_task(prompt=prompt, run_at=run_at, notify=form_data.notify)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("No se pudo crear la tarea programada.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ScheduledTaskResponse(
        id=task.id,
        prompt=task.prompt,
        run_at=task.run_at,
        notify=task.notify,
        status=task.status,
        created_at=task.created_at,
        executed_at=task.executed_at,
        last_error=task.last_error,
        last_response_preview=task.last_response_preview,
    )
