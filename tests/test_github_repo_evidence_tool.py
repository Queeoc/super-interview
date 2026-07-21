"""github_repo_evidence_tool 的单元测试。"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.tools.github_repo_context_tool import GitHubRepoBindingResult
from app.tools.github_repo_evidence_tool import github_repo_evidence_tool

evidence_tool_module = importlib.import_module("app.tools.github_repo_evidence_tool")


def _mock_binding() -> GitHubRepoBindingResult:
    return GitHubRepoBindingResult(
        found=True,
        repo_url="https://github.com/alice/interview-agent",
        repo_name="alice/interview-agent",
        owner="alice",
        repo="interview-agent",
        matched_project_title="AI 面试 Agent 项目",
        matched_project_excerpt="负责 Agent 编排、异步执行和追问链路设计。",
        retrieval_reason="github_repo_binding_ready",
    )


def _install_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        evidence_tool_module,
        "resolve_github_repo_binding",
        lambda **_: _mock_binding(),
    )


def _install_github_mcp_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    search_results: dict[str, list[str] | Exception],
    file_contents: dict[str, str],
    call_log: list[tuple[str, dict[str, Any]]],
) -> None:
    async def _fake_invoke_github_mcp_tool(tool_name: str, tool_args: dict[str, Any]) -> Any:
        call_log.append((tool_name, dict(tool_args)))
        if tool_name == "search_code":
            payload = search_results.get(tool_args["query"], [])
            if isinstance(payload, Exception):
                raise payload
            return json.dumps({"items": [{"path": path} for path in payload]})
        if tool_name == "get_file_contents":
            return file_contents.get(tool_args["path"], "")
        raise AssertionError(f"unexpected tool invocation: {tool_name}")

    monkeypatch.setattr(
        evidence_tool_module,
        "invoke_github_mcp_tool",
        _fake_invoke_github_mcp_tool,
    )


def _runtime_file_content() -> str:
    return "\n".join(
        [
            "import asyncio",
            "",
            "class CustomAgent:",
            "    def __init__(self, scheduler):",
            "        self.scheduler = scheduler",
            "",
            "    async def run(self, tasks: list[str]) -> list[str]:",
            "        prepared = [self._wrap_task(task) for task in tasks]",
            "        results = await asyncio.gather(*prepared)",
            "        return [item for item in results if item]",
            "",
            "    async def _wrap_task(self, task: str) -> str:",
            "        await self.scheduler.wait_for_slot(task)",
            "        return task.upper()",
            "",
            "async def bootstrap(agent: CustomAgent, tasks: list[str]) -> list[str]:",
            "    return await agent.run(tasks)",
        ]
    )


def _custom_agent_file_content() -> str:
    return "\n".join(
        [
            "import asyncio",
            "",
            "class CustomAgent:",
            "    def __init__(self, redis_lock):",
            "        self.redis_lock = redis_lock",
            "",
            "    async def handle(self, question: str) -> str:",
            "        async with self.redis_lock:",
            "            await asyncio.sleep(0)",
            "            return question.strip()",
            "",
            "    async def follow_up(self, question: str) -> str:",
            "        return await self.handle(question)",
        ]
    )


def _follow_up_service_content() -> str:
    return "\n".join(
        [
            "package interview.agent.workflow;",
            "",
            "public class FollowUpService {",
            "    public String buildPrompt(String answer) {",
            '        String provider = "SpringAI";',
            "        return provider + answer;",
            "    }",
            "}",
        ]
    )


def _spring_ai_configuration_content() -> str:
    return "\n".join(
        [
            "package interview.agent.common.ai;",
            "",
            "import org.springframework.context.annotation.Configuration;",
            "",
            "@Configuration",
            "public class AgentUtilsConfiguration {",
            "    private String provider = \"SpringAI\";",
            "}",
        ]
    )


def _redis_service_content() -> str:
    return "\n".join(
        [
            "package interview.guide.infrastructure.redis;",
            "",
            "public class RedisService {",
            "    public void addStreamMessage(String streamKey, String payload) {",
            "        // Redis Stream producer entrypoint",
            "    }",
            "}",
        ]
    )


def _stream_consumer_with_business_method_content() -> str:
    return "\n".join(
        [
            "package interview.guide.common.async;",
            "",
            "public class AbstractStreamConsumer {",
            "    private final RedisService redisService;",
            "",
            "    protected AbstractStreamConsumer(RedisService redisService) {",
            "        this.redisService = redisService;",
            "    }",
            "",
            "    public void consume(String streamKey) {",
            "        var messages = redisService.readStream(streamKey);",
            "        for (var message : messages) {",
            "            try {",
            "                handle(message);",
            "                redisService.ack(streamKey, message.id());",
            "            } catch (Exception ex) {",
            "                redisService.retry(streamKey, message.id());",
            "            }",
            "        }",
            "    }",
            "}",
        ]
    )


def _service_with_constructor_and_follow_up_method_content() -> str:
    return "\n".join(
        [
            "package interview.guide.modules.interview.service;",
            "",
            "public class InterviewQuestionService {",
            "    private final PromptLoader promptLoader;",
            "    private final OutputInvoker outputInvoker;",
            "",
            "    public InterviewQuestionService(PromptLoader promptLoader, OutputInvoker outputInvoker) {",
            "        this.promptLoader = promptLoader;",
            "        this.outputInvoker = outputInvoker;",
            "        this.systemPrompt = promptLoader.loadTemplate(\"system\");",
            "        this.userPrompt = promptLoader.loadTemplate(\"user\");",
            "    }",
            "",
            "    public List<String> generateFollowUps(List<String> answers) {",
            "        List<String> prompts = promptLoader.buildFollowUpPrompts(answers);",
            "        List<String> result = new ArrayList<>();",
            "        for (String prompt : prompts) {",
            "            result.add(outputInvoker.invoke(prompt));",
            "        }",
            "        return result;",
            "    }",
            "}",
        ]
    )


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_returns_code_snippet_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_code 命中后，应返回带行号和关键词的代码片段卡。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "asyncio repo:alice/interview-agent": ["app/runtime.py"],
            "CustomAgent repo:alice/interview-agent": ["app/runtime.py"],
        },
        file_contents={"app/runtime.py": _runtime_file_content()},
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲你们的异步 Agent 执行链路。",
        answer_text="我们用异步任务处理追问。",
        search_keywords=["asyncio", "CustomAgent"],
        top_k=2,
    )

    assert result.found is True
    assert result.retrieval_reason == "github_code_search_matched"
    assert result.matched_files == ["app/runtime.py"]
    assert len(result.items) == 1
    item = result.items[0]
    assert item.evidence_type == "code_snippet"
    assert item.source_path == "app/runtime.py"
    assert item.language == "python"
    assert item.start_line is not None
    assert item.end_line is not None
    assert item.start_line < item.end_line
    assert set(item.matched_terms) >= {"asyncio", "CustomAgent"}
    assert "app/runtime.py" in item.why_it_matched
    assert [tool_name for tool_name, _ in call_log].count("search_code") == 2


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_merges_keyword_hits_and_prefers_source_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个关键词命中同一源码文件时，应合并去重并压过配置文件兜底。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "CustomAgent repo:alice/interview-agent": [
                "app/agents/custom_agent.py",
                "docker-compose.yml",
            ],
            "asyncio repo:alice/interview-agent": ["app/agents/custom_agent.py"],
        },
        file_contents={
            "app/agents/custom_agent.py": _custom_agent_file_content(),
            "docker-compose.yml": "services:\n  app:\n    image: interview-agent\n",
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="CustomAgent 是怎么做异步追问的？",
        answer_text="主要靠统一调度。",
        search_keywords=["CustomAgent", "asyncio"],
        top_k=2,
    )

    assert result.found is True
    assert result.matched_files == ["app/agents/custom_agent.py"]
    assert len(result.items) == 1
    assert result.items[0].matched_terms == ["CustomAgent", "asyncio"]
    fetched_paths = [
        tool_args["path"]
        for tool_name, tool_args in call_log
        if tool_name == "get_file_contents"
    ]
    assert fetched_paths == ["app/agents/custom_agent.py"]


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_excludes_supporting_source_when_implementation_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务实现源码和接入配置源码同时命中时，应只抓取业务实现源码。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "SpringAI repo:alice/interview-agent": [
                "app/src/main/java/interview/agent/common/ai/AgentUtilsConfiguration.java",
                "app/src/main/java/interview/agent/workflow/FollowUpService.java",
            ],
        },
        file_contents={
            "app/src/main/java/interview/agent/common/ai/AgentUtilsConfiguration.java": _spring_ai_configuration_content(),
            "app/src/main/java/interview/agent/workflow/FollowUpService.java": _follow_up_service_content(),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲 SpringAI 在追问主链路里的使用。",
        answer_text="我负责 follow-up 追问生成链路。",
        search_keywords=["SpringAI"],
        top_k=2,
    )

    fetched_paths = [
        tool_args["path"]
        for tool_name, tool_args in call_log
        if tool_name == "get_file_contents"
    ]
    assert result.found is True
    assert result.matched_files == ["app/src/main/java/interview/agent/workflow/FollowUpService.java"]
    assert fetched_paths == ["app/src/main/java/interview/agent/workflow/FollowUpService.java"]
    assert result.items[0].heading == "buildPrompt"


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_anchors_snippet_to_body_hit_not_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关键词同时出现在 import 和方法体时，窗口应锚定到业务方法。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "SpringAI repo:alice/interview-agent": [
                "app/src/main/java/interview/agent/workflow/FollowUpService.java",
            ],
        },
        file_contents={
            "app/src/main/java/interview/agent/workflow/FollowUpService.java": "\n".join(
                [
                    "package interview.agent.workflow;",
                    "import com.example.SpringAI;",
                    "",
                    "public class FollowUpService {",
                    "    public String buildPrompt(String answer) {",
                    '        String provider = "SpringAI";',
                    "        return provider + answer;",
                    "    }",
                    "}",
                ]
            ),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲 SpringAI 在追问主链路里的使用。",
        answer_text="我负责 follow-up 追问生成链路。",
        search_keywords=["SpringAI"],
        top_k=2,
    )

    assert result.found is True
    item = result.items[0]
    assert item.heading == "buildPrompt"
    assert item.start_line == 5
    assert item.raw_excerpt.startswith("    public String buildPrompt")


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_prefers_business_window_over_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖注入和业务方法都命中时，应优先截取业务动作密度更高的窗口。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "RedisService repo:alice/interview-agent": [
                "app/src/main/java/interview/guide/common/async/AbstractStreamConsumer.java",
            ],
            "StreamConsumer repo:alice/interview-agent": [
                "app/src/main/java/interview/guide/common/async/AbstractStreamConsumer.java",
            ],
        },
        file_contents={
            "app/src/main/java/interview/guide/common/async/AbstractStreamConsumer.java": (
                _stream_consumer_with_business_method_content()
            ),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲 Redis Stream 消费链路。",
        answer_text="我负责异步消费。",
        search_keywords=["RedisService", "StreamConsumer"],
        top_k=2,
    )

    assert result.found is True
    item = result.items[0]
    assert item.heading == "consume"
    assert "readStream" in item.raw_excerpt
    assert "ack" in item.raw_excerpt
    assert "retry" in item.raw_excerpt


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_moves_same_file_constructor_to_business_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关键词只命中文件同名构造器时，也应尽量向下挪到业务方法窗口。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "InterviewQuestionService repo:alice/interview-agent": [
                "app/src/main/java/interview/guide/modules/interview/service/InterviewQuestionService.java",
            ],
        },
        file_contents={
            "app/src/main/java/interview/guide/modules/interview/service/InterviewQuestionService.java": (
                _service_with_constructor_and_follow_up_method_content()
            ),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲多轮追问生成链路。",
        answer_text="我负责追问生成。",
        search_keywords=["InterviewQuestionService"],
        top_k=2,
    )

    assert result.found is True
    item = result.items[0]
    assert item.heading == "generateFollowUps"
    assert "public InterviewQuestionService" not in item.raw_excerpt
    assert "buildFollowUpPrompts" in item.raw_excerpt
    assert "outputInvoker.invoke" in item.raw_excerpt


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_falls_back_to_config_files_when_source_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当源码文件完全无命中时，应允许配置文件兜底。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "redis repo:alice/interview-agent": ["pyproject.toml"],
        },
        file_contents={
            "pyproject.toml": "\n".join(
                [
                    "[project]",
                    'name = "interview-agent"',
                    "dependencies = [",
                    '  "redis>=5.0",',
                    '  "aioredlock>=0.7.0",',
                    "]",
                ]
            )
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="你们怎么处理 Redis 锁依赖？",
        answer_text="项目里做过锁能力接入。",
        search_keywords=["redis"],
        top_k=2,
    )

    assert result.found is True
    assert result.matched_files == ["pyproject.toml"]
    assert len(result.items) == 1
    assert result.items[0].source_path == "pyproject.toml"
    assert result.items[0].heading == "pyproject"
    assert result.items[0].language == "toml"
    assert result.items[0].matched_terms == ["redis"]


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_uses_fallback_queries_when_primary_terms_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原始关键词全空时，应使用有限备用查询命中真实代码命名。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "RedisStream repo:alice/interview-agent": [],
            "RAGService repo:alice/interview-agent": [],
            "FollowUpService repo:alice/interview-agent": [],
            '"Redis Stream" repo:alice/interview-agent': [],
            "Redis repo:alice/interview-agent": [
                "app/src/main/java/interview/guide/infrastructure/redis/RedisService.java"
            ],
        },
        file_contents={
            "app/src/main/java/interview/guide/infrastructure/redis/RedisService.java": _redis_service_content(),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲 Redis Stream 异步处理链路。",
        answer_text="我们用异步任务处理追问。",
        search_keywords=["RedisStream", "RAGService", "FollowUpService"],
        top_k=2,
    )

    search_queries = [tool_args["query"] for tool_name, tool_args in call_log if tool_name == "search_code"]
    assert result.found is True
    assert result.retrieval_reason == "github_code_search_fallback_matched"
    assert result.matched_files == ["app/src/main/java/interview/guide/infrastructure/redis/RedisService.java"]
    assert result.items[0].evidence_type == "code_snippet"
    assert result.items[0].matched_terms == ["RedisStream"]
    assert "备用查询：Redis" in result.items[0].why_it_matched
    assert "RedisStream repo:alice/interview-agent" in search_queries
    assert "Redis repo:alice/interview-agent" in search_queries


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_falls_back_to_readme_when_code_search_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """代码搜索全空但 README 命中时，应返回非代码仓库证据。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={},
        file_contents={
            "README.md": (
                "# Interview Agent\n\n"
                "系统使用 Redis Stream 处理异步任务，并通过 InterviewQuestionService 生成 followUps。"
            ),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲异步编排和追问生成。",
        answer_text="我负责整体架构。",
        search_keywords=["RedisStream", "FollowUpService"],
        top_k=2,
    )

    assert result.found is True
    assert result.retrieval_reason == "github_readme_fallback_matched"
    assert result.matched_files == ["README.md"]
    assert len(result.items) == 1
    assert result.items[0].evidence_type == "repo_readme_excerpt"
    assert result.items[0].source_path == "README.md"
    assert "Redis Stream" in result.items[0].raw_excerpt
    assert "代码搜索为空" in result.items[0].why_it_matched


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_falls_back_to_project_binding_when_readme_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """代码与 README 都无命中时，应使用简历绑定项目片段兜底。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={},
        file_contents={},
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲异步追问链路。",
        answer_text="我负责整体架构。",
        search_keywords=["UnknownPipeline"],
        top_k=2,
    )

    assert result.found is True
    assert result.retrieval_reason == "github_project_binding_fallback"
    assert result.matched_files == ["resume_project_binding"]
    assert result.items[0].evidence_type == "project_binding_excerpt"
    assert result.items[0].source_path == "resume_project_binding"
    assert "Agent 编排" in result.items[0].raw_excerpt


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_returns_unavailable_when_search_code_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_code 不可用时，应直接返回稳定空结果，不递归扫描整个仓库。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "asyncio repo:alice/interview-agent": RuntimeError("github search_code tool not found"),
        },
        file_contents={},
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲 asyncio 的应用。",
        answer_text="我们有异步处理。",
        search_keywords=["asyncio"],
        top_k=2,
    )

    assert result.found is False
    assert result.items == []
    assert result.retrieval_reason == "github_code_search_unavailable"
    assert [tool_name for tool_name, _ in call_log] == ["search_code"]


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_scopes_queries_to_repo_and_caps_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """查询字符串必须带 repo 限定，且 top_k 最终收敛到 2。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={
            "asyncio repo:alice/interview-agent": ["app/runtime.py"],
            "CustomAgent repo:alice/interview-agent": ["app/agents/custom_agent.py"],
        },
        file_contents={
            "app/runtime.py": _runtime_file_content(),
            "app/agents/custom_agent.py": _custom_agent_file_content(),
        },
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="详细讲讲你们的 Agent 运行机制。",
        answer_text="涉及异步和自定义 Agent。",
        search_keywords=["asyncio", "CustomAgent"],
        top_k=5,
    )

    search_queries = [
        tool_args["query"]
        for tool_name, tool_args in call_log
        if tool_name == "search_code"
    ]
    assert result.found is True
    assert len(result.items) == 2
    assert all("repo:alice/interview-agent" in query for query in search_queries)


