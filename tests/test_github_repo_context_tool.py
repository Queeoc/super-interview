"""github_repo_context_tool 的单元测试。"""

from __future__ import annotations

import importlib
import json

import pytest

from app.tools.github_repo_context_tool import github_repo_context_tool

context_tool_module = importlib.import_module("app.tools.github_repo_context_tool")


@pytest.mark.asyncio
async def test_github_repo_context_tool_extracts_readme_sections_with_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """README 应被切成高价值 section，并按问题相关性选出结构化结果。"""

    async def _fake_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> list[dict[str, str]]:
        assert owner == "Snailclimb"
        assert repo == "interview-guide"
        if path == "":
            return [
                {
                    "text": json.dumps(
                        [
                            {"type": "file", "name": "README.md", "path": "README.md", "size": 1200},
                            {"type": "file", "name": "pom.xml", "path": "pom.xml", "size": 2048},
                            {"type": "dir", "name": "app", "path": "app"},
                            {"type": "dir", "name": "frontend", "path": "frontend"},
                        ]
                    )
                }
            ]
        if path == "README.md":
            return [
                {
                    "text": (
                        "<div align=\"center\">\n"
                        "[![CI](https://img.shields.io/badge/ci-pass-green)](https://example.com)\n"
                        "**InterviewGuide**\n"
                        "持续更新的智能 AI 面试辅助与简历分析平台。\n"
                        "</div>\n\n"
                        "## 模块结构\n"
                        "- app: 负责后端服务与模拟面试流程\n"
                        "- frontend: 负责前端交互与实时体验\n"
                        "- docs: 负责知识库与文档整理\n\n"
                        "## 技术栈\n"
                        "Java 21 / Spring Boot 4.1 / PostgreSQL / Redis / Docker Compose\n\n"
                        "## Roadmap\n"
                        "- 持续更新知识库\n"
                        "- 规划新的面试能力\n"
                    )
                }
            ]
        if path == "pom.xml":
            return [
                {
                    "text": (
                        "<project>\n"
                        "  <properties>\n"
                        "    <java.version>21</java.version>\n"
                        "    <spring-boot.version>4.1.0</spring-boot.version>\n"
                        "  </properties>\n"
                        "</project>"
                    )
                }
            ]
        raise FileNotFoundError(path)

    monkeypatch.setattr(context_tool_module, "_invoke_github_get_file_contents", _fake_get_file_contents)

    result = await github_repo_context_tool(
        resume_markdown=(
            "# 项目经历\n"
            "## 项目一：InterviewGuide — 智能AI面试辅助与简历分析平台\n"
            "* **担任角色**：独立架构师 / 核心开发\n"
            "* **项目时间**：2025.03 - 2026.04\n"
            "* **项目地址**：[https://github.com/Snailclimb/interview-guide]"
            "(https://github.com/Snailclimb/interview-guide)\n"
            "* **项目描述**：InterviewGuide 是一个智能面试全栈平台。\n"
        ),
        resume_metadata={},
        question_text="请介绍这个项目的模块结构、核心能力以及你是如何持续维护它的",
        answer_text="我主要负责整体架构、RAG 能力和模拟面试流程的设计实现。",
        focus_topics=["模块结构", "维护", "技术栈"],
        missing_signals=["技术栈", "实现细节"],
    )

    assert result.found is True
    assert result.repo_url == "https://github.com/Snailclimb/interview-guide"
    assert result.matched_project_title == "项目一：InterviewGuide — 智能AI面试辅助与简历分析平台"
    assert "持续更新" in result.readme_intro
    assert result.readme_summary == result.readme_intro
    section_map = {section.heading: section for section in result.readme_sections}
    assert set(section_map) == {"模块结构", "技术栈", "Roadmap"}
    assert section_map["模块结构"].section_type == "module_structure"
    assert section_map["技术栈"].section_type == "tech_stack"
    assert section_map["Roadmap"].section_type == "maintenance_signal"
    assert max(result.readme_sections, key=lambda item: item.relevance_score).heading in {"模块结构", "技术栈"}
    assert [summary.path for summary in result.key_file_summaries] == ["pom.xml"]


