import logging
import time
import uuid
import json
import asyncio
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from open_webui.env import SRC_LOG_LEVELS, AIOHTTP_CLIENT_SESSION_SSL
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.models import Models
from open_webui.models.tools import Tools
from open_webui.models.groups import Groups
from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import has_access
from open_webui.utils.misc import get_message_list
from open_webui.utils.tools import get_tool_servers
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

DEFAULT_MODEL_ID = "soren"
API_TOKEN = "sk-7e7b1636fec4427a8e1cfe9a217984a1"
SANTO_DOMINGO_TZ = ZoneInfo("America/Santo_Domingo")
SOREN_DAILY_DATE_KEY = "soren_daily_date"
SOREN_DAILY_SOURCE_KEY = "soren_daily_source"
SOREN_DAILY_SOURCE_VALUE = "soren_endpoint"


class SorenCallBody(BaseModel):
    prompt: str = Field(..., min_length=1, description="Mensaje para el asistente Soren.")
    wait_for_response: bool = Field(
        default=False,
        description="Si es true, espera la respuesta final y la devuelve en 'response.content'.",
    )


def _get_model_id(request: Request) -> str:
    try:
        value = request.app.state.config.SCHEDULED_TASK_MODEL
        return value or DEFAULT_MODEL_ID
    except Exception:
        return DEFAULT_MODEL_ID


def build_history(prompt: str, assistant_id: str, model_id: str) -> dict:
    user_message_id = str(uuid.uuid4())
    timestamp = int(time.time())

    user_message = {
        "id": user_message_id,
        "parentId": None,
        "childrenIds": [assistant_id],
        "role": "user",
        "content": prompt,
        "timestamp": timestamp,
        "models": [model_id],
    }

    assistant_message = {
        "id": assistant_id,
        "parentId": user_message_id,
        "childrenIds": [],
        "role": "assistant",
        "content": "",
        "model": model_id,
        "modelName": model_id,
        "modelIdx": 0,
        "timestamp": timestamp,
    }

    history = {
        "messages": {
            user_message_id: user_message,
            assistant_id: assistant_message,
        },
        "currentId": assistant_id,
    }

    message_path = [user_message, assistant_message]

    return {
        "history": history,
        "messages": message_path,
        "user_message_id": user_message_id,
    }


def _get_santo_domingo_date_str() -> str:
    return datetime.now(SANTO_DOMINGO_TZ).strftime("%Y-%m-%d")


def _find_today_soren_chat(user_id: str, date_key: str):
    try:
        chats = Chats.get_chat_list_by_user_id(
            user_id, include_archived=False, skip=0, limit=100
        )
    except Exception:
        return None

    for chat in chats:
        chat_data = chat.chat or {}
        if (
            chat_data.get(SOREN_DAILY_DATE_KEY) == date_key
            and chat_data.get(SOREN_DAILY_SOURCE_KEY) == SOREN_DAILY_SOURCE_VALUE
        ):
            return chat

    return None


