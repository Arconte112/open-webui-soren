import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from open_webui.scheduled_tasks.repository import create_task
from open_webui.utils.auth import get_verified_user
from open_webui.config import SCHEDULED_TASK_MODEL

ALLOWED_RECURRENCE = {"daily", "weekly", "monthly"}

log = logging.getLogger(__name__)

router = APIRouter()


class ScheduledTasksConfigPayload(BaseModel):
    model_id: Optional[str] = None


class ScheduledTasksConfigResponse(BaseModel):
    model_id: str


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
    recurrence: Optional[str] = Field(
        default=None,
        description="Recurrencia: daily | weekly | monthly",
    )
    recurrence_interval_hours: Optional[int] = Field(
        default=None,
        description="Recurrencia por intervalo de horas (prioridad sobre recurrence).",
    )
    recurrence_end: Optional[str] = Field(
        default=None,
        description="Fecha/hora límite ISO8601 (UTC). Null = infinita.",
    )
    recurrence_weekdays: Optional[List[int]] = Field(
        default=None,
        description="Días de la semana permitidos (0=Lunes ... 6=Domingo).",
    )
    recurrence_window_start_hour: Optional[int] = Field(
        default=None,
        description="Hora inicial permitida (0-23) para la recurrencia en America/Santo_Domingo.",
    )
    recurrence_window_end_hour: Optional[int] = Field(
        default=None,
        description="Hora final permitida (0-23) para la recurrencia en America/Santo_Domingo.",
    )


class ScheduledTaskResponse(BaseModel):
    id: int
    prompt: str
    run_at: datetime
    recurrence: Optional[str]
    recurrence_interval_hours: Optional[int]
    recurrence_end: Optional[datetime]
    recurrence_weekdays: Optional[str]
    recurrence_window_start_hour: Optional[int]
    recurrence_window_end_hour: Optional[int]
    notify: bool
    status: str
    created_at: datetime
    executed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_response_preview: Optional[str] = None


@router.get(
    "/config",
    response_model=ScheduledTasksConfigResponse,
    summary="Lee la configuración de tareas programadas",
)
def get_scheduled_tasks_config(request: Request, user=Depends(get_verified_user)):
    model_id = request.app.state.config.SCHEDULED_TASK_MODEL or SCHEDULED_TASK_MODEL.value
    return ScheduledTasksConfigResponse(model_id=model_id)


@router.post(
    "/config",
    response_model=ScheduledTasksConfigResponse,
    summary="Actualiza la configuración de tareas programadas",
)
def update_scheduled_tasks_config(
    payload: ScheduledTasksConfigPayload, request: Request, user=Depends(get_verified_user)
):
    new_model_id = (payload.model_id or "").strip() or "soren"
    request.app.state.config.SCHEDULED_TASK_MODEL = new_model_id
    return ScheduledTasksConfigResponse(model_id=new_model_id)


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


def _parse_recurrence(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().lower()
    if value and value not in ALLOWED_RECURRENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"recurrence debe ser uno de {sorted(ALLOWED_RECURRENCE)}",
        )
    return value or None


def _parse_recurrence_interval_hours(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_interval_hours debe ser un entero válido.",
        ) from exc
    if interval <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_interval_hours debe ser mayor que 0.",
        )
    return interval


def _parse_recurrence_end(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_end debe tener formato ISO8601 válido.",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_recurrence_weekdays(value: Optional[List[int]]) -> Optional[str]:
    if value is None:
        return None
    if not value:
        return None
    try:
        weekdays = {int(day) for day in value}
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_weekdays debe ser una lista de enteros (0-6).",
        ) from exc
    if any(day < 0 or day > 6 for day in weekdays):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_weekdays solo acepta valores entre 0 y 6 (0=Lunes ... 6=Domingo).",
        )
    return ",".join(str(day) for day in sorted(weekdays))


def _parse_hour_range(start_hour: Optional[int], end_hour: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if start_hour is None and end_hour is None:
        return None, None
    if start_hour is None or end_hour is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_window_start_hour y recurrence_window_end_hour deben enviarse juntos.",
        )
    try:
        start = int(start_hour)
        end = int(end_hour)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_window_start_hour y recurrence_window_end_hour deben ser enteros.",
        ) from exc
    if start < 0 or start > 23 or end < 0 or end > 23:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_window_start_hour y recurrence_window_end_hour deben estar entre 0 y 23.",
        )
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recurrence_window_start_hour debe ser menor o igual a recurrence_window_end_hour.",
        )
    return start, end


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
    recurrence = _parse_recurrence(form_data.recurrence)
    recurrence_interval_hours = _parse_recurrence_interval_hours(
        form_data.recurrence_interval_hours
    )
    recurrence_end = _parse_recurrence_end(form_data.recurrence_end)
    recurrence_weekdays = _parse_recurrence_weekdays(form_data.recurrence_weekdays)
    recurrence_window_start_hour, recurrence_window_end_hour = _parse_hour_range(
        form_data.recurrence_window_start_hour,
        form_data.recurrence_window_end_hour,
    )

    try:
        task = create_task(
            prompt=prompt,
            run_at=run_at,
            notify=form_data.notify,
            recurrence=recurrence,
            recurrence_interval_hours=recurrence_interval_hours,
            recurrence_end=recurrence_end,
            recurrence_weekdays=recurrence_weekdays,
            recurrence_window_start_hour=recurrence_window_start_hour,
            recurrence_window_end_hour=recurrence_window_end_hour,
        )
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
        recurrence=task.recurrence,
        recurrence_interval_hours=task.recurrence_interval_hours,
        recurrence_end=task.recurrence_end,
        recurrence_weekdays=task.recurrence_weekdays,
        recurrence_window_start_hour=task.recurrence_window_start_hour,
        recurrence_window_end_hour=task.recurrence_window_end_hour,
        notify=task.notify,
        status=task.status,
        created_at=task.created_at,
        executed_at=task.executed_at,
        last_error=task.last_error,
        last_response_preview=task.last_response_preview,
    )
