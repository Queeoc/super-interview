"""管理员知识库管理 API。"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Header, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.knowledge import KnowledgeSearchRequest
from app.config import config
from app.core.database import get_db_session
from app.models.knowledge import KnowledgeBaseEntity, KnowledgeDocumentEntity
from app.services.admin_knowledge_service import AdminKnowledgeUploadFile, admin_knowledge_service
from app.services.rag_service import rag_service
from app.utils.exceptions import BusinessException, ErrorCode

router = APIRouter()


async def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """校验最小管理员 token。"""

    if not config.admin.enabled:
        raise BusinessException(
            code=ErrorCode.BAD_REQUEST,
            message="管理员知识库入口未启用",
            http_status=403,
        )

    expected_token = config.admin.token.strip()
    if not expected_token:
        raise BusinessException(
            code=ErrorCode.BAD_REQUEST,
            message="管理员 token 未配置",
            http_status=403,
        )

    provided_token = (x_admin_token or "").strip()
    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise BusinessException(
            code=ErrorCode.BAD_REQUEST,
            message="管理员 token 无效",
            http_status=403,
        )


class AdminKnowledgeBaseCreateRequest(BaseModel):
    """创建管理员知识库请求。"""

    name: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    description: str | None = None
    skill_id: str | None = None


class AdminKnowledgeBaseUpdateRequest(BaseModel):
    """更新管理员知识库请求。"""

    name: str | None = None
    description: str | None = None
    category: str | None = None
    skill_id: str | None = None
    is_enabled: bool | None = None
    status: str | None = None


class AdminKnowledgeDocumentUpdateRequest(BaseModel):
    """更新知识库文档请求。"""

    is_enabled: bool


def _success(data: Any) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "success",
        "data": data,
    }


def _datetime_to_string(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _knowledge_base_to_dict(entity: KnowledgeBaseEntity) -> dict[str, Any]:
    metadata = dict(entity.metadata_json or {})
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "category": entity.category,
        "skill_id": entity.skill_id,
        "source_type": entity.source_type,
        "status": entity.status,
        "is_enabled": entity.is_enabled,
        "document_count": int(metadata.get("document_count", 0) or 0),
        "metadata_json": metadata,
        "created_at": _datetime_to_string(entity.created_at),
        "updated_at": _datetime_to_string(entity.updated_at),
    }


def _document_to_dict(entity: KnowledgeDocumentEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "knowledge_base_id": entity.knowledge_base_id,
        "original_file_name": entity.original_file_name,
        "storage_path": entity.storage_path,
        "file_extension": entity.file_extension,
        "mime_type": entity.mime_type,
        "file_size": entity.file_size,
        "source_type": entity.source_type,
        "category": entity.category,
        "skill_id": entity.skill_id,
        "index_status": entity.index_status,
        "is_enabled": entity.is_enabled,
        "error_message": entity.error_message,
        "metadata_json": dict(entity.metadata_json or {}),
        "uploaded_at": _datetime_to_string(entity.uploaded_at),
        "indexed_at": _datetime_to_string(entity.indexed_at),
        "created_at": _datetime_to_string(entity.created_at),
        "updated_at": _datetime_to_string(entity.updated_at),
    }


@router.get("/bases", dependencies=[Depends(require_admin_token)])
async def list_knowledge_bases(
    category: str | None = None,
    skill_id: str | None = None,
    enabled_only: bool = False,
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """查询管理员知识库列表。"""

    items = await admin_knowledge_service.list_knowledge_bases(
        session,
        category=category,
        skill_id=skill_id,
        enabled_only=enabled_only,
        limit=limit,
    )
    return _success({"items": [_knowledge_base_to_dict(item) for item in items]})


@router.post("/bases", dependencies=[Depends(require_admin_token)])
async def create_knowledge_base(
    request: AdminKnowledgeBaseCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """创建管理员知识库。"""

    entity = await admin_knowledge_service.create_knowledge_base(
        session,
        name=request.name,
        category=request.category,
        description=request.description,
        skill_id=request.skill_id,
    )
    return _success(_knowledge_base_to_dict(entity))


@router.get("/bases/{knowledge_base_id}", dependencies=[Depends(require_admin_token)])
async def get_knowledge_base_detail(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """查询知识库详情和文档列表。"""

    knowledge_base, documents = await admin_knowledge_service.get_knowledge_base_detail(
        session,
        knowledge_base_id=knowledge_base_id,
    )
    return _success(
        {
            "knowledge_base": _knowledge_base_to_dict(knowledge_base),
            "documents": [_document_to_dict(document) for document in documents],
        }
    )


@router.patch("/bases/{knowledge_base_id}", dependencies=[Depends(require_admin_token)])
async def update_knowledge_base(
    knowledge_base_id: str,
    request: AdminKnowledgeBaseUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """更新知识库主记录。"""

    payload = request.model_dump(exclude_unset=True)
    entity = await admin_knowledge_service.update_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
        **payload,
    )
    return _success(_knowledge_base_to_dict(entity))


@router.post("/bases/{knowledge_base_id}/documents", dependencies=[Depends(require_admin_token)])
async def add_document_to_knowledge_base(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """向已有管理员知识库追加文档。"""

    try:
        file_content = await file.read()
        result = await admin_knowledge_service.add_document_to_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            file_name=file.filename or "",
            file_content=file_content,
            content_type=file.content_type,
        )
        return _success(result.to_dict())
    finally:
        await file.close()


@router.post("/bases/{knowledge_base_id}/documents/batch", dependencies=[Depends(require_admin_token)])
async def add_documents_to_knowledge_base(
    knowledge_base_id: str,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """向已有管理员知识库批量追加文档。"""

    try:
        upload_files: list[AdminKnowledgeUploadFile] = []
        for file in files:
            upload_files.append(
                AdminKnowledgeUploadFile(
                    file_name=file.filename or "",
                    file_content=await file.read(),
                    content_type=file.content_type,
                )
            )
        result = await admin_knowledge_service.add_documents_to_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            files=upload_files,
        )
        return _success(result.to_dict())
    finally:
        for file in files:
            await file.close()


@router.patch("/documents/{document_id}", dependencies=[Depends(require_admin_token)])
async def update_document(
    document_id: str,
    request: AdminKnowledgeDocumentUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """更新文档启用状态。"""

    entity = await admin_knowledge_service.update_document(
        session,
        document_id=document_id,
        is_enabled=request.is_enabled,
    )
    return _success(_document_to_dict(entity))


@router.delete("/documents/{document_id}", dependencies=[Depends(require_admin_token)])
async def delete_document(
    document_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """物理删除文档。"""

    entity = await admin_knowledge_service.delete_document(
        session,
        document_id=document_id,
    )
    return _success(_document_to_dict(entity))


@router.delete("/bases/{knowledge_base_id}", dependencies=[Depends(require_admin_token)])
async def delete_knowledge_base(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """物理删除知识库。"""

    entity = await admin_knowledge_service.delete_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
    )
    return _success(_knowledge_base_to_dict(entity))


@router.post("/bases/{knowledge_base_id}/reindex", dependencies=[Depends(require_admin_token)])
async def reindex_knowledge_base(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """同步重建知识库索引。"""

    result = await admin_knowledge_service.reindex_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
    )
    return _success(result.to_dict())


@router.post("/search", dependencies=[Depends(require_admin_token)])
async def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
    """管理员调试知识库检索。"""

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


__all__ = ["router", "require_admin_token"]
