"""文字面试主流程与统一评估链路的最小验证。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
import pytest

from app.agent.interview.executor import InterviewExecutor
from app.agent.evaluator.batch_evaluator import BatchEvaluator
from app.agent.evaluator.summarizer import InterviewSummarizer
from app.agent.interview.planner import InterviewPlanner
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
from app.services.skill_service import SkillService

TEST_VISITOR_ID = "00000000-0000-4000-8000-000000000001"


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


class _InMemoryInterviewRepository:
    """测试用面试仓储，绕过真实数据库。"""

    def __init__(self) -> None:
        self.sessions: dict[str, InterviewSessionEntity] = {}
        self.answers: list[InterviewAnswerEntity] = []
        self.reports: dict[str, InterviewReportEntity] = {}

    async def add_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        if not entity.id:
            entity.id = str(uuid4())
        self.sessions[entity.id] = entity
        return entity

    async def upsert_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        if not entity.id:
            entity.id = str(uuid4())
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


def _build_test_service() -> tuple[InterviewService, _InMemoryInterviewRepository, AsyncMock, _InMemoryRedisBackend]:
    """构建使用规则降级和内存仓储的面试服务。"""

    repository = _InMemoryInterviewRepository()
    redis_backend = _InMemoryRedisBackend()
    prompt_runner = InterviewPromptRunner(enable_llm=False)
    service = InterviewService(
        skill_service=SkillService(),
        persistence_service=InterviewPersistenceService(repository=repository),
        resume_service=_NullResumeService(),
        repository_factory=lambda _session: repository,
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


class _StructuredPromptSchema(BaseModel):
    """用于验证 structured output 参数的测试 schema。"""

    value: str


class _FollowUpAliasSchema(BaseModel):
    """用于验证追问别名兼容的测试 schema。"""

    question_text: str


class _ReplanAliasSchema(BaseModel):
    """用于验证 replanner 附加字段兼容的测试 schema。"""

    action: str
    reason: str
    next_question: str | None = None


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
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_interview_service_short_answer_triggers_follow_up() -> None:
    """短答案应触发追问分支。"""

    service, repository, session, _ = _build_test_service()
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

    assert plan_event["action"] == "follow_up"
    assert "追问" in content_event["content"] or "补充" in content_event["content"]
    assert done_event["session"]["current_question"]["question_key"] == "q-1-f-1"
    assert repository.answers[0].question_key == "q-1"


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

    assert plan_event["action"] == "next_question"
    assert done_event["session"]["current_question"]["question_key"] == "q-2"
    assert done_event["session"]["status"] == "active"


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

    assert plan_event["action"] == "complete"
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

    service, repository, session, _ = _build_test_service()
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
