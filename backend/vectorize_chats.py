#!/usr/bin/env python3
"""
Vectorización de pares usuario→asistente para RAG.

Ejecución típica (cron/Coolify):
    PYTHONPATH=. python vectorize_chats.py --once
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

# Asegura imports de open_webui cuando se ejecuta desde backend/
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Garantiza WEBUI_SECRET_KEY antes de importar módulos que lo requieren.
if not os.environ.get("WEBUI_SECRET_KEY"):
    secret_path = CURRENT_DIR / ".webui_secret_key"
    try:
        os.environ["WEBUI_SECRET_KEY"] = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        os.environ.setdefault("WEBUI_SECRET_KEY", "t0p-s3cr3t")

from open_webui.chat_summaries.repository import list_active_settings  # noqa: E402
from open_webui.utils.misc import get_content_from_message, get_message_list  # noqa: E402
from open_webui.utils.task import get_memories_engine  # noqa: E402

import re

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
log = logging.getLogger("chat_vectorizer")


def _setup_logger() -> None:
    """
    Configura un handler directo a stdout aun si ya hay logging global.
    """
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    log.addHandler(handler)
    log.propagate = False

OPENROUTER_EMBED_URL = os.getenv(
    "OPENROUTER_EMBEDDINGS_URL", "https://openrouter.ai/api/v1/embeddings"
)
OPENROUTER_REFERRER = os.getenv(
    "OPENROUTER_REFERRER", "https://soren-openwebui.local"
)
OPENROUTER_TITLE = os.getenv("OPENROUTER_TITLE", "soren-openwebui vectorizer")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
BATCH_SIZE_ENV = int(os.getenv("VECTORIZE_BATCH_SIZE", "10"))
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
MAX_TEXT_CHARS = int(os.getenv("VECTORIZE_MAX_TEXT_CHARS", "6000"))


# --------------------------- helpers de datos --------------------------- #
@dataclass
class MessagePair:
    chat_id: str
    pair_hash: str
    user_content: str
    assistant_content: str
    combined_text: str
    message_timestamp: datetime | None


def _detect_sqlite_path() -> Path:
    env_path = os.getenv("CHAT_DB_PATH")
    if env_path:
        return Path(env_path)

    candidates = [
        Path("/app/backend/data/webui.db"),
        CURRENT_DIR.parent / "data" / "webui.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def _load_chats(sqlite_path: Path, last_cursor: int | None) -> list[sqlite3.Row]:
    query = "SELECT id, chat, updated_at FROM chat"
    params: Sequence[int] = []
    if last_cursor is not None:
        query += " WHERE updated_at > ?"
        params = [last_cursor]
    query += " ORDER BY updated_at ASC"

    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(query, params))


def _parse_chat_blob(raw_chat) -> dict | None:
    if raw_chat is None:
        return None
    if isinstance(raw_chat, dict):
        return raw_chat
    if isinstance(raw_chat, str):
        try:
            return json.loads(raw_chat)
        except json.JSONDecodeError:
            log.warning("Chat JSON inválido; se omite.")
            return None
    return None


def _extract_messages(chat_blob: dict | None) -> list[dict]:
    if not chat_blob:
        return []
    history = chat_blob.get("history") or {}
    messages_map = history.get("messages") or {}
    current_id = history.get("currentId") or history.get("current_id")
    if messages_map and current_id:
        return get_message_list(messages_map, current_id)
    if isinstance(chat_blob.get("messages"), list):
        return chat_blob.get("messages")  # type: ignore[return-value]
    return []


def _safe_content(message: dict) -> str:
    content = get_content_from_message(message) or message.get("content") or ""
    return str(content).strip()


_DETAILS_PATTERN = re.compile(
    r"<details[^>]*type=\"(?:reasoning|tool_calls?|tool_results?)\"[^>]*>.*?</details>",
    re.IGNORECASE | re.DOTALL,
)


def _clean_assistant_content(raw: str) -> str:
    """Quita bloques de reasoning y tool_calls para dejar solo el texto útil."""
    if not raw:
        return ""
    cleaned = _DETAILS_PATTERN.sub("", raw)
    # Colapsa espacios en blanco excesivos.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _truncate_text(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[: MAX_TEXT_CHARS]


def _to_datetime(ts) -> datetime | None:
    if ts is None:
        return None
    try:
        val = float(ts)
        # Maneja segundos o milisegundos.
        if val > 1e12:  # ms
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    except Exception:
        return None


def _hash_pair(chat_id: str, user_msg: dict, assistant_msg: dict, user_content: str, assistant_content: str) -> str:
    import hashlib

    raw = "|".join(
        [
            chat_id,
            str(user_msg.get("id") or ""),
            str(assistant_msg.get("id") or ""),
            str(user_msg.get("timestamp") or ""),
            str(assistant_msg.get("timestamp") or ""),
            user_content,
            assistant_content,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pair_messages(chat_id: str, messages: Iterable[dict]) -> list[MessagePair]:
    pairs: list[MessagePair] = []
    pending_user: dict | None = None

    for message in messages:
        role = (message.get("role") or "").lower()
        if role == "user":
            pending_user = message
            continue
        if role == "assistant" and pending_user:
            user_content = _safe_content(pending_user)
            assistant_content = _clean_assistant_content(_safe_content(message))
            if not user_content or not assistant_content:
                pending_user = None
                continue

            combined = f"Usuario: {user_content}\nAsistente: {assistant_content}"
            combined = _truncate_text(combined)
            pair_hash = _hash_pair(chat_id, pending_user, message, user_content, assistant_content)
            message_ts = _to_datetime(message.get("timestamp") or pending_user.get("timestamp"))

            pairs.append(
                MessagePair(
                    chat_id=chat_id,
                    pair_hash=pair_hash,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    combined_text=combined,
                    message_timestamp=message_ts,
                )
            )
            pending_user = None

    return pairs


# --------------------------- Postgres helpers --------------------------- #
CREATE_TABLES_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chat_embeddings (
    id BIGSERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    message_pair_hash TEXT NOT NULL UNIQUE,
    user_content TEXT,
    assistant_content TEXT,
    combined_text TEXT NOT NULL,
    embedding VECTOR({EMBED_DIM}) NOT NULL,
    message_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_embeddings_chat_idx ON chat_embeddings (chat_id);
CREATE INDEX IF NOT EXISTS chat_embeddings_msg_ts_idx ON chat_embeddings (message_timestamp);
CREATE INDEX IF NOT EXISTS chat_embeddings_vec_idx ON chat_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS vectorization_cursor (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    last_updated_at BIGINT,
    last_run_at TIMESTAMPTZ
);

INSERT INTO vectorization_cursor (id, last_run_at)
VALUES (1, NOW())
ON CONFLICT (id) DO NOTHING;
"""


