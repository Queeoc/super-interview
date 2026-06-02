"""简历模块 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.middleware.visitor_context import get_request_visitor_id
from app.services.resume_service import resume_service

router = APIRouter()


@router.post("/resumes/upload")
async def upload_resume(
    http_request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """上传简历并同步完成标准化处理。"""

    result = await resume_service.upload_resume(
        session,
        file=file,
        visitor_id=get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.get("/resumes")
async def list_resumes(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """列出当前匿名访客的历史简历。"""

    result = await resume_service.list_resumes(
        session,
        visitor_id=get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": [item.model_dump(mode="json") for item in result],
    }


@router.get("/resumes/{resume_id}")
async def get_resume(
    resume_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """查询单份简历详情。"""

    result = await resume_service.get_resume(
        session,
        resume_id=resume_id,
        visitor_id=get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }
