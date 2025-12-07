# Almacenamiento de chats y mensajes en OpenWebUI

## 1. Tablas relevantes
- `chat`: conversación persistida por usuario; guarda historial completo en un JSON (`chat`).
- `chatidtag`: relación chat ↔ etiqueta (tag_name) por usuario; timestamp para ordenamiento.
- `message`: mensajes tipo “canal” (feeds/thread); opcional `channel_id`, replies y pins.
- `message_reaction`: reacciones por mensaje.
- `channel`, `channel_member`, `channel_webhook`: contenedores y membresías para los mensajes de `message`.
- `user`: referencia de propietarios/participantes (no hay FK declaradas).

## 2. Esquemas de tablas (SQLite, `backend/data/webui.db`)

### `chat`
- `id` TEXT PK
- `user_id` TEXT
- `title` TEXT
- `chat` JSON (historial y metadatos, ver sección 3)
- `created_at`, `updated_at` BIGINT (epoch segundos)
- `share_id` TEXT
- `archived` BOOLEAN
- `pinned` BOOLEAN
- `meta` JSON (default `{}`) — incluye `tags` opcionales
- `folder_id` TEXT

### `chatidtag`
- `id` TEXT
- `tag_name` TEXT
- `chat_id` TEXT
- `user_id` TEXT
- `timestamp` INTEGER (epoch segundos)

### `message`
- `id` TEXT PK
- `user_id` TEXT
- `channel_id` TEXT (nullable)
- `reply_to_id` TEXT (mensaje citado)
- `parent_id` TEXT (thread root)
- `is_pinned` BOOLEAN, `pinned_at`, `pinned_by`
- `content` TEXT
- `data` JSON, `meta` JSON
- `created_at`, `updated_at` BIGINT (epoch **nanosegundos**) usados para orden

### `message_reaction`
- `id` TEXT PK
- `user_id` TEXT
- `message_id` TEXT
- `name` TEXT (emoji/shortcode)
- `created_at` BIGINT (epoch ns)

### `channel`
- `id` TEXT PK; `user_id` creador
- `type` TEXT (p.ej. `group`), `name`, `description`
- `is_private` BOOLEAN
- `data`, `meta`, `access_control` JSON
- `created_at`, `updated_at`, `archived_at`, `deleted_at` BIGINT (epoch ns); `updated_by`, `archived_by`, `deleted_by`

### `channel_member`
- `id` TEXT PK; `channel_id`, `user_id`
- `role`, `status`
- `is_active`, `is_channel_muted`, `is_channel_pinned` BOOLEAN
- `data`, `meta` JSON
- `invited_at`, `invited_by`, `joined_at`, `left_at`, `last_read_at`, `created_at`, `updated_at` BIGINT (epoch ns)

## 3. Estructura del JSON `chat.chat`
Claves superiores observadas: `id`, `title`, `models`, `params`, `history`, `messages`, `tags`, `timestamp`, `files`.

- `history.messages`: diccionario `{messageId: messageObject}` con árbol vía `parentId`/`childrenIds`; `currentId` apunta al último.
- `messages`: lista ordenada de los mismos objetos (timeline lineal).
- Cada `messageObject` incluye:
  - `id`, `parentId`, `childrenIds[]`
  - `role`: `user` | `assistant`
  - `content`: texto/HTML (puede contener bloques `<details>` para reasoning)
  - `timestamp`: epoch segundos
  - `models` (user) o `model`/`modelName`/`modelIdx` (assistant)
  - opcionales: `usage` {prompt_tokens, completion_tokens, total_tokens}, `done` (bool), `followUps`[], `lastSentence`, `files`.

Ejemplo resumido (recortado):
```json
{
  "id": "b0b5edf9-...",
  "role": "assistant",
  "content": "<details ...>...",
  "timestamp": 1759405989,
  "model": "soren",
  "usage": {"prompt_tokens": 6200, "completion_tokens": 489}
}
```

## 4. Relaciones y flujos
- Conversaciones “clásicas” del chat principal viven en `chat.chat` (JSON). No se normalizan en `message`.
- Etiquetas: `chat.meta.tags` y filas en `chatidtag` permiten filtrar por tag/usuario.
- Compartir/duplicar: `chat.share_id` apunta al chat compartido (se crea como nuevo `chat` con `user_id = "shared-{id}"`).
- Ordenación UI: listado por `chat.updated_at DESC`; archivos se cargan por `id`.
- Para canales (feeds/thread), los mensajes se insertan en `message` ligados a `channel_id`; replies usan `parent_id` y `reply_to_id`.

## 5. Campos recomendados como cursor
- Chats JSON: usar `history.messages.*.timestamp` creciente dentro de cada chat; para saber si el chat cambió, observar `chat.updated_at` (epoch segundos) o `history.currentId`.
- Mensajes de canal (`message`): usar `created_at` (epoch **nanosegundos**) ascendente; en empates, `id` como desempate estable. `updated_at` refleja ediciones.

## 6. Notas prácticas
- No hay claves foráneas activas en SQLite; validar integridad en la aplicación.
- El JSON se duplica en mapa (`history.messages`) y lista (`messages`); la lista mantiene el orden cronológico mostrado.
- El `message` table estaba vacío en este entorno, indicando que el flujo principal actual sigue usando el JSON embebido en `chat`.
