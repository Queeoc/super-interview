"""知识库 Phase 1 结构升级脚本的最小验证。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_script_module():
    """按文件路径加载知识库升级脚本模块。"""

    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "upgrade_knowledge_schema_admin_rag.py"
    )
    spec = importlib.util.spec_from_file_location(
        "upgrade_knowledge_schema_admin_rag",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("无法加载 scripts/upgrade_knowledge_schema_admin_rag.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_get_target_tables_returns_knowledge_tables() -> None:
    """脚本应声明知识库升级关注的目标表。"""

    module = _load_script_module()
    assert module.get_target_tables() == {"knowledge_bases", "knowledge_documents"}


def test_get_expected_table_columns_includes_is_enabled() -> None:
    """ORM 元数据应包含本次升级新增的 is_enabled 字段。"""

    module = _load_script_module()
    expected_columns = module.get_expected_table_columns()

    assert "knowledge_bases" in expected_columns
    assert "knowledge_documents" in expected_columns
    assert "is_enabled" in expected_columns["knowledge_bases"]
    assert "is_enabled" in expected_columns["knowledge_documents"]


def test_build_table_column_diffs_detects_missing_is_enabled() -> None:
    """列差异检测应识别 is_enabled 缺失场景。"""

    module = _load_script_module()
    diffs = module.build_table_column_diffs(
        expected_table_columns={
            "knowledge_bases": {"id", "name", "is_enabled"},
            "knowledge_documents": {"id", "knowledge_base_id", "is_enabled"},
        },
        actual_table_columns={
            "knowledge_bases": {"id", "name"},
            "knowledge_documents": {"id", "knowledge_base_id", "legacy_field"},
        },
    )

    assert diffs["knowledge_bases"].missing_columns == {"is_enabled"}
    assert diffs["knowledge_bases"].extra_columns == set()
    assert diffs["knowledge_bases"].has_drift is True

    assert diffs["knowledge_documents"].missing_columns == {"is_enabled"}
    assert diffs["knowledge_documents"].extra_columns == {"legacy_field"}
    assert diffs["knowledge_documents"].has_drift is True


def test_build_boolean_column_sql_contains_idempotent_statements() -> None:
    """升级 SQL 应包含加列、回填和索引创建三步。"""

    module = _load_script_module()
    statements = module._build_boolean_column_sql("knowledge_bases", "is_enabled")

    assert len(statements) == 3
    assert 'ADD COLUMN IF NOT EXISTS "is_enabled" BOOLEAN NOT NULL DEFAULT TRUE' in statements[0]
    assert 'SET "is_enabled" = TRUE WHERE "is_enabled" IS NULL' in statements[1]
    assert 'CREATE INDEX IF NOT EXISTS "ix_knowledge_bases_is_enabled"' in statements[2]
