"""Provider 管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.provider import LlmProviderCreateRequest, LlmProviderUpdateRequest
from app.services.provider_service import provider_service

router = APIRouter()


@router.get("/providers")
async def list_providers(
    status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """列出 Provider 配置。"""

    result = await provider_service.list_providers(session, status=status, limit=limit)
    return {
        "code": 200,
        "message": "success",
        "data": [item.model_dump(mode="json") for item in result],
    }


@router.get("/providers/runtime")
async def get_runtime_provider() -> dict[str, object]:
    """读取当前运行时默认 Provider。"""

    result = await provider_service.get_runtime_snapshot()
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """读取单个 Provider。"""

    result = await provider_service.get_provider(session, provider_id)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.post("/providers")
async def create_provider(
    request: LlmProviderCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """创建 Provider。"""

    result = await provider_service.create_provider(session, request)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.put("/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    request: LlmProviderUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """更新 Provider。"""

    result = await provider_service.update_provider(session, provider_id, request)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.post("/providers/{provider_id}/set-default")
async def set_default_provider(
    provider_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """将指定 Provider 设为默认。"""

    result = await provider_service.set_default_provider(session, provider_id)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.post("/providers/{provider_id}/test")
async def test_provider_connection(
    provider_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    """测试 Provider 连通性。"""

    result = await provider_service.test_connection(session, provider_id)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """删除 Provider。"""

    await provider_service.delete_provider(session, provider_id)
    return Response(status_code=204)
