"""开发环境数据库重建脚本的最小验证。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_script_module():
    """按文件路径加载初始化脚本模块。"""

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "init_dev_db.py"
    spec = importlib.util.spec_from_file_location("init_dev_db", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError("无法加载 scripts/init_dev_db.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_get_expected_tables_returns_required_tables() -> None:
    """脚本应声明开发环境必须存在的目标表。"""

    module = _load_script_module()
    assert module.get_expected_tables() == {
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


def test_get_expected_table_columns_includes_resume_markdown_content() -> None:
    """ORM 元数据应包含当前受管表的最新列定义。"""

    module = _load_script_module()
    module.import_model_modules()

    expected_columns = module.get_expected_table_columns()

    assert "resumes" in expected_columns
    assert "markdown_content" in expected_columns["resumes"]
    assert "source_metadata_json" in expected_columns["resumes"]


def test_build_table_column_diffs_detects_missing_and_extra_columns() -> None:
    """列漂移诊断应识别缺列、多列和缺表。"""

    module = _load_script_module()
    diffs = module.build_table_column_diffs(
        expected_table_columns={
            "resumes": {"id", "content_hash", "markdown_content"},
            "knowledge_bases": {"id", "name"},
        },
        actual_table_columns={
            "resumes": {"id", "content_hash", "legacy_field"},
            "knowledge_bases": set(),
        },
    )

    resumes_diff = diffs["resumes"]
    assert resumes_diff.missing_columns == {"markdown_content"}
    assert resumes_diff.extra_columns == {"legacy_field"}
    assert resumes_diff.is_missing_table is False
    assert resumes_diff.has_drift is True

    kb_diff = diffs["knowledge_bases"]
    assert kb_diff.missing_columns == {"id", "name"}
    assert kb_diff.extra_columns == set()
    assert kb_diff.is_missing_table is True
    assert kb_diff.has_drift is True


def test_build_initialization_report_tracks_unmanaged_tables_and_drift() -> None:
    """重建报告应正确记录非受管表和列漂移。"""

    module = _load_script_module()
    table_column_diffs = module.build_table_column_diffs(
        expected_table_columns={
            "a": {"id", "markdown_content"},
            "b": {"id"},
            "c": {"id"},
        },
        actual_table_columns={
            "a": {"id"},
            "b": {"id", "legacy_field"},
            "c": set(),
        },
    )
    report = module.build_initialization_report(
        database_host="localhost",
        database_port=5432,
        database_name="super_interview",
        reset_strategy=module.RESET_STRATEGY,
        schema_name=module.PUBLIC_SCHEMA_NAME,
        expected_tables={"a", "b", "c"},
        existing_tables_before={"a", "b", "legacy"},
        existing_tables_after={"a", "b", "c"},
        table_column_diffs=table_column_diffs,
    )

    assert report.reset_strategy == "reset_public_schema"
    assert report.schema_name == "public"
    assert report.preexisting_tables == {"a", "b"}
    assert report.created_tables == {"a", "b", "c"}
    assert report.missing_tables == set()
    assert report.unmanaged_tables_before == {"legacy"}
    assert report.drifted_tables == {"a", "b", "c"}
    assert report.detected_target_table_count == 3
    assert report.is_successful is True


def test_build_initialization_report_marks_failure_when_target_table_missing_after_rebuild() -> None:
    """若重建后仍缺表，应返回失败状态。"""

    module = _load_script_module()
    report = module.build_initialization_report(
        database_host="localhost",
        database_port=5432,
        database_name="super_interview",
        reset_strategy=module.RESET_STRATEGY,
        schema_name=module.PUBLIC_SCHEMA_NAME,
        expected_tables={"knowledge_bases", "knowledge_documents"},
        existing_tables_before={"knowledge_bases", "legacy_cache"},
        existing_tables_after={"knowledge_bases"},
        table_column_diffs={},
    )

    assert report.created_tables == {"knowledge_bases"}
    assert report.missing_tables == {"knowledge_documents"}
    assert report.unmanaged_tables_before == {"legacy_cache"}
    assert report.is_successful is False
