"""
Servicio independiente para generar resúmenes automáticos de chats.

Ejemplo de uso:
    PYTHONPATH=backend python backend/open_webui/chat_summaries/service.py --loop

El servicio:
1) Lee los chats almacenados en SQLite (webui.db).
2) Filtra los chats inactivos (>10 minutos), no resumidos y no triviales.
3) Usa el LLM configurado por cada usuario (OpenRouter) para crear un resumen de ≤2 líneas.
4) Guarda el resultado en PostgreSQL sin borrarlos automáticamente y regenera si el chat tuvo actividad posterior al último resumen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx
import tiktoken

from open_webui.chat_summaries.repository import (
    DEFAULT_SUMMARY_PROMPT,
    get_active_chat_ids,
    list_active_settings,
    save_summary,
)
from open_webui.utils.misc import get_content_from_message, get_message_list
from open_webui.utils.task import get_memories_engine, clear_chat_summaries_cache

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("chat_summarizer")

OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
OPENROUTER_REFERRER = os.getenv(
    "OPENROUTER_REFERRER", "https://soren-openwebui.local"
)
OPENROUTER_TITLE = os.getenv("OPENROUTER_TITLE", "soren-openwebui chat summaries")
MAX_TRANSCRIPT_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "12000"))
ENCODING = tiktoken.get_encoding("cl100k_base")


def _detect_sqlite_path() -> Path:
    """
    Resuelve la ruta del webui.db. Se respeta CHAT_DB_PATH cuando está presente.
    """
    env_path = os.getenv("CHAT_DB_PATH")
    if env_path:
        return Path(env_path)

    candidates = [
        Path("/app/backend/data/webui.db"),
        Path(__file__).resolve().parents[2] / "data" / "webui.db",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Devuelve la última opción aunque no exista; el error se emitirá más adelante.
    return candidates[-1]


def _load_sqlite_rows(sqlite_path: Path, idle_seconds: int) -> list[sqlite3.Row]:
    threshold = int(time.time()) - idle_seconds
    query = "SELECT id, user_id, chat, updated_at FROM chat WHERE updated_at <= ?"

    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(query, (threshold,)))


def _extract_history_messages(chat_blob) -> list[dict]:
    """
    Devuelve la rama actual de mensajes en orden cronológico.
    """
    if isinstance(chat_blob, str):
        try:
            chat_blob = json.loads(chat_blob)
        except json.JSONDecodeError:
            log.warning("No se pudo parsear el JSON del chat; se omite.")
            return []

    if not isinstance(chat_blob, dict):
        return []

    history = chat_blob.get("history") or {}
    messages_map = history.get("messages") or {}
    current_id = history.get("currentId") or history.get("current_id")

    if messages_map and current_id:
        return get_message_list(messages_map, current_id)

    if isinstance(chat_blob.get("messages"), list):
        return chat_blob.get("messages")  # type: ignore[return-value]

    return []


def _flatten_text(messages: Iterable[dict]) -> tuple[str, int, int]:
    """
    Convierte mensajes en texto plano y devuelve (texto, num_mensajes, num_tokens).
    Cuenta solo mensajes de usuario/asistente.
    """
    filtered = []
    for message in messages:
        role = (message.get("role") or "").lower()
        if role not in {"user", "assistant"}:
            continue
        content = get_content_from_message(message) or message.get("content") or ""
        content = str(content).strip()
        if not content:
            continue
        filtered.append((role, content))

    if not filtered:
        return "", 0, 0

    transcript = "\n".join(f"{role.upper()}: {content}" for role, content in filtered)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
    try:
        token_count = len(ENCODING.encode(transcript))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("No se pudo contar tokens: %s", exc)
        token_count = len(transcript.split())

    return transcript, len(filtered), token_count


@dataclass
class ChatCandidate:
    chat_id: str
    user_id: str
    transcript: str
    message_count: int
    token_count: int
    updated_at: int  # epoch seconds from SQLite


def load_candidates(
    sqlite_path: Path,
    idle_seconds: int,
    min_messages: int,
    min_tokens: int,
) -> list[ChatCandidate]:
    rows = _load_sqlite_rows(sqlite_path, idle_seconds)
    candidates: list[ChatCandidate] = []

    for row in rows:
        chat_blob = row["chat"]
        messages = _extract_history_messages(chat_blob)
        transcript, msg_count, token_count = _flatten_text(messages)

        if msg_count < min_messages:
            continue
        if token_count < min_tokens:
            continue

        candidates.append(
            ChatCandidate(
                chat_id=row["id"],
                user_id=row["user_id"],
                transcript=transcript,
                message_count=msg_count,
                token_count=token_count,
                updated_at=row["updated_at"],
            )
        )

    return candidates


async def _call_openrouter(
    client: httpx.AsyncClient,
    api_key: str,
    model_id: str,
    prompt: str,
    transcript: str,
) -> str | None:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": prompt or DEFAULT_SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.2,
        "max_tokens": 180,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERRER,
        "X-Title": OPENROUTER_TITLE,
    }

    response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if content is None:
        return None

    normalized = " ".join(content.strip().split())
    if normalized.lower() in {"null", "none", "n/a", "na"}:
        return None

    # Enforce máximo 2 líneas.
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) > 2:
        lines = lines[:2]

    summary = "\n".join(lines)
    return summary or None


async def summarize_candidates(
    candidates: list[ChatCandidate],
    engine,
    settings,
    client: httpx.AsyncClient,
) -> int:
    """
    Procesa los candidatos de un usuario y guarda los resúmenes.
    Devuelve la cantidad de resúmenes guardados.
    """
    if not candidates:
        return 0

    existing = get_active_chat_ids(
        engine, settings.user_id, [c.chat_id for c in candidates]
    )

    saved = 0
    for candidate in candidates:
        summary_created_at = existing.get(candidate.chat_id)
        chat_updated_at = datetime.fromtimestamp(candidate.updated_at, tz=timezone.utc)

        if summary_created_at and summary_created_at.tzinfo is None:
            summary_created_at = summary_created_at.replace(tzinfo=timezone.utc)

        # Skip only if there is a summary newer or equal to the chat's last update.
        if summary_created_at and summary_created_at >= chat_updated_at:
            continue

        try:
            summary = await _call_openrouter(
                client=client,
                api_key=settings.api_key,
                model_id=settings.model_id,
                prompt=settings.prompt,
                transcript=candidate.transcript,
            )
        except httpx.HTTPStatusError as exc:
            log.error(
                "OpenRouter devolvió %s para chat %s (usuario %s): %s",
                exc.response.status_code,
                candidate.chat_id,
                settings.user_id,
                exc.response.text,
            )
            continue
        except Exception as exc:  # pragma: no cover - red defensiva
            log.error(
                "Fallo al resumir chat %s (usuario %s): %s",
                candidate.chat_id,
                settings.user_id,
                exc,
            )
            continue

        save_summary(
            engine=engine,
            chat_id=candidate.chat_id,
            user_id=settings.user_id,
            summary=summary,
            created_at=datetime.now(timezone.utc),
        )
        clear_chat_summaries_cache(settings.user_id)
        saved += 1
        log.info(
            "Resumen guardado para chat %s (usuario %s) - %s mensajes, %s tokens",
            candidate.chat_id,
            settings.user_id,
            candidate.message_count,
            candidate.token_count,
        )

    return saved


async def run_cycle(
    sqlite_path: Path,
    idle_seconds: int,
    min_messages: int,
    min_tokens: int,
) -> int:
    engine = get_memories_engine()
    if engine is None:
        log.warning("Base PostgreSQL externa no configurada; se omite el ciclo.")
        return 0

    settings = list_active_settings(engine)
    if not settings:
        log.info("No hay configuraciones activas para resúmenes; se omite el ciclo.")
        return 0

    candidates = load_candidates(sqlite_path, idle_seconds, min_messages, min_tokens)
    if not candidates:
        log.info("No se encontraron chats candidatos para resumir.")
        return 0

    candidates_by_user = {}
    for candidate in candidates:
        candidates_by_user.setdefault(candidate.user_id, []).append(candidate)

    saved_total = 0
    async with httpx.AsyncClient(timeout=45) as client:
        for setting in settings:
            user_candidates = candidates_by_user.get(setting.user_id, [])
            if not user_candidates:
                continue
            saved_total += await summarize_candidates(
                user_candidates, engine, setting, client
            )

    return saved_total


async def run_loop(
    sqlite_path: Path,
    idle_seconds: int,
    min_messages: int,
    min_tokens: int,
    interval_seconds: int,
):
    while True:
        await run_cycle(sqlite_path, idle_seconds, min_messages, min_tokens)
        await asyncio.sleep(interval_seconds)


def parse_args():
    parser = argparse.ArgumentParser(description="Servicio de resúmenes de chats")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=_detect_sqlite_path(),
        help="Ruta al webui.db de OpenWebUI",
    )
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=600,
        help="Tiempo mínimo de inactividad del chat antes de resumir (segundos).",
    )
    parser.add_argument(
        "--min-messages",
        type=int,
        default=4,
        help="Mínimo de mensajes (user/assistant) para generar resumen.",
    )
    parser.add_argument(
        "--min-tokens",
        type=int,
        default=200,
        help="Mínimo de tokens estimados para generar resumen.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Intervalo entre ciclos en modo loop (segundos).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta solo un ciclo y termina.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.sqlite_path.exists():
        log.error("No se encontró SQLite en %s", args.sqlite_path)
        raise SystemExit(1)

    log.info("Usando SQLite en %s", args.sqlite_path)
    log.info("Intervalo del cron: %ss", args.interval_seconds)

    if args.once:
        asyncio.run(
            run_cycle(
                sqlite_path=args.sqlite_path,
                idle_seconds=args.idle_seconds,
                min_messages=args.min_messages,
                min_tokens=args.min_tokens,
            )
        )
    else:
        asyncio.run(
            run_loop(
                sqlite_path=args.sqlite_path,
                idle_seconds=args.idle_seconds,
                min_messages=args.min_messages,
                min_tokens=args.min_tokens,
                interval_seconds=args.interval_seconds,
            )
        )


if __name__ == "__main__":
    main()
