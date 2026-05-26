"""开发环境数据库初始化脚本的最小验证。"""

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


def test_build_initialization_report_tracks_created_existing_and_missing_tables() -> None:
    """建表前后汇总应正确区分已存在、新创建和缺失表。"""

    module = _load_script_module()
    report = module.build_initialization_report(
        database_host="localhost",
        database_port=5432,
        database_name="super_interview",
        expected_tables={"a", "b", "c"},
        existing_tables_before={"a", "legacy"},
        existing_tables_after={"a", "b", "legacy"},
    )

    assert report.preexisting_tables == {"a"}
    assert report.created_tables == {"b"}
    assert report.missing_tables == {"c"}
    assert report.detected_target_table_count == 2
    assert report.is_successful is False


def test_build_initialization_report_marks_success_when_all_tables_exist() -> None:
    """当所有目标表都已存在时，应返回成功状态。"""

    module = _load_script_module()
    report = module.build_initialization_report(
        database_host="localhost",
        database_port=5432,
        database_name="super_interview",
        expected_tables={"knowledge_bases", "knowledge_documents"},
        existing_tables_before={"knowledge_bases"},
        existing_tables_after={"knowledge_bases", "knowledge_documents"},
    )

    assert report.preexisting_tables == {"knowledge_bases"}
    assert report.created_tables == {"knowledge_documents"}
    assert report.missing_tables == set()
    assert report.is_successful is True
