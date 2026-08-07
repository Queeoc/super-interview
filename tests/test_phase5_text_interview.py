"""文字面试主流程与统一评估链路的最小验证。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
import pytest

from app.config import InterviewSettings, config
from app.agent.interview.executor import InterviewExecutor
from app.agent.interview.plan_observer import InterviewPlanObserver
from app.agent.evaluator.batch_evaluator import BatchEvaluator
from app.agent.evaluator.summarizer import InterviewSummarizer
from app.agent.interview.planner import InterviewPlanner, _PlannedQuestionItem, _PlannerOutput
from app.agent.interview.prompts import InterviewPromptRunner
from app.agent.interview.replanner import InterviewReplanner
from app.api import interview as interview_api
from app.core.database import get_db_session
from app.middleware.error_handler import register_exception_handlers
from app.middleware.visitor_context import VISITOR_ID_COOKIE_NAME, VisitorContextMiddleware
from app.models.interview import (
    CreateInterviewRequest,
    InterviewAnswerEntity,
    InterviewReportEntity,
    InterviewReportStatus,
    InterviewSessionEntity,
    SubmitAnswerRequest,
)
from app.services.evaluation_service import EvaluationService
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.interview_service import InterviewService, InterviewSessionCache
from app.services.resume_service import ResumeContextBundle
from app.services.skill_service import SkillService

resume_tool_module = importlib.import_module("app.tools.resume_evidence_tool")
knowledge_tool_module = importlib.import_module("app.tools.knowledge_evidence_tool")

TEST_VISITOR_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _stub_resume_embedding_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认禁用真实 embedding 后端，确保测试完全离线。"""

    resume_tool_module._SEMANTIC_CACHE.clear()
    resume_tool_module._QUERY_EMBEDDING_CACHE.clear()
    monkeypatch.setattr(
        resume_tool_module,
        "_get_embedding_backend",
        lambda: (_ for _ in ()).throw(RuntimeError("embedding backend disabled in tests")),
    )


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


class _NullResumeService:
    """测试用简历桩，默认不注入任何简历上下文。"""

    async def resolve_resume_for_interview(
        self,
        session: Any,
        *,
        visitor_id: str,
        resume_id: str | None,
    ) -> None:
        return None


class _StaticResumeService:
    """测试用简历桩，固定返回简历上下文。"""

    async def resolve_resume_for_interview(
        self,
        session: Any,
        *,
        visitor_id: str,
        resume_id: str | None,
    ) -> ResumeContextBundle | None:
        return ResumeContextBundle(
            resume_id=resume_id or "resume-1",
            markdown_content=(
                "# 项目经历\n"
                "## 分布式缓存平台项目\n"
                "- 负责设计 Redis 缓存失效策略与一致性方案，优化热点 key。\n"
                "- 将接口延迟降低 35%，并提升峰值 QPS。\n"
            ),
            metadata={"original_file_name": "resume.md"},
        )


class _DisabledStreamProducer:
    """Test stub that forces synchronous report generation."""

    enabled = False

    async def publish(self, envelope: Any) -> str:
        raise RuntimeError("stream producer disabled in tests")


class _EnabledStreamProducer:
    """Test stub that simulates async publish success."""

    enabled = True

    async def publish(self, envelope: Any) -> str:
        return "1-0"


class _InMemoryInterviewRepository:
    """测试用面试仓储，绕过真实数据库。"""

    def __init__(self) -> None:
        self.sessions: dict[str, InterviewSessionEntity] = {}
        self.answers: list[InterviewAnswerEntity] = []
        self.reports: dict[str, InterviewReportEntity] = {}
        self._sequence = 0

    def _tick(self) -> datetime:
        self._sequence += 1
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self._sequence)

    async def add_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        if not entity.id:
            entity.id = str(uuid4())
        now = self._tick()
        entity.created_at = entity.created_at or now
        entity.updated_at = now
        self.sessions[entity.id] = entity
        return entity

    async def upsert_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        if not entity.id:
            entity.id = str(uuid4())
        now = self._tick()
        entity.created_at = entity.created_at or now
        entity.updated_at = now
        self.sessions[entity.id] = entity
        return entity

    async def get_session(self, session_id: str) -> InterviewSessionEntity | None:
        return self.sessions.get(session_id)

    async def list_sessions_by_visitor(
        self,
        visitor_id: str,
        limit: int = 20,
    ) -> list[InterviewSessionEntity]:
        sessions = [session for session in self.sessions.values() if session.visitor_id == visitor_id]
        sessions.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
        return sessions[:limit]

    async def add_answer(self, entity: InterviewAnswerEntity) -> InterviewAnswerEntity:
        if not entity.id:
            entity.id = str(uuid4())
        self.answers.append(entity)
        return entity

    async def update_answer_metadata(
        self,
        answer_id: str,
        metadata: dict,
    ) -> InterviewAnswerEntity | None:
        for answer in self.answers:
            if answer.id == answer_id:
                answer.answer_metadata_json = dict(metadata or {})
                return answer
        return None

    async def list_answers_by_session(self, session_id: str) -> list[InterviewAnswerEntity]:
        return [answer for answer in self.answers if answer.session_id == session_id]

    async def get_report_by_session(self, session_id: str) -> InterviewReportEntity | None:
        return self.reports.get(session_id)

    async def upsert_report(self, entity: InterviewReportEntity) -> InterviewReportEntity:
        if not entity.id:
            entity.id = str(uuid4())
        self.reports[entity.session_id] = entity
        return entity

    async def get_session_snapshot(
        self,
        session_id: str,
    ) -> tuple[
        InterviewSessionEntity | None,
        list[InterviewAnswerEntity],
        InterviewReportEntity | None,
    ]:
        return (
            self.sessions.get(session_id),
            [answer for answer in self.answers if answer.session_id == session_id],
            self.reports.get(session_id),
        )


def _build_test_service(
    *,
    use_disabled_stream_producer: bool = True,
    resume_service: Any | None = None,
) -> tuple[InterviewService, _InMemoryInterviewRepository, AsyncMock, _InMemoryRedisBackend]:
    """构建使用规则降级和内存仓储的面试服务。"""

    repository = _InMemoryInterviewRepository()
    redis_backend = _InMemoryRedisBackend()
    prompt_runner = InterviewPromptRunner(enable_llm=False)
    service = InterviewService(
        skill_service=SkillService(),
        persistence_service=InterviewPersistenceService(repository=repository),
        resume_service=resume_service or _NullResumeService(),
        repository_factory=lambda _session: repository,
        prompt_runner=prompt_runner,
        planner=InterviewPlanner(prompt_runner),
        plan_observer=InterviewPlanObserver(prompt_runner),
        executor=InterviewExecutor(prompt_runner),
        replanner=InterviewReplanner(prompt_runner),
        cache=InterviewSessionCache(redis_backend=redis_backend),
        evaluation_service=EvaluationService(
            batch_evaluator=BatchEvaluator(enable_llm=False),
            summarizer=InterviewSummarizer(enable_llm=False),
            repository_factory=lambda _session: repository,  # type: ignore[arg-type]
        ),
        stream_producer_backend=(
            _DisabledStreamProducer() if use_disabled_stream_producer else _EnabledStreamProducer()
        ),
    )
    session = AsyncMock()
    return service, repository, session, redis_backend


class _StructuredPromptSchema(BaseModel):
    """用于验证 structured output 参数的测试 schema。"""

    value: str


class _FollowUpAliasSchema(BaseModel):
    """用于验证追问别名兼容的测试 schema。"""

    question_text: str


class _ToolDecisionSchema(BaseModel):
    """用于验证工具决策结构化输出。"""

    should_call_tool: bool
    tool_name: str | None = None
    arguments: dict[str, Any] = {}
    reason: str = ""


class _ReplanAliasSchema(BaseModel):
    """用于验证 replanner 附加字段兼容的测试 schema。"""

    action: str
    reason: str
    next_question: str | None = None
    topic_status: str = "covered"
    coverage_update: str = "enough evidence"
    remaining_risk: str = ""
    next_topic_key: str | None = None
    plan_adjustment: str | None = None


def test_executor_builds_available_follow_up_tools_by_category() -> None:
    """追问阶段应先按 category_key 生成可暴露给 LLM 的工具列表。"""

    executor = InterviewExecutor(InterviewPromptRunner(enable_llm=False))

    assert executor._build_available_follow_up_tools(  # type: ignore[attr-defined]
        category_key="PROJECT",
        resume_markdown="# 项目经历\n- 负责缓存平台。",
    ) == ["resume_evidence_tool"]
    assert executor._build_available_follow_up_tools(  # type: ignore[attr-defined]
        category_key="PROJECT",
        resume_markdown="# 项目经历\n- GitHub: https://github.com/acme/cache-platform",
    ) == ["resume_evidence_tool", "github_repo_evidence_tool"]
    assert executor._build_available_follow_up_tools(  # type: ignore[attr-defined]
        category_key="SYSTEM_DESIGN_SCENARIO",
        resume_markdown="# 项目经历\n- GitHub: https://github.com/acme/cache-platform",
    ) == ["knowledge_evidence_tool"]
    assert executor._build_available_follow_up_tools(  # type: ignore[attr-defined]
        category_key="JAVA",
        resume_markdown="# 项目经历\n- GitHub: https://github.com/acme/cache-platform",
    ) == ["knowledge_evidence_tool"]
    assert executor._build_available_follow_up_tools(  # type: ignore[attr-defined]
        category_key="UNKNOWN",
        resume_markdown="",
    ) == ["knowledge_evidence_tool"]


