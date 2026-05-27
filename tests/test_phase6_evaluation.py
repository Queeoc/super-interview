"""Phase 6 统一评估引擎测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
import pytest

from app.agent.evaluator.batch_evaluator import BatchEvaluator
from app.agent.evaluator.summarizer import InterviewSummarizer
from app.agent.interview.executor import InterviewExecutor
from app.agent.interview.planner import InterviewPlanner
from app.agent.interview.replanner import InterviewReplanner
from app.api import interview as interview_api
from app.core.database import get_db_session
from app.core.structured_output import StructuredOutputRunner
from app.middleware.error_handler import register_exception_handlers
from app.models.interview import (
    CreateInterviewRequest,
    InterviewAnswerEntity,
    InterviewQuestionEvaluationDTO,
    InterviewReportEntity,
    InterviewSessionEntity,
    InterviewSessionStatus,
)
from app.services.evaluation_service import EvaluationService
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.interview_service import InterviewService, InterviewSessionCache
from app.services.skill_service import SkillService


class _StructuredSchema(BaseModel):
    """结构化输出测试 schema。"""

    value: str


class _PlannerSchemaProbe(BaseModel):
    """用于验证 planner prompt 契约内容的测试 schema。"""

    questions: list[dict[str, str]]


class _BatchFakeLlm:
    """批量评估测试用 LLM 假对象。"""

    def __init__(self, response_factory: Any) -> None:
        self._response_factory = response_factory

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
        response_factory = self._response_factory

        class _Invoker:
            async def ainvoke(self, prompt_text: str) -> Any:
                return response_factory(prompt_text)

        return _Invoker()


class _SummarizerFakeLlm:
    """汇总测试用 LLM 假对象。"""

    def __init__(self, response_factory: Any) -> None:
        self._response_factory = response_factory

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
        response_factory = self._response_factory

        class _Invoker:
            async def ainvoke(self, prompt_text: str) -> Any:
                return response_factory(prompt_text)

        return _Invoker()


class _FakePromptRunner:
    """测试用 prompt runner。"""

    def __init__(self, enable_llm: bool = False) -> None:
        self._enable_llm = enable_llm

    @staticmethod
    def wrap_untrusted_text(tag_name: str, content: str) -> str:
        return f"<{tag_name}>\n{content.strip()}\n</{tag_name}>"

    @staticmethod
    def render_categories(categories: list[dict[str, Any]]) -> str:
        if not categories:
            return "- GENERAL | 通用问答 | NORMAL"
        return "\n".join(
            [
                "- {key} | {label} | {priority}".format(
                    key=category.get("key", "GENERAL"),
                    label=category.get("label", "通用问答"),
                    priority=category.get("priority", "NORMAL"),
                )
                for category in categories
            ]
        )

    async def ainvoke_structured(
        self,
        *,
        template_name: str,
        schema: type[BaseModel],
        variables: dict[str, Any],
        model: str | None = None,
        temperature: float = 0.2,
    ) -> Any:
        raise RuntimeError("LLM disabled in tests")


class _InMemoryRedisBackend:
    """测试用 Redis 假对象。"""

    def __init__(self) -> None:
        self.enabled = True
        self.storage: dict[str, str] = {}

    async def get_value(self, key: str) -> str | None:
        return self.storage.get(key)

    async def set_value(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.storage[key] = value
        return True

    async def delete_key(self, key: str) -> int:
        self.storage.pop(key, None)
        return 1


class _InMemoryInterviewRepository:
    """测试用面试仓储，绕过真实数据库。"""

    def __init__(self) -> None:
        self.sessions: dict[str, InterviewSessionEntity] = {}
        self.answers: list[InterviewAnswerEntity] = []
        self.reports: dict[str, InterviewReportEntity] = {}

    async def add_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        self.sessions[entity.id] = entity
        return entity

    async def upsert_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        self.sessions[entity.id] = entity
        return entity

    async def get_session(self, session_id: str) -> InterviewSessionEntity | None:
        return self.sessions.get(session_id)

    async def add_answer(self, entity: InterviewAnswerEntity) -> InterviewAnswerEntity:
        self.answers.append(entity)
        return entity

    async def list_answers_by_session(self, session_id: str) -> list[InterviewAnswerEntity]:
        return [answer for answer in self.answers if answer.session_id == session_id]

    async def get_report_by_session(self, session_id: str) -> InterviewReportEntity | None:
        return self.reports.get(session_id)

    async def upsert_report(self, entity: InterviewReportEntity) -> InterviewReportEntity:
        self.reports[entity.session_id] = entity
        return entity

    async def get_session_snapshot(
        self,
        session_id: str,
    ) -> tuple[InterviewSessionEntity | None, list[InterviewAnswerEntity], InterviewReportEntity | None]:
        return (
            self.sessions.get(session_id),
            [answer for answer in self.answers if answer.session_id == session_id],
            self.reports.get(session_id),
        )


@dataclass
class _ReportRepository:
    """报告测试用内存仓储。"""

    session: InterviewSessionEntity
    answers: list[InterviewAnswerEntity]
    report: InterviewReportEntity | None = None

    async def get_session_snapshot(
        self,
        session_id: str,
    ) -> tuple[InterviewSessionEntity | None, list[InterviewAnswerEntity], InterviewReportEntity | None]:
        return self.session, self.answers, self.report

    async def upsert_report(self, entity: InterviewReportEntity) -> InterviewReportEntity:
        self.report = entity
        return entity

    async def get_session(self, session_id: str) -> InterviewSessionEntity | None:
        return self.session if self.session.id == session_id else None


def _build_structured_runner(response_payload: dict[str, Any]) -> StructuredOutputRunner:
    """构造可控 structured output runner。"""

    class _FakeLlm:
        def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
            class _Invoker:
                async def ainvoke(self, prompt_text: str) -> Any:
                    return response_payload

            return _Invoker()

    return StructuredOutputRunner(enable_llm=True, llm_factory_fn=lambda **_: _FakeLlm())


@pytest.mark.asyncio
async def test_structured_output_runner_handles_native_output() -> None:
    """structured output runner 应能解析 native structured result。"""

    runner = _build_structured_runner(
        {
            "raw": None,
            "parsed": _StructuredSchema(value="ok"),
            "parsing_error": None,
        }
    )

    result = await runner.ainvoke(
        prompt_text="hello",
        schema=_StructuredSchema,
    )

    assert result.value == "ok"


@pytest.mark.asyncio
async def test_structured_output_runner_repairs_json_text() -> None:
    """structured output runner 应能修复基础 JSON 文本。"""

    class _FakeLlm:
        async def ainvoke(self, prompt_text: str) -> str:
            return "```json\n{'value': 'repaired',}\n```"

    runner = StructuredOutputRunner(enable_llm=True, llm_factory_fn=lambda **_: _FakeLlm())

    result = await runner.ainvoke(
        prompt_text="hello",
        schema=_StructuredSchema,
    )

    assert result.value == "repaired"


@pytest.mark.asyncio
async def test_structured_output_runner_injects_schema_contract() -> None:
    """structured output prompt 应显式注入字段契约和 JSON 示例。"""

    captured: dict[str, str] = {}

    class _FakeLlm:
        async def ainvoke(self, prompt_text: str) -> str:
            captured["prompt_text"] = prompt_text
            return '{"questions": [{"category_key": "GENERAL", "question_text": "示例"}]}'

    runner = StructuredOutputRunner(enable_llm=True, llm_factory_fn=lambda **_: _FakeLlm())

    await runner.ainvoke(
        prompt_text="planner task",
        schema=_PlannerSchemaProbe,
    )

    prompt_text = captured["prompt_text"]
    assert "### Schema Contract" in prompt_text
    assert '"questions"' in prompt_text
    assert "Canonical JSON example" in prompt_text


@pytest.mark.asyncio
async def test_batch_evaluator_falls_back_to_rule_evaluations() -> None:
    """批量评估器在 LLM 不可用时应返回稳定兜底结果。"""

    evaluator = BatchEvaluator(enable_llm=False, batch_size=2)
    results = await evaluator.evaluate_questions(
        skill_name="Python Backend",
        skill_markdown="skill",
        reference_markdown="ref",
        rubric_markdown="rubric",
        question_items=[
            {
                "question_key": "q-1",
                "round_index": 1,
                "question_text": "如何设计缓存？",
                "answer_text": "我会从场景和失效策略展开说明，结合真实线上经验。",
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "question_text": "如何排查慢查询？",
                "answer_text": "我会先看执行计划，再结合索引和事务锁分析。",
            },
        ],
    )

    assert len(results) == 2
    assert results[0].source == "fallback"
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_batch_evaluator_accepts_legacy_output_keys() -> None:
    """批量评估应兼容 evaluations 别名和字符串数组字段。"""

    class _FakeLlm:
        def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
            class _Invoker:
                async def ainvoke(self, prompt_text: str) -> Any:
                    return {
                        "raw": None,
                        "parsed": {
                            "batch_index": 1,
                            "batch_total": 1,
                            "evaluations": [
                                {
                                    "question_key": "q-1",
                                    "score": 88,
                                    "rating": "good",
                                    "strengths": "实现路径清晰",
                                    "weaknesses": "边界情况展开不足",
                                    "suggestions": "补充一次真实故障处理案例",
                                    "rationale": "能够说明核心方案。",
                                }
                            ],
                        },
                        "parsing_error": None,
                    }

            return _Invoker()

    evaluator = BatchEvaluator(enable_llm=True, llm_factory_fn=lambda **_: _FakeLlm(), batch_size=1)
    results = await evaluator.evaluate_questions(
        skill_name="Python Backend",
        skill_markdown="skill",
        reference_markdown="ref",
        rubric_markdown="rubric",
        question_items=[
            {
                "question_key": "q-1",
                "round_index": 1,
                "question_text": "如何设计缓存？",
                "answer_text": "我会从缓存穿透、击穿和失效策略展开。",
            }
        ],
    )

    assert len(results) == 1
    assert results[0].question_key == "q-1"
    assert results[0].strengths == ["实现路径清晰"]
    assert results[0].source == "llm"


@pytest.mark.asyncio
async def test_summarizer_falls_back_to_aggregated_summary() -> None:
    """汇总器在 LLM 不可用时应返回题目级聚合结果。"""

    summarizer = InterviewSummarizer(enable_llm=False)
    summary = await summarizer.summarize(
        skill_name="Python Backend",
        skill_markdown="skill",
        reference_markdown="ref",
        rubric_markdown="rubric",
        question_evaluations=[
            InterviewQuestionEvaluationDTO(
                question_key="q-1",
                round_index=1,
                question_text="如何设计缓存？",
                answer_text="我会从场景和失效策略展开说明，结合真实线上经验。",
                score=82,
                rating="good",
                strengths=["说明了实现细节"],
                weaknesses=["还可以补充边界条件"],
                suggestions=["补充真实项目复盘"],
                rationale="demo",
                source="fallback",
            )
        ],
    )

    assert summary["overall_score"] == 82.0
    assert summary["generation_mode"] == "fallback"


@pytest.mark.asyncio
async def test_summarizer_accepts_legacy_summary_shape() -> None:
    """汇总器应兼容旧字段名、字符串数组和扁平维度分。"""

    class _FakeLlm:
        def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
            class _Invoker:
                async def ainvoke(self, prompt_text: str) -> Any:
                    return {
                        "raw": None,
                        "parsed": {
                            "summary": "整体表现较稳，但细节说明仍可加强。",
                            "overall_score": 79,
                            "overall_rating": "pass",
                            "highlights": "表达整体清晰",
                            "shortcomings": "细节展开不足",
                            "improvement_suggestions": "补充真实项目中的权衡说明",
                            "technical_depth_score": 78,
                            "implementation_clarity_score": 80,
                            "problem_solving_score": 79,
                        },
                        "parsing_error": None,
                    }

            return _Invoker()

    summarizer = InterviewSummarizer(enable_llm=True, llm_factory_fn=lambda **_: _FakeLlm())
    summary = await summarizer.summarize(
        skill_name="Python Backend",
        skill_markdown="skill",
        reference_markdown="ref",
        rubric_markdown="rubric",
        question_evaluations=[
            InterviewQuestionEvaluationDTO(
                question_key="q-1",
                round_index=1,
                question_text="如何设计缓存？",
                answer_text="我会从缓存穿透、击穿和失效策略展开。",
                score=79,
                rating="pass",
                strengths=["表达整体清晰"],
                weaknesses=["细节展开不足"],
                suggestions=["补充真实项目中的权衡说明"],
                rationale="demo",
                source="fallback",
            )
        ],
    )

    assert summary["generation_mode"] == "llm"
    assert summary["summary_text"] == "整体表现较稳，但细节说明仍可加强。"
    assert summary["strengths"] == ["表达整体清晰"]
    assert summary["dimension_scores"]["technical_depth"] == 78.0


@pytest.mark.asyncio
async def test_evaluation_service_generates_and_reads_report() -> None:
    """评估服务应生成并读取统一报告。"""

    session = InterviewSessionEntity(
        id="session-1",
        skill_id="python-backend",
        title="面试",
        language="zh-CN",
        mode="text",
        status=InterviewSessionStatus.COMPLETED.value,
        current_round=1,
        max_rounds=1,
        questions_json=[],
        session_context_json={
            "workflow_state": {},
        },
        metadata_json={
            "skill_display_name": "Python Backend",
            "skill_markdown": "skill",
            "reference_markdown": "ref",
        },
    )
    answers = [
        InterviewAnswerEntity(
            session_id="session-1",
            round_index=1,
            question_key="q-1",
            question_text="如何设计缓存？",
            answer_text="我会从场景和失效策略展开说明，结合真实线上经验。",
            score_json={},
            feedback_json={},
            answer_metadata_json={},
        )
    ]
    repository = _ReportRepository(session=session, answers=answers)
    service = EvaluationService(
        batch_evaluator=BatchEvaluator(enable_llm=False),
        summarizer=InterviewSummarizer(enable_llm=False),
        repository_factory=lambda _session: repository,  # type: ignore[arg-type]
        rubric_root_dir=Path("knowledge_base/rubrics"),
    )
    db_session = AsyncMock()

    report_entity = await service.generate_report(db_session, "session-1")
    report_dto = await service.get_report(db_session, "session-1")
    export_dto = await service.export_report(db_session, "session-1")

    assert report_entity.status == "generated"
    assert report_dto.status == "generated"
    assert report_dto.overall_score is not None
    assert export_dto.export_format == "markdown"
    assert "# 面试评估报告" in export_dto.content


def _build_api_client(
    monkeypatch: pytest.MonkeyPatch,
    service: InterviewService,
    session: AsyncMock,
) -> TestClient:
    """构建只挂载 interview 路由的测试客户端。"""

    app = FastAPI()
    register_exception_handlers(app)
    monkeypatch.setattr(interview_api, "interview_service", service)

    async def override_get_db_session() -> AsyncIterator[AsyncMock]:
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(interview_api.router, prefix="/api")
    return TestClient(app)


def _build_test_service() -> tuple[InterviewService, _InMemoryInterviewRepository, AsyncMock, _InMemoryRedisBackend]:
    """构建使用规则降级和内存仓储的面试服务。"""

    repository = _InMemoryInterviewRepository()
    redis_backend = _InMemoryRedisBackend()
    prompt_runner = _FakePromptRunner(enable_llm=False)
    service = InterviewService(
        skill_service=SkillService(),
        persistence_service=InterviewPersistenceService(repository=repository),
        repository_factory=lambda _session: repository,  # type: ignore[arg-type]
        prompt_runner=prompt_runner,
        planner=InterviewPlanner(prompt_runner),
        executor=InterviewExecutor(prompt_runner),
        replanner=InterviewReplanner(prompt_runner),
        cache=InterviewSessionCache(redis_backend=redis_backend),
        evaluation_service=EvaluationService(
            batch_evaluator=BatchEvaluator(enable_llm=False),
            summarizer=InterviewSummarizer(enable_llm=False),
            repository_factory=lambda _session: repository,  # type: ignore[arg-type]
        ),
    )
    session = AsyncMock()
    return service, repository, session, redis_backend
