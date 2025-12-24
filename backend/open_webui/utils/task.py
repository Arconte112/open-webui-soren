import logging
import math
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Any
from zoneinfo import ZoneInfo


from open_webui.utils.misc import get_last_user_message, get_messages_content

from open_webui.env import SRC_LOG_LEVELS
from open_webui.config import DEFAULT_RAG_TEMPLATE
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


MEMORIES_DATABASE_URL = "postgresql+psycopg2://postgres:zFns3MZAQNnZ2UVj3q41j1kJn0ORdfJ9qNjHU7skR7ev50Ugi9y7aGOsrFlBbQPs@5.78.120.77:5434/soren_openwebui"
_MEMORIES_ENGINE: Engine | None = None
MEMORIES_CACHE_TTL = int(os.getenv("MEMORIES_CACHE_TTL", "300"))
_MEMORIES_CACHE: tuple[float, str] | None = None
_MEMORIES_CACHE_WARMED: bool = False

# Chat summaries cache (per user)
CHAT_SUMMARIES_CACHE_TTL = int(os.getenv("CHAT_SUMMARIES_CACHE_TTL", "300"))
_CHAT_SUMMARIES_CACHE: dict[str, tuple[float, int, str]] = {}

# Scheduled tasks cache
SCHEDULED_TASKS_CACHE_TTL = int(os.getenv("SCHEDULED_TASKS_CACHE_TTL", "60"))
_SCHEDULED_TASKS_CACHE: tuple[float, str] | None = None

SANTO_DOMINGO_TZ = ZoneInfo("America/Santo_Domingo")


def get_memories_engine() -> Engine | None:
    global _MEMORIES_ENGINE
    if _MEMORIES_ENGINE is None:
        try:
            _MEMORIES_ENGINE = create_engine(MEMORIES_DATABASE_URL, pool_pre_ping=True)
        except Exception as exc:  # pragma: no cover - defensive guard
            log.error("Failed to create memories engine: %s", exc)
            _MEMORIES_ENGINE = None
    return _MEMORIES_ENGINE


def clear_memories_cache() -> None:
    global _MEMORIES_CACHE, _MEMORIES_CACHE_WARMED
    _MEMORIES_CACHE = None
    _MEMORIES_CACHE_WARMED = False


def clear_chat_summaries_cache(user_id: Optional[str] = None) -> None:
    if user_id:
        _CHAT_SUMMARIES_CACHE.pop(user_id, None)
    else:
        _CHAT_SUMMARIES_CACHE.clear()


def build_calendar_variable(months: int = 2) -> str:
    """
    Devuelve un calendario compacto desde hoy hacia adelante.
    Formato por mes: "Dic 2025: L1 M2 X3 J4 V5 S6 D7 | L8 ..."
    """
    today = date.today()
    month_abbr = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    weekday_abbr = ["L", "M", "X", "J", "V", "S", "D"]  # Monday first

    def month_year_offset(base: date, offset: int) -> tuple[int, int]:
        month = base.month - 1 + offset
        year = base.year + month // 12
        month = month % 12 + 1
        return year, month

    lines: list[str] = []

    for offset in range(months):
        year, month = month_year_offset(today, offset)
        first_day = date(year, month, 1)
        next_month_year, next_month = month_year_offset(first_day, 1)
        last_day = date(next_month_year, next_month, 1) - timedelta(days=1)

        start_day = today if (today.year == year and today.month == month) else first_day

        week_chunks: list[str] = []
        current_week: list[str] = []

        total_days = (last_day - start_day).days + 1
        for delta in range(total_days):
            current_day = start_day + timedelta(days=delta)

            # Split weeks when a new Monday arrives (except for the first day)
            if current_day.weekday() == 0 and current_week:
                week_chunks.append(" ".join(current_week))
                current_week = []

            current_week.append(f"{weekday_abbr[current_day.weekday()]}{current_day.day}")

        if current_week:
            week_chunks.append(" ".join(current_week))

        if week_chunks:
            line = f"{month_abbr[month - 1]} {year}: " + " | ".join(week_chunks)
            lines.append(line)

    return "\n".join(lines)