def test_interview_default_max_follow_up_questions_is_five(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认每个主问题最多允许 5 个追问。"""

    monkeypatch.delenv("INTERVIEW__MAX_FOLLOW_UP_QUESTIONS", raising=False)
    monkeypatch.delenv("INTERVIEW_MAX_FOLLOW_UP_QUESTIONS", raising=False)

    settings = InterviewSettings(_env_file=None)

    assert settings.max_follow_up_questions == 5


def test_interview_max_follow_up_questions_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """追问上限仍应支持环境变量覆盖。"""

    monkeypatch.setenv("INTERVIEW_MAX_FOLLOW_UP_QUESTIONS", "7")

    settings = InterviewSettings(_env_file=None)

    assert settings.max_follow_up_questions == 7


def _build_follow_up_limit_state(*, include_next_question: bool = True) -> dict[str, Any]:
    """构造当前主问题已经生成 5 个追问的状态。"""

    main_question = {
        "question_key": "q-1",
        "round_index": 1,
        "category_key": "CACHE",
        "question_text": "请讲讲你的缓存方案设计",
        "parent_question_key": None,
        "source": "planned",
        "status": "answered",
        "is_follow_up": False,
        "asked_at": None,
        "answered_at": None,
    }
    follow_up_questions = [
        {
            "question_key": f"q-1-f-{index}",
            "round_index": 1,
            "category_key": "CACHE",
            "question_text": f"缓存追问 {index}",
            "parent_question_key": "q-1",
            "source": "follow_up",
            "status": "answered" if index < 5 else "asked",
            "is_follow_up": True,
            "asked_at": None,
            "answered_at": None,
        }
        for index in range(1, 6)
    ]
    questions = [main_question, *follow_up_questions]
    blueprints = [
        {
            "question_key": "q-1",
            "category_key": "CACHE",
            "question_text": "请讲讲你的缓存方案设计",
            "round_index": 1,
            "intent": "考察缓存设计权衡",
            "must_observe_signals": ["代码实现", "公平性"],
            "follow_up_focus": ["代码实现", "公平性"],
            "completion_criteria": ["说清代码实现"],
            "priority": 1,
            "can_skip": False,
        }
    ]
    remaining_main_question_keys: list[str] = []
    remaining_required_main_question_keys: list[str] = ["q-1"]
    if include_next_question:
        questions.append(
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "请讲讲数据库优化",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        )
        blueprints.append(
            {
                "question_key": "q-2",
                "category_key": "DB",
                "question_text": "请讲讲数据库优化",
                "round_index": 2,
                "intent": "考察数据库优化",
                "must_observe_signals": ["索引"],
                "follow_up_focus": ["索引"],
                "completion_criteria": ["说清优化方法"],
                "priority": 2,
                "can_skip": False,
            }
        )
        remaining_main_question_keys = ["q-2"]
        remaining_required_main_question_keys = ["q-1", "q-2"]

    return {
        "session_id": "session-follow-up-limit",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": questions,
        "interview_plan": {"question_blueprints": blueprints},
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1-f-5",
                "required": True,
                "status": "partial",
                "confidence": 0.65,
                "observed_signals": ["限流"],
                "missing_signals": ["代码实现"],
                "follow_up_count": 5,
                "evidence_count": 3,
                "completed": False,
                "last_updated": None,
            }
        },
        "termination_decision_context": {
            "remaining_required_main_question_keys": remaining_required_main_question_keys,
            "remaining_round_budget": 1 if include_next_question else 0,
            "all_required_main_questions_covered": False,
        },
        "current_question_key": "q-1-f-5",
        "current_main_question_key": "q-1",
        "remaining_main_question_keys": remaining_main_question_keys,
        "current_round": 1,
        "max_rounds": 2 if include_next_question else 1,
        "follow_up_count": 5,
        "latest_answer_text": "我还是想再补充一下。",
    }


@pytest.mark.asyncio
async def test_interview_prompt_runner_uses_json_schema_strict_mode() -> None:
    """InterviewPromptRunner 应显式启用 provider-native json_schema 且 strict=True。"""

    captured: dict[str, Any] = {}

    class _StructuredInvoker:
        async def ainvoke(self, prompt_text: str) -> dict[str, Any]:
            captured["prompt_text"] = prompt_text
            return {
                "raw": None,
                "parsed": _StructuredPromptSchema(value="ok"),
                "parsing_error": None,
            }

    class _FakeLlm:
        def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> _StructuredInvoker:
            captured["schema"] = schema
            captured["kwargs"] = kwargs
            return _StructuredInvoker()

    runner = InterviewPromptRunner(
        enable_llm=True,
        llm_factory_fn=lambda **_: _FakeLlm(),
    )

    result = await runner.ainvoke_structured(
        template_name="planner_system.st",
        schema=_StructuredPromptSchema,
        variables={
            "skill_name": "Python Backend",
            "skill_description": "desc",
            "language": "zh-CN",
            "max_rounds": 1,
            "category_section": "- GENERAL | 通用问答 | NORMAL",
            "skill_markdown": "<skill_markdown>skill</skill_markdown>",
            "reference_markdown": "<reference_markdown>ref</reference_markdown>",
        },
    )

    assert result.value == "ok"
    assert captured["schema"] is _StructuredPromptSchema
    assert captured["kwargs"]["method"] == "json_schema"
    assert captured["kwargs"]["strict"] is True
    assert captured["kwargs"]["include_raw"] is True
    assert "Return only a JSON object." in captured["prompt_text"]
    assert "### Schema Contract" in captured["prompt_text"]
    assert "Canonical JSON example" in captured["prompt_text"]


@pytest.mark.asyncio
async def test_interview_prompt_runner_rejects_invalid_structured_fields() -> None:
    """错误字段名的 structured output 不应被当成有效结果。"""

    class _StructuredInvoker:
        async def ainvoke(self, prompt_text: str) -> dict[str, Any]:
            return {
                "raw": {"content": '{"category": "PROJECT", "question": "..." }'},
                "parsed": None,
                "parsing_error": ValueError("field mismatch"),
            }

    class _FakeLlm:
        def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> _StructuredInvoker:
            return _StructuredInvoker()

    runner = InterviewPromptRunner(
        enable_llm=True,
        llm_factory_fn=lambda **_: _FakeLlm(),
    )

    with pytest.raises(ValueError, match="structured output 解析失败"):
        await runner.ainvoke_structured(
            template_name="planner_system.st",
            schema=_StructuredPromptSchema,
            variables={
                "skill_name": "Python Backend",
                "skill_description": "desc",
                "language": "zh-CN",
                "max_rounds": 1,
                "category_section": "- GENERAL | 通用问答 | NORMAL",
                "skill_markdown": "<skill_markdown>skill</skill_markdown>",
                "reference_markdown": "<reference_markdown>ref</reference_markdown>",
            },
        )


@pytest.mark.asyncio
async def test_planner_falls_back_when_provider_structured_output_fails() -> None:
    """provider-native structured output 失败后，planner 应稳定回退到规则模板。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(side_effect=ValueError("provider does not support json_schema"))  # type: ignore[method-assign]
    planner = InterviewPlanner(runner)
    skill_service = SkillService()
    skill_detail = skill_service.get_skill_detail("python-backend")
    reference_section = skill_service.build_reference_section("python-backend")

    state = {
        "session_id": "session-1",
        "visitor_id": None,
        "resume_id": None,
        "title": "test",
        "language": "zh-CN",
        "max_rounds": 3,
        "skill": {
            "skill_id": skill_detail.skill_id,
            "display_name": skill_detail.display_name,
            "description": skill_detail.description,
            "content_markdown": skill_detail.content_markdown,
            "reference_markdown": reference_section.reference_markdown,
            "categories": [category.model_dump(mode="json") for category in skill_detail.categories],
            "reference_files": reference_section.resolved_reference_files,
        },
        "questions": [],
        "current_question_key": None,
        "current_round": 0,
        "follow_up_count": 0,
        "latest_answer_text": "",
        "latest_answer_metadata": {},
        "next_action": "initial_ask",
        "action_reason": None,
        "feedback": {},
        "report_summary": {},
        "completed": False,
        "completion_message": None,
        "assistant_message": None,
        "last_draft_answer": {},
        "answer_count": 0,
    }

    planned_state = await planner.run(state)

    assert len(planned_state["questions"]) == 3
    assert planned_state["questions"][0]["question_key"] == "q-1"
    assert planned_state["questions"][0]["category_key"]
    assert planned_state["questions"][0]["question_text"]
    assert planned_state["interview_plan"]["question_blueprints"]
    assert planned_state["remaining_main_question_keys"]


@pytest.mark.asyncio
async def test_planner_uses_planning_snapshot_input() -> None:
    """planner 应消费预组装好的 planning snapshot，而不是临时拼接上下文。"""

    captured: dict[str, Any] = {}

    async def _fake_ainvoke_structured(
        *,
        template_name: str,
        schema: type[BaseModel],
        variables: dict[str, Any],
        temperature: float,
    ) -> _PlannerOutput:
        captured["template_name"] = template_name
        captured["variables"] = deepcopy(variables)
        return _PlannerOutput(
            plan_summary="structured plan",
            questions=[
                _PlannedQuestionItem(
                    category_key="PYTHON_BASIC",
                    question_text="请介绍你最熟悉的 Python 项目经验。",
                    intent="验证项目深度",
                    must_observe_signals=["implementation details"],
                    follow_up_focus=["tradeoffs"],
                    completion_criteria=["candidate explains what they built"],
                    priority=1,
                    can_skip=False,
                )
            ],
        )

    runner = InterviewPromptRunner(enable_llm=False)
    runner.ainvoke_structured = AsyncMock(side_effect=_fake_ainvoke_structured)  # type: ignore[method-assign]
    planner = InterviewPlanner(runner)
    skill_service = SkillService()
    skill_detail = skill_service.get_skill_detail("python-backend")
    reference_section = skill_service.build_reference_section("python-backend")
    planning_snapshot_markdown = (
        "# Planning Snapshot\n"
        "- skill_markdown: frozen\n"
        "- reference_files: frozen\n"
        "- resume_markdown: frozen\n"
    )

    state = {
        "session_id": "session-1",
        "visitor_id": None,
        "resume_id": None,
        "title": "test",
        "language": "zh-CN",
        "max_rounds": 3,
        "skill": {
            "skill_id": skill_detail.skill_id,
            "display_name": skill_detail.display_name,
            "description": skill_detail.description,
            "content_markdown": skill_detail.content_markdown,
            "reference_markdown": reference_section.reference_markdown,
            "planning_snapshot_markdown": planning_snapshot_markdown,
            "planning_snapshot_sources": [
                {"source_type": "skill_markdown", "name": "SKILL.md"},
                {"source_type": "reference_files", "name": "reference_files"},
            ],
            "categories": [category.model_dump(mode="json") for category in skill_detail.categories],
            "reference_files": reference_section.resolved_reference_files,
            "resume_markdown": "",
            "resume_metadata": {},
        },
        "questions": [],
        "current_question_key": None,
        "current_round": 0,
        "follow_up_count": 0,
        "latest_answer_text": "",
        "latest_answer_metadata": {},
        "next_action": "initial_ask",
        "action_reason": None,
        "feedback": {},
        "report_summary": {},
        "completed": False,
        "completion_message": None,
        "assistant_message": None,
        "last_draft_answer": {},
        "answer_count": 0,
    }

    planned_state = await planner.run(state)

    assert captured["template_name"] == "planner_system.st"
    assert "Planning Snapshot" in captured["variables"]["planning_snapshot_markdown"]
    assert "frozen" in captured["variables"]["planning_snapshot_markdown"]
    assert "Python 后端开发 参考资料" in captured["variables"]["reference_markdown"]
    assert planned_state["questions"][0]["question_key"] == "q-1"
    assert planned_state["skill"]["planning_snapshot_markdown"] == planning_snapshot_markdown


@pytest.mark.asyncio
async def test_interview_service_persists_planning_snapshot_in_session_metadata() -> None:
    """创建会话时应把 planner 的冻结快照同时写入 state 与持久化元数据。"""

    service, repository, session, _ = _build_test_service()
    captured_state: dict[str, Any] = {}

    async def _echo_state(initial_state: Any) -> Any:
        captured_state.update(deepcopy(initial_state))
        return initial_state

    service._initial_graph = SimpleNamespace(ainvoke=AsyncMock(side_effect=_echo_state))  # type: ignore[assignment]

    result = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=3),
        TEST_VISITOR_ID,
    )

    stored_session = next(iter(repository.sessions.values()))
    assert captured_state["skill"]["planning_snapshot_markdown"].startswith("# Planning Snapshot")
    assert captured_state["skill"]["planning_snapshot_sources"]
    assert stored_session.metadata_json["planning_snapshot_markdown"] == captured_state["skill"]["planning_snapshot_markdown"]
    assert stored_session.metadata_json["planning_snapshot_sources"] == captured_state["skill"]["planning_snapshot_sources"]
    assert stored_session.session_context_json["workflow_state"]["skill"]["planning_snapshot_markdown"] == captured_state["skill"]["planning_snapshot_markdown"]
    assert result.session_id == stored_session.id


