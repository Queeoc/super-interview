"""Phase 2 管理员知识库接口与服务的最小验证。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import admin_knowledge
from app.core.database import get_db_session
from app.middleware.error_handler import register_exception_handlers
from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
)
from app.services.admin_knowledge_service import AdminKnowledgeService, AdminKnowledgeUploadFile
from app.services.knowledge_service import KnowledgeUploadResult
from app.services.vector_index_service import KnowledgeIndexingResult
from app.utils.exceptions import BusinessException, ErrorCode


class _FakeScalarResult:
    """简化的 SQLAlchemy scalars 结果。"""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


@dataclass
class _FakeAsyncSession:
    """管理员服务测试用的最小异步 Session。"""

    knowledge_bases: dict[str, KnowledgeBaseEntity] = field(default_factory=dict)
    documents: dict[str, KnowledgeDocumentEntity] = field(default_factory=dict)
    scalar_result: _FakeScalarResult = field(default_factory=lambda: _FakeScalarResult([]))
    merged: list[Any] = field(default_factory=list)
    deleted: list[Any] = field(default_factory=list)
    commit_count: int = 0
    rollback_count: int = 0

    def add(self, instance: Any) -> None:
        if isinstance(instance, KnowledgeBaseEntity):
            self.knowledge_bases[instance.id] = instance
        if isinstance(instance, KnowledgeDocumentEntity):
            self.documents[instance.id] = instance

    async def flush(self) -> None:
        return None

    async def merge(self, instance: Any) -> Any:
        self.merged.append(instance)
        if isinstance(instance, KnowledgeBaseEntity):
            self.knowledge_bases[instance.id] = instance
        if isinstance(instance, KnowledgeDocumentEntity):
            self.documents[instance.id] = instance
        return instance

    async def delete(self, instance: Any) -> None:
        self.deleted.append(instance)
        if isinstance(instance, KnowledgeBaseEntity):
            self.knowledge_bases.pop(instance.id, None)
            self.documents = {
                document_id: document
                for document_id, document in self.documents.items()
                if document.knowledge_base_id != instance.id
            }
        if isinstance(instance, KnowledgeDocumentEntity):
            self.documents.pop(instance.id, None)

    async def get(self, model: type[Any], ident: str) -> Any:
        if model is KnowledgeBaseEntity:
            return self.knowledge_bases.get(ident)
        if model is KnowledgeDocumentEntity:
            return self.documents.get(ident)
        return None

    async def scalars(self, statement: Any) -> _FakeScalarResult:
        return self.scalar_result

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class _FakeVectorStore:
    """记录向量删除调用。"""

    def __init__(self) -> None:
        self.deleted_document_ids: list[str] = []
        self.deleted_knowledge_base_ids: list[str] = []

    def delete_by_document_id(self, document_id: str) -> int:
        self.deleted_document_ids.append(document_id)
        return 1

    def delete_by_knowledge_base_id(self, knowledge_base_id: str) -> int:
        self.deleted_knowledge_base_ids.append(knowledge_base_id)
        return 1


class _SuccessfulIndexService:
    """返回成功索引结果。"""

    def __init__(self, chunk_count: int = 2) -> None:
        self.calls: list[str] = []
        self.chunk_count = chunk_count

    def index_knowledge_document(self, document: KnowledgeDocumentEntity) -> KnowledgeIndexingResult:
        self.calls.append(document.id)
        return KnowledgeIndexingResult(
            success=True,
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            storage_path=document.storage_path,
            chunk_count=self.chunk_count,
        )


class _BatchUploadKnowledgeService:
    """批量上传测试用的知识库服务替身。"""

    async def add_document_to_knowledge_base(
        self,
        session: Any,
        *,
        knowledge_base_id: str,
        file_name: str,
        file_content: bytes,
        content_type: str | None = None,
        source_type: str | None = None,
    ) -> KnowledgeUploadResult:
        if file_name.endswith(".pdf"):
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_FILE_TYPE_NOT_SUPPORTED,
                message="仅支持上传 .txt 和 .md 文件",
            )
        return KnowledgeUploadResult(
            knowledge_base_id=knowledge_base_id,
            document_id=f"doc-{file_name}",
            name="Python",
            category="reference_knowledge",
            source_type=source_type or "admin",
            skill_id="python-backend",
            file_name=file_name,
            file_size=len(file_content),
            index_status="indexed",
            chunk_count=1,
        )


def _build_admin_api_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "secret",
    service: Any | None = None,
    rag_service: Any | None = None,
) -> TestClient:
    """构建只挂载管理员知识库路由的测试客户端。"""

    monkeypatch.setattr(admin_knowledge.config.admin, "enabled", True)
    monkeypatch.setattr(admin_knowledge.config.admin, "token", token)
    if service is not None:
        monkeypatch.setattr(admin_knowledge, "admin_knowledge_service", service)
    if rag_service is not None:
        monkeypatch.setattr(admin_knowledge, "rag_service", rag_service)

    app = FastAPI()
    register_exception_handlers(app)

    async def override_get_db_session():
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(admin_knowledge.router, prefix="/api/admin/knowledge")
    return TestClient(app, base_url="https://testserver")


def _build_knowledge_base() -> KnowledgeBaseEntity:
    now = datetime.now(timezone.utc)
    return KnowledgeBaseEntity(
        id="kb-1",
        name="Python 核心知识库",
        category="reference_knowledge",
        source_type="admin",
        skill_id="python-backend",
        is_enabled=True,
        metadata_json={"document_count": 1, "doc_type": "domain_corpus"},
        created_at=now,
        updated_at=now,
    )


def _build_document() -> KnowledgeDocumentEntity:
    now = datetime.now(timezone.utc)
    return KnowledgeDocumentEntity(
        id="doc-1",
        knowledge_base_id="kb-1",
        original_file_name="python.md",
        storage_path="/tmp/python.md",
        file_extension=".md",
        mime_type="text/markdown",
        file_size=128,
        source_type="admin",
        category="reference_knowledge",
        skill_id="python-backend",
        index_status=KnowledgeDocumentIndexStatus.INDEXED.value,
        is_enabled=True,
        metadata_json={"chunk_count": 2},
        uploaded_at=now,
        created_at=now,
        updated_at=now,
    )


def test_admin_knowledge_api_rejects_missing_and_wrong_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员接口必须校验 X-Admin-Token。"""

    client = _build_admin_api_client(monkeypatch)

    missing_response = client.get("/api/admin/knowledge/bases")
    wrong_response = client.get(
        "/api/admin/knowledge/bases",
        headers={"X-Admin-Token": "wrong"},
    )

    assert missing_response.status_code == 403
    assert wrong_response.status_code == 403
    assert missing_response.json()["message"] == "管理员 token 无效"


