"""健康检查接口"""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import config
from app.core.database import database_manager
from app.core.milvus_client import milvus_manager
from app.core.redis_client import redis_manager
from app.core.storage_client import storage_manager
from loguru import logger

router = APIRouter()


@router.get("/health")
async def health_check():
    """健康检查接口
    检查服务状态和数据库连接状态
    
    Returns:
        JSONResponse: 健康检查结果
    """
    health_data: dict[str, Any] = {  # pyright: ignore[reportExplicitAny]
        "service": config.app_name,
        "version": config.app_version,
        "environment": "development" if config.debug else "production",
        "status": "healthy",
        "dependencies": {},
    }

    health_data["dependencies"]["postgres"] = await _build_dependency_status(
        name="PostgreSQL",
        enabled=config.postgres.enabled,
        healthy=await database_manager.health_check() if config.postgres.enabled else False,
        success_message="PostgreSQL 连接正常",
        failure_message="PostgreSQL 连接异常",
    )
    health_data["dependencies"]["redis"] = await _build_dependency_status(
        name="Redis",
        enabled=config.redis.enabled,
        healthy=await redis_manager.health_check() if config.redis.enabled else False,
        success_message="Redis 连接正常",
        failure_message="Redis 连接异常",
    )
    health_data["dependencies"]["milvus"] = await _build_dependency_status(
        name="Milvus",
        enabled=True,
        healthy=milvus_manager.health_check(),
        success_message="Milvus 连接正常",
        failure_message="Milvus 连接异常",
    )
    health_data["dependencies"]["storage"] = await _build_dependency_status(
        name=f"Storage({config.storage.provider})",
        enabled=True,
        healthy=await storage_manager.health_check(),
        success_message="存储后端可用",
        failure_message="存储后端异常",
    )

    overall_status = "healthy"
    status_code = 200

    for dependency_name, dependency_status in health_data["dependencies"].items():
        if dependency_status["status"] == "unhealthy":
            logger.warning("依赖异常: {} -> {}", dependency_name, dependency_status)
            overall_status = "unhealthy"
            status_code = 503
            break

    if overall_status != "healthy":
        overall_status = "unhealthy"
        health_data["error"] = "关键依赖不可用"

    health_data["status"] = overall_status

    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "服务运行正常" if overall_status == "healthy" else "服务不可用",
            "data": health_data,
        },
    )


async def _build_dependency_status(
    name: str,
    enabled: bool,
    healthy: bool,
    success_message: str,
    failure_message: str,
) -> dict[str, str]:
    """Build a normalized dependency status payload."""

    if not enabled:
        return {
            "name": name,
            "status": "disabled",
            "message": f"{name} 未启用",
        }

    if healthy:
        return {
            "name": name,
            "status": "healthy",
            "message": success_message,
        }

    return {
        "name": name,
        "status": "unhealthy",
        "message": failure_message,
    }
