import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Prompt used when the user has not customized one yet.
DEFAULT_SUMMARY_PROMPT = (
    "Eres un asistente que genera resúmenes breves para conversaciones previas. "
    "Devuelve un resumen conciso en máximo 2 líneas (separadas por saltos de línea). "
    "Si la conversación no aporta contexto útil para el futuro, responde únicamente con la palabra null."
)

# Mantén las filas indefinidamente; se usa una expiración lejana solo para cumplir la columna NOT NULL.
DEFAULT_EXPIRY_DAYS = 3650


@dataclass
class SummarySettings:
    user_id: str
    enabled: bool = False
    model_id: Optional[str] = None
    api_key: Optional[str] = None
    prompt: str = DEFAULT_SUMMARY_PROMPT
    max_items: int = 20
    updated_at: Optional[datetime] = None


@dataclass
class ChatSummary:
    chat_id: str
    user_id: str
    summary: Optional[str]
    created_at: datetime
    expires_at: datetime


CREATE_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS chat_summary_settings (
    user_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    model_id TEXT,
    api_key TEXT,
    prompt TEXT,
    max_items INTEGER NOT NULL DEFAULT 20,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_SUMMARIES_TABLE = """
CREATE TABLE IF NOT EXISTS chat_summaries (
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT chat_summaries_user_chat PRIMARY KEY (chat_id, user_id)
);
"""

CREATE_SUMMARIES_INDEX = """
CREATE INDEX IF NOT EXISTS chat_summaries_expires_idx
    ON chat_summaries (expires_at DESC);
"""


def _ensure_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(CREATE_SETTINGS_TABLE))
        conn.execute(text(CREATE_SUMMARIES_TABLE))
        conn.execute(text(CREATE_SUMMARIES_INDEX))
        # Backfill missing column if table already existed
        conn.execute(
            text(
                "ALTER TABLE chat_summary_settings "
                "ADD COLUMN IF NOT EXISTS max_items INTEGER NOT NULL DEFAULT 20"
            )
        )


def get_settings(engine: Engine, user_id: str) -> SummarySettings:
    _ensure_tables(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT user_id, enabled, model_id, api_key, prompt, max_items, updated_at "
                "FROM chat_summary_settings WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).mappings().first()

    if row:
        return SummarySettings(
            user_id=row["user_id"],
            enabled=bool(row["enabled"]),
            model_id=row.get("model_id"),
            api_key=row.get("api_key"),
            prompt=row.get("prompt") or DEFAULT_SUMMARY_PROMPT,
            max_items=int(row.get("max_items") or 20),
            updated_at=row.get("updated_at"),
        )

    return SummarySettings(user_id=user_id)


def upsert_settings(
    engine: Engine,
    user_id: str,
    enabled: bool,
    model_id: Optional[str],
    api_key: Optional[str],
    prompt: Optional[str],
    max_items: Optional[int] = None,
) -> SummarySettings:
    _ensure_tables(engine)

    cleaned_prompt = (prompt or "").strip() or DEFAULT_SUMMARY_PROMPT
    cleaned_model_id = (model_id or "").strip() or None
    cleaned_api_key = (api_key or "").strip() or None
    cleaned_max_items = max_items if max_items is not None else 20
    if cleaned_max_items < 1:
        cleaned_max_items = 1
    if cleaned_max_items > 200:
        cleaned_max_items = 200

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_summary_settings (user_id, enabled, model_id, api_key, prompt, max_items, updated_at) "
                "VALUES (:user_id, :enabled, :model_id, :api_key, :prompt, :max_items, NOW()) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "enabled = EXCLUDED.enabled, "
                "model_id = EXCLUDED.model_id, "
                "api_key = EXCLUDED.api_key, "
                "prompt = EXCLUDED.prompt, "
                "max_items = EXCLUDED.max_items, "
                "updated_at = NOW()"
            ),
            {
                "user_id": user_id,
                "enabled": enabled,
                "model_id": cleaned_model_id,
                "api_key": cleaned_api_key,
                "prompt": cleaned_prompt,
                "max_items": cleaned_max_items,
            },
        )

    return get_settings(engine, user_id)


def list_recent_summaries(
    engine: Engine, user_id: str, limit: int = 20
) -> list[ChatSummary]:
    _ensure_tables(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT chat_id, user_id, summary, created_at, expires_at "
                "FROM chat_summaries "
                "WHERE user_id = :user_id "
                "ORDER BY created_at DESC "
                "LIMIT :limit"
            ),
            {"user_id": user_id, "limit": limit},
        ).mappings()

        return [
            ChatSummary(
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                summary=row.get("summary"),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]


def list_active_settings(engine: Engine) -> list[SummarySettings]:
    """
    Return every settings row that can be used to generate summaries.
    """
    _ensure_tables(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT user_id, enabled, model_id, api_key, prompt, max_items, updated_at "
                "FROM chat_summary_settings "
                "WHERE enabled = TRUE AND model_id IS NOT NULL AND api_key IS NOT NULL"
            )
        ).mappings()

        return [
            SummarySettings(
                user_id=row["user_id"],
                enabled=True,
                model_id=row.get("model_id"),
                api_key=row.get("api_key"),
                prompt=row.get("prompt") or DEFAULT_SUMMARY_PROMPT,
                max_items=int(row.get("max_items") or 20),
                updated_at=row.get("updated_at"),
            )
            for row in rows
        ]


def get_active_chat_ids(
    engine: Engine, user_id: str, chat_ids: Iterable[str]
) -> dict[str, datetime]:
    """
    Returns mapping chat_id -> summary_created_at for chats that already have a summary.
    """
    _ensure_tables(engine)
    id_list = list(chat_ids)
    if not id_list:
        return {}

    query = text(
        "SELECT chat_id, created_at FROM chat_summaries "
        "WHERE user_id = :user_id AND chat_id IN :chat_ids"
    ).bindparams(bindparam("chat_ids", expanding=True))

    with engine.connect() as conn:
        rows = conn.execute(query, {"user_id": user_id, "chat_ids": id_list})
        return {row[0]: row[1] for row in rows}


def save_summary(
    engine: Engine,
    chat_id: str,
    user_id: str,
    summary: Optional[str],
    created_at: Optional[datetime] = None,
) -> None:
    _ensure_tables(engine)

    created = created_at or datetime.now(timezone.utc)
    expires_at = created + timedelta(days=DEFAULT_EXPIRY_DAYS)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_summaries (chat_id, user_id, summary, created_at, expires_at) "
                "VALUES (:chat_id, :user_id, :summary, :created_at, :expires_at) "
                "ON CONFLICT (chat_id, user_id) DO UPDATE SET "
                "summary = EXCLUDED.summary, "
                "created_at = EXCLUDED.created_at, "
                "expires_at = EXCLUDED.expires_at"
            ),
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "summary": summary,
                "created_at": created,
                "expires_at": expires_at,
            },
        )


def purge_expired(engine: Engine) -> int:
    """
    Deprecated: los resúmenes ya no se eliminan automáticamente.
    """
    return 0