def build_memories_variable() -> str:
    global _MEMORIES_CACHE, _MEMORIES_CACHE_WARMED

    if _MEMORIES_CACHE is not None:
        cached_at, cached_value = _MEMORIES_CACHE
        if time.time() - cached_at < MEMORIES_CACHE_TTL:
            _MEMORIES_CACHE_WARMED = True
            return cached_value
        _MEMORIES_CACHE_WARMED = False

    engine = get_memories_engine()
    if engine is None:
        return ""

    try:
        with engine.connect() as conn:
            table_rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN ('memories', 'memory')"
                )
            )
            available_tables = {row[0] for row in table_rows}

            rows = []
            if "memories" in available_tables:
                result = conn.execute(
                    text(
                        "SELECT id, content, importance, category FROM memories "
                        "ORDER BY category, id"
                    )
                )
                rows = result.mappings().all()
            elif "memory" in available_tables:
                result = conn.execute(
                    text(
                        "SELECT id, content, NULL::text AS importance, NULL::text AS category "
                        "FROM memory ORDER BY id"
                    )
                )
                rows = result.mappings().all()
            else:
                log.warning("No memories table found in external database")
                _MEMORIES_CACHE = (time.time(), "")
                _MEMORIES_CACHE_WARMED = True
                return ""
    except SQLAlchemyError as exc:
        log.error("Failed to fetch memories: %s", exc)
        return ""

    if not rows:
        _MEMORIES_CACHE = (time.time(), "")
        _MEMORIES_CACHE_WARMED = True
        return ""

    grouped_memories: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        content = row.get("content")  # RowMapping supports get()
        category = row.get("category")
        memory_id = row.get("id")
        grouped_memories[str(category or "uncategorized")].append(
            (str(memory_id), str(content or ""))
        )

    sections = []
    for category, entries in grouped_memories.items():
        lines = [category]
        for memory_id, content in entries:
            lines.append(f"[ID:{memory_id}] {content}".strip())
        sections.append("\n".join(lines))

    result = "\n\n".join(sections)
    _MEMORIES_CACHE = (time.time(), result)
    _MEMORIES_CACHE_WARMED = True
    return result


def build_chat_summaries_variable(user_id: Optional[str]) -> str:
    """
    Devuelve un bloque de texto con los últimos resúmenes de chat del usuario.
    Usa la misma base externa que memorias.
    """
    if not user_id:
        return ""

    cached = _CHAT_SUMMARIES_CACHE.get(user_id)
    if cached and (time.time() - cached[0] < CHAT_SUMMARIES_CACHE_TTL):
        return cached[2]

    engine = get_memories_engine()
    if engine is None:
        return ""

    # Obtener límite personalizado
    try:
        with engine.connect() as conn:
            max_items_row = conn.execute(
                text(
                    "SELECT COALESCE(max_items, 20) FROM chat_summary_settings WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            ).first()
            max_items = int(max_items_row[0]) if max_items_row else 20
            if max_items < 1:
                max_items = 1
            if max_items > 200:
                max_items = 200
    except SQLAlchemyError as exc:
        log.error("Failed to fetch chat summary settings: %s", exc)
        max_items = 20

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT chat_id, COALESCE(summary, 'null') AS summary, created_at "
                    "FROM chat_summaries WHERE user_id = :user_id "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"user_id": user_id, "limit": max_items},
            ).all()
    except SQLAlchemyError as exc:
        log.error("Failed to fetch chat summaries: %s", exc)
        return ""

    if not rows:
        _CHAT_SUMMARIES_CACHE[user_id] = (time.time(), max_items, "")
        return ""

    lines = ["Chat Summaries:"]
    for idx, row in enumerate(rows, start=1):
        chat_id, summary, created_at = row
        stamp = (
            created_at.strftime("%Y-%m-%d %H:%M")
            if isinstance(created_at, datetime)
            else str(created_at)
        )
        lines.append(f"{idx}. {chat_id}: {summary} ({stamp})")

    result = "\n".join(lines)
    _CHAT_SUMMARIES_CACHE[user_id] = (time.time(), max_items, result)
    return result


