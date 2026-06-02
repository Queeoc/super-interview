"""开发环境 PostgreSQL schema 重建脚本。"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from dataclasses import dataclass, field
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

PUBLIC_SCHEMA_NAME = "public"
RESET_STRATEGY = "reset_public_schema"

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
class TableColumnDiff:
    """单表列级漂移信息。"""

    table_name: str
    expected_columns: set[str]
    actual_columns: set[str]
    missing_columns: set[str]
    extra_columns: set[str]

    @property
    def is_missing_table(self) -> bool:
        """返回该表在重建前是否不存在。"""

        return not self.actual_columns

    @property
    def has_drift(self) -> bool:
        """返回该表是否存在列级漂移。"""

        return self.is_missing_table or bool(self.missing_columns or self.extra_columns)


@dataclass(slots=True)
class TableInitializationReport:
    """开发环境 schema 重建结果。"""

    database_host: str
    database_port: int
    database_name: str
    reset_strategy: str
    schema_name: str
    expected_tables: set[str]
    existing_tables_before: set[str]
    existing_tables_after: set[str]
    preexisting_tables: set[str]
    created_tables: set[str]
    missing_tables: set[str]
    unmanaged_tables_before: set[str] = field(default_factory=set)
    drifted_tables: set[str] = field(default_factory=set)
    table_column_diffs: dict[str, TableColumnDiff] = field(default_factory=dict)

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


def get_expected_table_columns() -> dict[str, set[str]]:
    """从 ORM 元数据中提取受管表的期望列集合。"""

    managed_tables = get_expected_tables()
    expected_columns: dict[str, set[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in managed_tables:
            continue
        expected_columns[table.name] = {column.name for column in table.columns}
    return expected_columns


def build_table_column_diffs(
    *,
    expected_table_columns: dict[str, set[str]],
    actual_table_columns: dict[str, set[str]],
) -> dict[str, TableColumnDiff]:
    """构建所有受管表的列级漂移报告。"""

    diffs: dict[str, TableColumnDiff] = {}
    for table_name, expected_columns in expected_table_columns.items():
        actual_columns = set(actual_table_columns.get(table_name, set()))
        missing_columns = expected_columns - actual_columns
        extra_columns = actual_columns - expected_columns
        diffs[table_name] = TableColumnDiff(
            table_name=table_name,
            expected_columns=set(expected_columns),
            actual_columns=actual_columns,
            missing_columns=missing_columns,
            extra_columns=extra_columns,
        )
    return diffs


def build_initialization_report(
    *,
    database_host: str,
    database_port: int,
    database_name: str,
    reset_strategy: str,
    schema_name: str,
    expected_tables: Iterable[str],
    existing_tables_before: Iterable[str],
    existing_tables_after: Iterable[str],
    table_column_diffs: dict[str, TableColumnDiff] | None = None,
) -> TableInitializationReport:
    """根据重建前后状态生成汇总报告。"""

    expected_set = set(expected_tables)
    before_set = set(existing_tables_before)
    after_set = set(existing_tables_after)
    preexisting_tables = expected_set & before_set
    created_tables = expected_set & after_set
    missing_tables = expected_set - after_set
    unmanaged_tables_before = before_set - expected_set
    resolved_diffs = dict(table_column_diffs or {})
    drifted_tables = {
        table_name
        for table_name, diff in resolved_diffs.items()
        if diff.has_drift
    }

    return TableInitializationReport(
        database_host=database_host,
        database_port=database_port,
        database_name=database_name,
        reset_strategy=reset_strategy,
        schema_name=schema_name,
        expected_tables=expected_set,
        existing_tables_before=before_set,
        existing_tables_after=after_set,
        preexisting_tables=preexisting_tables,
        created_tables=created_tables,
        missing_tables=missing_tables,
        unmanaged_tables_before=unmanaged_tables_before,
        drifted_tables=drifted_tables,
        table_column_diffs=resolved_diffs,
    )


def _inspect_public_table_names(connection: Connection) -> set[str]:
    """读取 public schema 下的表名集合。"""

    inspector = inspect(connection)
    return set(inspector.get_table_names(schema=PUBLIC_SCHEMA_NAME))


def _inspect_table_columns(connection: Connection, table_names: Iterable[str]) -> dict[str, set[str]]:
    """读取指定表的列集合。"""

    inspector = inspect(connection)
    column_map: dict[str, set[str]] = {}
    for table_name in table_names:
        if table_name not in inspector.get_table_names(schema=PUBLIC_SCHEMA_NAME):
            column_map[table_name] = set()
            continue
        columns = inspector.get_columns(table_name, schema=PUBLIC_SCHEMA_NAME)
        column_map[table_name] = {column["name"] for column in columns}
    return column_map


def _recreate_public_schema(connection: Connection) -> None:
    """删除并重建 public schema。"""

    connection.execute(text(f'DROP SCHEMA IF EXISTS "{PUBLIC_SCHEMA_NAME}" CASCADE'))
    connection.execute(text(f'CREATE SCHEMA "{PUBLIC_SCHEMA_NAME}"'))
    connection.execute(text(f'GRANT ALL ON SCHEMA "{PUBLIC_SCHEMA_NAME}" TO CURRENT_USER'))
    connection.execute(text(f'GRANT ALL ON SCHEMA "{PUBLIC_SCHEMA_NAME}" TO PUBLIC'))


async def initialize_postgresql_tables(engine: AsyncEngine | None = None) -> TableInitializationReport:
    """重建开发环境 PostgreSQL public schema 并返回结果报告。"""

    if not config.postgres.enabled:
        raise RuntimeError("POSTGRES_ENABLED=false，当前未启用 PostgreSQL，无法执行重建。")

    import_model_modules()
    expected_tables = get_expected_tables()
    expected_table_columns = get_expected_table_columns()

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
            actual_table_columns = await connection.run_sync(
                _inspect_table_columns,
                expected_tables,
            )
            table_column_diffs = build_table_column_diffs(
                expected_table_columns=expected_table_columns,
                actual_table_columns=actual_table_columns,
            )
            await connection.run_sync(_recreate_public_schema)
            await connection.run_sync(Base.metadata.create_all)
            existing_tables_after = await connection.run_sync(_inspect_public_table_names)

        return build_initialization_report(
            database_host=config.postgres.host,
            database_port=config.postgres.port,
            database_name=config.postgres.database,
            reset_strategy=RESET_STRATEGY,
            schema_name=PUBLIC_SCHEMA_NAME,
            expected_tables=expected_tables,
            existing_tables_before=existing_tables_before,
            existing_tables_after=existing_tables_after,
            table_column_diffs=table_column_diffs,
        )
    finally:
        if owned_engine:
            await target_engine.dispose()


def _log_column_drift(report: TableInitializationReport) -> None:
    """输出列级漂移诊断结果。"""

    if not report.table_column_diffs:
        logger.info("未收集到列级漂移信息")
        return

    if not report.drifted_tables:
        logger.info("重建前未检测到受管表列漂移")
        return

    for table_name in sorted(report.drifted_tables):
        diff = report.table_column_diffs[table_name]
        logger.warning(
            "表结构漂移: table={}, missing_columns={}, extra_columns={}, missing_table={}",
            table_name,
            sorted(diff.missing_columns),
            sorted(diff.extra_columns),
            diff.is_missing_table,
        )


async def async_main() -> int:
    """脚本异步主入口。"""

    setup_logger(force=True)
    logger.warning("当前脚本会重置开发库 public schema，仅适用于纯开发环境")
    logger.info("开始重建开发环境 PostgreSQL 表结构")
    logger.info(
        "当前数据库目标: host={}, port={}, database={}, schema={}, strategy={}",
        config.postgres.host,
        config.postgres.port,
        config.postgres.database,
        PUBLIC_SCHEMA_NAME,
        RESET_STRATEGY,
    )
    logger.info("Redis 无建表概念，本脚本不做表初始化")
    logger.info("Milvus collection 由现有运行时代码初始化，本脚本不重复建表")

    try:
        report = await initialize_postgresql_tables()
    except Exception as exc:
        logger.exception("开发环境数据库重建失败: {}", exc)
        return 1

    logger.info("目标表数量: {}", len(report.expected_tables))
    logger.info("已检测到的目标表数量: {}", report.detected_target_table_count)

    if report.preexisting_tables:
        logger.info("重建前已存在的受管表: {}", ", ".join(sorted(report.preexisting_tables)))
    else:
        logger.info("重建前已存在的受管表: 无")

    if report.unmanaged_tables_before:
        logger.warning("重建前检测到非受管表: {}", ", ".join(sorted(report.unmanaged_tables_before)))
    else:
        logger.info("重建前检测到非受管表: 无")

    _log_column_drift(report)

    if report.created_tables:
        logger.info("本次重建后已创建表: {}", ", ".join(sorted(report.created_tables)))
    else:
        logger.warning("本次重建后未检测到新建表，请检查 ORM 元数据是否为空")

    if report.missing_tables:
        logger.error("缺失表: {}", ", ".join(sorted(report.missing_tables)))
        logger.error("开发环境数据库表重建未完成")
        return 2

    logger.success("开发环境数据库重建完成，全部目标表已就绪")
    return 0


def main() -> int:
    """同步主入口。"""

    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
