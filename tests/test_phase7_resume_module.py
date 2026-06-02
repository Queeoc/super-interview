"""Phase 7 简历模块的最小闭环验证。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
import pymupdf4llm
import pytest
from starlette.datastructures import Headers

from app.agent.interview.prompts import InterviewPromptRunner
from app.api import resume as resume_api
from app.core.database import get_db_session
from app.middleware.error_handler import register_exception_handlers
from app.middleware.visitor_context import VISITOR_ID_COOKIE_NAME, VisitorContextMiddleware
from app.models.interview import CreateInterviewRequest
from app.models.resume import ResumeEntity, ResumeStatus
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.interview_service import InterviewService
from app.services.resume_persistence_service import ResumePersistenceService
from app.services.resume_service import ResumeContextBundle, ResumeService
from app.services.skill_service import SkillService
from app.utils.exceptions import BusinessException, ErrorCode


TEST_VISITOR_A = "00000000-0000-4000-8000-000000000011"
TEST_VISITOR_B = "00000000-0000-4000-8000-000000000022"


class _InMemoryResumeRepository:
    """测试用简历仓储。"""

    def __init__(self) -> None:
        self.resumes: dict[str, ResumeEntity] = {}
        self._sequence = 0

    def _tick(self) -> datetime:
        self._sequence += 1
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self._sequence)

    async def upsert_resume(self, entity: ResumeEntity) -> ResumeEntity:
        now = self._tick()
        if not entity.uploaded_at:
            entity.uploaded_at = now
        entity.updated_at = now
        self.resumes[entity.id] = entity
        return entity

    async def get_resume(self, resume_id: str) -> ResumeEntity | None:
        return self.resumes.get(resume_id)

    async def get_resume_by_visitor_and_content_hash(
        self,
        visitor_id: str,
        content_hash: str,
    ) -> ResumeEntity | None:
        matches = [
            resume
            for resume in self.resumes.values()
            if resume.visitor_id == visitor_id and resume.content_hash == content_hash
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]

    async def get_resume_by_visitor(
        self,
        resume_id: str,
        visitor_id: str,
    ) -> ResumeEntity | None:
        resume = self.resumes.get(resume_id)
        if resume is None or resume.visitor_id != visitor_id:
            return None
        return resume

    async def list_resumes_by_visitor(
        self,
        visitor_id: str,
        limit: int = 20,
    ) -> list[ResumeEntity]:
        resumes = [resume for resume in self.resumes.values() if resume.visitor_id == visitor_id]
        return sorted(resumes, key=lambda item: item.updated_at, reverse=True)[:limit]

    async def get_latest_available_resume_by_visitor(
        self,
        visitor_id: str,
    ) -> ResumeEntity | None:
        resumes = [
            resume
            for resume in self.resumes.values()
            if resume.visitor_id == visitor_id and resume.status == ResumeStatus.COMPLETED.value
        ]
        if not resumes:
            return None
        return sorted(resumes, key=lambda item: item.updated_at, reverse=True)[0]


class _FakeStorageClient:
    """测试用存储客户端。"""

    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    async def upload_file(self, object_key: str, content: bytes) -> str:
        self.uploaded[object_key] = content
        return f"memory://{object_key}"


class _InMemoryInterviewRepository:
    """测试用面试仓储。"""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}

    async def upsert_session(self, entity: Any) -> Any:
        self.sessions[entity.id] = entity
        return entity


@dataclass(slots=True)
class _ResumeServiceBundle:
    service: ResumeService
    repository: _InMemoryResumeRepository
    storage: _FakeStorageClient
    session: AsyncMock


class _InterviewResumeStub:
    """测试用简历注入桩。"""

    def __init__(self, bundle: ResumeContextBundle | None) -> None:
        self.bundle = bundle
        self.calls: list[dict[str, Any]] = []

    async def resolve_resume_for_interview(
        self,
        session: Any,
        *,
        visitor_id: str,
        resume_id: str | None,
    ) -> ResumeContextBundle | None:
        self.calls.append(
            {
                "visitor_id": visitor_id,
                "resume_id": resume_id,
            }
        )
        return self.bundle


def _build_resume_service() -> _ResumeServiceBundle:
    repository = _InMemoryResumeRepository()
    storage = _FakeStorageClient()
    session = AsyncMock()
    service = ResumeService(
        persistence_service=ResumePersistenceService(repository=repository),
        storage_client=storage,
    )
    return _ResumeServiceBundle(service=service, repository=repository, storage=storage, session=session)


def _upload_file(name: str, content: bytes, content_type: str = "text/markdown") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


def _build_resume_api_client(
    monkeypatch: pytest.MonkeyPatch,
    service: ResumeService,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(VisitorContextMiddleware)
    monkeypatch.setattr(resume_api, "resume_service", service)

    async def override_get_db_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(resume_api.router, prefix="/api")
    return TestClient(app, base_url="https://testserver")


@pytest.mark.asyncio
async def test_resume_service_uploads_pdf_and_standardizes_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF 简历应通过 pymupdf4llm 转成标准化 Markdown 并持久化。"""

    bundle = _build_resume_service()
    monkeypatch.setattr(pymupdf4llm, "to_markdown", lambda path: "# Title\r\n\r\nAlice  \nBackend")

    result = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume.pdf", b"%PDF-1.4 fake", "application/pdf"),
        visitor_id=TEST_VISITOR_A,
    )

    assert result.reused_existing is False
    assert result.resume.original_file_name == "resume.pdf"
    assert result.resume.markdown_content == "# Title\n\nAlice\nBackend"
    assert result.resume.source_metadata["parser"] == "pymupdf4llm"
    assert len(bundle.repository.resumes) == 1
    assert len(bundle.storage.uploaded) == 1
    bundle.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_service_reuses_same_visitor_and_keeps_visitor_isolation() -> None:
    """同一访客重复上传同一份简历应复用，不同访客不应互相复用。"""

    bundle = _build_resume_service()
    first = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume.md", b"# Resume\nRedis\n"),
        visitor_id=TEST_VISITOR_A,
    )
    second = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume-copy.md", b"# Resume\nRedis\n"),
        visitor_id=TEST_VISITOR_A,
    )
    third = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume-other.md", b"# Resume\nRedis\n"),
        visitor_id=TEST_VISITOR_B,
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert second.resume.resume_id == first.resume.resume_id
    assert third.reused_existing is False
    assert third.resume.resume_id != first.resume.resume_id
    assert len(bundle.repository.resumes) == 2
    assert len(bundle.storage.uploaded) == 2


