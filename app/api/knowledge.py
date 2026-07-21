"""知识库上传、公开浏览与检索 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.middleware.visitor_context import get_request_visitor_id
from app.models.knowledge import KnowledgeBaseEntity, KnowledgeDocumentEntity
from app.services.knowledge_service import knowledge_service
from app.services.rag_service import rag_service

router = APIRouter()


class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求体。"""

    query: str = Field(..., min_length=1, description="用户检索问题")
    knowledge_base_id: str | None = Field(default=None, description="单个知识库 ID")
    knowledge_base_ids: list[str] = Field(default_factory=list, description="多个知识库联合检索 ID 列表")
    category: str | None = Field(default=None, description="可选分类过滤")
    top_k: int | None = Field(default=None, ge=1, description="返回结果数量")
    score_threshold: float | None = Field(default=None, ge=0, description="最大距离阈值")
    rewrite_enabled: bool | None = Field(default=None, description="是否启用 query rewrite")


@router.post("/knowledge/upload")
async def upload_knowledge_document(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    category: str = Form(...),
    source_type: str = Form(...),
    description: str | None = Form(default=None),
    skill_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """兼容保留的上传接口。"""

    try:
        file_content = await file.read()
        result = await knowledge_service.upload_knowledge_document(
            session,
            name=name,
            category=category,
            source_type=source_type,
            file_name=file.filename or "",
            file_content=file_content,
            description=description,
            skill_id=skill_id,
            visitor_id=get_request_visitor_id(request),
            content_type=file.content_type,
        )
        return _success(result.to_dict())
    finally:
        await file.close()


@router.get("/knowledge/bases")
async def list_public_knowledge_bases(
    category: str | None = None,
    skill_id: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """列出公开可见的启用知识库。"""

    items = await knowledge_service.list_public_knowledge_bases(
        session,
        category=category,
        skill_id=skill_id,
        limit=limit,
    )
    return _success({"items": [_public_knowledge_base_to_dict(item) for item in items]})


@router.get("/knowledge/bases/{knowledge_base_id}")
async def get_public_knowledge_base_detail(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """获取公开可见知识库详情。"""

    knowledge_base, documents = await knowledge_service.get_public_knowledge_base_detail(
        session,
        knowledge_base_id=knowledge_base_id,
    )
    return _success(
        {
            "knowledge_base": _public_knowledge_base_to_dict(knowledge_base),
            "documents": [_public_document_to_dict(document) for document in documents],
        }
    )


@router.post("/knowledge/search")
async def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
    """公开知识库检索。"""

    result = await rag_service.search(
        query=request.query,
        knowledge_base_id=request.knowledge_base_id,
        knowledge_base_ids=request.knowledge_base_ids,
        category=request.category,
        top_k=request.top_k,
        score_threshold=request.score_threshold,
        rewrite_enabled=request.rewrite_enabled,
    )
    return _success(result.to_dict())


def _success(data: Any) -> dict[str, Any]:
    return {"code": 200, "message": "success", "data": data}


def _datetime_to_string(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _public_knowledge_base_to_dict(entity: KnowledgeBaseEntity) -> dict[str, Any]:
    metadata = dict(entity.metadata_json or {})
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "category": entity.category,
        "skill_id": entity.skill_id,
        "document_count": int(metadata.get("document_count", 0) or 0),
        "created_at": _datetime_to_string(entity.created_at),
        "updated_at": _datetime_to_string(entity.updated_at),
    }


def _public_document_to_dict(entity: KnowledgeDocumentEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "knowledge_base_id": entity.knowledge_base_id,
        "original_file_name": entity.original_file_name,
        "file_extension": entity.file_extension,
        "mime_type": entity.mime_type,
        "file_size": entity.file_size,
        "source_type": entity.source_type,
        "category": entity.category,
        "skill_id": entity.skill_id,
        "index_status": entity.index_status,
        "uploaded_at": _datetime_to_string(entity.uploaded_at),
        "indexed_at": _datetime_to_string(entity.indexed_at),
        "created_at": _datetime_to_string(entity.created_at),
        "updated_at": _datetime_to_string(entity.updated_at),
    }


__all__ = ["router"]