def _append_history_to_chat(chat_data: dict, prompt: str, model_id: str) -> dict:
    history = chat_data.get("history") or {}
    messages_map = history.get("messages") or {}

    if not isinstance(messages_map, dict):
        messages_map = {}

    current_id = history.get("currentId")

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    timestamp = int(time.time())

    user_message = {
        "id": user_message_id,
        "parentId": current_id,
        "childrenIds": [assistant_message_id],
        "role": "user",
        "content": prompt,
        "timestamp": timestamp,
        "models": [model_id],
    }

    assistant_message = {
        "id": assistant_message_id,
        "parentId": user_message_id,
        "childrenIds": [],
        "role": "assistant",
        "content": "",
        "model": model_id,
        "modelName": model_id,
        "modelIdx": 0,
        "timestamp": timestamp,
    }

    if current_id and current_id in messages_map:
        parent_message = messages_map[current_id]
        children = parent_message.get("childrenIds") or []
        if user_message_id not in children:
            children.append(user_message_id)
        parent_message["childrenIds"] = children
        messages_map[current_id] = parent_message

    messages_map[user_message_id] = user_message
    messages_map[assistant_message_id] = assistant_message

    history["messages"] = messages_map
    history["currentId"] = assistant_message_id
    chat_data["history"] = history
    chat_data["messages"] = get_message_list(messages_map, assistant_message_id)
    chat_data["timestamp"] = int(time.time() * 1000)

    return {
        "chat": chat_data,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


def extract_tool_ids(model_meta: Optional[dict], model_params: Optional[dict]) -> list[str]:
    """
    Extract tool identifiers from model metadata/params.
    """
    candidates: list[str] = []

    for source in (model_meta, model_params):
        if isinstance(source, dict):
            tool_ids = source.get("toolIds") or source.get("tools") or []
            if isinstance(tool_ids, list):
                candidates.extend(tool_ids)

    # Preserve order while removing duplicates
    return list(dict.fromkeys(candidates))


async def _get_accessible_tool_ids(request: Request, user) -> list[str]:
    """
    Build the default toolIds list for scheduled tasks:
    - local workspace tools readable by the user
    - enabled OpenAPI tool servers (server:<id>)
    - MCP tool servers (server:mcp:<id>)
    with the same access-control filtering as /api/v1/tools.
    """
    local_ids = [tool.id for tool in Tools.get_tools_by_user_id(user.id, "read")]

    tool_server_ids: list[str] = []
    mcp_server_ids: list[str] = []

    # OpenAPI servers (already filtered by config.enable inside get_tool_servers_data)
    try:
        openapi_servers = await get_tool_servers(request)
    except Exception as exc:  # pylint: disable=broad-except
        log.error("Failed to fetch OpenAPI tool servers: %s", exc)
        openapi_servers = []

    openapi_access: list[tuple[str, Optional[dict]]] = []
    for server in openapi_servers:
        server_id = str(server.get("id"))
        idx = server.get("idx", 0)
        access_control = (
            request.app.state.config.TOOL_SERVER_CONNECTIONS[idx]
            .get("config", {})
            .get("access_control", None)
        )
        tool_server_ids.append(f"server:{server_id}")
        openapi_access.append((server_id, access_control))

    # MCP servers live directly in TOOL_SERVER_CONNECTIONS
    for server in request.app.state.config.TOOL_SERVER_CONNECTIONS:
        if server.get("type", "openapi") == "mcp":
            server_id = server.get("info", {}).get("id")
            if not server_id:
                continue
            mcp_server_ids.append(f"server:mcp:{server_id}")

    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        return list(dict.fromkeys(local_ids + tool_server_ids + mcp_server_ids))

    user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id)}

    filtered_server_ids: list[str] = []
    for server_id, ac in openapi_access:
        if has_access(user.id, "read", ac, user_group_ids):
            filtered_server_ids.append(f"server:{server_id}")

    filtered_mcp_ids: list[str] = []
    for server in request.app.state.config.TOOL_SERVER_CONNECTIONS:
        if server.get("type", "openapi") != "mcp":
            continue
        server_id = server.get("info", {}).get("id")
        if not server_id:
            continue
        ac = server.get("config", {}).get("access_control", None)
        if has_access(user.id, "read", ac, user_group_ids):
            filtered_mcp_ids.append(f"server:mcp:{server_id}")

    return list(dict.fromkeys(local_ids + filtered_server_ids + filtered_mcp_ids))


