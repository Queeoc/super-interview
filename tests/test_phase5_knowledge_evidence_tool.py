"""Phase 5 知识证据工具的轻量化回归测试。"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from pydantic import BaseModel

from app.agent.interview.executor import InterviewExecutor
from app.tools.knowledge_evidence_tool import (
    KnowledgeEvidenceDocument,
    KnowledgeEvidenceItem,
    KnowledgeEvidenceToolResult,
)

knowledge_tool_module = importlib.import_module("app.tools.knowledge_evidence_tool")


@pytest.mark.asyncio
async def test_knowledge_evidence_tool_returns_stable_result_and_limits_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识证据工具应返回稳定结构，并保留有限命中。"""

    from app.services.rag_service import RagSearchHit, RagSearchResult

    captured: dict[str, Any] = {}

    async def _fake_search(**kwargs: Any) -> RagSearchResult:
        captured.update(kwargs)
        return RagSearchResult(
            query=kwargs["query"],
            rewritten_query=kwargs["query"],
            used_rewrite=False,
            top_k=kwargs["top_k"],
            score_threshold=None,
            hits=[
                RagSearchHit(
                    knowledge_base_id="kb-1",
                    document_id="doc-1",
                    category="CACHE",
                    source_type="admin",
                    skill_id="python-backend",
                    file_name="cache.md",
                    source="/docs/cache.md",
                    score=0.12,
                    content="缓存一致性需要考虑失效、更新和并发写入。",
                ),
                RagSearchHit(
                    knowledge_base_id="kb-2",
                    document_id="doc-2",
                    category="CACHE",
                    source_type="admin",
                    skill_id=None,
                    file_name="cache-base.md",
                    source="/docs/cache-base.md",
                    score=0.18,
                    content="常见缓存模式包括旁路缓存和写穿。",
                ),
            ],
            message="success",
        )

    monkeypatch.setattr(knowledge_tool_module.rag_service, "search", _fake_search)

    result = await knowledge_tool_module.knowledge_evidence_tool(
        skill_id="python-backend",
        category_key="CACHE",
        question_text="解释缓存一致性的原理",
        answer_text="我会关注失效和并发写入",
        focus_topics=["缓存失效"],
        missing_signals=["原理"],
        top_k=2,
    )

    assert captured["skill_id"] == "python-backend"
    assert captured["include_global_skill"] is True
    assert captured["rewrite_enabled"] is False
    assert result.found is True
    assert result.matched_categories == ["CACHE"]
    assert result.items[0].file_name == "cache.md"
    assert len(result.items) == 2
    assert result.matched_documents[0].file_name == "cache.md"


@pytest.mark.asyncio
async def test_executor_injects_knowledge_evidence_context_when_knowledge_tool_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识证据工具命中时，追问 prompt 应注入知识上下文。"""

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {}

    async def _fake_knowledge_tool(**kwargs: Any) -> KnowledgeEvidenceToolResult:
        return KnowledgeEvidenceToolResult(
            found=True,
            query="CACHE 缓存一致性 原理",
            retrieval_reason="knowledge_search_matched",
            matched_categories=["CACHE"],
            matched_documents=[
                KnowledgeEvidenceDocument(
                    knowledge_base_id="kb-1",
                    document_id="doc-1",
                    category="CACHE",
                    skill_id="python-backend",
                    file_name="cache.md",
                    source="/docs/cache.md",
                    score=0.12,
                )
            ],
            items=[
                KnowledgeEvidenceItem(
                    knowledge_base_id="kb-1",
                    document_id="doc-1",
                    category="CACHE",
                    skill_id="python-backend",
                    file_name="cache.md",
                    source="/docs/cache.md",
                    score=0.12,
                    content="缓存一致性通常要处理失效时机、并发写入和回源策略。",
                )
            ],
        )

    monkeypatch.setattr(executor_module, "knowledge_evidence_tool", _fake_knowledge_tool)

    class _RecordingPromptRunner:
        @staticmethod
        def wrap_untrusted_text(tag_name: str, content: str) -> str:
            return content

        async def ainvoke_structured(
            self,
            *,
            template_name: str,
            schema: type[BaseModel],
            variables: dict[str, Any],
            model: str | None = None,
            temperature: float = 0.2,
        ) -> Any:
            if template_name == "tool_decision.st":
                return schema(
                    should_call_tool=True,
                    tool_name="knowledge_evidence_tool",
                    arguments={
                        "skill_id": "python-backend",
                        "category_key": "CACHE",
                        "question_text": "解释缓存一致性的原理",
                        "answer_text": "我会关注失效和并发写入",
                        "focus_topics": ["缓存失效"],
                        "missing_signals": ["原理"],
                        "top_k": 2,
                    },
                    reason="需要稳定知识证据",
                )
            captured["follow_up_evidence_context"] = variables["follow_up_evidence_context"]
            return schema(question_text="你能具体说明缓存失效和并发写入时的处理策略吗？")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-knowledge-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": "# 项目经历\n- 负责缓存一致性与失效策略设计。\n",
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "解释缓存一致性的原理",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.3,
                "observed_signals": [],
                "missing_signals": ["原理", "边界"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会关注失效和并发写入",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["follow_up_evidence_context"]
    assert "cache.md" in captured["follow_up_evidence_context"]
    assert "缓存一致性" in captured["follow_up_evidence_context"]
    assert next_state["latest_tool_context"]["tool_name"] == "knowledge_evidence_tool"
    assert next_state["latest_tool_context"]["success"] is True
    assert next_state["latest_tool_context"]["arguments"]["skill_id"] == "python-backend"


@pytest.mark.asyncio
async def test_executor_tool_decision_fallback_can_choose_knowledge_tool() -> None:
    """工具决策失败时，规则兜底也应能选中知识工具。"""

    class _RecordingPromptRunner:
        @staticmethod
        def wrap_untrusted_text(tag_name: str, content: str) -> str:
            return f"<{tag_name}>\n{content.strip()}\n</{tag_name}>"

        async def ainvoke_structured(
            self,
            *,
            template_name: str,
            schema: type[BaseModel],
            variables: dict[str, Any],
            model: str | None = None,
            temperature: float = 0.2,
        ) -> Any:
            if template_name == "tool_decision.st":
                raise ValueError("provider unavailable")
            return schema(question_text="请解释缓存一致性的实现机制。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-knowledge-2",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": "# 项目经历\n- 负责缓存一致性与失效策略设计。\n",
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请解释缓存一致性的实现机制。",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.2,
                "observed_signals": [],
                "missing_signals": ["原理", "边界"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会补充实现机制。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert next_state["latest_tool_context"]["tool_name"] == "knowledge_evidence_tool"
    assert next_state["latest_tool_context"]["decision_reason"] == "fallback_rule_based_decision"
