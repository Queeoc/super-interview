"""知识库管理员 RAG Phase 1 的 PostgreSQL 结构升级脚本。"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

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
TARGET_TABLES: tuple[str, ...] = ("knowledge_bases", "knowledge_documents")
MANAGED_MODEL_MODULES: tuple[str, ...] = ("app.models.knowledge",)
TARGET_BOOLEAN_COLUMNS: dict[str, tuple[str, ...]] = {
    "knowledge_bases": ("is_enabled",),
    "knowledge_documents": ("is_enabled",),
}


@dataclass(slots=True)
class TableColumnDiff:
    """单表列差异信息。"""

    table_name: str
    expected_columns: set[str]
    actual_columns: set[str]
    missing_columns: set[str]
    extra_columns: set[str]

    @property
    def has_drift(self) -> bool:
        """返回是否存在列差异。"""

        return bool(self.missing_columns or self.extra_columns)


@dataclass(slots=True)
class KnowledgeSchemaUpgradeReport:
    """知识库结构升级执行结果。"""

    database_name: str
    target_tables: set[str]
    existing_tables_before: set[str]
    existing_tables_after: set[str]
    added_columns: dict[str, list[str]]
    existing_indexes_before: set[str]
    existing_indexes_after: set[str]
    table_column_diffs_before: dict[str, TableColumnDiff]


def import_model_modules() -> None:
    """显式导入知识库模型模块。"""

    for module_name in MANAGED_MODEL_MODULES:
        importlib.import_module(module_name)


def get_target_tables() -> set[str]:
    """返回脚本关注的目标表集合。"""

    return set(TARGET_TABLES)


def get_expected_table_columns() -> dict[str, set[str]]:
    """从 ORM 元数据中提取目标表的列集合。"""

    import_model_modules()
    target_tables = get_target_tables()
    expected_columns: dict[str, set[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in target_tables:
            continue
        expected_columns[table.name] = {column.name for column in table.columns}
    return expected_columns


def build_table_column_diffs(
    *,
    expected_table_columns: dict[str, set[str]],
    actual_table_columns: dict[str, set[str]],
) -> dict[str, TableColumnDiff]:
    """构建目标表列差异结果。"""

    diffs: dict[str, TableColumnDiff] = {}
    for table_name, expected_columns in expected_table_columns.items():
        actual_columns = set(actual_table_columns.get(table_name, set()))
        diffs[table_name] = TableColumnDiff(
            table_name=table_name,
            expected_columns=set(expected_columns),
            actual_columns=actual_columns,
            missing_columns=expected_columns - actual_columns,
            extra_columns=actual_columns - expected_columns,
        )
    return diffs


def _inspect_public_table_names(connection: Connection) -> set[str]:
    """读取 public schema 下的表名集合。"""

    inspector = inspect(connection)
    return set(inspector.get_table_names(schema=PUBLIC_SCHEMA_NAME))


def _inspect_table_columns(connection: Connection, table_names: set[str]) -> dict[str, set[str]]:
    """读取目标表的列集合。"""

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names(schema=PUBLIC_SCHEMA_NAME))
    column_map: dict[str, set[str]] = {}
    for table_name in table_names:
        if table_name not in existing_tables:
            column_map[table_name] = set()
            continue
        columns = inspector.get_columns(table_name, schema=PUBLIC_SCHEMA_NAME)
        column_map[table_name] = {column["name"] for column in columns}
    return column_map


def _inspect_index_names(connection: Connection, table_names: set[str]) -> set[str]:
    """读取目标表当前索引名集合。"""

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names(schema=PUBLIC_SCHEMA_NAME))
    index_names: set[str] = set()
    for table_name in table_names:
        if table_name not in existing_tables:
            continue
        for index in inspector.get_indexes(table_name, schema=PUBLIC_SCHEMA_NAME):
            index_names.add(str(index["name"]))
    return index_names


def _build_boolean_column_sql(table_name: str, column_name: str) -> list[str]:
    """构建布尔列升级 SQL。"""

    index_name = f"ix_{table_name}_{column_name}"
    qualified_table = f'"{PUBLIC_SCHEMA_NAME}"."{table_name}"'
    return [
        (
            f'ALTER TABLE {qualified_table} '
            f'ADD COLUMN IF NOT EXISTS "{column_name}" BOOLEAN NOT NULL DEFAULT TRUE'
        ),
        f'UPDATE {qualified_table} SET "{column_name}" = TRUE WHERE "{column_name}" IS NULL',
        f'CREATE INDEX IF NOT EXISTS "{index_name}" ON {qualified_table} ("{column_name}")',
    ]


async def upgrade_knowledge_schema(engine: AsyncEngine | None = None) -> KnowledgeSchemaUpgradeReport:
    """执行知识库 Phase 1 的最小结构升级。"""

    if not config.postgres.enabled:
        raise RuntimeError("POSTGRES_ENABLED=false，当前未启用 PostgreSQL，无法执行知识库结构升级。")

    import_model_modules()
    expected_columns = get_expected_table_columns()
    target_tables = get_target_tables()
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
            existing_tables_before = await connection.run_sync(_inspect_public_table_names)
            actual_columns_before = await connection.run_sync(_inspect_table_columns, target_tables)
            existing_indexes_before = await connection.run_sync(_inspect_index_names, target_tables)
            table_column_diffs_before = build_table_column_diffs(
                expected_table_columns=expected_columns,
                actual_table_columns=actual_columns_before,
            )

            added_columns: dict[str, list[str]] = {}
            for table_name, column_names in TARGET_BOOLEAN_COLUMNS.items():
                missing_columns = sorted(
                    column_name
                    for column_name in column_names
                    if column_name not in actual_columns_before.get(table_name, set())
                )
                if not missing_columns:
                    continue
                added_columns[table_name] = list(missing_columns)
                for column_name in missing_columns:
                    for statement in _build_boolean_column_sql(table_name, column_name):
                        await connection.execute(text(statement))

            existing_tables_after = await connection.run_sync(_inspect_public_table_names)
            existing_indexes_after = await connection.run_sync(_inspect_index_names, target_tables)

        return KnowledgeSchemaUpgradeReport(
            database_name=config.postgres.database,
            target_tables=target_tables,
            existing_tables_before=existing_tables_before,
            existing_tables_after=existing_tables_after,
            added_columns=added_columns,
            existing_indexes_before=existing_indexes_before,
            existing_indexes_after=existing_indexes_after,
            table_column_diffs_before=table_column_diffs_before,
        )
    finally:
        if owned_engine:
            await target_engine.dispose()


async def async_main() -> int:
    """异步主入口。"""

    setup_logger(force=True)
    logger.info(
        "开始执行知识库结构升级: host={}, port={}, database={}, schema={}",
        config.postgres.host,
        config.postgres.port,
        config.postgres.database,
        PUBLIC_SCHEMA_NAME,
    )
    report = await upgrade_knowledge_schema()
    logger.info(
        "知识库结构升级完成: database={}, added_columns={}, indexes_after={}",
        report.database_name,
        report.added_columns,
        sorted(report.existing_indexes_after),
    )
    return 0


def main() -> int:
    """同步脚本入口。"""

    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
