"""知识库上传与检索 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.middleware.visitor_context import get_request_visitor_id
from app.services.knowledge_service import knowledge_service
from app.services.rag_service import rag_service

router = APIRouter()


class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求体。"""

    query: str = Field(..., min_length=1, description="用户检索问题")
    knowledge_base_id: str | None = Field(default=None, description="单个知识库 ID")
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        description="多知识库联合检索 ID 列表",
    )
    category: str | None = Field(default=None, description="可选分类过滤")
    top_k: int | None = Field(default=None, ge=1, description="返回结果数量")
    score_threshold: float | None = Field(
        default=None,
        ge=0,
        description="最大距离阈值，越小表示越相似",
    )
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
    """
    上传知识库文件并完成结构化入库与向量化。

    Args:
        file: 上传文件
        name: 知识库名称
        category: 分类
        source_type: 来源类型
        description: 描述
        skill_id: 可选 skill 标识
        session: 数据库会话

    Returns:
        dict[str, Any]: 统一响应结构
    """

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
        return {
            "code": 200,
            "message": "success",
            "data": result.to_dict(),
        }
    finally:
        await file.close()


@router.post("/knowledge/search")
async def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
    """
    按知识库与分类进行知识检索。

    Args:
        request: 检索请求体

    Returns:
        dict[str, Any]: 统一响应结构
    """

    result = await rag_service.search(
        query=request.query,
        knowledge_base_id=request.knowledge_base_id,
        knowledge_base_ids=request.knowledge_base_ids,
        category=request.category,
        top_k=request.top_k,
        score_threshold=request.score_threshold,
        rewrite_enabled=request.rewrite_enabled,
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.to_dict(),
    }