@pytest.mark.asyncio
async def test_executor_accepts_follow_up_question_alias() -> None:
    """追问结构化输出应兼容 follow_up_question 别名。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(
        return_value=_FollowUpAliasSchema(question_text="请进一步说明你的缓存失效策略。")
    )  # type: ignore[method-assign]
    executor = InterviewExecutor(runner)

    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请介绍你的缓存设计经验。",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我用过 Redis。",
        "next_action": "follow_up",
    }

    follow_up_text = await executor._generate_follow_up_text(
        skill=state["skill"],
        current_question=state["questions"][0],
        answer_text=state["latest_answer_text"],
    )

    assert follow_up_text == "请进一步说明你的缓存失效策略。"


@pytest.mark.asyncio
async def test_executor_injects_previous_follow_up_questions_for_same_main_question() -> None:
    """生成追问时应注入同一主问题下的历史追问，并排除其他主问题追问。"""

    captured: dict[str, Any] = {}

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
            captured["template_name"] = template_name
            captured["previous_follow_up_questions"] = variables["previous_follow_up_questions"]
            return schema(question_text="请换一个角度说明公平锁初始化的具体代码。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-history-follow-up",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": "",
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "JAVA",
                "question_text": "请说明你如何实现限流。",
                "parent_question_key": None,
                "source": "planned",
                "status": "answered",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-1-f-1",
                "round_index": 1,
                "category_key": "JAVA",
                "question_text": "请展示 static final 公平信号量的初始化方式。",
                "parent_question_key": "q-1",
                "source": "follow_up",
                "status": "answered",
                "is_follow_up": True,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "请说明数据库优化。",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2-f-1",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "其他主问题的追问不应注入。",
                "parent_question_key": "q-2",
                "source": "follow_up",
                "status": "answered",
                "is_follow_up": True,
                "asked_at": None,
                "answered_at": None,
            },
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "JAVA",
                    "question_text": "请说明你如何实现限流。",
                    "round_index": 1,
                    "intent": "考察代码实现",
                    "must_observe_signals": ["代码实现"],
                    "follow_up_focus": ["公平信号量"],
                    "completion_criteria": ["说清初始化代码"],
                    "priority": 1,
                    "can_skip": False,
                }
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1-f-1",
                "required": True,
                "status": "partial",
                "confidence": 0.6,
                "observed_signals": ["公平模式"],
                "missing_signals": ["代码实现"],
                "follow_up_count": 1,
                "evidence_count": 1,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1-f-1",
        "current_main_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 1,
        "latest_answer_text": "我选择公平模式。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["template_name"] == "executor_system.st"
    assert "q-1-f-1: 请展示 static final 公平信号量的初始化方式。" in captured[
        "previous_follow_up_questions"
    ]
    assert "其他主问题的追问不应注入" not in captured["previous_follow_up_questions"]
    assert next_state["current_question_key"] == "q-1-f-2"


@pytest.mark.asyncio
async def test_executor_injects_resume_evidence_context_when_tool_called() -> None:
    """工具决策命中时，应把 resume evidence context 注入追问 prompt。"""

    captured: dict[str, Any] = {}

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
                return schema(
                    should_call_tool=True,
                    tool_name="resume_evidence_tool",
                    arguments={
                        "category_key": "PROJECT",
                        "question_text": "请结合你的真实项目说明缓存设计经验。",
                        "answer_text": "我做过 Redis 优化。",
                        "focus_topics": ["redis", "失效策略"],
                        "missing_signals": ["结果"],
                        "top_k": 2,
                    },
                    reason="需要结合真实项目追问",
                )
            captured["template_name"] = template_name
            captured["variables"] = variables
            return schema(question_text="请结合你在分布式缓存平台项目中的实践，具体说明失效策略如何设计。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 分布式缓存平台项目\n"
            "- 负责设计 Redis 缓存失效策略，优化热点 key，并将延迟降低 35%。"
        ),
        "resume_metadata": {"original_file_name": "resume.md"},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "请结合你的真实项目说明缓存设计经验。",
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
                "confidence": 0.4,
                "observed_signals": ["implementation details"],
                "missing_signals": ["结果"],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": False,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我做过 Redis 优化。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["template_name"] == "executor_system.st"
    assert "分布式缓存平台项目" in captured["variables"]["resume_evidence_context"]
    assert next_state["latest_tool_context"]["tool_name"] == "resume_evidence_tool"
    assert next_state["latest_tool_context"]["success"] is True


@pytest.mark.asyncio
async def test_executor_tool_decision_skip_keeps_follow_up_generation() -> None:
    """工具决策明确跳过时，应直接继续原追问生成。"""

    captured: dict[str, Any] = {}

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
                return schema(
                    should_call_tool=False,
                    tool_name=None,
                    arguments={},
                    reason="当前回答已经足够具体",
                )
            captured["resume_evidence_context"] = variables["resume_evidence_context"]
            return schema(question_text="请继续说明你是如何权衡缓存一致性的。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": "# 项目经历\n## 缓存项目\n- 负责 Redis 缓存设计。",
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请介绍你的缓存设计经验。",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会从一致性、热点 key 和失效策略三方面设计 Redis 方案。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert "无可用简历证据" in captured["resume_evidence_context"]
    assert next_state["assistant_message"] == "请继续说明你是如何权衡缓存一致性的。"
    assert next_state["latest_tool_context"]["decision_reason"] == "当前回答已经足够具体"


@pytest.mark.asyncio
async def test_executor_tool_decision_failure_uses_rule_based_fallback() -> None:
    """工具决策失败时，应走规则降级并继续执行。"""

    call_count = {"value": 0}

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
            call_count["value"] += 1
            if template_name == "tool_decision.st":
                raise ValueError("provider does not support json_schema")
            return schema(question_text="请结合你实际项目继续说明结果和收益。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": "# 项目经历\n## 缓存优化项目\n- 实现 Redis 多级缓存并提升 QPS。",
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请介绍你的缓存设计经验。",
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
                "missing_signals": ["结果", "tradeoffs"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我用过 Redis。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert call_count["value"] >= 2
    assert next_state["latest_tool_context"]["decision_reason"] == "fallback_rule_based_decision"
    assert next_state["assistant_message"] == "请结合你实际项目继续说明结果和收益。"


@pytest.mark.asyncio
async def test_replanner_accepts_optional_next_question_payload() -> None:
    """replanner 应兼容模型附带的 next_question 文本，而不触发回退。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=_ReplanAliasSchema(
            action="next_question",
            reason="当前问题已经获得基本信息，可以进入下一题。",
            next_question="能否分享更多关于缓存性能优化带来的收益？",
        )
    )
    replanner = InterviewReplanner(runner)

    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请介绍你的缓存设计经验。",
                "parent_question_key": None,
                "source": "planned",
                "status": "answered",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "请介绍你处理慢查询的经验。",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我补充了缓存穿透和热点 key 处理。",
        "max_rounds": 2,
    }

    next_state = await replanner.run(state)

    assert next_state["next_action"] == "next_question"
    assert "基本信息" in next_state["action_reason"]


