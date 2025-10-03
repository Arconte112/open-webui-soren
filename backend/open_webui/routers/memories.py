from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import logging
from typing import Optional
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from open_webui.models.memories import Memories, MemoryModel
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.utils.auth import get_verified_user
from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.task import get_memories_engine


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


EXTERNAL_MEMORIES_TABLE_CACHE: Optional[str] = None
EXTERNAL_MEMORIES_COLUMNS_CACHE: Optional[set[str]] = None


class ExternalMemoryRecord(BaseModel):
    id: int
    content: str
    category: Optional[str] = None
    importance: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExternalMemoryCreate(BaseModel):
    content: str
    category: Optional[str] = None
    importance: Optional[int] = None


class ExternalMemoryUpdate(BaseModel):
    content: Optional[str] = None
    category: Optional[str] = None
    importance: Optional[int] = None


def _get_external_memories_engine():
    engine = get_memories_engine()
    if engine is None:
        raise HTTPException(
            status_code=500,
            detail="External memories database is not configured.",
        )
    return engine


def _resolve_table_metadata(conn):
    global EXTERNAL_MEMORIES_TABLE_CACHE, EXTERNAL_MEMORIES_COLUMNS_CACHE

    if EXTERNAL_MEMORIES_TABLE_CACHE is None:
        table_rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN ('memories', 'memory')"
            )
        )
        available_tables = {row[0] for row in table_rows}

        if "memories" in available_tables:
            EXTERNAL_MEMORIES_TABLE_CACHE = "memories"
        elif "memory" in available_tables:
            EXTERNAL_MEMORIES_TABLE_CACHE = "memory"
        else:
            raise HTTPException(
                status_code=404,
                detail="Memories table not found in external database.",
            )

    if EXTERNAL_MEMORIES_COLUMNS_CACHE is None:
        column_rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": EXTERNAL_MEMORIES_TABLE_CACHE},
        )
        EXTERNAL_MEMORIES_COLUMNS_CACHE = {row[0] for row in column_rows}

    return EXTERNAL_MEMORIES_TABLE_CACHE, EXTERNAL_MEMORIES_COLUMNS_CACHE


def _row_to_external_memory(row, columns):
    return ExternalMemoryRecord(
        id=row.get("id"),
        content=row.get("content", ""),
        category=row.get("category") if "category" in columns else None,
        importance=row.get("importance") if "importance" in columns else None,
        created_at=row.get("created_at") if "created_at" in columns else None,
        updated_at=row.get("updated_at") if "updated_at" in columns else None,
    )


@router.get("/external", response_model=list[ExternalMemoryRecord])
async def get_external_memories(user=Depends(get_verified_user)):
    engine = _get_external_memories_engine()

    try:
        with engine.connect() as conn:
            table_name, columns = _resolve_table_metadata(conn)

            select_columns = ["id", "content"]
            if "category" in columns:
                select_columns.append("category")
            if "importance" in columns:
                select_columns.append("importance")
            if "created_at" in columns:
                select_columns.append("created_at")
            if "updated_at" in columns:
                select_columns.append("updated_at")

            order_columns = []
            if "category" in columns:
                order_columns.append("category")
            order_columns.append("id")

            query = text(
                f"SELECT {', '.join(select_columns)} FROM {table_name} "
                f"ORDER BY {', '.join(order_columns)}"
            )

            rows = conn.execute(query).mappings().all()
            return [_row_to_external_memory(row, columns) for row in rows]
    except SQLAlchemyError as exc:
        log.error("Failed to fetch external memories: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch memories")


@router.post("/external", response_model=ExternalMemoryRecord)
async def create_external_memory(
    payload: ExternalMemoryCreate, user=Depends(get_verified_user)
):
    engine = _get_external_memories_engine()

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Content must not be empty")

    try:
        with engine.begin() as conn:
            table_name, columns = _resolve_table_metadata(conn)

            insert_columns = ["content"]
            params = {"content": content}

            if "category" in columns:
                insert_columns.append("category")
                params["category"] = (
                    payload.category.strip() if payload.category else None
                )

            if "importance" in columns and payload.importance is not None:
                insert_columns.append("importance")
                params["importance"] = payload.importance

            placeholders = ", ".join([f":{col}" for col in insert_columns])

            returning_columns = ["id", "content"]
            if "category" in columns:
                returning_columns.append("category")
            if "importance" in columns:
                returning_columns.append("importance")
            if "created_at" in columns:
                returning_columns.append("created_at")
            if "updated_at" in columns:
                returning_columns.append("updated_at")

            query = text(
                f"INSERT INTO {table_name} ({', '.join(insert_columns)}) "
                f"VALUES ({placeholders}) RETURNING {', '.join(returning_columns)}"
            )

            row = conn.execute(query, params).mappings().one()
            return _row_to_external_memory(row, columns)
    except SQLAlchemyError as exc:
        log.error("Failed to create external memory: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create memory")