@pytest.mark.asyncio
async def test_resume_service_only_resolves_explicit_resume_and_rejects_foreign_resume_id() -> None:
    """面试仅在显式选择简历时注入上下文，且必须校验简历归属。"""

    bundle = _build_resume_service()
    first = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume-v1.md", b"# Resume\nVersion 1"),
        visitor_id=TEST_VISITOR_A,
    )
    second = await bundle.service.upload_resume(
        bundle.session,
        file=_upload_file("resume-v2.md", b"# Resume\nVersion 2"),
        visitor_id=TEST_VISITOR_A,
    )

    latest = await bundle.service.resolve_resume_for_interview(
        bundle.session,
        visitor_id=TEST_VISITOR_A,
        resume_id=None,
    )

    assert latest is None

    explicit = await bundle.service.resolve_resume_for_interview(
        bundle.session,
        visitor_id=TEST_VISITOR_A,
        resume_id=second.resume.resume_id,
    )

    assert explicit is not None
    assert explicit.resume_id == second.resume.resume_id
    assert explicit.metadata["original_file_name"] == "resume-v2.md"

    with pytest.raises(BusinessException) as exc_info:
        await bundle.service.resolve_resume_for_interview(
            bundle.session,
            visitor_id=TEST_VISITOR_B,
            resume_id=first.resume.resume_id,
        )

    assert exc_info.value.code == ErrorCode.RESUME_NOT_FOUND


@pytest.mark.asyncio
async def test_interview_service_injects_resume_context_when_creating_session() -> None:
    """创建面试会话时应把简历上下文注入 planner/executor 使用的状态里。"""

    bundle = _build_resume_service()
    interview_repository = _InMemoryInterviewRepository()
    resume_bundle = ResumeContextBundle(
        resume_id="resume-123",
        markdown_content="# Resume\nAlice\nPython Backend",
        metadata={
            "original_file_name": "resume.md",
            "file_extension": ".md",
            "file_size": 42,
            "normalized_content_hash": "hash-123",
        },
    )
    resume_stub = _InterviewResumeStub(resume_bundle)

    interview_service = InterviewService(
        skill_service=SkillService(),
        persistence_service=InterviewPersistenceService(repository=interview_repository),
        resume_service=resume_stub,  # type: ignore[arg-type]
        repository_factory=lambda _session: interview_repository,
        prompt_runner=InterviewPromptRunner(enable_llm=False),
    )

    result = await interview_service.create_session(
        bundle.session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=1),
        TEST_VISITOR_A,
    )

    assert resume_stub.calls == [{"visitor_id": TEST_VISITOR_A, "resume_id": None}]
    assert result.resume_id == "resume-123"
    assert result.current_question is not None
    assert result.current_question.question_key == "q-1"
    assert len(interview_repository.sessions) == 1
    assert bundle.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_interview_service_does_not_attach_resume_when_none_selected() -> None:
    """未显式选择简历时，创建面试会话不应自动关联任意简历。"""

    bundle = _build_resume_service()
    interview_repository = _InMemoryInterviewRepository()
    resume_stub = _InterviewResumeStub(None)

    interview_service = InterviewService(
        skill_service=SkillService(),
        persistence_service=InterviewPersistenceService(repository=interview_repository),
        resume_service=resume_stub,  # type: ignore[arg-type]
        repository_factory=lambda _session: interview_repository,
        prompt_runner=InterviewPromptRunner(enable_llm=False),
    )

    result = await interview_service.create_session(
        bundle.session,
        CreateInterviewRequest(skill_id="python-backend", max_rounds=1),
        TEST_VISITOR_A,
    )

    assert resume_stub.calls == [{"visitor_id": TEST_VISITOR_A, "resume_id": None}]
    assert result.resume_id is None
    assert len(interview_repository.sessions) == 1


@pytest.mark.asyncio
async def test_resume_api_upload_list_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """简历 API 应覆盖上传、列表与详情查询闭环。"""

    bundle = _build_resume_service()
    client = _build_resume_api_client(monkeypatch, bundle.service)
    monkeypatch.setattr(pymupdf4llm, "to_markdown", lambda path: "# Resume\nAlice Backend")

    upload_response = client.post(
        "/api/resumes/upload",
        files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert upload_response.status_code == 200
    payload = upload_response.json()["data"]
    resume_id = payload["resume"]["resume_id"]
    assert payload["reused_existing"] is False
    assert client.cookies.get(VISITOR_ID_COOKIE_NAME) is not None

    list_response = client.get("/api/resumes")
    get_response = client.get(f"/api/resumes/{resume_id}")

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert list_response.json()["data"][0]["resume_id"] == resume_id
    assert get_response.json()["data"]["resume_id"] == resume_id
