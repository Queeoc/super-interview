"""Async SQLAlchemy database infrastructure."""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import config


class DatabaseManager:
    """Manage the PostgreSQL async engine and session factory."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def enabled(self) -> bool:
        """Whether PostgreSQL integration is enabled."""

        return config.postgres.enabled

    def _ensure_engine(self) -> None:
        """Create the engine lazily when it is first needed."""

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
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    async def connect(self) -> bool:
        """Connect to PostgreSQL and run a lightweight probe."""

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
        """Return the shared async session factory."""

        self._ensure_engine()

        if self._session_factory is None:
            raise RuntimeError("PostgreSQL session factory 未初始化")

        return self._session_factory

    async def health_check(self) -> bool:
        """Return whether PostgreSQL is reachable."""

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
        """Dispose the engine if it was created."""

        if self._engine is None:
            return

        await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        logger.info("PostgreSQL 引擎已关闭")


database_manager = DatabaseManager()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async database session."""

    session_factory = database_manager.get_session_factory()
    async with session_factory() as session:
        yield session