@pytest.mark.asyncio
async def test_replanner_normalizes_none_remaining_risk_from_llm_payload() -> None:
    """LLM 若返回 remaining_risk=None，不应触发 schema 失败。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=type(
            "_Decision",
            (),
            {
                "action": "complete",
                "reason": "当前问题和必答主题都已覆盖，可以结束面试。",
                "next_question": None,
                "main_question_status": "covered",
                "coverage_update": "关键信号已满足。",
                "remaining_risk": None,
                "next_main_question_key": None,
                "plan_adjustment": None,
            },
        )()
    )
    replanner = InterviewReplanner(runner)

    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请介绍你的缓存设计经验。",
                "parent_question_key": None,
                "source": "planned",
                "status": "answered",
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
                "status": "covered",
                "confidence": 0.9,
                "observed_signals": ["implementation details", "tradeoffs", "results"],
                "missing_signals": [],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": True,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我补充了实现、权衡和结果。",
        "max_rounds": 1,
    }

    next_state = await replanner.run(state)

    latest_decision = next_state["termination_decision_context"]["latest_decision"]
    assert next_state["next_action"] == "complete"
    assert latest_decision["remaining_risk"] == ""


@pytest.mark.asyncio
async def test_executor_uses_completion_prompt_template() -> None:
    """结束语生成应使用独立 completion prompt，而不是复用 replanner 模板。"""

    captured: dict[str, Any] = {}

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
            captured["template_name"] = template_name
            return schema(closing_message="本次面试到这里先结束，正式评估报告将稍后生成。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "skill": {
            "display_name": "Python Backend",
        },
        "questions": [
            {
                "question_key": "q-1",
                "question_text": "请介绍一个项目。",
                "status": "answered",
            }
        ],
    }

    result = await executor._generate_completion_payload(state)  # type: ignore[arg-type]

    assert result.closing_message
    assert captured["template_name"] == "completion_system.st"


@pytest.mark.asyncio
async def test_interview_service_creates_session_and_caches_initial_question() -> None:
    """创建会话后应生成首题、持久化会话并写入缓存。"""

    service, repository, session, redis_backend = _build_test_service()

    result = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=3),
        TEST_VISITOR_ID,
    )

    assert result.status == "active"
    assert result.current_question is not None
    assert result.current_question.question_key == "q-1"
    assert len(result.questions) == 3
    assert len(repository.sessions) == 1
    assert len(redis_backend.storage) == 1
    stored_session = next(iter(repository.sessions.values()))
    assert stored_session.session_context_json["interview_plan"]["question_blueprints"]
    assert stored_session.session_context_json["remaining_main_question_keys"]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_interview_service_short_answer_triggers_follow_up() -> None:
    """短答案应触发追问分支。"""

    service, repository, session, _ = _build_test_service(use_disabled_stream_producer=False)
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=2),
        TEST_VISITOR_ID,
    )

    events = [
        event
        async for event in service.submit_answer_stream(
            session,
            created.session_id,
            SubmitAnswerRequest(answer_text="会用 Redis。"),
            TEST_VISITOR_ID,
        )
    ]

    plan_event = next(event for event in events if event["type"] == "plan")
    content_event = next(event for event in events if event["type"] == "content")
    done_event = next(event for event in events if event["type"] == "done")
    status_stages = [event.get("stage") for event in events if event["type"] == "status"]

    assert plan_event["action"] == "follow_up"
    assert "answer_observation_start" in status_stages
    assert "replan_start" in status_stages
    assert "tool_prepare_start" in status_stages
    assert any(stage in status_stages for stage in ["tool_call_complete", "tool_skipped"])
    assert "llm_generation_start" in status_stages
    assert "persist_complete" in status_stages
    assert "追问" in content_event["content"] or "补充" in content_event["content"]
    assert done_event["session"]["current_question"]["question_key"] == "q-1-f-1"
    assert repository.answers[0].question_key == "q-1"
    process_run = repository.answers[0].answer_metadata_json["process_run"]
    process_step_ids = [step["id"] for step in process_run["steps"]]
    assert process_run["status"] == "completed"
    assert process_run["question_key"] == "q-1"
    assert "tool_prepare" in process_step_ids
    assert any(step_id in process_step_ids for step_id in ["tool_call", "tool_prepare"])
    assert "done" in process_step_ids


@pytest.mark.asyncio
async def test_interview_service_persists_latest_tool_context_in_follow_up_flow() -> None:
    """追问分支应把 latest_tool_context 写入 session_context_json。"""

    service, repository, session, _ = _build_test_service(
        use_disabled_stream_producer=False,
        resume_service=_StaticResumeService(),
    )
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", resume_id="resume-1", max_rounds=2),
        TEST_VISITOR_ID,
    )

    events = [
        event
        async for event in service.submit_answer_stream(
            session,
            created.session_id,
            SubmitAnswerRequest(answer_text="我做过 Redis 优化。"),
            TEST_VISITOR_ID,
        )
    ]

    stored_session = repository.sessions[created.session_id]
    done_event = next(event for event in events if event["type"] == "done")

    assert stored_session.session_context_json["latest_tool_context"]["tool_source"] == "local_tool"
    assert "latest_tool_context" in done_event["session"] or done_event["session"]["current_question"]["question_key"] == "q-1-f-1"


@pytest.mark.asyncio
async def test_interview_service_normal_answer_advances_to_next_question() -> None:
    """正常答案应进入下一题。"""

    service, _, session, _ = _build_test_service()
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=2),
        TEST_VISITOR_ID,
    )

    answer_text = (
        "我会先说明缓存目标，再结合热点数据、一致性要求和失效策略设计 Redis 方案，"
        "同时补充持久化、监控告警和故障降级处理。"
    )
    events = [
        event
        async for event in service.submit_answer_stream(
            session,
            created.session_id,
            SubmitAnswerRequest(answer_text=answer_text),
            TEST_VISITOR_ID,
        )
    ]

    plan_event = next(event for event in events if event["type"] == "plan")
    done_event = next(event for event in events if event["type"] == "done")
    status_stages = [event.get("stage") for event in events if event["type"] == "status"]

    assert plan_event["action"] == "next_question"
    assert "answer_observation_start" in status_stages
    assert "replan_start" in status_stages
    assert "llm_generation_start" in status_stages
    assert "persist_complete" in status_stages
    assert done_event["session"]["current_question"]["question_key"] == "q-2"
    assert done_event["session"]["status"] == "active"
    process_run = done_event["session"]["answers"][0]["answer_metadata"]["process_run"]
    assert process_run["status"] == "completed"
    assert [step["id"] for step in process_run["steps"]][-1] == "done"


@pytest.mark.asyncio
async def test_interview_service_last_question_auto_completes_when_no_next_question_exists() -> None:
    """最后一题即使先进入 next_question 分支，也应在无剩余主问题时自动完成。"""

    service, repository, session, redis_backend = _build_test_service()
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=2),
        TEST_VISITOR_ID,
    )

    first_answer = (
        "我会先说明缓存目标，再结合热点数据、一致性要求和失效策略设计 Redis 方案，"
        "同时补充持久化、监控告警和故障降级处理。"
    )
    first_events = [
        event
        async for event in service.submit_answer_stream(
            session,
            created.session_id,
            SubmitAnswerRequest(answer_text=first_answer),
            TEST_VISITOR_ID,
        )
    ]
    first_plan = next(event for event in first_events if event["type"] == "plan")
    assert first_plan["action"] == "next_question"

    second_answer = (
        "这个功能我会从接口契约、事务边界、幂等控制、可观测性和回滚策略五个方面设计，"
        "并结合一次真实线上问题说明如何验证最终效果。"
    )
    original_decide_next_action = service._replanner._decide_next_action
    service._replanner._decide_next_action = AsyncMock(  # type: ignore[method-assign]
        return_value=type(
            "_Decision",
            (),
            {
                "action": "next_question",
                "reason": "当前问题已获得足够信息，可以进入下一题。",
            },
        )()
    )

    try:
        second_events = [
            event
            async for event in service.submit_answer_stream(
                session,
                created.session_id,
                SubmitAnswerRequest(answer_text=second_answer),
                TEST_VISITOR_ID,
            )
        ]
    finally:
        service._replanner._decide_next_action = original_decide_next_action  # type: ignore[method-assign]

    plan_event = next(event for event in second_events if event["type"] == "plan")
    status_event = next(event for event in second_events if event["type"] == "status" and "统一评估报告" in event["message"])
    report_event = next(event for event in second_events if event["type"] == "report")
    step_event = next(event for event in second_events if event["type"] == "step_complete")
    done_event = next(event for event in second_events if event["type"] == "done")

    assert plan_event["action"] == "next_question"
    assert status_event["message"] == "面试已结束，开始生成统一评估报告"
    assert step_event["response"]["completed"] is True
    assert step_event["response"]["report_status"] == "generated"
    assert report_event["report"]["status"] == "generated"
    assert done_event["session"]["status"] == "completed"
    assert done_event["session"]["completed"] is True
    assert done_event["session"]["current_question"] is None
    assert created.session_id in repository.reports
    assert redis_backend.storage == {}


@pytest.mark.asyncio
async def test_interview_service_last_round_completes_and_persists_report() -> None:
    """达到最后一轮时应完成会话并生成统一评估报告。"""

    service, repository, session, redis_backend = _build_test_service()
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=1),
        TEST_VISITOR_ID,
    )

    answer_text = (
        "我会从接口设计、数据库事务、一致性控制和回滚策略四个维度解释这个方案，"
        "并结合一次线上故障的复盘说明最终如何验证发布结果。"
    )
    events = [
        event
        async for event in service.submit_answer_stream(
            session,
            created.session_id,
            SubmitAnswerRequest(answer_text=answer_text),
            TEST_VISITOR_ID,
        )
    ]

    status_event = next(event for event in events if event["type"] == "status" and "统一评估报告" in event["message"])
    report_event = next(event for event in events if event["type"] == "report")
    step_event = next(event for event in events if event["type"] == "step_complete")
    done_event = next(event for event in events if event["type"] == "done")

    assert status_event["message"] == "面试已结束，开始生成统一评估报告"
    assert done_event["session"]["completed"] is True
    assert done_event["session"]["status"] == "completed"
    assert created.session_id in repository.reports
    assert step_event["response"]["report_status"] == "generated"
    assert report_event["report"]["status"] == "generated"
    assert report_event["report"]["overall_score"] is not None
    process_run = repository.answers[0].answer_metadata_json["process_run"]
    process_step_ids = [step["id"] for step in process_run["steps"]]
    assert process_run["status"] == "completed"
    assert "report" in process_step_ids
    assert "done" in process_step_ids
    assert redis_backend.storage == {}


def _build_api_client(
    monkeypatch: pytest.MonkeyPatch,
    service: InterviewService,
    session: AsyncMock,
) -> TestClient:
    """构建只挂载 interview 路由的测试客户端。"""

    app = FastAPI()
    register_exception_handlers(app)
    monkeypatch.setattr(interview_api, "interview_service", service)
    app.add_middleware(VisitorContextMiddleware)

    async def override_get_db_session() -> AsyncIterator[AsyncMock]:
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(interview_api.router, prefix="/api")
    return TestClient(app, base_url="https://testserver")


def test_interview_api_supports_create_and_sse_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 应覆盖创建会话和 SSE 提交答案主路径。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1},
    )
    assert create_response.status_code == 200
    visitor_id = client.cookies.get(VISITOR_ID_COOKIE_NAME)
    assert visitor_id is not None
    assert str(UUID(visitor_id)) == visitor_id
    session_id = create_response.json()["data"]["session_id"]

    with client.stream(
        "POST",
        f"/api/interview/sessions/{session_id}/answers",
        json={
            "answer_text": (
                "我会先拆清楚问题边界，再结合现有链路说明存储、缓存、失败重试、监控指标与容量预估，"
                "最后补充真实故障场景下的降级和回滚处理。"
            )
        },
    ) as response:
        assert response.status_code == 200
        raw_lines = [line for line in response.iter_lines() if line]

    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in raw_lines
        if line.startswith("data: ")
    ]
    payload_types = [payload["type"] for payload in payloads]

    assert "step_complete" in payload_types
    assert "done" in payload_types
    assert "report" in payload_types
    assert "status" in payload_types
    assert create_response.headers.get("set-cookie")


def test_interview_api_lists_sessions_for_current_visitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 应支持列出当前访客的历史面试会话摘要。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    first_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 2},
    )
    second_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1, "title": "第二场面试"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    list_response = client.get("/api/interview/sessions")

    assert list_response.status_code == 200
    payload = list_response.json()["data"]
    assert len(payload) == 2
    assert payload[0]["session_id"] == second_response.json()["data"]["session_id"]
    assert payload[0]["title"] == "第二场面试"
    assert payload[0]["skill_display_name"] == "Python 后端开发"
    assert payload[0]["current_question"]["question_key"] == "q-1"


def test_interview_api_does_not_auto_attach_resume_when_resume_id_is_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未显式传入 resume_id 时，创建的面试会话不应自动关联任何简历。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1, "resume_id": None},
    )

    assert create_response.status_code == 200
    assert create_response.json()["data"]["resume_id"] is None


def test_interview_api_sse_frames_are_all_json_business_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认面试 SSE 只返回可被前端直接 JSON.parse 的业务事件帧。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 2},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["data"]["session_id"]

    with client.stream(
        "POST",
        f"/api/interview/sessions/{session_id}/answers",
        json={
            "answer_text": (
                "我会先说明缓存目标，再结合热点数据、一致性要求和失效策略设计 Redis 方案，"
                "同时补充持久化、监控告警和故障降级处理。"
            )
        },
    ) as response:
        assert response.status_code == 200
        raw_lines = [line for line in response.iter_lines()]

    non_empty_lines = [line for line in raw_lines if line]
    assert non_empty_lines, "SSE 不应返回空结果"
    assert all(not line.startswith(":") for line in non_empty_lines), non_empty_lines

    data_lines = [line for line in non_empty_lines if line.startswith("data: ")]
    assert data_lines, non_empty_lines

    payloads = [json.loads(line.removeprefix("data: ")) for line in data_lines]
    assert all(isinstance(item, dict) and isinstance(item.get("type"), str) for item in payloads)


def test_interview_api_returns_domain_error_for_unknown_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知会话应返回面试域错误码。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    response = client.get("/api/interview/sessions/not-found")

    assert response.status_code == 404
    assert response.json()["code"] == 3101


def test_interview_api_blocks_cross_visitor_session_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同匿名访客不应访问彼此的面试会话。"""

    service, _, session, _ = _build_test_service()
    owner_client = _build_api_client(monkeypatch, service, session)
    create_response = owner_client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["data"]["session_id"]

    stranger_client = _build_api_client(monkeypatch, service, session)
    response = stranger_client.get(f"/api/interview/sessions/{session_id}")

    assert response.status_code == 404
    assert response.json()["code"] == 3101


