"""GitHub 工具联调脚本。

用途：
1. 手工调用 `github_repo_context_tool` / `github_repo_evidence_tool`
2. 打印工具原始返回结果
3. 模拟 InterviewExecutor 中的 `latest_tool_context`
4. 打印项目实际会传给 LLM 的 `follow-up evidence context`

示例：
    conda run -n biz_agent python scripts/test_github_tool_flow.py --show-context-tool
    conda run -n biz_agent python scripts/test_github_tool_flow.py --resume-file ./tmp/resume.md
    conda run -n biz_agent python scripts/test_github_tool_flow.py --question-text "请介绍这个项目的模块结构和维护方式"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.agent.interview.executor import InterviewExecutor
from app.agent.interview.prompts import InterviewPromptRunner
from app.tools.github_repo_context_tool import github_repo_context_tool
from app.tools.github_repo_evidence_tool import github_repo_evidence_tool
from app.utils.logger import setup_logger

_DEFAULT_RESUME_MARKDOWN = """### 项目一：InterviewGuide - 智能AI面试辅助与简历分析平台
* **担任角色**：独立架构师 / 核心开发
* **项目时间**：2025.03 - 2026.04
* **项目地址**：[https://github.com/Snailclimb/interview-guide](https://github.com/Snailclimb/interview-guide)
* **技术栈**：Java 21 / Spring Boot 4.1 / Spring AI 2.0 / PostgreSQL (pgvector) / Redis Stream / MinIO / WebSocket / Docker Compose
* **项目描述**：InterviewGuide 是一个集成了智能简历分析、文字 / 语音多模态模拟面试、面试智能安排、专家级 RAG 知识库管理及多模型动态配置的一站式智能面试全栈平台。系统利用大语言模型（LLM）、向量数据库、异步任务流和实时语音通话技术，解决求职者练习成本高、反馈不及时和面试流程繁琐的痛点。
"""


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub 工具联调脚本")
    parser.add_argument(
        "--resume-file",
        default="",
        help="可选：从文件读取简历 Markdown；不传则使用脚本内置 InterviewGuide 示例",
    )
    parser.add_argument(
        "--question-text",
        default="请介绍这个项目的模块结构、核心能力以及你是如何持续维护它的？",
        help="当前问题",
    )
    parser.add_argument(
        "--answer-text",
        default="我主要负责整体架构、RAG 能力和模拟面试流程的设计实现。",
        help="候选人最近一次回答",
    )
    parser.add_argument(
        "--focus-topics",
        default="模块结构,维护,技术栈",
        help="逗号分隔的 follow-up focus topics",
    )
    parser.add_argument(
        "--missing-signals",
        default="技术栈,实现细节",
        help="逗号分隔的 missing signals",
    )
    parser.add_argument(
        "--category-key",
        default="PROJECT",
        help="问题分类，默认 PROJECT",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="github_repo_evidence_tool 返回证据条数，默认 3",
    )
    parser.add_argument(
        "--show-context-tool",
        action="store_true",
        help="先打印 github_repo_context_tool 的结果，便于看中间层上下文",
    )
    parser.add_argument(
        "--max-output-chars",
        type=int,
        default=12000,
        help="打印文本最大长度，默认 12000",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="可选：把完整输出保存到 UTF-8 文件，便于查看实际传给 LLM 的内容",
    )
    return parser


def _load_resume_markdown(resume_file: str) -> str:
    if not resume_file.strip():
        return _DEFAULT_RESUME_MARKDOWN
    return Path(resume_file).read_text(encoding="utf-8")


def _split_csv_text(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _preview_text(value: Any, max_output_chars: int) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(rendered) <= max_output_chars:
        return rendered
    return rendered[:max_output_chars] + "\n...<truncated>..."


def _append_output_block(
    blocks: list[str],
    *,
    title: str,
    content: Any,
    max_output_chars: int,
) -> None:
    rendered = _preview_text(content, max_output_chars)
    blocks.append(f"## {title}\n\n{rendered}\n")


async def _async_main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    setup_logger()

    resume_markdown = _load_resume_markdown(args.resume_file)
    focus_topics = _split_csv_text(args.focus_topics)
    missing_signals = _split_csv_text(args.missing_signals)
    output_blocks: list[str] = []

    logger.info(
        "开始测试 GitHub 工具链 question_preview={}, focus_topics={}, missing_signals={}",
        args.question_text[:80],
        focus_topics[:4],
        missing_signals[:4],
    )

    common_payload = {
        "resume_markdown": resume_markdown,
        "resume_metadata": {},
        "question_text": args.question_text,
        "answer_text": args.answer_text,
        "focus_topics": focus_topics,
        "missing_signals": missing_signals,
    }
    evidence_arguments = {
        "category_key": args.category_key,
        "question_text": args.question_text,
        "answer_text": args.answer_text,
        "focus_topics": focus_topics,
        "missing_signals": missing_signals,
        "top_k": args.top_k,
    }
    executor = InterviewExecutor(InterviewPromptRunner(enable_llm=False))

    if args.show_context_tool:
        context_result = await github_repo_context_tool(**common_payload)
        logger.info("github_repo_context_tool 原始返回：")
        print(_preview_text(context_result.model_dump(mode="json"), args.max_output_chars))
        print("\n" + "=" * 80 + "\n")
        _append_output_block(
            output_blocks,
            title="github_repo_context_tool 原始返回",
            content=context_result.model_dump(mode="json"),
            max_output_chars=args.max_output_chars,
        )

    evidence_result = await github_repo_evidence_tool(
        **common_payload,
        category_key=args.category_key,
        top_k=args.top_k,
    )
    latest_tool_context = await executor._invoke_interview_tool(
        tool_name="github_repo_evidence_tool",
        arguments=evidence_arguments,
        decision_reason="manual_script_test",
        resume_markdown=resume_markdown,
        resume_metadata={},
    )
    llm_context = executor._build_follow_up_evidence_context(latest_tool_context)

    logger.info("github_repo_evidence_tool 原始返回：")
    print(_preview_text(evidence_result.model_dump(mode="json"), args.max_output_chars))
    print("\n" + "=" * 80 + "\n")
    _append_output_block(
        output_blocks,
        title="github_repo_evidence_tool 原始返回",
        content=evidence_result.model_dump(mode="json"),
        max_output_chars=args.max_output_chars,
    )

    logger.info("executor._invoke_interview_tool(...) 返回的 latest_tool_context：")
    print(_preview_text(latest_tool_context, args.max_output_chars))
    print("\n" + "=" * 80 + "\n")
    _append_output_block(
        output_blocks,
        title="executor latest_tool_context",
        content=latest_tool_context,
        max_output_chars=args.max_output_chars,
    )

    logger.info("项目实际传给 LLM 的 evidence_context：")
    print(llm_context)
    print("\n" + "=" * 80 + "\n")
    _append_output_block(
        output_blocks,
        title="最终传给 LLM 的 evidence_context",
        content=llm_context,
        max_output_chars=args.max_output_chars,
    )

    if args.output_file.strip():
        output_path = Path(args.output_file).expanduser()
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(output_blocks), encoding="utf-8")
        logger.info("已写出 UTF-8 调试结果文件 path={}", output_path)

    logger.info(
        "测试完成 found={}, repo_name={}, item_count={}",
        evidence_result.found,
        evidence_result.repo_name,
        len(evidence_result.items),
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