def _convert_run_at_to_santo_domingo(run_at: Any) -> str:
    if not run_at:
        return ""

    dt = run_at
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return str(run_at)

    if not isinstance(dt, datetime):
        return str(run_at)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    local_dt = dt.astimezone(SANTO_DOMINGO_TZ)
    return local_dt.strftime("%Y-%m-%d %H:%M")


def _shorten_task_prompt(prompt: Any) -> str:
    if prompt is None:
        return ""

    text = str(prompt).replace("\r\n", "\n")
    first_line = text.split("\n", 1)[0]
    return first_line.rstrip()


def build_scheduled_tasks_pending_variable() -> str:
    global _SCHEDULED_TASKS_CACHE

    if _SCHEDULED_TASKS_CACHE is not None:
        cached_at, cached_value = _SCHEDULED_TASKS_CACHE
        if time.time() - cached_at < SCHEDULED_TASKS_CACHE_TTL:
            return cached_value

    engine = get_memories_engine()
    if engine is None:
        result = "<tareas_programadas_pendientes>\nNinguna\n</tareas_programadas_pendientes>"
        _SCHEDULED_TASKS_CACHE = (time.time(), result)
        return result

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, prompt, run_at, recurrence "
                    "FROM scheduled_tasks "
                    "WHERE status = 'pending' "
                    "ORDER BY run_at ASC"
                )
            ).mappings().all()
    except SQLAlchemyError as exc:
        log.error("Failed to fetch scheduled tasks: %s", exc)
        result = "<tareas_programadas_pendientes>\nNinguna\n</tareas_programadas_pendientes>"
        _SCHEDULED_TASKS_CACHE = (time.time(), result)
        return result

    lines = ["<tareas_programadas_pendientes>"]

    if not rows:
        lines.append("Ninguna")
    else:
        for row in rows:
            run_at = _convert_run_at_to_santo_domingo(row.get("run_at"))
            description = _shorten_task_prompt(row.get("prompt"))
            lines.append(f"- {run_at} | {description}".rstrip())

    lines.append("</tareas_programadas_pendientes>")
    result = "\n".join(lines)
    _SCHEDULED_TASKS_CACHE = (time.time(), result)
    return result


def is_memories_cache_warm() -> bool:
    if _MEMORIES_CACHE is None or not _MEMORIES_CACHE_WARMED:
        return False
    cached_at, _ = _MEMORIES_CACHE
    return time.time() - cached_at < MEMORIES_CACHE_TTL

def get_task_model_id(
    default_model_id: str, task_model: str, task_model_external: str, models
) -> str:
    # Set the task model
    task_model_id = default_model_id
    # Check if the user has a custom task model and use that model
    if models[task_model_id].get("connection_type") == "local":
        if task_model and task_model in models:
            task_model_id = task_model
    else:
        if task_model_external and task_model_external in models:
            task_model_id = task_model_external

    return task_model_id


def prompt_variables_template(template: str, variables: dict[str, str]) -> str:
    for variable, value in variables.items():
        template = template.replace(variable, value)
    return template