def test_interview_api_rejects_blank_answer_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空白答案应触发请求参数校验错误。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)
    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1},
    )
    session_id = create_response.json()["data"]["session_id"]

    response = client.post(
        f"/api/interview/sessions/{session_id}/answers/draft",
        json={"answer_text": "   "},
    )

    assert response.status_code == 422
    assert response.json()["code"] == 1001


def test_interview_api_supports_report_query_and_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 应支持报告查询与 Markdown 导出占位。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 1},
    )
    session_id = create_response.json()["data"]["session_id"]

    with client.stream(
        "POST",
        f"/api/interview/sessions/{session_id}/answers",
        json={
            "answer_text": (
                "我会先做问题拆解，再结合数据库事务、一致性、回滚和监控指标说明完整方案，"
                "并补充一次真实故障复盘中的处理过程。"
            )
        },
    ) as response:
        assert response.status_code == 200
        _ = list(response.iter_lines())

    report_response = client.get(f"/api/interview/sessions/{session_id}/report")
    export_response = client.get(f"/api/interview/sessions/{session_id}/report/export")

    assert report_response.status_code == 200
    assert report_response.json()["data"]["status"] == "generated"
    assert export_response.status_code == 200
    assert export_response.json()["data"]["export_format"] == "markdown"
    assert "# 面试评估报告" in export_response.json()["data"]["content"]


