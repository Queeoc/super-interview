"""Phase 2 领域模型与持久化骨架的最小验证。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.base import Base
from app.models.interview import (
    InterviewAnswerEntity,
    InterviewReportEntity,
    InterviewSessionEntity,
)
from app.models.knowledge import KnowledgeBaseEntity, RagChatMessageEntity, RagChatSessionEntity
from app.models.provider import LlmProviderEntity
from app.models.resume import ResumeAnalysisEntity, ResumeEntity
from app.repositories.interview_repository import InterviewRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.resume_repository import ResumeRepository
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.resume_persistence_service import ResumePersistenceService


class _FakeScalarResult:
    """简化的 SQLAlchemy 标量结果对象。"""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


@dataclass
class _FakeAsyncSession:
    """用于仓储单测的最小异步 Session 假对象。"""

    merged: list[Any] = field(default_factory=list)
    added: list[Any] = field(default_factory=list)
    deleted: list[Any] = field(default_factory=list)
    executed_statements: list[Any] = field(default_factory=list)
    scalar_result: _FakeScalarResult = field(default_factory=lambda: _FakeScalarResult([]))
    get_result: Any = None

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None

    async def merge(self, instance: Any) -> Any:
        self.merged.append(instance)
        return instance

    async def get(self, model: type[Any], ident: Any) -> Any:
        return self.get_result

    async def scalars(self, statement: Any) -> _FakeScalarResult:
        self.executed_statements.append(statement)
        return self.scalar_result

    async def execute(self, statement: Any) -> Any:
        self.executed_statements.append(statement)
        return None

    async def delete(self, instance: Any) -> None:
        self.deleted.append(instance)


def test_phase2_models_register_expected_tables() -> None:
    """导入实体后，Base 元数据应包含 Phase 2 的目标表。"""

    expected_tables = {
        "interview_sessions",
        "interview_answers",
        "interview_reports",
        "resumes",
        "resume_analyses",
        "knowledge_bases",
        "rag_chat_sessions",
        "rag_chat_messages",
        "llm_providers",
    }

    assert expected_tables.issubset(set(Base.metadata.tables))


def test_phase2_models_do_not_define_duplicate_index_names() -> None:
    """同一张表内不应定义重复的索引名。"""

    for table in Base.metadata.tables.values():
        index_names = [index.name for index in table.indexes if index.name]
        assert len(index_names) == len(set(index_names)), table.name


@pytest.mark.asyncio
async def test_interview_repository_basic_crud() -> None:
    """面试仓储应支持基础写入和查询。"""

    session = _FakeAsyncSession(get_result=InterviewSessionEntity())
    repository = InterviewRepository(session)
    entity = InterviewSessionEntity(title="后端面试")

    saved_entity = await repository.upsert_session(entity)
    assert saved_entity is entity
    assert session.merged == [entity]

    session.get_result = entity
    found_entity = await repository.get_session(entity.id)
    assert found_entity is entity

    session.scalar_result = _FakeScalarResult([entity])
    sessions = await repository.list_sessions_by_status("draft")
    assert sessions == [entity]


@pytest.mark.asyncio
async def test_resume_repository_tracks_analysis() -> None:
    """简历仓储应支持简历与分析结果的基础查询。"""

    session = _FakeAsyncSession()
    repository = ResumeRepository(session)
    resume = ResumeEntity(
        visitor_id="00000000-0000-4000-8000-000000000001",
        original_file_name="resume.pdf",
        storage_path="/tmp/resume.pdf",
        file_extension=".pdf",
        mime_type="application/pdf",
        content_hash="hash-1",
        file_size=1024,
    )
    analysis = ResumeAnalysisEntity(resume_id=resume.id)

    await repository.add_resume(resume)
    await repository.upsert_analysis(analysis)

    assert session.added[:1] == [resume]
    assert session.merged == [analysis]

    session.get_result = resume
    assert await repository.get_resume(resume.id) is resume

    session.scalar_result = _FakeScalarResult([analysis])
    assert await repository.list_analysis_by_status("pending") == [analysis]


@pytest.mark.asyncio
async def test_knowledge_repository_handles_chat_sessions() -> None:
    """知识库仓储应支持知识库、会话与消息的基础写入。"""

    session = _FakeAsyncSession()
    repository = KnowledgeRepository(session)
    knowledge_base = KnowledgeBaseEntity(name="算法题库")
    chat_session = RagChatSessionEntity(knowledge_base_id=knowledge_base.id)
    message = RagChatMessageEntity(
        session_id=chat_session.id,
        role="user",
        content="请介绍冒泡排序。",
    )

    await repository.add_knowledge_base(knowledge_base)
    await repository.add_chat_session(chat_session)
    await repository.add_message(message)

    assert session.added[:3] == [knowledge_base, chat_session, message]

    session.get_result = chat_session
    assert await repository.get_chat_session(chat_session.id) is chat_session

    session.scalar_result = _FakeScalarResult([message])
    messages = await repository.list_messages_by_session(chat_session.id)
    assert messages == [message]


@pytest.mark.asyncio
async def test_provider_repository_can_pick_default_provider() -> None:
    """Provider 仓储应支持默认 Provider 查询。"""

    session = _FakeAsyncSession()
    repository = ProviderRepository(session)
    provider = LlmProviderEntity(provider_code="qwen", provider_name="Qwen")

    await repository.add_provider(provider)
    assert session.added == [provider]

    session.scalar_result = _FakeScalarResult([provider])
    default_provider = await repository.get_default_provider()
    assert default_provider is provider


@pytest.mark.asyncio
async def test_interview_persistence_service_orchestrates_repository_calls() -> None:
    """面试持久化服务应聚合会话、答案与报告的写入。"""

    repository = MagicMock(spec=InterviewRepository)
    repository.upsert_session = AsyncMock(side_effect=lambda entity: entity)
    repository.add_answer = AsyncMock(side_effect=lambda entity: entity)
    repository.upsert_report = AsyncMock(side_effect=lambda entity: entity)
    service = InterviewPersistenceService(repository=repository)
    session = AsyncMock()
    interview_session = InterviewSessionEntity(id="session-1", title="后端面试")
    answer = InterviewAnswerEntity(
        session_id="",
        round_index=1,
        question_text="什么是事务？",
        answer_text="事务是一组原子操作。",
    )
    report = InterviewReportEntity(session_id="")

    saved_session = await service.save_session_bundle(
        session=session,
        interview_session=interview_session,
        answers=[answer],
        report=report,
    )

    assert saved_session is interview_session
    assert answer.session_id == "session-1"
    assert report.session_id == "session-1"
    repository.upsert_session.assert_awaited_once_with(interview_session)
    repository.add_answer.assert_awaited_once_with(answer)
    repository.upsert_report.assert_awaited_once_with(report)


@pytest.mark.asyncio
async def test_resume_persistence_service_orchestrates_repository_calls() -> None:
    """简历持久化服务应聚合简历和分析结果写入。"""

    repository = MagicMock(spec=ResumeRepository)
    repository.upsert_resume = AsyncMock(side_effect=lambda entity: entity)
    repository.upsert_analysis = AsyncMock(side_effect=lambda entity: entity)
    service = ResumePersistenceService(repository=repository)
    session = AsyncMock()
    resume = ResumeEntity(
        id="resume-1",
        visitor_id="00000000-0000-4000-8000-000000000002",
        original_file_name="resume.pdf",
        storage_path="/tmp/resume.pdf",
        file_extension=".pdf",
        mime_type="application/pdf",
        content_hash="hash-2",
        file_size=2048,
    )
    analysis = ResumeAnalysisEntity(resume_id="")

    saved_resume = await service.save_resume_bundle(
        session=session,
        resume=resume,
        analysis=analysis,
    )

    assert saved_resume is resume
    assert analysis.resume_id == "resume-1"
    repository.upsert_resume.assert_awaited_once_with(resume)
    repository.upsert_analysis.assert_awaited_once_with(analysis)
