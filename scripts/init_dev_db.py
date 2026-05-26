"""开发环境 PostgreSQL 表初始化脚本。"""

import asyncio
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import config
from app.models.base import Base
from app.utils.logger import setup_logger

MODEL_MODULES: tuple[str, ...] = (
    "app.models.interview",
    "app.models.resume",
    "app.models.knowledge",
    "app.models.provider",
)

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "interview_sessions",
        "interview_answers",
        "interview_reports",
        "resumes",
        "resume_analyses",
        "knowledge_bases",
        "knowledge_documents",
        "rag_chat_sessions",
        "rag_chat_messages",
        "llm_providers",
    }
)


@dataclass(slots=True)
class TableInitializationReport:
    """开发环境表初始化结果。"""

    database_host: str
    database_port: int
    database_name: str
    expected_tables: set[str]
    existing_tables_before: set[str]
    existing_tables_after: set[str]
    preexisting_tables: set[str]
    created_tables: set[str]
    missing_tables: set[str]

    @property
    def detected_target_table_count(self) -> int:
        """返回当前数据库中已存在的目标表数量。"""

        return len(self.expected_tables & self.existing_tables_after)

    @property
    def is_successful(self) -> bool:
        """返回是否所有目标表都已准备完成。"""

        return not self.missing_tables


def import_model_modules() -> None:
    """显式导入所有 ORM 模型模块，确保元数据完整注册。"""

    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)


def get_expected_tables() -> set[str]:
    """返回开发环境必须存在的目标表集合。"""

    return set(EXPECTED_TABLES)


def build_initialization_report(
    *,
    database_host: str,
    database_port: int,
    database_name: str,
    expected_tables: Iterable[str],
    existing_tables_before: Iterable[str],
    existing_tables_after: Iterable[str],
) -> TableInitializationReport:
    """根据建表前后状态生成汇总报告。"""

    expected_set = set(expected_tables)
    before_set = set(existing_tables_before)
    after_set = set(existing_tables_after)
    preexisting_tables = expected_set & before_set
    created_tables = (expected_set & after_set) - preexisting_tables
    missing_tables = expected_set - after_set

    return TableInitializationReport(
        database_host=database_host,
        database_port=database_port,
        database_name=database_name,
        expected_tables=expected_set,
        existing_tables_before=before_set,
        existing_tables_after=after_set,
        preexisting_tables=preexisting_tables,
        created_tables=created_tables,
        missing_tables=missing_tables,
    )


def _inspect_public_table_names(connection: Connection) -> set[str]:
    """读取 public schema 下的表名集合。"""

    inspector = inspect(connection)
    return set(inspector.get_table_names(schema="public"))


async def initialize_postgresql_tables(engine: AsyncEngine | None = None) -> TableInitializationReport:
    """初始化开发环境 PostgreSQL 表结构并返回结果报告。"""

    if not config.postgres.enabled:
        raise RuntimeError("POSTGRES_ENABLED=false，当前未启用 PostgreSQL，无法执行建表。")

    import_model_modules()
    owned_engine = engine is None
    target_engine = engine or create_async_engine(
        config.postgres.async_url,
        echo=config.postgres.echo,
        pool_pre_ping=True,
        pool_size=config.postgres.pool_size,
        max_overflow=config.postgres.max_overflow,
        pool_timeout=config.postgres.pool_timeout_seconds,
    )

    try:
        async with target_engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
            existing_tables_before = await connection.run_sync(_inspect_public_table_names)
            await connection.run_sync(Base.metadata.create_all)
            existing_tables_after = await connection.run_sync(_inspect_public_table_names)

        return build_initialization_report(
            database_host=config.postgres.host,
            database_port=config.postgres.port,
            database_name=config.postgres.database,
            expected_tables=get_expected_tables(),
            existing_tables_before=existing_tables_before,
            existing_tables_after=existing_tables_after,
        )
    finally:
        if owned_engine:
            await target_engine.dispose()


async def async_main() -> int:
    """脚本异步主入口。"""

    setup_logger(force=True)
    logger.info("开始初始化开发环境 PostgreSQL 表结构")
    logger.info(
        "当前数据库目标: host={}, port={}, database={}",
        config.postgres.host,
        config.postgres.port,
        config.postgres.database,
    )
    logger.info("Redis 无建表概念，本脚本不做表初始化")
    logger.info("Milvus collection 由现有运行时代码初始化，本脚本不重复建表")

    try:
        report = await initialize_postgresql_tables()
    except Exception as exc:
        logger.exception("开发环境数据库表初始化失败: {}", exc)
        return 1

    logger.info("目标表数量: {}", len(report.expected_tables))
    logger.info("已检测到的目标表数量: {}", report.detected_target_table_count)

    if report.preexisting_tables:
        logger.info("已存在表: {}", ", ".join(sorted(report.preexisting_tables)))
    else:
        logger.info("已存在表: 无")

    if report.created_tables:
        logger.info("本次新创建表: {}", ", ".join(sorted(report.created_tables)))
    else:
        logger.info("本次新创建表: 无，当前表结构已是最新")

    if report.missing_tables:
        logger.error("缺失表: {}", ", ".join(sorted(report.missing_tables)))
        logger.error("开发环境数据库表初始化未完成")
        return 2

    logger.success("开发环境数据库表初始化完成，全部目标表已就绪")
    return 0


def main() -> int:
    """同步主入口。"""

    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