@pytest.mark.asyncio
async def test_interview_service_does_not_fallback_to_sync_after_async_publish_succeeds() -> None:
    """异步任务发布成功后，即使状态持久化失败，也不应再回退同步执行。"""

    service, repository, session, _ = _build_test_service(use_disabled_stream_producer=False)
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=1),
        TEST_VISITOR_ID,
    )
    report_entity = InterviewReportEntity(
        session_id=created.session_id,
        status=InterviewReportStatus.PENDING.value,
        report_json={"status": "pending"},
        score_json={"status": "pending"},
    )
    repository.reports[created.session_id] = report_entity

    async def _raise_on_save_report(*args: Any, **kwargs: Any) -> InterviewReportEntity:
        raise RuntimeError("db write failed after publish")

    service._stream_producer.publish = AsyncMock(return_value="1-0")  # type: ignore[method-assign]
    service._persistence_service.save_report = AsyncMock(side_effect=_raise_on_save_report)  # type: ignore[method-assign]
    service._evaluation_service.generate_report = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="db write failed after publish"):
        await service._schedule_or_generate_report(
            session,
            session_id=created.session_id,
            interview_session=repository.sessions[created.session_id],
            state={
                "completion_message": "completed",
            },
            existing_report=report_entity,
        )

    service._stream_producer.publish.assert_awaited_once()
    service._evaluation_service.generate_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_interview_service_restores_legacy_topic_state_into_main_question_schema() -> None:
    """旧 topic 会话恢复后应自动转换为主问题计划单元。"""

    service, repository, session, redis_backend = _build_test_service()
    created = await service.create_session(
        session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=2),
        TEST_VISITOR_ID,
    )

    stored_session = repository.sessions[created.session_id]
    stored_session.session_context_json = {
        **deepcopy(stored_session.session_context_json),
        "current_topic_key": "LEGACY_TOPIC_1",
        "remaining_topics": ["LEGACY_TOPIC_1", "LEGACY_TOPIC_2"],
        "interview_plan": {
            **deepcopy(stored_session.session_context_json["interview_plan"]),
            "question_blueprints": [
                {
                    **deepcopy(stored_session.session_context_json["interview_plan"]["question_blueprints"][0]),
                    "topic_key": "LEGACY_TOPIC_1",
                },
                {
                    **deepcopy(stored_session.session_context_json["interview_plan"]["question_blueprints"][1]),
                    "topic_key": "LEGACY_TOPIC_2",
                },
            ],
            "required_topics": ["LEGACY_TOPIC_1"],
            "optional_topics": ["LEGACY_TOPIC_2"],
        },
        "plan_progress": {
            "covered_topics": ["LEGACY_TOPIC_1"],
            "closed_topics": ["LEGACY_TOPIC_1"],
            "closed_question_keys": ["q-1"],
            "current_topic_key": "LEGACY_TOPIC_1",
            "remaining_topics": ["LEGACY_TOPIC_2"],
            "rounds_used": 1,
            "evidence_count": 1,
            "history": [
                {
                    "event": "answer_observed",
                    "topic_key": "LEGACY_TOPIC_1",
                    "question_key": "q-1",
                    "topic_status": "covered",
                }
            ],
        },
        "coverage_status": {
            "LEGACY_TOPIC_1": {
                "topic_key": "LEGACY_TOPIC_1",
                "question_key": "q-1",
                "required": True,
                "status": "covered",
                "confidence": 0.9,
                "observed_signals": ["implementation details"],
                "missing_signals": [],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": True,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "topic_key": "LEGACY_TOPIC_1",
                "answer_text_summary": "legacy summary",
                "observed_signals": ["implementation details"],
                "missing_signals": [],
                "topic_status": "covered",
                "confidence": 0.9,
                "suggested_action": "next_question",
                "reasoning": "legacy",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        "termination_decision_context": {
            "current_topic_key": "LEGACY_TOPIC_1",
            "all_required_topics_covered": True,
            "remaining_required_topics": [],
            "remaining_optional_topics": ["LEGACY_TOPIC_2"],
            "remaining_round_budget": 1,
            "latest_decision": {
                "topic_status": "covered",
                "next_topic_key": "LEGACY_TOPIC_2",
            },
        },
    }
    stored_session.questions_json = list(stored_session.questions_json or [])
    stored_session.current_round = 1
    redis_backend.storage.clear()

    loaded = await service.get_session(session, created.session_id, TEST_VISITOR_ID)

    assert loaded.current_question is not None
    assert loaded.current_question.question_key == "q-1"

    saved = await service.save_draft_answer(
        session,
        created.session_id,
        SubmitAnswerRequest(question_key="q-1", answer_text="legacy draft"),
        TEST_VISITOR_ID,
    )
    reloaded_session = repository.sessions[created.session_id]

    assert saved.current_question is not None
    assert reloaded_session.session_context_json["current_main_question_key"] == "q-1"
    assert reloaded_session.session_context_json["remaining_main_question_keys"] == ["q-2"]
    assert "current_topic_key" not in reloaded_session.session_context_json
    assert "remaining_topics" not in reloaded_session.session_context_json


@pytest.mark.asyncio
async def test_executor_injects_github_evidence_context_when_github_tool_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub 工具命中时，应把仓库证据注入 follow-up prompt。"""

    from app.tools.github_repo_evidence_tool import GitHubRepoEvidenceItem, GitHubRepoEvidenceToolResult

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {}

    async def _fake_github_tool(**kwargs: Any) -> GitHubRepoEvidenceToolResult:
        return GitHubRepoEvidenceToolResult(
            found=True,
            repo_url="https://github.com/Snailclimb/interview-guide",
            repo_name="Snailclimb/interview-guide",
            matched_project_title="面试知识库项目",
            matched_project_excerpt="维护 Java 面试知识库并持续更新内容。",
            matched_files=["app/agents/custom_agent.py"],
            items=[
                GitHubRepoEvidenceItem(
                    evidence_type="code_snippet",
                    source_path="app/agents/custom_agent.py",
                    heading="CustomAgent",
                    raw_excerpt=(
                        "class CustomAgent:\n"
                        "    async def follow_up(self, prompts: list[str]) -> list[str]:\n"
                        "        tasks = [self._run(prompt) for prompt in prompts]\n"
                        "        return await asyncio.gather(*tasks)"
                    ),
                    normalized_snippet="CustomAgent 通过 asyncio.gather 并发执行 follow-up 任务。",
                    matched_terms=["asyncio", "CustomAgent"],
                    relevance_score=0.91,
                    why_it_matched=(
                        "命中关键词：asyncio, CustomAgent；"
                        "符号：CustomAgent；"
                        "位置：app/agents/custom_agent.py:1-4"
                    ),
                    language="python",
                    start_line=1,
                    end_line=4,
                )
            ],
            retrieval_reason="github_code_search_matched",
        )

    monkeypatch.setattr(executor_module, "github_repo_evidence_tool", _fake_github_tool)

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
                    tool_name="github_repo_evidence_tool",
                    arguments={
                        "category_key": "PROJECT",
                        "question_text": "这个项目是怎么持续维护的？",
                        "answer_text": "我会持续更新内容。",
                        "search_keywords": ["asyncio", "CustomAgent"],
                        "focus_topics": ["维护", "项目"],
                        "missing_signals": ["维护方式"],
                        "top_k": 2,
                    },
                    reason="需要 GitHub 仓库事实补证",
                )
            captured["template_name"] = template_name
            captured["follow_up_evidence_context"] = variables["follow_up_evidence_context"]
            return schema(question_text="你提到持续更新，这个仓库的维护机制具体是怎么落地的？")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "这个项目是怎么持续维护的？",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "PROJECT",
                    "question_text": "这个项目是怎么持续维护的？",
                    "round_index": 1,
                    "intent": "考察项目维护与实现细节",
                    "must_observe_signals": ["维护方式"],
                    "follow_up_focus": ["asyncio", "CustomAgent"],
                    "completion_criteria": ["说清楚真实实现"],
                    "priority": 1,
                    "can_skip": False,
                }
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.3,
                "observed_signals": ["项目概述"],
                "missing_signals": ["维护方式"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会持续更新内容。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["template_name"] == "executor_system.st"
    assert "app/agents/custom_agent.py (1-4)" in captured["follow_up_evidence_context"]
    assert "代码片段：" in captured["follow_up_evidence_context"]
    assert "asyncio.gather" in captured["follow_up_evidence_context"]
    assert "Snailclimb/interview-guide" not in captured["follow_up_evidence_context"]
    assert "简历绑定项目" not in captured["follow_up_evidence_context"]
    assert "关键词：" not in captured["follow_up_evidence_context"]
    assert "命中原因：" not in captured["follow_up_evidence_context"]
    assert "README 命中章节" not in captured["follow_up_evidence_context"]
    assert "摘要：" not in captured["follow_up_evidence_context"]
    assert next_state["latest_tool_context"]["tool_name"] == "github_repo_evidence_tool"
    assert next_state["latest_tool_context"]["success"] is True
    assert next_state["latest_tool_context"]["arguments"]["search_keywords"] == ["asyncio", "CustomAgent"]
    assert next_state["latest_tool_context"]["arguments"]["keywords"] == ["asyncio", "CustomAgent"]


@pytest.mark.asyncio
async def test_executor_tool_decision_receives_project_available_tools() -> None:
    """PROJECT 追问应把简历和 GitHub 工具作为可用工具传入 tool decision。"""

    captured: dict[str, Any] = {}

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
                captured["available_tools"] = variables["available_tools"]
                captured["available_tool_guidance"] = variables["available_tool_guidance"]
                return schema(
                    should_call_tool=False,
                    tool_name=None,
                    arguments={},
                    reason="直接追问即可",
                )
            return schema(question_text="请继续说明这个项目中的真实实现细节。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-project-tools",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "请结合真实项目说明你的实现经验。",
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
                "observed_signals": ["项目背景"],
                "missing_signals": ["实现细节"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我参与过这个项目。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert "resume_evidence_tool" in captured["available_tools"]
    assert "github_repo_evidence_tool" in captured["available_tools"]
    assert "knowledge_evidence_tool" not in captured["available_tools"]
    assert "PROJECT" in captured["available_tool_guidance"]
    assert next_state["latest_tool_context"]["tool_name"] is None


@pytest.mark.asyncio
async def test_executor_blocks_github_when_not_available_for_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 PROJECT 分类即使模型返回 GitHub 工具，也不应执行越权工具。"""

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {"github_called": False}

    async def _fake_github_tool(**kwargs: Any) -> Any:
        captured["github_called"] = True
        raise AssertionError("github tool must not be called for non-PROJECT categories")

    monkeypatch.setattr(executor_module, "github_repo_evidence_tool", _fake_github_tool)

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
                captured["available_tools"] = variables["available_tools"]
                return schema(
                    should_call_tool=True,
                    tool_name="github_repo_evidence_tool",
                    arguments={
                        "category_key": "SYSTEM_DESIGN_SCENARIO",
                        "question_text": "如何处理库存扣减一致性？",
                        "answer_text": "我会设计库存扣减流程。",
                        "search_keywords": ["InventoryService", "Redis Stream"],
                        "focus_topics": ["库存扣减"],
                        "missing_signals": ["失败回滚"],
                        "top_k": 2,
                    },
                    reason="模型误选 GitHub",
                )
            return schema(question_text="请具体说明库存预扣、最终扣减和失败回滚流程。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-block-github",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 秒杀项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "SYSTEM_DESIGN_SCENARIO",
                "question_text": "在秒杀场景中，你会如何处理库存扣减一致性？",
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
                "missing_signals": ["失败回滚"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会设计库存扣减流程。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["available_tools"].strip() == "- knowledge_evidence_tool"
    assert captured["github_called"] is False
    assert next_state["latest_tool_context"]["tool_name"] == "github_repo_evidence_tool"
    assert next_state["latest_tool_context"]["decision_reason"] == "tool_not_available_for_category"
    assert next_state["latest_tool_context"]["success"] is False


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

    from app.tools.knowledge_evidence_tool import (
        KnowledgeEvidenceDocument,
        KnowledgeEvidenceItem,
        KnowledgeEvidenceToolResult,
    )

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


def test_executor_keeps_two_github_code_cards_and_truncates_prompt_excerpts() -> None:
    """GitHub 代码证据传给 LLM 时应稳定保留两张卡，并只裁剪展示文本。"""

    from app.tools.github_repo_evidence_tool import GitHubRepoEvidenceItem, GitHubRepoEvidenceToolResult

    long_excerpt = "\n".join(f"line-{index}: CustomAgent asyncio detail" for index in range(1, 20))
    result = GitHubRepoEvidenceToolResult(
        found=True,
        repo_url="https://github.com/alice/interview-agent",
        repo_name="alice/interview-agent",
        matched_project_title="AI 面试 Agent 项目",
        matched_project_excerpt="负责追问生成链路。",
        matched_files=["app/agents/custom_agent.py", "app/runtime.py"],
        items=[
            GitHubRepoEvidenceItem(
                evidence_type="code_snippet",
                source_path="app/agents/custom_agent.py",
                heading="CustomAgent",
                raw_excerpt=long_excerpt,
                normalized_snippet="CustomAgent asyncio detail",
                matched_terms=["CustomAgent", "asyncio"],
                relevance_score=0.95,
                why_it_matched="命中关键词：CustomAgent, asyncio；符号：CustomAgent；位置：app/agents/custom_agent.py:1-19",
                language="python",
                start_line=1,
                end_line=19,
            ),
            GitHubRepoEvidenceItem(
                evidence_type="code_snippet",
                source_path="app/runtime.py",
                heading="run",
                raw_excerpt=long_excerpt,
                normalized_snippet="runtime asyncio detail",
                matched_terms=["asyncio"],
                relevance_score=0.9,
                why_it_matched="命中关键词：asyncio；符号：run；位置：app/runtime.py:1-19",
                language="python",
                start_line=1,
                end_line=19,
            ),
        ],
        retrieval_reason="github_code_search_matched",
    )
    executor = InterviewExecutor(InterviewPromptRunner(enable_llm=False))

    context = executor._format_github_code_evidence_context(result.model_dump(mode="json"))

    assert "1. 文件：app/agents/custom_agent.py (1-19)" in context
    assert "2. 文件：app/runtime.py (1-19)" in context
    assert context.count("代码片段：") == 2
    assert "关键词：" not in context
    assert "命中原因：" not in context
    assert "语言：" not in context
    assert "符号：" not in context
    assert "line-12: CustomAgent asyncio detail" in context
    assert "line-13: CustomAgent asyncio detail" not in context
    assert result.items[0].raw_excerpt == long_excerpt


def test_executor_formats_github_readme_fallback_as_available_evidence() -> None:
    """README 兜底证据应进入追问上下文，而不是显示为无可用证据。"""

    from app.tools.github_repo_evidence_tool import GitHubRepoEvidenceItem, GitHubRepoEvidenceToolResult

    result = GitHubRepoEvidenceToolResult(
        found=True,
        repo_url="https://github.com/alice/interview-agent",
        repo_name="alice/interview-agent",
        matched_project_title="AI 面试 Agent 项目",
        matched_project_excerpt="负责 Agent 编排、异步执行和追问链路设计。",
        matched_files=["README.md"],
        items=[
            GitHubRepoEvidenceItem(
                evidence_type="repo_readme_excerpt",
                source_path="README.md",
                heading="核心能力",
                raw_excerpt="系统使用 Redis Stream 处理异步任务，并通过 InterviewQuestionService 生成 followUps。",
                normalized_snippet="Redis Stream InterviewQuestionService followUps",
                matched_terms=["RedisStream", "FollowUpService"],
                relevance_score=0.4,
                why_it_matched="代码搜索为空，使用 README 作为 GitHub 追问兜底证据",
                language="markdown",
                start_line=None,
                end_line=None,
            )
        ],
        retrieval_reason="github_readme_fallback_matched",
    )
    executor = InterviewExecutor(InterviewPromptRunner(enable_llm=False))

    context = executor._format_github_code_evidence_context(result.model_dump(mode="json"))

    assert "无可用" not in context
    assert "文件：README.md" in context
    assert "Redis Stream" in context
    assert "检索原因：" not in context


@pytest.mark.asyncio
async def test_executor_falls_back_to_resume_tool_when_github_tool_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub 工具失败时，应降级到简历证据工具，不中断追问。"""

    from app.tools.resume_evidence_tool import ResumeEvidenceItem, ResumeEvidenceToolResult

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {}

    async def _failing_github_tool(**kwargs: Any) -> Any:
        raise RuntimeError("github mcp timeout")

    def _fake_resume_tool(**kwargs: Any) -> ResumeEvidenceToolResult:
        return ResumeEvidenceToolResult(
            found=True,
            items=[
                ResumeEvidenceItem(
                    snippet="负责知识库内容维护和版本整理。",
                    source_section="面试知识库项目",
                    matched_terms=["维护", "版本整理"],
                    relevance_score=0.88,
                )
            ],
            matched_projects=["面试知识库项目"],
            matched_skills=[],
            retrieval_reason="hybrid_bm25_embedding",
        )

    monkeypatch.setattr(executor_module, "github_repo_evidence_tool", _failing_github_tool)
    monkeypatch.setattr(executor_module, "resume_evidence_tool", _fake_resume_tool)

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
                    tool_name="github_repo_evidence_tool",
                    arguments={
                        "category_key": "PROJECT",
                        "question_text": "这个项目是怎么维护的？",
                        "answer_text": "我会持续更新。",
                        "search_keywords": ["VersionManager", "Scheduler"],
                        "focus_topics": ["维护"],
                        "missing_signals": ["维护方式"],
                        "top_k": 2,
                    },
                    reason="优先尝试 GitHub 仓库补证",
                )
            captured["follow_up_evidence_context"] = variables["follow_up_evidence_context"]
            return schema(question_text="除了持续更新，你具体是如何做版本整理和维护节奏管理的？")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- 负责知识库内容维护和版本整理\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "这个项目是怎么维护的？",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "PROJECT",
                    "question_text": "这个项目是怎么维护的？",
                    "round_index": 1,
                    "intent": "考察维护流程与工程实践",
                    "must_observe_signals": ["维护方式"],
                    "follow_up_focus": ["VersionManager", "Scheduler"],
                    "completion_criteria": ["说清楚维护方式"],
                    "priority": 1,
                    "can_skip": False,
                }
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.2,
                "observed_signals": [],
                "missing_signals": ["维护方式"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我会持续更新。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert "GitHub 补证失败" in captured["follow_up_evidence_context"]
    assert next_state["latest_tool_context"]["tool_name"] == "resume_evidence_tool"
    assert next_state["latest_tool_context"]["fallback_from_tool"] == "github_repo_evidence_tool"
    assert "github mcp timeout" in next_state["latest_tool_context"]["error_message"]


@pytest.mark.asyncio
async def test_replanner_allows_follow_up_for_long_answer_when_missing_signals_remain() -> None:
    """回答较长但仍缺关键信号时，仍应允许 follow-up。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(side_effect=ValueError("provider unavailable"))  # type: ignore[method-assign]
    replanner = InterviewReplanner(runner)

    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "请讲讲你的缓存方案设计",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "请讲讲数据库优化",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "CACHE",
                    "question_text": "请讲讲你的缓存方案设计",
                    "round_index": 1,
                    "intent": "考察缓存设计权衡",
                    "must_observe_signals": ["失效策略", "结果"],
                    "follow_up_focus": ["失效策略", "结果"],
                    "completion_criteria": ["说清设计和结果"],
                    "priority": 1,
                    "can_skip": False,
                },
                {
                    "question_key": "q-2",
                    "category_key": "DB",
                    "question_text": "请讲讲数据库优化",
                    "round_index": 2,
                    "intent": "考察数据库优化",
                    "must_observe_signals": ["索引", "结果"],
                    "follow_up_focus": ["索引"],
                    "completion_criteria": ["说清优化方法"],
                    "priority": 2,
                    "can_skip": False,
                },
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.72,
                "observed_signals": ["缓存"],
                "missing_signals": ["结果", "失效策略"],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": False,
                "last_updated": None,
            }
        },
        "termination_decision_context": {
            "remaining_required_main_question_keys": ["q-1", "q-2"],
            "remaining_round_budget": 1,
            "all_required_main_questions_covered": False,
        },
        "current_question_key": "q-1",
        "current_main_question_key": "q-1",
        "remaining_main_question_keys": ["q-1", "q-2"],
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "我当时主要从缓存分层、热点 key 控制、读写路径拆分以及一致性补偿机制几个方面做了整体设计，同时还考虑了系统容量和服务稳定性。",
    }

    next_state = await replanner.run(state)  # type: ignore[arg-type]

    assert next_state["next_action"] == "follow_up"