def prompt_template(template: str, user: Optional[Any] = None) -> str:

    USER_VARIABLES = {}

    if user:
        if hasattr(user, "model_dump"):
            user = user.model_dump()

        if isinstance(user, dict):
            user_info = user.get("info", {}) or {}
            birth_date = user.get("date_of_birth")
            age = None

            if birth_date:
                try:
                    # If birth_date is str, convert to datetime
                    if isinstance(birth_date, str):
                        birth_date = datetime.strptime(birth_date, "%Y-%m-%d")

                    today = datetime.now()
                    age = (
                        today.year
                        - birth_date.year
                        - (
                            (today.month, today.day)
                            < (birth_date.month, birth_date.day)
                        )
                    )
                except Exception as e:
                    pass

            USER_VARIABLES = {
                "name": str(user.get("name")),
                "location": str(user_info.get("location")),
                "bio": str(user.get("bio")),
                "gender": str(user.get("gender")),
                "birth_date": str(birth_date),
                "age": str(age),
            }

    # Get the current date
    current_date = datetime.now()

    # Format the date to YYYY-MM-DD
    formatted_date = current_date.strftime("%Y-%m-%d")
    formatted_time = current_date.strftime("%I:%M:%S %p")
    formatted_weekday = current_date.strftime("%A")

    template = template.replace("{{CURRENT_DATE}}", formatted_date)
    template = template.replace("{{CURRENT_TIME}}", formatted_time)
    template = template.replace(
        "{{CURRENT_DATETIME}}", f"{formatted_date} {formatted_time}"
    )
    template = template.replace("{{CURRENT_WEEKDAY}}", formatted_weekday)

    template = template.replace("{{USER_NAME}}", USER_VARIABLES.get("name", "Unknown"))
    template = template.replace("{{USER_BIO}}", USER_VARIABLES.get("bio", "Unknown"))
    template = template.replace(
        "{{USER_GENDER}}", USER_VARIABLES.get("gender", "Unknown")
    )
    template = template.replace(
        "{{USER_BIRTH_DATE}}", USER_VARIABLES.get("birth_date", "Unknown")
    )
    template = template.replace(
        "{{USER_AGE}}", str(USER_VARIABLES.get("age", "Unknown"))
    )
    template = template.replace(
        "{{USER_LOCATION}}", USER_VARIABLES.get("location", "Unknown")
    )

    # Chat summaries variable (per user)
    user_id = None
    if user:
        if isinstance(user, dict):
            user_id = user.get("id") or user.get("user_id")
        else:
            user_id = getattr(user, "id", None)

    if "{{CHAT_SUMMARIES}}" in template:
        template = template.replace(
            "{{CHAT_SUMMARIES}}", build_chat_summaries_variable(user_id)
        )

    if "{{SCHEDULED_TASKS_PENDING}}" in template or "{{TAREAS_PROGRAMADAS_PENDIENTES}}" in template:
        scheduled_tasks = build_scheduled_tasks_pending_variable()
        template = template.replace("{{SCHEDULED_TASKS_PENDING}}", scheduled_tasks)
        template = template.replace(
            "{{TAREAS_PROGRAMADAS_PENDIENTES}}", scheduled_tasks
        )

    if "{{CALENDAR}}" in template or "{{CALENDARIO}}" in template:
        calendar_value = build_calendar_variable()
        template = template.replace("{{CALENDAR}}", calendar_value)
        template = template.replace("{{CALENDARIO}}", calendar_value)

    if "{{MEMORIES}}" in template:
        template = template.replace("{{MEMORIES}}", build_memories_variable())

    return template


def replace_prompt_variable(template: str, prompt: str) -> str:
    def replacement_function(match):
        full_match = match.group(
            0
        ).lower()  # Normalize to lowercase for consistent handling
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)

        if full_match == "{{prompt}}":
            return prompt
        elif start_length is not None:
            return prompt[: int(start_length)]
        elif end_length is not None:
            return prompt[-int(end_length) :]
        elif middle_length is not None:
            middle_length = int(middle_length)
            if len(prompt) <= middle_length:
                return prompt
            start = prompt[: math.ceil(middle_length / 2)]
            end = prompt[-math.floor(middle_length / 2) :]
            return f"{start}...{end}"
        return ""

    # Updated regex pattern to make it case-insensitive with the `(?i)` flag
    pattern = r"(?i){{prompt}}|{{prompt:start:(\d+)}}|{{prompt:end:(\d+)}}|{{prompt:middletruncate:(\d+)}}"
    template = re.sub(pattern, replacement_function, template)
    return template


def replace_messages_variable(
    template: str, messages: Optional[list[dict]] = None
) -> str:
    def replacement_function(match):
        full_match = match.group(0)
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)
        # If messages is None, handle it as an empty list
        if messages is None:
            return ""

        # Process messages based on the number of messages required
        if full_match == "{{MESSAGES}}":
            return get_messages_content(messages)
        elif start_length is not None:
            return get_messages_content(messages[: int(start_length)])
        elif end_length is not None:
            return get_messages_content(messages[-int(end_length) :])
        elif middle_length is not None:
            mid = int(middle_length)

            if len(messages) <= mid:
                return get_messages_content(messages)
            # Handle middle truncation: split to get start and end portions of the messages list
            half = mid // 2
            start_msgs = messages[:half]
            end_msgs = messages[-half:] if mid % 2 == 0 else messages[-(half + 1) :]
            formatted_start = get_messages_content(start_msgs)
            formatted_end = get_messages_content(end_msgs)
            return f"{formatted_start}\n{formatted_end}"
        return ""

    template = re.sub(
        r"{{MESSAGES}}|{{MESSAGES:START:(\d+)}}|{{MESSAGES:END:(\d+)}}|{{MESSAGES:MIDDLETRUNCATE:(\d+)}}",
        replacement_function,
        template,
    )

    return template