@pytest.mark.asyncio
async def test_github_repo_context_tool_prefers_most_relevant_repo_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个链接时，应优先选择与当前问题更相关的项目段落。"""

    selected_repos: list[str] = []

    async def _fake_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> list[dict[str, str]]:
        selected_repos.append(f"{owner}/{repo}:{path}")
        if path == "":
            return [{"text": json.dumps([{"type": "file", "name": "README.md", "path": "README.md"}])}]
        if path == "README.md":
            return [{"text": f"# {repo}\nPython 数据平台，支持持续维护和数据处理。"}]
        raise FileNotFoundError(path)

    monkeypatch.setattr(context_tool_module, "_invoke_github_get_file_contents", _fake_get_file_contents)

    result = await github_repo_context_tool(
        resume_markdown=(
            "# 项目经历\n"
            "## Java 电商项目\n"
            "- GitHub: https://github.com/demo/java-shop\n\n"
            "## Python 数据平台\n"
            "- 负责数据处理与长期维护\n"
            "- GitHub: https://github.com/demo/python-data-platform\n"
        ),
        resume_metadata={},
        question_text="请讲讲你这个 Python 项目的维护方式",
        answer_text="我做过一些维护和迭代。",
        focus_topics=["Python", "维护"],
        missing_signals=["长期维护方式"],
    )

    assert result.repo_name == "demo/python-data-platform"
    assert selected_repos[0].startswith("demo/python-data-platform:")


@pytest.mark.asyncio
async def test_github_repo_context_tool_scores_maintenance_section_above_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当问题偏维护时，maintenance_signal 应高于普通项目概述。"""

    async def _fake_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> list[dict[str, str]]:
        if path == "":
            return [{"text": json.dumps([{"type": "file", "name": "README.md", "path": "README.md"}])}]
        if path == "README.md":
            return [
                {
                    "text": (
                        "# Cache Platform\n"
                        "分布式缓存平台。\n\n"
                        "## Features\n"
                        "支持缓存访问和服务能力。\n\n"
                        "## Roadmap\n"
                        "- 持续维护缓存策略\n"
                        "- 规划版本迭代与更新节奏\n"
                    )
                }
            ]
        raise FileNotFoundError(path)

    monkeypatch.setattr(context_tool_module, "_invoke_github_get_file_contents", _fake_get_file_contents)

    result = await github_repo_context_tool(
        resume_markdown="# 项目经历\n## Cache Platform\n- GitHub: https://github.com/demo/cache-platform\n",
        resume_metadata={},
        question_text="这个项目后续是怎么持续维护和更新的？",
        answer_text="我做了一些维护。",
        focus_topics=["维护", "更新"],
        missing_signals=["长期维护方式"],
    )

    assert [section.heading for section in result.readme_sections] == ["Roadmap"]
    assert result.readme_sections[0].section_type == "maintenance_signal"
    assert result.readme_sections[0].relevance_score >= 0.45


@pytest.mark.asyncio
async def test_github_repo_context_tool_falls_back_to_key_files_when_readme_is_weak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """README 信息不足时，仍可依赖根目录和关键文件形成稳定上下文。"""

    async def _fake_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> list[dict[str, str]]:
        if path == "":
            return [
                {
                    "text": json.dumps(
                        [
                            {"type": "file", "name": "README.md", "path": "README.md"},
                            {"type": "file", "name": "Dockerfile", "path": "Dockerfile"},
                        ]
                    )
                }
            ]
        if path == "README.md":
            return [{"text": "[![CI](https://img.shields.io)](https://example.com)\nHi"}]
        if path == "Dockerfile":
            return [{"text": "FROM python:3.11-slim\nRUN uvicorn app.main:app"}]
        raise FileNotFoundError(path)

    monkeypatch.setattr(context_tool_module, "_invoke_github_get_file_contents", _fake_get_file_contents)

    result = await github_repo_context_tool(
        resume_markdown="# 项目经历\n## 项目 A\n- GitHub: https://github.com/demo/project-a\n",
        resume_metadata={},
        question_text="请介绍项目实现",
        answer_text="我负责后端。",
        focus_topics=["实现"],
        missing_signals=["细节"],
    )

    assert result.found is True
    assert result.readme_sections == []
    assert [item.path for item in result.key_file_summaries] == ["Dockerfile"]


@pytest.mark.asyncio
async def test_github_repo_context_tool_returns_stable_empty_when_repo_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仓库访问失败时，应返回稳定空结果而不是抛异常。"""

    async def _fake_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> list[dict[str, str]]:
        raise RuntimeError("mcp unavailable")

    monkeypatch.setattr(context_tool_module, "_invoke_github_get_file_contents", _fake_get_file_contents)

    result = await github_repo_context_tool(
        resume_markdown="# 项目经历\n## 项目 A\n- GitHub: https://github.com/demo/project-a\n",
        resume_metadata={},
        question_text="请介绍项目实现",
        answer_text="我负责后端。",
        focus_topics=["实现"],
        missing_signals=["细节"],
    )

    assert result.found is False
    assert result.repo_name == "demo/project-a"
    assert result.retrieval_reason == "github_repo_fetch_failed"
    assert result.error_message == "mcp unavailable"