def test_admin_knowledge_api_rejects_unconfigured_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置管理员 token 时接口应稳定拒绝访问。"""

    client = _build_admin_api_client(monkeypatch, token="")
    response = client.get(
        "/api/admin/knowledge/bases",
        headers={"X-Admin-Token": "secret"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "管理员 token 未配置"


def test_admin_knowledge_api_lists_and_creates_knowledge_bases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员可查询和创建逻辑知识库。"""

    knowledge_base = _build_knowledge_base()
    service = SimpleNamespace(
        list_knowledge_bases=AsyncMock(return_value=[knowledge_base]),
        create_knowledge_base=AsyncMock(return_value=knowledge_base),
    )
    client = _build_admin_api_client(monkeypatch, service=service)

    list_response = client.get(
        "/api/admin/knowledge/bases?enabled_only=true",
        headers={"X-Admin-Token": "secret"},
    )
    create_response = client.post(
        "/api/admin/knowledge/bases",
        headers={"X-Admin-Token": "secret"},
        json={
            "name": "Python 核心知识库",
            "category": "reference_knowledge",
            "skill_id": "python-backend",
        },
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["items"][0]["id"] == "kb-1"
    assert create_response.status_code == 200
    assert create_response.json()["data"]["source_type"] == "admin"
    assert service.create_knowledge_base.await_args.kwargs["skill_id"] == "python-backend"


def test_admin_knowledge_api_uploads_document_to_existing_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员上传接口应向已有知识库追加文档。"""

    upload_result = SimpleNamespace(
        to_dict=lambda: {
            "knowledge_base_id": "kb-1",
            "document_id": "doc-1",
            "name": "Python",
            "category": "reference_knowledge",
            "source_type": "admin",
            "skill_id": "python-backend",
            "file_name": "python.md",
            "file_size": 16,
            "index_status": "indexed",
            "chunk_count": 1,
        }
    )
    service = SimpleNamespace(add_document_to_knowledge_base=AsyncMock(return_value=upload_result))
    client = _build_admin_api_client(monkeypatch, service=service)

    response = client.post(
        "/api/admin/knowledge/bases/kb-1/documents",
        headers={"X-Admin-Token": "secret"},
        files={"file": ("python.md", b"# Python", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["document_id"] == "doc-1"
    assert service.add_document_to_knowledge_base.await_args.kwargs["knowledge_base_id"] == "kb-1"


def test_admin_knowledge_api_batch_uploads_documents_to_existing_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员批量上传接口应向已有知识库追加多个文档。"""

    upload_result = SimpleNamespace(
        to_dict=lambda: {
            "knowledge_base_id": "kb-1",
            "total_files": 2,
            "success_count": 2,
            "failed_count": 0,
            "items": [
                {
                    "file_name": "python.md",
                    "success": True,
                    "document_id": "doc-1",
                    "index_status": "indexed",
                    "file_size": 8,
                    "chunk_count": 1,
                    "error_message": "",
                },
                {
                    "file_name": "java.md",
                    "success": True,
                    "document_id": "doc-2",
                    "index_status": "indexed",
                    "file_size": 6,
                    "chunk_count": 1,
                    "error_message": "",
                },
            ],
        }
    )
    service = SimpleNamespace(add_documents_to_knowledge_base=AsyncMock(return_value=upload_result))
    client = _build_admin_api_client(monkeypatch, service=service)

    response = client.post(
        "/api/admin/knowledge/bases/kb-1/documents/batch",
        headers={"X-Admin-Token": "secret"},
        files=[
            ("files", ("python.md", b"# Python", "text/markdown")),
            ("files", ("java.md", b"# Java", "text/markdown")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["data"]["total_files"] == 2
    uploaded_files = service.add_documents_to_knowledge_base.await_args.kwargs["files"]
    assert [item.file_name for item in uploaded_files] == ["python.md", "java.md"]
    assert service.add_documents_to_knowledge_base.await_args.kwargs["knowledge_base_id"] == "kb-1"


def test_admin_knowledge_api_hard_deletes_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员删除文档接口应走硬删除。"""

    document = _build_document()
    service = SimpleNamespace(delete_document=AsyncMock(return_value=document))
    client = _build_admin_api_client(monkeypatch, service=service)

    response = client.delete(
        "/api/admin/knowledge/documents/doc-1",
        headers={"X-Admin-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "doc-1"
    assert service.delete_document.await_args.kwargs["document_id"] == "doc-1"


def test_admin_knowledge_api_hard_deletes_knowledge_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员删除知识库接口应走硬删除。"""

    knowledge_base = _build_knowledge_base()
    service = SimpleNamespace(delete_knowledge_base=AsyncMock(return_value=knowledge_base))
    client = _build_admin_api_client(monkeypatch, service=service)

    response = client.delete(
        "/api/admin/knowledge/bases/kb-1",
        headers={"X-Admin-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "kb-1"
    assert service.delete_knowledge_base.await_args.kwargs["knowledge_base_id"] == "kb-1"


@pytest.mark.asyncio
async def test_admin_knowledge_service_batch_uploads_with_partial_failure() -> None:
    """批量上传应允许单个文件失败并继续处理后续文件。"""

    service = AdminKnowledgeService(knowledge_service=_BatchUploadKnowledgeService())
    knowledge_base = _build_knowledge_base()

    result = await service.add_documents_to_knowledge_base(
        _FakeAsyncSession(knowledge_bases={knowledge_base.id: knowledge_base}),
        knowledge_base_id="kb-1",
        files=[
            AdminKnowledgeUploadFile(
                file_name="python.md",
                file_content=b"# Python",
                content_type="text/markdown",
            ),
            AdminKnowledgeUploadFile(
                file_name="python.pdf",
                file_content=b"%PDF",
                content_type="application/pdf",
            ),
        ],
    )

    assert result.total_files == 2
    assert result.success_count == 1
    assert result.failed_count == 1
    assert result.items[0].success is True
    assert result.items[1].success is False
    assert result.items[1].error_message == "仅支持上传 .txt 和 .md 文件"


@pytest.mark.asyncio
async def test_admin_knowledge_service_deletes_document_and_removes_vectors() -> None:
    """文档硬删除应清理数据库记录、向量与原始文件。"""

    document = _build_document()
    session = _FakeAsyncSession(documents={document.id: document})
    vector_store = _FakeVectorStore()
    service = AdminKnowledgeService(vector_store=vector_store)

    result = await service.delete_document(session, document_id=document.id)

    assert result.id == document.id
    assert vector_store.deleted_document_ids == [document.id]
    assert session.commit_count == 1
    assert document.id not in session.documents


@pytest.mark.asyncio
async def test_admin_knowledge_service_deletes_knowledge_base_and_related_documents() -> None:
    """知识库硬删除应清理知识库、其下文档和整库向量。"""

    knowledge_base = _build_knowledge_base()
    document = _build_document()
    session = _FakeAsyncSession(
        knowledge_bases={knowledge_base.id: knowledge_base},
        documents={document.id: document},
    )
    vector_store = _FakeVectorStore()
    service = AdminKnowledgeService(vector_store=vector_store)

    result = await service.delete_knowledge_base(session, knowledge_base_id=knowledge_base.id)

    assert result.id == knowledge_base.id
    assert vector_store.deleted_knowledge_base_ids == [knowledge_base.id]
    assert session.commit_count == 1
    assert knowledge_base.id not in session.knowledge_bases
    assert document.id not in session.documents


@pytest.mark.asyncio
async def test_admin_knowledge_service_reindexes_enabled_documents() -> None:
    """重建索引应只处理启用文档并回写索引结果。"""

    knowledge_base = _build_knowledge_base()
    document = _build_document()
    session = _FakeAsyncSession(
        knowledge_bases={knowledge_base.id: knowledge_base},
        documents={document.id: document},
        scalar_result=_FakeScalarResult([document]),
    )
    index_service = _SuccessfulIndexService(chunk_count=3)
    service = AdminKnowledgeService(index_service=index_service)

    result = await service.reindex_knowledge_base(session, knowledge_base_id=knowledge_base.id)

    assert result.total_documents == 1
    assert result.success_count == 1
    assert result.failed_count == 0
    assert index_service.calls == [document.id]
    assert document.index_status == KnowledgeDocumentIndexStatus.INDEXED.value
    assert document.metadata_json["chunk_count"] == 3