# {{prompt:middletruncate:8000}}


def rag_template(template: str, context: str, query: str):
    if template.strip() == "":
        template = DEFAULT_RAG_TEMPLATE

    template = prompt_template(template)

    if "[context]" not in template and "{{CONTEXT}}" not in template:
        log.debug(
            "WARNING: The RAG template does not contain the '[context]' or '{{CONTEXT}}' placeholder."
        )

    if "<context>" in context and "</context>" in context:
        log.debug(
            "WARNING: Potential prompt injection attack: the RAG "
            "context contains '<context>' and '</context>'. This might be "
            "nothing, or the user might be trying to hack something."
        )

    query_placeholders = []
    if "[query]" in context:
        query_placeholder = "{{QUERY" + str(uuid.uuid4()) + "}}"
        template = template.replace("[query]", query_placeholder)
        query_placeholders.append((query_placeholder, "[query]"))

    if "{{QUERY}}" in context:
        query_placeholder = "{{QUERY" + str(uuid.uuid4()) + "}}"
        template = template.replace("{{QUERY}}", query_placeholder)
        query_placeholders.append((query_placeholder, "{{QUERY}}"))

    template = template.replace("[context]", context)
    template = template.replace("{{CONTEXT}}", context)

    template = template.replace("[query]", query)
    template = template.replace("{{QUERY}}", query)

    for query_placeholder, original_placeholder in query_placeholders:
        template = template.replace(query_placeholder, original_placeholder)

    return template


def title_generation_template(
    template: str, messages: list[dict], user: Optional[Any] = None
) -> str:

    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)

    return template


def follow_up_generation_template(
    template: str, messages: list[dict], user: Optional[Any] = None
) -> str:
    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)
    return template


def tags_generation_template(
    template: str, messages: list[dict], user: Optional[Any] = None
) -> str:
    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)
    return template


def image_prompt_generation_template(
    template: str, messages: list[dict], user: Optional[Any] = None
) -> str:
    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)
    return template


def emoji_generation_template(
    template: str, prompt: str, user: Optional[Any] = None
) -> str:
    template = replace_prompt_variable(template, prompt)
    template = prompt_template(template, user)

    return template


def autocomplete_generation_template(
    template: str,
    prompt: str,
    messages: Optional[list[dict]] = None,
    type: Optional[str] = None,
    user: Optional[Any] = None,
) -> str:
    template = template.replace("{{TYPE}}", type if type else "")
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)
    return template


def query_generation_template(
    template: str, messages: list[dict], user: Optional[Any] = None
) -> str:
    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, user)
    return template


def moa_response_generation_template(
    template: str, prompt: str, responses: list[str]
) -> str:
    def replacement_function(match):
        full_match = match.group(0)
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)

        if full_match == "{{prompt}}":
            return prompt
        elif start_length is not None:
            return prompt[: int(start_length)]
        elif end_length is not None:
            return prompt[-int(end_length) :]
        elif middle_length is not None:
            middle_length = int(middle_length)
            if len(prompt) <= middle_length:
                return prompt
            start = prompt[: math.ceil(middle_length / 2)]
            end = prompt[-math.floor(middle_length / 2) :]
            return f"{start}...{end}"
        return ""

    template = re.sub(
        r"{{prompt}}|{{prompt:start:(\d+)}}|{{prompt:end:(\d+)}}|{{prompt:middletruncate:(\d+)}}",
        replacement_function,
        template,
    )

    responses = [f'"""{response}"""' for response in responses]
    responses = "\n\n".join(responses)

    template = template.replace("{{responses}}", responses)
    return template


def tools_function_calling_generation_template(template: str, tools_specs: str) -> str:
    template = template.replace("{{TOOLS}}", tools_specs)
    return template
