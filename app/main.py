"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import config
from app.core.database import database_manager
from app.core.milvus_client import milvus_manager
from app.core.redis_client import redis_manager
from app.core.storage_client import storage_manager
from app.middleware.error_handler import register_exception_handlers
from app.api import aiops, chat, file, health, interview, knowledge, skill
from app.utils.exceptions import ErrorCode, InfrastructureException
from app.utils.logger import setup_logger

setup_logger()


async def _initialize_infrastructure() -> None:
    """Initialize shared infrastructure clients in a predictable order."""

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


async def _shutdown_infrastructure() -> None:
    """Close shared infrastructure clients."""

    await redis_manager.close()
    await database_manager.close()
    milvus_manager.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logger()

    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")

    logger.info("🔌 正在初始化基础设施...")
    await _initialize_infrastructure()
    logger.info("✅ 基础设施初始化完成")

    logger.info("=" * 60)

    yield

    logger.info("🔌 正在关闭基础设施连接...")
    await _shutdown_infrastructure()
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的智能oncall运维系统",
    lifespan=lifespan,
)

register_exception_handlers(app)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(knowledge.router, prefix="/api", tags=["知识库"])
app.include_router(skill.router, prefix="/api", tags=["Skill"])
app.include_router(interview.router, prefix="/api", tags=["文字面试"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps智能运维"])

# 挂载静态文件
static_dir = "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """返回首页"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
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