@pytest.mark.asyncio
async def test_replanner_fallback_respects_observer_next_question_suggestion() -> None:
    """Observer 已建议下一题时，fallback replanner 不应只因残留 missing_signals 继续追问。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(side_effect=ValueError("provider unavailable"))  # type: ignore[method-assign]
    replanner = InterviewReplanner(runner)

    state = {
        "session_id": "session-observer-next",
        "skill": {
            "skill_id": "python-backend",
            "display_name": "Python Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "CACHE",
                "question_text": "Please describe your cache design.",
                "parent_question_key": None,
                "source": "planned",
                "status": "answered",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "DB",
                "question_text": "Please describe your database optimization experience.",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "CACHE",
                    "question_text": "Please describe your cache design.",
                    "round_index": 1,
                    "intent": "Check cache design tradeoffs.",
                    "must_observe_signals": ["consistency", "failure handling", "results"],
                    "follow_up_focus": ["failure handling", "results"],
                    "completion_criteria": ["Baseline design evidence is clear."],
                    "priority": 1,
                    "can_skip": False,
                },
                {
                    "question_key": "q-2",
                    "category_key": "DB",
                    "question_text": "Please describe your database optimization experience.",
                    "round_index": 2,
                    "intent": "Check database optimization.",
                    "must_observe_signals": ["index"],
                    "follow_up_focus": ["index"],
                    "completion_criteria": ["Optimization method is clear."],
                    "priority": 2,
                    "can_skip": False,
                },
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.67,
                "observed_signals": ["consistency", "failure handling"],
                "missing_signals": ["results"],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": False,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "answer_text_summary": "baseline cache design evidence",
                "observed_signals": ["consistency", "failure handling"],
                "missing_signals": ["results"],
                "main_question_status": "partial",
                "confidence": 0.67,
                "suggested_action": "next_question",
                "reasoning": "Fallback observer found enough baseline evidence.",
            }
        ],
        "termination_decision_context": {
            "remaining_required_main_question_keys": ["q-1", "q-2"],
            "remaining_round_budget": 1,
            "latest_decision": {"observer_suggested_action": "next_question"},
            "all_required_main_questions_covered": False,
        },
        "current_question_key": "q-1",
        "current_main_question_key": "q-1",
        "remaining_main_question_keys": ["q-1", "q-2"],
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "I covered goals, consistency, hot keys, failure handling, monitoring, and fallback.",
        "max_rounds": 2,
    }

    next_state = await replanner.run(state)  # type: ignore[arg-type]

    assert next_state["next_action"] == "next_question"
    assert next_state["current_main_question_key"] == "q-2"


@pytest.mark.asyncio
async def test_replanner_forces_next_question_when_follow_up_limit_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使 LLM 继续选择 follow_up，达到上限后也必须转入下一主问题。"""

    monkeypatch.setattr(config.interview, "max_follow_up_questions", 5)
    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=_ReplanAliasSchema(
            action="follow_up",
            reason="仍缺少代码实现，继续追问。",
            topic_status="partial",
            coverage_update="仍缺代码实现。",
            remaining_risk="缺少代码实现。",
            next_topic_key="q-1",
            plan_adjustment="继续追问当前主问题。",
        )
    )
    replanner = InterviewReplanner(runner)

    next_state = await replanner.run(_build_follow_up_limit_state(include_next_question=True))  # type: ignore[arg-type]

    assert next_state["next_action"] == "next_question"
    assert next_state["termination_decision_context"]["latest_decision"]["next_main_question_key"] == "q-2"
    assert "追问上限" in next_state["action_reason"]


