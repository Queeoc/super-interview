"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api import admin_knowledge, chat, file, health, interview, knowledge, provider, resume, skill
from app.config import config
from app.core.database import database_manager
from app.core.milvus_client import milvus_manager
from app.core.redis_client import redis_manager
from app.core.storage_client import storage_manager
from app.middleware.error_handler import register_exception_handlers
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.visitor_context import VisitorContextMiddleware
from app.services.async_task_service import async_task_service
from app.services.provider_service import provider_service
from app.utils.exceptions import ErrorCode, InfrastructureException
from app.utils.logger import setup_logger

setup_logger()


async def _initialize_infrastructure() -> None:
    """
    异步初始化共享基础设施流水线。
    
    核心逻辑:
        1. 依次异步/同步连接核心组件：Storage -> PostgreSQL -> Redis -> Milvus 向量库。
        2. 严格的故障Fail-Fast防御：任何一个基础设施连接失败，立即捕获并将其包装为
           带标准业务错误码(ErrorCode)的 InfrastructureException 并抛出，强行中断程序启动。
    """

    try:
        await storage_manager.connect()
    except Exception as exc:
        raise InfrastructureException(
            code=ErrorCode.STORAGE_UNAVAILABLE,
            message="Storage 初始化失败",
            details={"error": str(exc)},
        ) from exc

    if config.postgres.enabled:
        try:
            await database_manager.connect()
        except Exception as exc:
            raise InfrastructureException(
                code=ErrorCode.POSTGRES_UNAVAILABLE,
                message="PostgreSQL 初始化失败",
                details={"error": str(exc)},
            ) from exc

    if config.redis.enabled:
        try:
            await redis_manager.connect()
        except Exception as exc:
            raise InfrastructureException(
                code=ErrorCode.REDIS_UNAVAILABLE,
                message="Redis 初始化失败",
                details={"error": str(exc)},
            ) from exc

    try:
        milvus_manager.connect()
    except Exception as exc:
        raise InfrastructureException(
            code=ErrorCode.MILVUS_UNAVAILABLE,
            message="Milvus 初始化失败",
            details={"error": str(exc)},
        ) from exc

    await provider_service.initialize_runtime_registry()


async def _shutdown_infrastructure() -> None:
    """关闭共享基础设施。"""

    await async_task_service.stop()
    await redis_manager.close()
    await database_manager.close()
    milvus_manager.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """管理应用生命周期。"""

    setup_logger()
    logger.info("=" * 60)
    logger.info("启动 {} v{}", config.app_name, config.app_version)
    logger.info("环境: {}", "开发" if config.debug else "生产")
    logger.info("监听地址: http://{}:{}", config.host, config.port)
    logger.info("API 文档: http://{}:{}/docs", config.host, config.port)

    logger.info("正在初始化基础设施...")
    await _initialize_infrastructure()
    await async_task_service.start()
    logger.info("基础设施初始化完成")

    yield

    logger.info("正在关闭基础设施连接...")
    await _shutdown_infrastructure()
    logger.info("{} 已关闭", config.app_name)


app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的 AI 面试平台",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(VisitorContextMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(knowledge.router, prefix="/api", tags=["知识库"])
app.include_router(admin_knowledge.router, prefix="/api/admin/knowledge", tags=["管理员知识库"])
app.include_router(resume.router, prefix="/api", tags=["简历"])
app.include_router(skill.router, prefix="/api", tags=["Skill"])
app.include_router(provider.router, prefix="/api", tags=["Provider"])
app.include_router(interview.router, prefix="/api", tags=["文字面试"])


@app.get("/")
async def root() -> dict[str, str]:
    """返回基础服务信息。"""

    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="info",
    )