@router.put("/external/{memory_id}", response_model=ExternalMemoryRecord)
async def update_external_memory(
    memory_id: int, payload: ExternalMemoryUpdate, user=Depends(get_verified_user)
):
    engine = _get_external_memories_engine()

    try:
        with engine.begin() as conn:
            table_name, columns = _resolve_table_metadata(conn)

            updates = []
            params = {"id": memory_id}

            if payload.content is not None:
                content = payload.content.strip()
                if not content:
                    raise HTTPException(
                        status_code=422, detail="Content must not be empty"
                    )
                updates.append("content = :content")
                params["content"] = content

            if "category" in columns and payload.category is not None:
                category_value = payload.category.strip()
                updates.append("category = :category")
                params["category"] = category_value or None

            if "importance" in columns and payload.importance is not None:
                updates.append("importance = :importance")
                params["importance"] = payload.importance

            if not updates:
                raise HTTPException(status_code=400, detail="No changes provided")

            if "updated_at" in columns:
                updates.append("updated_at = CURRENT_TIMESTAMP")

            returning_columns = ["id", "content"]
            if "category" in columns:
                returning_columns.append("category")
            if "importance" in columns:
                returning_columns.append("importance")
            if "created_at" in columns:
                returning_columns.append("created_at")
            if "updated_at" in columns:
                returning_columns.append("updated_at")

            query = text(
                f"UPDATE {table_name} SET {', '.join(updates)} "
                "WHERE id = :id "
                f"RETURNING {', '.join(returning_columns)}"
            )

            row = conn.execute(query, params).mappings().first()
            if row is None:
                raise HTTPException(status_code=404, detail="Memory not found")

            return _row_to_external_memory(row, columns)
    except SQLAlchemyError as exc:
        log.error("Failed to update external memory %s: %s", memory_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update memory")


@router.delete("/external/{memory_id}", response_model=bool)
async def delete_external_memory(memory_id: int, user=Depends(get_verified_user)):
    engine = _get_external_memories_engine()

    try:
        with engine.begin() as conn:
            table_name, _ = _resolve_table_metadata(conn)

            result = conn.execute(
                text(f"DELETE FROM {table_name} WHERE id = :id"), {"id": memory_id}
            )

            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Memory not found")

            return True
    except SQLAlchemyError as exc:
        log.error("Failed to delete external memory %s: %s", memory_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete memory")


@router.get("/ef")
async def get_embeddings(request: Request):
    return {"result": request.app.state.EMBEDDING_FUNCTION("hello world")}


############################
# GetMemories
############################


@router.get("/", response_model=list[MemoryModel])
async def get_memories(user=Depends(get_verified_user)):
    return Memories.get_memories_by_user_id(user.id)


############################
# AddMemory
############################


class AddMemoryForm(BaseModel):
    content: str


class MemoryUpdateModel(BaseModel):
    content: Optional[str] = None


@router.post("/add", response_model=Optional[MemoryModel])
async def add_memory(
    request: Request,
    form_data: AddMemoryForm,
    user=Depends(get_verified_user),
):
    memory = Memories.insert_new_memory(user.id, form_data.content)

    VECTOR_DB_CLIENT.upsert(
        collection_name=f"user-memory-{user.id}",
        items=[
            {
                "id": memory.id,
                "text": memory.content,
                "vector": request.app.state.EMBEDDING_FUNCTION(
                    memory.content, user=user
                ),
                "metadata": {"created_at": memory.created_at},
            }
        ],
    )

    return memory


############################
# QueryMemory
############################


class QueryMemoryForm(BaseModel):
    content: str
    k: Optional[int] = 1


@router.post("/query")
async def query_memory(
    request: Request, form_data: QueryMemoryForm, user=Depends(get_verified_user)
):
    memories = Memories.get_memories_by_user_id(user.id)
    if not memories:
        raise HTTPException(status_code=404, detail="No memories found for user")

    results = VECTOR_DB_CLIENT.search(
        collection_name=f"user-memory-{user.id}",
        vectors=[request.app.state.EMBEDDING_FUNCTION(form_data.content, user=user)],
        limit=form_data.k,
    )

    return results


############################
# ResetMemoryFromVectorDB
############################
@router.post("/reset", response_model=bool)
async def reset_memory_from_vector_db(
    request: Request, user=Depends(get_verified_user)
):
    VECTOR_DB_CLIENT.delete_collection(f"user-memory-{user.id}")

    memories = Memories.get_memories_by_user_id(user.id)
    VECTOR_DB_CLIENT.upsert(
        collection_name=f"user-memory-{user.id}",
        items=[
            {
                "id": memory.id,
                "text": memory.content,
                "vector": request.app.state.EMBEDDING_FUNCTION(
                    memory.content, user=user
                ),
                "metadata": {
                    "created_at": memory.created_at,
                    "updated_at": memory.updated_at,
                },
            }
            for memory in memories
        ],
    )

    return True


############################
# DeleteMemoriesByUserId
############################


@router.delete("/delete/user", response_model=bool)
async def delete_memory_by_user_id(user=Depends(get_verified_user)):
    result = Memories.delete_memories_by_user_id(user.id)

    if result:
        try:
            VECTOR_DB_CLIENT.delete_collection(f"user-memory-{user.id}")
        except Exception as e:
            log.error(e)
        return True

    return False


############################
# UpdateMemoryById
############################


@router.post("/{memory_id}/update", response_model=Optional[MemoryModel])
async def update_memory_by_id(
    memory_id: str,
    request: Request,
    form_data: MemoryUpdateModel,
    user=Depends(get_verified_user),
):
    memory = Memories.update_memory_by_id_and_user_id(
        memory_id, user.id, form_data.content
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    if form_data.content is not None:
        VECTOR_DB_CLIENT.upsert(
            collection_name=f"user-memory-{user.id}",
            items=[
                {
                    "id": memory.id,
                    "text": memory.content,
                    "vector": request.app.state.EMBEDDING_FUNCTION(
                        memory.content, user=user
                    ),
                    "metadata": {
                        "created_at": memory.created_at,
                        "updated_at": memory.updated_at,
                    },
                }
            ],
        )

    return memory


############################
# DeleteMemoryById
############################


@router.delete("/{memory_id}", response_model=bool)
async def delete_memory_by_id(memory_id: str, user=Depends(get_verified_user)):
    result = Memories.delete_memory_by_id_and_user_id(memory_id, user.id)

    if result:
        VECTOR_DB_CLIENT.delete(
            collection_name=f"user-memory-{user.id}", ids=[memory_id]
        )
        return True

    return False