@pytest.mark.asyncio
async def test_replanner_forces_complete_when_follow_up_limit_reached_without_next_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """达到追问上限且没有后续主问题时，应结束面试。"""

    monkeypatch.setattr(config.interview, "max_follow_up_questions", 5)
    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=_ReplanAliasSchema(
            action="follow_up",
            reason="仍缺少代码实现，继续追问。",
            topic_status="partial",
            coverage_update="仍缺代码实现。",
            remaining_risk="缺少代码实现。",
            next_topic_key="q-1",
            plan_adjustment="继续追问当前主问题。",
        )
    )
    replanner = InterviewReplanner(runner)

    next_state = await replanner.run(_build_follow_up_limit_state(include_next_question=False))  # type: ignore[arg-type]

    assert next_state["next_action"] == "complete"
    assert next_state["completed"] is True
    assert "追问上限" in next_state["action_reason"]


@pytest.mark.asyncio
async def test_replanner_overrides_next_question_with_github_follow_up() -> None:
    """项目题存在 GitHub 链接且尚未追问时，应优先保留一次 GitHub follow-up。"""

    runner = InterviewPromptRunner(enable_llm=True)
    runner.ainvoke_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=_ReplanAliasSchema(
            action="next_question",
            reason="当前问题已经足够完整，可以继续下一题。",
            next_question=None,
            topic_status="covered",
            coverage_update="已覆盖项目设计。",
            remaining_risk="",
            next_topic_key="q-2",
            plan_adjustment=None,
        )
    )
    replanner = InterviewReplanner(runner)

    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "请介绍你做过的项目，以及你是如何长期维护它的？",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
            {
                "question_key": "q-2",
                "round_index": 2,
                "category_key": "JAVA",
                "question_text": "请讲讲 JVM 调优经验",
                "parent_question_key": None,
                "source": "planned",
                "status": "planned",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            },
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "PROJECT",
                    "question_text": "请介绍你做过的项目，以及你是如何长期维护它的？",
                    "round_index": 1,
                    "intent": "考察项目设计与维护",
                    "must_observe_signals": ["维护方式"],
                    "follow_up_focus": ["维护", "实现"],
                    "completion_criteria": ["说清项目价值"],
                    "priority": 1,
                    "can_skip": False,
                },
                {
                    "question_key": "q-2",
                    "category_key": "JAVA",
                    "question_text": "请讲讲 JVM 调优经验",
                    "round_index": 2,
                    "intent": "考察 JVM",
                    "must_observe_signals": ["调优"],
                    "follow_up_focus": ["调优"],
                    "completion_criteria": ["说清调优方法"],
                    "priority": 2,
                    "can_skip": False,
                },
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "covered",
                "confidence": 0.95,
                "observed_signals": ["项目概述"],
                "missing_signals": [],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": True,
                "last_updated": None,
            }
        },
        "termination_decision_context": {
            "remaining_required_main_question_keys": ["q-2"],
            "remaining_round_budget": 1,
            "all_required_main_questions_covered": False,
        },
        "current_question_key": "q-1",
        "current_main_question_key": "q-1",
        "remaining_main_question_keys": ["q-2"],
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "这个项目我一直在维护，也做过很多内容更新。",
    }

    next_state = await replanner.run(state)  # type: ignore[arg-type]

    assert next_state["next_action"] == "follow_up"
    assert "GitHub" in next_state["action_reason"] or "repository" in next_state["action_reason"]


@pytest.mark.asyncio
async def test_executor_prioritizes_github_tool_on_first_project_follow_up_when_llm_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次项目追问若有 GitHub 链接，即使模型跳过工具，也应优先尝试 GitHub 补证。"""

    from app.tools.github_repo_evidence_tool import GitHubRepoEvidenceItem, GitHubRepoEvidenceToolResult

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {}

    async def _fake_github_tool(**kwargs: Any) -> GitHubRepoEvidenceToolResult:
        captured["tool_kwargs"] = kwargs
        return GitHubRepoEvidenceToolResult(
            found=True,
            repo_url="https://github.com/Snailclimb/interview-guide",
            repo_name="Snailclimb/interview-guide",
            matched_project_title="面试知识库项目",
            matched_project_excerpt="长期维护面试知识库。",
            matched_files=["app/agents/custom_agent.py"],
            items=[
                GitHubRepoEvidenceItem(
                    evidence_type="code_snippet",
                    source_path="app/agents/custom_agent.py",
                    heading="CustomAgent",
                    raw_excerpt=(
                        "class CustomAgent:\n"
                        "    async def follow_up(self, prompts: list[str]) -> list[str]:\n"
                        "        tasks = [self._run(prompt) for prompt in prompts]\n"
                        "        return await asyncio.gather(*tasks)"
                    ),
                    normalized_snippet="CustomAgent 通过 asyncio.gather 并发执行 follow-up 任务。",
                    matched_terms=["CustomAgent", "asyncio"],
                    relevance_score=0.9,
                    why_it_matched=(
                        "命中关键词：CustomAgent, asyncio；"
                        "符号：CustomAgent；"
                        "位置：app/agents/custom_agent.py:1-4"
                    ),
                    language="python",
                    start_line=1,
                    end_line=4,
                )
            ],
            retrieval_reason="github_code_search_matched",
        )

    monkeypatch.setattr(executor_module, "github_repo_evidence_tool", _fake_github_tool)

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
                    should_call_tool=False,
                    tool_name=None,
                    arguments={},
                    reason="当前回答已经足够具体",
                )
            return schema(question_text="你提到持续更新，这个仓库具体如何维护？")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-1",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "请介绍这个项目，以及你是如何维护它的？",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "PROJECT",
                    "question_text": "请介绍这个项目，以及你是如何维护它的？",
                    "round_index": 1,
                    "intent": "考察项目维护与代码实现",
                    "must_observe_signals": ["维护方式"],
                    "follow_up_focus": ["CustomAgent", "asyncio"],
                    "completion_criteria": ["说清楚实现细节"],
                    "priority": 1,
                    "can_skip": False,
                }
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.85,
                "observed_signals": ["项目概述"],
                "missing_signals": [],
                "follow_up_count": 0,
                "evidence_count": 1,
                "completed": False,
                "last_updated": None,
            }
        },
        "answer_observations": [
            {
                "question_key": "q-1",
                "main_question_key": "q-1",
                "search_keywords": ["CustomAgent", "asyncio"],
            }
        ],
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "这是一个我长期维护的项目。",
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert next_state["latest_tool_context"]["tool_name"] == "github_repo_evidence_tool"
    assert next_state["latest_tool_context"]["decision_reason"] == "forced_first_github_follow_up"
    assert captured["tool_kwargs"]["search_keywords"] == ["CustomAgent", "asyncio"]
    assert captured["tool_kwargs"]["keywords"] == ["CustomAgent", "asyncio"]
    assert captured["tool_kwargs"]["top_k"] == 2


@pytest.mark.asyncio
async def test_executor_does_not_force_github_without_explicit_search_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次项目追问即使仓库可用，没有显式 search_keywords 也不应强制走 GitHub。"""

    executor_module = importlib.import_module("app.agent.interview.executor")
    captured: dict[str, Any] = {"github_called": False, "resume_called": False}

    async def _fake_github_tool(**kwargs: Any) -> Any:
        captured["github_called"] = True
        raise AssertionError("github tool should not be called without explicit search keywords")

    def _fake_resume_tool(**kwargs: Any) -> Any:
        captured["resume_called"] = True
        return executor_module.ResumeEvidenceToolResult(
            found=False,
            items=[],
            matched_projects=[],
            matched_skills=[],
            retrieval_reason="hybrid_bm25_embedding",
        )

    monkeypatch.setattr(executor_module, "github_repo_evidence_tool", _fake_github_tool)
    monkeypatch.setattr(executor_module, "resume_evidence_tool", _fake_resume_tool)

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
                    should_call_tool=False,
                    tool_name=None,
                    arguments={},
                    reason="当前回答已经足够具体",
                )
            return schema(question_text="请继续补充你在这个项目里的具体维护动作。")

    executor = InterviewExecutor(_RecordingPromptRunner())  # type: ignore[arg-type]
    state = {
        "session_id": "session-2",
        "skill": {
            "skill_id": "java-backend",
            "display_name": "Java Backend",
            "description": "desc",
            "content_markdown": "skill",
            "reference_markdown": "ref",
            "categories": [],
            "reference_files": [],
        },
        "resume_markdown": (
            "# 项目经历\n"
            "## 面试知识库项目\n"
            "- GitHub: https://github.com/Snailclimb/interview-guide\n"
        ),
        "resume_metadata": {},
        "questions": [
            {
                "question_key": "q-1",
                "round_index": 1,
                "category_key": "PROJECT",
                "question_text": "请介绍这个项目，以及你是如何维护它的？",
                "parent_question_key": None,
                "source": "planned",
                "status": "asked",
                "is_follow_up": False,
                "asked_at": None,
                "answered_at": None,
            }
        ],
        "interview_plan": {
            "question_blueprints": [
                {
                    "question_key": "q-1",
                    "category_key": "PROJECT",
                    "question_text": "请介绍这个项目，以及你是如何维护它的？",
                    "round_index": 1,
                    "intent": "考察项目维护与代码实现",
                    "must_observe_signals": ["维护方式"],
                    "follow_up_focus": ["CustomAgent", "asyncio"],
                    "completion_criteria": ["说清楚实现细节"],
                    "priority": 1,
                    "can_skip": False,
                }
            ]
        },
        "coverage_status": {
            "q-1": {
                "main_question_key": "q-1",
                "question_key": "q-1",
                "required": True,
                "status": "partial",
                "confidence": 0.4,
                "observed_signals": ["项目概述"],
                "missing_signals": ["维护方式"],
                "follow_up_count": 0,
                "evidence_count": 0,
                "completed": False,
                "last_updated": None,
            }
        },
        "current_question_key": "q-1",
        "current_round": 1,
        "follow_up_count": 0,
        "latest_answer_text": "这是一个我长期维护的项目。",
        "latest_observation": {},
        "next_action": "follow_up",
    }

    next_state = await executor.run(state)  # type: ignore[arg-type]

    assert captured["github_called"] is False
    assert captured["resume_called"] is False
    assert next_state["latest_tool_context"]["tool_name"] is None