@pytest.mark.asyncio
async def test_github_repo_evidence_tool_does_not_fallback_to_focus_topics_or_missing_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有显式 search_keywords 时，GitHub 工具应直接返回关键词缺失。"""

    _install_binding(monkeypatch)
    call_log: list[tuple[str, dict[str, Any]]] = []
    _install_github_mcp_stub(
        monkeypatch,
        search_results={},
        file_contents={},
        call_log=call_log,
    )

    result = await github_repo_evidence_tool(
        resume_markdown="# 项目经历\n- GitHub: https://github.com/alice/interview-agent\n",
        resume_metadata={},
        category_key="PROJECT",
        question_text="讲讲你们的异步追问链路。",
        answer_text="我主要负责整体架构。",
        focus_topics=["CustomAgent", "asyncio"],
        missing_signals=["具体代码入口"],
        top_k=2,
    )

    assert result.found is False
    assert result.retrieval_reason == "github_code_keywords_missing"
    assert call_log == []


def test_real_case_markdown_renderer_outputs_readable_chinese() -> None:
    """真实案例 Markdown 应默认适合人工阅读。"""

    script_module = importlib.import_module("scripts.test_github_repo_evidence_real_case")
    result = evidence_tool_module.GitHubRepoEvidenceToolResult(
        found=True,
        repo_url="https://github.com/alice/interview-agent",
        repo_name="alice/interview-agent",
        matched_project_title="AI 面试 Agent 项目",
        matched_project_excerpt="负责追问生成链路。",
        matched_files=["app/agents/custom_agent.py"],
        items=[],
        retrieval_reason="github_code_search_matched",
    )
    args = argparse.Namespace(
        repo_url="https://github.com/alice/interview-agent",
        question_text="请说明追问生成链路。",
        answer_text="我负责 Agent 编排。",
        search_keywords="CustomAgent,asyncio",
        focus_topics="项目,实现",
        missing_signals="代码入口",
    )

    markdown = script_module._render_markdown(
        args=args,
        resume_markdown="# 项目经历\n- GitHub：https://github.com/alice/interview-agent",
        github_arguments={"search_keywords": ["CustomAgent", "asyncio"]},
        latest_tool_context={"success": True, "result": result.model_dump(mode="json")},
        llm_context="命中文件：app/agents/custom_agent.py\n代码片段：\nclass CustomAgent: ...",
        max_json_chars=4000,
    )

    assert "项目经历" in markdown
    assert "Search Keywords: `CustomAgent, asyncio`" in markdown
    assert "命中文件" in markdown
    assert "代码片段" in markdown
    assert "浣犲" not in markdown
    assert "椤圭洰" not in markdown


def test_tool_decision_prompt_guides_mixed_github_search_keywords() -> None:
    """工具决策 prompt 应要求宽窄混合的 GitHub 搜索词。"""

    prompt_text = Path("prompts/interview/tool_decision.st").read_text(encoding="utf-8")

    assert "search_keywords" in prompt_text
    assert "broad technical phrase" in prompt_text
    assert "code-shaped term" in prompt_text
    assert "Redis Stream" in prompt_text
    assert "InterviewQuestionService" in prompt_text
    assert "RAGService" in prompt_text
