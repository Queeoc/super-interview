"""真实调用 github_repo_evidence_tool，并模拟正式面试里的 GitHub follow-up 输入。

输出内容包括：
1. 简历里绑定到仓库的项目描述
2. executor 按正式面试逻辑构造出的 GitHub 工具参数
3. latest_tool_context
4. 最终传给 LLM 的 follow_up_evidence_context

示例：
    conda run -n biz_agent python scripts/test_github_repo_evidence_real_case.py
    conda run -n biz_agent python scripts/test_github_repo_evidence_real_case.py --output-file tmp/github_repo_evidence_real_case_output.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.agent.interview.executor import InterviewExecutor
from app.agent.interview.prompts import InterviewPromptRunner
from app.tools.github_repo_evidence_tool import GitHubRepoEvidenceToolResult
from app.utils.logger import setup_logger

DEFAULT_REPO_URL = "https://github.com/Snailclimb/interview-guide"
DEFAULT_PROJECT_TITLE = "InterviewGuide 智能 AI 面试平台"
DEFAULT_PROJECT_DESCRIPTION = (
    "负责智能简历分析、模拟面试、知识库检索增强和面试报告生成链路的架构设计与核心开发，"
    "重点实现 Redis Stream 异步处理、RAG 检索编排和 follow-up 追问生成。"
)
DEFAULT_QUESTION_TEXT = "你刚才提到负责智能面试平台，请继续追问代码里是如何做异步编排、仓库检索和多轮追问生成的。"
DEFAULT_ANSWER_TEXT = "我主要负责整体架构，把异步任务、知识库检索和面试追问链路串起来了。"
DEFAULT_FOCUS_TOPICS = "Redis Stream,SpringAI,WebSocket,RAGService"
DEFAULT_MISSING_SIGNALS = "具体代码入口,异步消费链路,真实工具调用过程,关键类或方法"
DEFAULT_SEARCH_KEYWORDS = "Redis Stream,RedisService,StreamConsumer,InterviewQuestionService"
DEFAULT_QUESTION_INTENT = "考察候选人是否真的实现过异步任务编排、检索增强和追问生成，而不是停留在概念描述。"
DEFAULT_OUTPUT_FILE = "tmp/github_repo_evidence_real_case_output.md"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="真实调用 github_repo_evidence_tool 并输出正式面试链路上下文")
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="简历里的 GitHub 仓库链接")
    parser.add_argument("--project-title", default=DEFAULT_PROJECT_TITLE, help="简历里的项目标题")
    parser.add_argument("--project-description", default=DEFAULT_PROJECT_DESCRIPTION, help="简历里的项目描述")
    parser.add_argument("--question-text", default=DEFAULT_QUESTION_TEXT, help="当前主问题")
    parser.add_argument("--answer-text", default=DEFAULT_ANSWER_TEXT, help="候选人最近一次回答")
    parser.add_argument("--search-keywords", default=DEFAULT_SEARCH_KEYWORDS, help="逗号分隔的显式 search_keywords")
    parser.add_argument("--focus-topics", default=DEFAULT_FOCUS_TOPICS, help="逗号分隔的 follow_up_focus")
    parser.add_argument("--missing-signals", default=DEFAULT_MISSING_SIGNALS, help="逗号分隔的 missing_signals")
    parser.add_argument("--question-intent", default=DEFAULT_QUESTION_INTENT, help="追问意图")
    parser.add_argument("--category-key", default="PROJECT", help="分类键")
    parser.add_argument("--top-k", type=int, default=2, help="期望代码片段数")
    parser.add_argument("--output-file", default=DEFAULT_OUTPUT_FILE, help="Markdown 输出路径")
    parser.add_argument("--max-json-chars", type=int, default=24000, help="JSON 最长展示长度")
    return parser


def _split_csv_text(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _build_resume_markdown(*, project_title: str, project_description: str, repo_url: str) -> str:
    return (
        "# 项目经历\n"
        f"## {project_title}\n"
        f"- 项目描述：{project_description}\n"
        f"- GitHub：{repo_url}\n"
        "- 角色：后端 / Agent 基础设施负责人\n"
        "- 亮点：MCP 工具集成、仓库检索、追问生成、异步任务编排\n"
    )


def _build_executor_payload(
    *,
    question_text: str,
    answer_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
    question_intent: str,
    category_key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current_question = {
        "question_key": "q-1",
        "round_index": 1,
        "category_key": category_key,
        "question_text": question_text,
        "parent_question_key": None,
        "source": "planned",
        "status": "asked",
        "is_follow_up": False,
        "asked_at": None,
        "answered_at": None,
    }
    blueprint = {
        "question_key": "q-1",
        "category_key": category_key,
        "question_text": question_text,
        "round_index": 1,
        "intent": question_intent,
        "must_observe_signals": missing_signals[:3],
        "follow_up_focus": focus_topics,
        "completion_criteria": ["说清楚真实代码入口、关键组件和执行链路"],
        "priority": 1,
        "can_skip": False,
    }
    coverage = {
        "main_question_key": "q-1",
        "question_key": "q-1",
        "required": True,
        "status": "partial",
        "confidence": 0.25,
        "observed_signals": ["做过整体架构", "负责过平台链路"],
        "missing_signals": missing_signals,
        "follow_up_count": 0,
        "evidence_count": 0,
        "completed": False,
        "last_updated": None,
        "answer_text": answer_text,
    }
    return current_question, blueprint, coverage


def _truncate_json(value: Any, max_chars: int) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + "\n...<truncated>..."


def _render_markdown(
    *,
    args: argparse.Namespace,
    resume_markdown: str,
    github_arguments: dict[str, Any],
    latest_tool_context: dict[str, Any],
    llm_context: str,
    max_json_chars: int,
) -> str:
    result_payload = latest_tool_context.get("result", {})
    try:
        normalized_result = GitHubRepoEvidenceToolResult.model_validate(result_payload) if result_payload else None
    except Exception:
        normalized_result = None

    lines = [
        "# GitHub Repo Evidence Real Case Output",
        "",
        f"- Generated At: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Repo URL: `{args.repo_url}`",
        f"- Question: `{args.question_text}`",
        f"- Answer: `{args.answer_text}`",
        f"- Search Keywords: `{', '.join(_split_csv_text(args.search_keywords))}`",
        f"- Focus Topics: `{', '.join(_split_csv_text(args.focus_topics))}`",
        f"- Missing Signals: `{', '.join(_split_csv_text(args.missing_signals))}`",
        "",
        "## Resume Markdown",
        "",
        "```md",
        resume_markdown.rstrip(),
        "```",
        "",
        "## Executor GitHub Arguments",
        "",
        "```json",
        _truncate_json(github_arguments, max_json_chars),
        "```",
        "",
        "## latest_tool_context",
        "",
        "```json",
        _truncate_json(latest_tool_context, max_json_chars),
        "```",
        "",
    ]

    if normalized_result is not None:
        lines.extend(
            [
                "## Tool Summary",
                "",
                f"- Found: `{normalized_result.found}`",
                f"- Retrieval Reason: `{normalized_result.retrieval_reason}`",
                f"- Repo Name: `{normalized_result.repo_name}`",
                f"- Matched Files: `{', '.join(normalized_result.matched_files) if normalized_result.matched_files else '(none)'}`",
                f"- Item Count: `{len(normalized_result.items)}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Final follow_up_evidence_context Sent To LLM",
            "",
            "```text",
            llm_context.rstrip(),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


async def _async_main() -> int:
    args = _build_arg_parser().parse_args()
    setup_logger(force=True)

    resume_markdown = _build_resume_markdown(
        project_title=args.project_title,
        project_description=args.project_description,
        repo_url=args.repo_url,
    )
    focus_topics = _split_csv_text(args.focus_topics)[:4]
    missing_signals = _split_csv_text(args.missing_signals)[:4]
    search_keywords = _split_csv_text(args.search_keywords)[:4]
    current_question, blueprint, coverage = _build_executor_payload(
        question_text=args.question_text,
        answer_text=args.answer_text,
        focus_topics=focus_topics,
        missing_signals=missing_signals,
        question_intent=args.question_intent,
        category_key=args.category_key,
    )

    executor = InterviewExecutor(InterviewPromptRunner(enable_llm=False))
    github_arguments = executor._build_github_evidence_tool_arguments(
        current_question=current_question,
        blueprint=blueprint,
        coverage=coverage,
        answer_text=args.answer_text,
        top_k=args.top_k,
        keyword_candidates=search_keywords,
    )
    normalized_arguments = executor._normalize_evidence_tool_arguments(
        tool_name="github_repo_evidence_tool",
        arguments=github_arguments,
        current_question=current_question,
        blueprint=blueprint,
        coverage=coverage,
        answer_text=args.answer_text,
    )

    logger.info(
        "running formal interview style github evidence case repo_url={}, keywords={}",
        args.repo_url,
        normalized_arguments.get("search_keywords", []),
    )
    latest_tool_context = await executor._invoke_interview_tool(
        tool_name="github_repo_evidence_tool",
        arguments=normalized_arguments,
        decision_reason="manual_script_test_formal_interview",
        resume_markdown=resume_markdown,
        resume_metadata={},
    )
    llm_context = executor._build_follow_up_evidence_context(latest_tool_context)

    markdown_output = _render_markdown(
        args=args,
        resume_markdown=resume_markdown,
        github_arguments=normalized_arguments,
        latest_tool_context=latest_tool_context,
        llm_context=llm_context,
        max_json_chars=args.max_json_chars,
    )

    output_path = Path(args.output_file).expanduser()
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_output, encoding="utf-8")

    logger.info(
        "formal interview real case output saved path={}, success={}, retrieval_reason={}",
        output_path,
        latest_tool_context.get("success"),
        latest_tool_context.get("result", {}).get("retrieval_reason"),
    )
    print(markdown_output)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
