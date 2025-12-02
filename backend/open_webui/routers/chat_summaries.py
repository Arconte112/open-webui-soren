import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from open_webui.chat_summaries.repository import (
    DEFAULT_SUMMARY_PROMPT,
    get_settings,
    list_recent_summaries,
    upsert_settings,
)
from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.auth import get_verified_user
from open_webui.utils.task import get_memories_engine, clear_chat_summaries_cache

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


class SummarySettingsPayload(BaseModel):
    enabled: bool = False
    model_id: str | None = None
    api_key: str | None = None
    prompt: str | None = None
    max_items: int | None = None


class SummarySettingsResponse(BaseModel):
    enabled: bool
    model_id: str | None
    api_key: str | None
    prompt: str
    max_items: int
    updated_at: datetime | None = None


class SummaryItem(BaseModel):
    chat_id: str
    summary: str | None
    created_at: datetime
    expires_at: datetime


def _get_engine():
    engine = get_memories_engine()
    if engine is None:
        raise HTTPException(
            status_code=500,
            detail="External PostgreSQL database is not configured.",
        )
    return engine


@router.get("/config", response_model=SummarySettingsResponse)
async def read_summary_settings(user=Depends(get_verified_user)):
    engine = _get_engine()
    settings = get_settings(engine, user.id)
    return SummarySettingsResponse(
        enabled=settings.enabled,
        model_id=settings.model_id,
        api_key=settings.api_key,
        prompt=settings.prompt or DEFAULT_SUMMARY_PROMPT,
        max_items=settings.max_items,
        updated_at=settings.updated_at,
    )


@router.post("/config", response_model=SummarySettingsResponse)
async def save_summary_settings(
    payload: SummarySettingsPayload, user=Depends(get_verified_user)
):
    engine = _get_engine()

    settings = upsert_settings(
        engine=engine,
        user_id=user.id,
        enabled=payload.enabled,
        model_id=payload.model_id,
        api_key=payload.api_key,
        prompt=payload.prompt,
        max_items=payload.max_items,
    )
    clear_chat_summaries_cache(user.id)

    return SummarySettingsResponse(
        enabled=settings.enabled,
        model_id=settings.model_id,
        api_key=settings.api_key,
        prompt=settings.prompt or DEFAULT_SUMMARY_PROMPT,
        max_items=settings.max_items,
        updated_at=settings.updated_at,
    )


@router.get("/", response_model=list[SummaryItem])
async def read_recent_summaries(
    limit: int | None = Query(None, ge=1, le=200), user=Depends(get_verified_user)
):
    engine = _get_engine()
    settings = get_settings(engine, user.id)
    effective_limit = limit or settings.max_items or 20
    summaries = list_recent_summaries(engine, user.id, limit=effective_limit)
    return [
        SummaryItem(
            chat_id=item.chat_id,
            summary=item.summary,
            created_at=item.created_at,
            expires_at=item.expires_at,
        )
        for item in summaries
    ]