def ensure_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLES_SQL))


def get_last_cursor(engine: Engine) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT last_updated_at FROM vectorization_cursor WHERE id = 1")
        ).first()
        if not row:
            return None
        return row[0]


def update_cursor(engine: Engine, last_updated_at: int | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO vectorization_cursor (id, last_updated_at, last_run_at) "
                "VALUES (1, :last_updated_at, NOW()) "
                "ON CONFLICT (id) DO UPDATE SET "
                "last_updated_at = EXCLUDED.last_updated_at, "
                "last_run_at = NOW()"
            ),
            {"last_updated_at": last_updated_at},
        )


def find_existing_hashes(engine: Engine, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    query = text(
        "SELECT message_pair_hash FROM chat_embeddings WHERE message_pair_hash IN :hashes"
    ).bindparams(bindparam("hashes", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(query, {"hashes": hashes}).scalars().all()
        return set(rows)


def _embedding_to_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def insert_embeddings(engine: Engine, pairs: list[MessagePair], embeddings: list[list[float]]) -> int:
    """
    Inserta uno a uno para evitar problemas de placeholder con ejecutemany y facilitar logs de error.
    """
    if not pairs:
        return 0
    if len(pairs) != len(embeddings):
        raise ValueError("El número de embeddings no coincide con los pares.")

    sql = text(
        "INSERT INTO chat_embeddings (chat_id, message_pair_hash, user_content, assistant_content, combined_text, embedding, message_timestamp) "
        "VALUES (:chat_id, :message_pair_hash, :user_content, :assistant_content, :combined_text, CAST(:embedding AS vector), :message_timestamp) "
        "ON CONFLICT (message_pair_hash) DO NOTHING"
    )

    inserted = 0
    with engine.begin() as conn:
        for pair, emb in zip(pairs, embeddings):
            payload = {
                "chat_id": pair.chat_id,
                "message_pair_hash": pair.pair_hash,
                "user_content": pair.user_content,
                "assistant_content": pair.assistant_content,
                "combined_text": pair.combined_text,
                "embedding": _embedding_to_literal(emb),
                "message_timestamp": pair.message_timestamp,
            }
            try:
                res = conn.execute(sql, payload)
                inserted += res.rowcount or 0
            except Exception as exc:  # pragma: no cover - logging
                log.error("Fallo insert hash %s: %s", pair.pair_hash, exc)
    return inserted


# --------------------------- OpenRouter embeddings --------------------------- #
def resolve_api_key(engine: Engine | None) -> str:
    env_key = os.getenv("OPENROUTER_API_KEY")
    if env_key:
        log.info("Usando OPENROUTER_API_KEY de entorno")
        return env_key

    if engine is None:
        raise RuntimeError("No se pudo obtener API key de OpenRouter (sin engine y sin variable de entorno).")

    settings = list_active_settings(engine)
    for setting in settings:
        if setting.api_key:
            log.info("Usando API key de chat_summary_settings (usuario %s)", setting.user_id)
            return setting.api_key

    raise RuntimeError("No se encontró API key de OpenRouter; defina OPENROUTER_API_KEY o configure resúmenes de chat.")


def fetch_embeddings(
    client: httpx.Client,
    api_key: str,
    texts: list[str],
    model: str,
) -> list[list[float]]:
    payload = {"model": model, "input": texts}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERRER,
        "X-Title": OPENROUTER_TITLE,
    }

    response = client.post(OPENROUTER_EMBED_URL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    embeddings = [item.get("embedding") for item in data.get("data", [])]
    if len(embeddings) != len(texts):
        raise RuntimeError(
            f"OpenRouter devolvió {len(embeddings)} embeddings para {len(texts)} textos"
        )
    return embeddings


# --------------------------- ciclo principal --------------------------- #
def chunked(seq: list[MessagePair], size: int) -> Iterable[list[MessagePair]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def run_once(sqlite_path: Path, batch_size: int) -> None:
    engine = get_memories_engine()
    if engine is None:
        log.error("Base PostgreSQL externa no configurada; abortando.")
        raise SystemExit(1)

    ensure_tables(engine)
    api_key = resolve_api_key(engine)
    last_cursor = get_last_cursor(engine)

    if not sqlite_path.exists():
        log.error("SQLite no encontrado en %s", sqlite_path)
        raise SystemExit(1)

    try:
        db_size = sqlite_path.stat().st_size
    except OSError:
        db_size = -1
    log.info("Usando SQLite en %s (size=%s bytes)", sqlite_path, db_size)

    rows = _load_chats(sqlite_path, last_cursor)
    if not rows:
        log.info("No hay chats para vectorizar (cursor=%s).", last_cursor)
        update_cursor(engine, last_cursor)
        return

    log.info("Cargando %s chats desde %s (cursor=%s)", len(rows), sqlite_path, last_cursor)

    all_pairs: list[MessagePair] = []
    max_updated_at = last_cursor or 0
    chats_with_pairs = 0
    for row in rows:
        chat_blob = _parse_chat_blob(row["chat"])
        messages = _extract_messages(chat_blob)
        pairs = _pair_messages(row["id"], messages)
        if pairs:
            all_pairs.extend(pairs)
            chats_with_pairs += 1
            log.info("Chat %s: %s pares generados", row["id"], len(pairs))
        else:
            log.info("Chat %s: sin pares user→assistant detectados", row["id"])
        if row["updated_at"] and row["updated_at"] > max_updated_at:
            max_updated_at = row["updated_at"]

    if not all_pairs:
        log.info("No se encontraron pares user→assistant nuevos.")
        update_cursor(engine, max_updated_at if max_updated_at else last_cursor)
        return
    log.info("Total pares a vectorizar: %s (en %s chats)", len(all_pairs), chats_with_pairs)

    inserted_total = 0
    skipped_existing = 0
    error_batches = 0
    start_time = time.time()

    log.info("Procesando %s batches (tam=%s) contra OpenRouter %s", (len(all_pairs) + batch_size - 1) // batch_size, batch_size, EMBEDDING_MODEL)

    with httpx.Client(timeout=60) as client:
        for batch in chunked(all_pairs, batch_size):
            hashes = [p.pair_hash for p in batch]
            existing = find_existing_hashes(engine, hashes)
            pending = [p for p in batch if p.pair_hash not in existing]
            skipped_existing += len(batch) - len(pending)
            if not pending:
                continue

            try:
                embeds = fetch_embeddings(
                    client=client,
                    api_key=api_key,
                    texts=[p.combined_text for p in pending],
                    model=EMBEDDING_MODEL,
                )
                inserted = insert_embeddings(engine, pending, embeds)
                inserted_total += inserted
                log.info(
                    "Batch %s items: nuevos=%s, duplicados=%s, insertados=%s",
                    len(batch),
                    len(pending),
                    len(batch) - len(pending),
                    inserted,
                )
            except httpx.HTTPStatusError as exc:
                log.error(
                    "OpenRouter devolvió %s: %s",
                    exc.response.status_code,
                    exc.response.text,
                )
                error_batches += 1
            except Exception as exc:  # pragma: no cover - defensivo
                log.error("Error al procesar batch: %s", exc)
                error_batches += 1

    elapsed = time.time() - start_time
    log.info(
        "Vectorización finalizada. Pairs=%s, insertados=%s, duplicados=%s, batches con error=%s, elapsed=%.2fs",
        len(all_pairs),
        inserted_total,
        skipped_existing,
        error_batches,
        elapsed,
    )

    update_cursor(engine, max_updated_at if inserted_total or skipped_existing else last_cursor)


def parse_args():
    parser = argparse.ArgumentParser(description="Vectoriza chats de OpenWebUI en pgvector.")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=_detect_sqlite_path(),
        help="Ruta al webui.db (SQLite).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE_ENV,
        help="Cantidad de pares por request de embedding (default 50).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta un solo ciclo y termina (modo cron).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Intervalo entre ciclos en modo loop.",
    )
    return parser.parse_args()


def main():
    _setup_logger()
    args = parse_args()
    if not args.sqlite_path.exists():
        log.error("SQLite no encontrado en %s", args.sqlite_path)
        raise SystemExit(1)

    log.info("Modelo de embedding: %s | Batch size: %s", EMBEDDING_MODEL, args.batch_size)

    if args.once:
        run_once(args.sqlite_path, args.batch_size)
    else:
        while True:
            run_once(args.sqlite_path, args.batch_size)
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