@router.post("/call")
async def soren_call(
    form_data: SorenCallBody,
    request: Request,
    user=Depends(get_verified_user),
):
    raw_prompt = form_data.prompt.strip()
    if not raw_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty.",
        )

    prompt = f"[MENSAJE ENVIADO DESDE EL ENDPOINT] {raw_prompt}"

    model_id = _get_model_id(request)

    model_info = Models.get_model_by_id(model_id)
    if not model_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_id}' not found.",
        )

    tool_ids = extract_tool_ids(model_info.meta, model_info.params)

    default_tool_ids = await _get_accessible_tool_ids(request, user)

    # If model has no toolIds, use full default (local + servers).
    if not tool_ids:
        tool_ids = default_tool_ids
    else:
        # If model specifies only local tools, append accessible tool servers.
        if not any(tid.startswith("server:") for tid in tool_ids):
            server_ids = [tid for tid in default_tool_ids if tid.startswith("server:")]
            tool_ids = list(dict.fromkeys(tool_ids + server_ids))

    log.info(
        "soren_call toolIds resolved (model_id=%s, user_id=%s, count=%s, servers=%s)",
        model_id,
        user.id,
        len(tool_ids),
        len([tid for tid in tool_ids if tid.startswith("server:")]),
    )

    daily_key = _get_santo_domingo_date_str()
    existing_chat = _find_today_soren_chat(user.id, daily_key)
    assistant_message_id = str(uuid.uuid4())
    user_message_id = None

    if existing_chat:
        try:
            updated_chat = dict(existing_chat.chat or {})
            updated_chat.setdefault("title", existing_chat.title)
            if tool_ids:
                updated_chat["toolIds"] = tool_ids

            appended = _append_history_to_chat(updated_chat, prompt, model_id)
            user_message_id = appended["user_message_id"]
            assistant_message_id = appended["assistant_message_id"]

            chat = Chats.update_chat_by_id(existing_chat.id, appended["chat"])
            if not chat:
                raise ValueError("Unable to update daily chat record.")

            chat_id = chat.id
        except Exception as exc:
            log.exception("Failed to update daily chat for soren call.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update daily chat: {exc}",
            ) from exc
    else:
        history_payload = build_history(prompt, assistant_message_id, model_id)
        user_message_id = history_payload["user_message_id"]

        try:
            chat_form = ChatForm(
                chat={
                    "id": "",
                    "title": "New Chat",
                    "models": [model_id],
                    "params": {},
                    "system": None,
                    "history": history_payload["history"],
                    "messages": history_payload["messages"],
                    "tags": [],
                    "timestamp": int(time.time() * 1000),
                    "toolIds": tool_ids,
                    SOREN_DAILY_DATE_KEY: daily_key,
                    SOREN_DAILY_SOURCE_KEY: SOREN_DAILY_SOURCE_VALUE,
                }
            )

            chat = Chats.insert_new_chat(user.id, chat_form)
            if not chat:
                raise ValueError("Unable to create chat record.")

            chat_id = chat.id
        except Exception as exc:
            log.exception("Failed to create chat for soren call.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create chat: {exc}",
            ) from exc

    base_url = str(request.base_url).rstrip("/")
    target_url = f"{base_url}/api/chat/completions"

    session_id = f"soren-{uuid.uuid4().hex}"

    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": True,
        "chat_id": chat_id,
        "id": assistant_message_id,
        "parent_id": user_message_id,
        "session_id": session_id,
        "params": {"function_calling": "default"},
        "background_tasks": {
            "title_generation": True,
            "tags_generation": False,
            "follow_up_generation": False,
        },
    }

    if tool_ids:
        payload["tool_ids"] = tool_ids

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                target_url,
                json=payload,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response_text = await response.text()

                if response.status >= 400:
                    try:
                        error_payload = json.loads(response_text)
                    except json.JSONDecodeError:
                        error_payload = response_text
                    raise HTTPException(
                        status_code=response.status,
                        detail=error_payload,
                    )

                try:
                    response_payload = json.loads(response_text)
                except json.JSONDecodeError as exc:
                    log.exception("Invalid JSON response from chat completions.")
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Invalid response from chat completion endpoint.",
                    ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Error calling chat completion for soren.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return {
        "chat_id": chat_id,
        "assistant_message_id": assistant_message_id,
        "response": response_payload,
        **(
            {}
            if not form_data.wait_for_response
            else {
                "response": {
                    "content": await _wait_for_content(chat_id, assistant_message_id),
                    "chat_response": response_payload,
                }
            }
        ),
    }


async def _wait_for_content(chat_id: str, assistant_message_id: str, attempts: int = 180, delay: float = 1.0) -> Optional[str]:
    """Polls the chat until the assistant message has content or timeout."""
    for _ in range(attempts):
        try:
            chat = Chats.get_chat_by_id(chat_id)
            messages = chat.chat.get("history", {}).get("messages", {}) if chat else {}
            content = messages.get(assistant_message_id, {}).get("content")
            if content:
                return content
        except Exception:
            pass
        await asyncio.sleep(delay)
    return None
