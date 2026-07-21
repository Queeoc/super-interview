"""数据库基础设施组件。

用于统一管理 PostgreSQL 的异步连接、SQLAlchemy Engine 和 SessionFactory，
并向 FastAPI 路由层提供可注入的数据库会话依赖。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import config


class DatabaseManager:
    """PostgreSQL 异步连接管理器。

    整个项目访问数据库的总入口：
    - 应用启动时负责初始化数据库引擎
    - 运行期间负责提供共享的 SessionFactory
    - 应用关闭时负责统一释放连接资源
    """

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def enabled(self) -> bool:
        """返回当前是否启用了 PostgreSQL 能力。"""

        return config.postgres.enabled

    def _ensure_engine(self) -> None:
        """按需创建 Engine 和 SessionFactory。

        这里使用“懒加载”：
        - 如果代码还没有真正用到数据库，就先不创建底层连接对象
        - 第一次需要数据库时，再根据配置初始化引擎

        这样能减少应用启动时的额外开销，也便于测试场景按需接管配置。
        """

        if self._engine is not None and self._session_factory is not None:
            return

        logger.info(
            "初始化 PostgreSQL 引擎: host={}, port={}, database={}",
            config.postgres.host,
            config.postgres.port,
            config.postgres.database,
        )

        self._engine = create_async_engine(
            config.postgres.async_url,
            echo=config.postgres.echo,
            pool_pre_ping=True,
            pool_size=config.postgres.pool_size,
            max_overflow=config.postgres.max_overflow,
            pool_timeout=config.postgres.pool_timeout_seconds,
        )
        # 关闭“提交后立即过期”行为，避免事务提交后对象字段立刻失效，
        # 这样在 Service / Repository 层提交事务后仍可继续读取对象内容。
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    async def connect(self) -> bool:
        """初始化数据库连接，并执行一次轻量探活。

        返回值语义：
        - True：数据库已启用且连接成功
        - False：数据库未启用，因此跳过初始化
        """

        if not self.enabled:
            logger.info("PostgreSQL 未启用，跳过初始化")
            return False

        self._ensure_engine()

        if self._engine is None:
            raise RuntimeError("PostgreSQL 引擎初始化失败")

        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

        logger.info("PostgreSQL 连接成功")
        return True

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """返回全局共享的异步 SessionFactory。

        上层不会直接创建 SQLAlchemy Engine，
        而是统一通过这个工厂获取请求级数据库会话。
        """

        self._ensure_engine()

        if self._session_factory is None:
            raise RuntimeError("PostgreSQL session factory 未初始化")

        return self._session_factory

    async def health_check(self) -> bool:
        """执行数据库健康检查，判断当前 PostgreSQL 是否可达。"""

        if not self.enabled:
            return False

        try:
            self._ensure_engine()

            if self._engine is None:
                return False

            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.warning("PostgreSQL 健康检查失败: {}", exc)
            return False

    async def close(self) -> None:
        """关闭并释放数据库引擎资源。"""

        if self._engine is None:
            return

        await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        logger.info("PostgreSQL 引擎已关闭")


database_manager = DatabaseManager()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 的数据库会话依赖。

    常见使用方式是在 Router 中通过 `Depends(get_db_session)` 注入。
    每个请求都会拿到一个独立的 `AsyncSession`，请求结束后自动退出上下文。

    可以把它理解成：系统为每个请求临时发一把数据库“工位钥匙”，
    用完归还，避免连接长期占用或泄漏。
    """

    session_factory = database_manager.get_session_factory()
    async with session_factory() as session:
        yield session
