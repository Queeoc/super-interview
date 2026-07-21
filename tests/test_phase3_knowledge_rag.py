"""Phase 3 知识库与 RAG 服务重构的最小验证。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
import pytest

from app.api import knowledge as knowledge_api
from app.config import config
from app.core.database import get_db_session
from app.middleware.error_handler import register_exception_handlers
from app.middleware.visitor_context import VISITOR_ID_COOKIE_NAME, VisitorContextMiddleware
from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
)
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_service import KnowledgeService
from app.services.rag_service import RagService
from app.services.vector_index_service import KnowledgeIndexingResult, VectorIndexService
from app.services.vector_store_manager import VectorSearchHit
from app.utils.exceptions import BusinessException, ErrorCode


class _FakeScalarResult:
    """简化的 SQLAlchemy 标量结果。"""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


@dataclass
class _FakeAsyncSession:
    """用于仓储测试的最小异步 Session。"""

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

    async def delete(self, instance: Any) -> None:
        self.deleted.append(instance)


class _RecordingKnowledgeRepository:
    """记录知识库写入过程的假仓储。"""

    def __init__(self) -> None:
        self.added_knowledge_bases: list[KnowledgeBaseEntity] = []
        self.added_documents: list[KnowledgeDocumentEntity] = []
        self.upserted_knowledge_bases: list[KnowledgeBaseEntity] = []
        self.document_update_snapshots: list[dict[str, Any]] = []
        self.knowledge_bases_by_id: dict[str, KnowledgeBaseEntity] = {}

    async def add_knowledge_base(self, entity: KnowledgeBaseEntity) -> KnowledgeBaseEntity:
        self.added_knowledge_bases.append(entity)
        self.knowledge_bases_by_id[entity.id] = entity
        return entity

    async def upsert_knowledge_base(self, entity: KnowledgeBaseEntity) -> KnowledgeBaseEntity:
        self.upserted_knowledge_bases.append(entity)
        self.knowledge_bases_by_id[entity.id] = entity
        return entity

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseEntity | None:
        return self.knowledge_bases_by_id.get(knowledge_base_id)

    async def add_document(self, entity: KnowledgeDocumentEntity) -> KnowledgeDocumentEntity:
        self.added_documents.append(entity)
        return entity

    async def upsert_document(self, entity: KnowledgeDocumentEntity) -> KnowledgeDocumentEntity:
        self.document_update_snapshots.append(
            {
                "document_id": entity.id,
                "knowledge_base_id": entity.knowledge_base_id,
                "index_status": entity.index_status,
                "storage_path": entity.storage_path,
                "error_message": entity.error_message,
                "metadata_json": dict(entity.metadata_json),
            }
        )
        return entity


class _FakeStorageClient:
    """只模拟 upload_file 的假存储客户端。"""

    def __init__(self, stored_path: str = "/tmp/knowledge/python.md") -> None:
        self.stored_path = stored_path
        self.calls: list[dict[str, Any]] = []

    async def upload_file(self, object_key: str, content: bytes) -> str:
        self.calls.append({"object_key": object_key, "content": content})
        return self.stored_path


class _SuccessfulIndexService:
    """返回成功索引结果的假索引服务。"""

    def __init__(self, chunk_count: int = 3) -> None:
        self.chunk_count = chunk_count
        self.calls: list[KnowledgeDocumentEntity] = []

    def index_knowledge_document(
        self,
        document: KnowledgeDocumentEntity,
    ) -> KnowledgeIndexingResult:
        self.calls.append(document)
        return KnowledgeIndexingResult(
            success=True,
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            storage_path=document.storage_path,
            chunk_count=self.chunk_count,
        )


class _FailingIndexService:
    """返回失败索引结果的假索引服务。"""

    def __init__(self, error_message: str = "milvus failed") -> None:
        self.error_message = error_message
        self.calls: list[KnowledgeDocumentEntity] = []

    def index_knowledge_document(
        self,
        document: KnowledgeDocumentEntity,
    ) -> KnowledgeIndexingResult:
        self.calls.append(document)
        return KnowledgeIndexingResult(
            success=False,
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            storage_path=document.storage_path,
            error_message=self.error_message,
        )


class _FakeVectorStore:
    """记录检索入参的假向量存储管理器。"""

    def __init__(self, hits: list[VectorSearchHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def search_documents(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None,
        knowledge_base_ids: list[str] | None,
        category: str | None,
    ) -> list[VectorSearchHit]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "score_threshold": score_threshold,
                "knowledge_base_ids": knowledge_base_ids,
                "category": category,
            }
        )
        return list(self.hits)


class _ApiKnowledgeUploadResult:
    """用于知识库上传 API 测试的最小返回对象。"""

    def __init__(self) -> None:
        self.payload = {
            "knowledge_base_id": "kb-1",
            "document_id": "doc-1",
            "name": "Python 面试参考",
            "category": "reference_knowledge",
            "source_type": "manual",
            "skill_id": "python-backend",
            "file_name": "python.md",
            "file_size": 32,
            "index_status": "indexed",
            "chunk_count": 2,
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class _SuccessfulRewriteModel:
    """返回稳定文本的假改写模型。"""

    async def ainvoke(self, prompt_text: str) -> SimpleNamespace:
        return SimpleNamespace(content="改写后的检索问题")


class _FailingRewriteModel:
    """抛出异常的假改写模型。"""

    async def ainvoke(self, prompt_text: str) -> SimpleNamespace:
        raise RuntimeError("rewrite failed")


@pytest.mark.asyncio
async def test_knowledge_repository_handles_knowledge_documents() -> None:
    """知识库仓储应支持知识库文件记录的基础写入和查询。"""

    session = _FakeAsyncSession()
    repository = KnowledgeRepository(session)
    knowledge_base = KnowledgeBaseEntity(
        id="kb-1",
        name="Python 参考知识",
        category="reference_knowledge",
        source_type="manual",
        skill_id="python-backend",
        is_enabled=True,
    )
    document = KnowledgeDocumentEntity(
        id="doc-1",
        knowledge_base_id=knowledge_base.id,
        original_file_name="python.md",
        storage_path="/tmp/python.md",
        file_extension=".md",
        mime_type="text/markdown",
        file_size=256,
        source_type="manual",
        category="reference_knowledge",
        skill_id="python-backend",
        is_enabled=True,
    )

    await repository.add_knowledge_base(knowledge_base)
    await repository.add_document(document)
    assert session.added[:2] == [knowledge_base, document]

    session.get_result = document
    assert await repository.get_document(document.id) is document

    session.scalar_result = _FakeScalarResult([document])
    assert await repository.list_documents_by_knowledge_base(knowledge_base.id) == [document]
    assert await repository.list_documents_by_status(document.index_status) == [document]
    assert await repository.list_enabled_documents_by_knowledge_base(knowledge_base.id) == [document]

    session.scalar_result = _FakeScalarResult([knowledge_base])
    assert await repository.list_enabled_knowledge_bases() == [knowledge_base]
    assert (
        await repository.find_knowledge_base_by_name_and_scope(
            name=knowledge_base.name,
            category=knowledge_base.category,
            skill_id=knowledge_base.skill_id,
            source_type=knowledge_base.source_type,
            only_enabled=True,
        )
        is knowledge_base
    )


@pytest.mark.asyncio
async def test_knowledge_service_uploads_and_indexes_document() -> None:
    """知识库服务应按记录、上传、索引、回写状态的顺序编排。"""

    session = AsyncMock()
    repository = _RecordingKnowledgeRepository()
    storage_client = _FakeStorageClient()
    index_service = _SuccessfulIndexService(chunk_count=4)
    service = KnowledgeService(
        repository=repository,
        storage_client=storage_client,
        index_service=index_service,
    )

    result = await service.upload_knowledge_document(
        session=session,
        name="Python 面试参考",
        category="reference_knowledge",
        source_type="manual",
        file_name="python.md",
        file_content=b"# Python\nclosures and decorators",
        skill_id="python-backend",
        visitor_id="00000000-0000-4000-8000-000000000011",
        content_type="text/markdown",
    )

    assert len(repository.added_knowledge_bases) == 1
    assert len(repository.added_documents) == 1
    assert repository.added_knowledge_bases[0].visitor_id == "00000000-0000-4000-8000-000000000011"
    assert storage_client.calls[0]["object_key"].startswith("knowledge/")
    assert session.commit.await_count == 4
    assert repository.document_update_snapshots[0]["index_status"] == (
        KnowledgeDocumentIndexStatus.INDEXING.value
    )
    assert repository.document_update_snapshots[-1]["index_status"] == (
        KnowledgeDocumentIndexStatus.INDEXED.value
    )
    assert repository.document_update_snapshots[-1]["metadata_json"]["doc_type"] == "domain_corpus"
    assert repository.document_update_snapshots[-1]["metadata_json"]["chunk_count"] == 4
    assert repository.upserted_knowledge_bases[-1].metadata_json["document_count"] == 1
    assert result.index_status == KnowledgeDocumentIndexStatus.INDEXED.value
    assert result.chunk_count == 4


@pytest.mark.asyncio
async def test_knowledge_service_create_knowledge_base_only_persists_logical_record() -> None:
    """创建知识库只写主记录，不上传文件、不触发索引。"""

    session = AsyncMock()
    repository = _RecordingKnowledgeRepository()
    storage_client = _FakeStorageClient()
    index_service = _SuccessfulIndexService(chunk_count=2)
    service = KnowledgeService(
        repository=repository,
        storage_client=storage_client,
        index_service=index_service,
    )

    knowledge_base = await service.create_knowledge_base(
        session=session,
        name="Python 核心知识库",
        category="reference_knowledge",
        source_type="manual",
        description="用于 Python 后端技术问答",
        skill_id="python-backend",
        visitor_id="00000000-0000-4000-8000-000000000011",
    )

    assert knowledge_base.name == "Python 核心知识库"
    assert knowledge_base.metadata_json["doc_type"] == "domain_corpus"
    assert knowledge_base.metadata_json["document_count"] == 0
    assert knowledge_base.is_enabled is True
    assert len(repository.added_knowledge_bases) == 1
    assert storage_client.calls == []
    assert index_service.calls == []
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_knowledge_service_add_document_to_existing_knowledge_base() -> None:
    """可向已有知识库追加文档并回写文档计数。"""

    session = AsyncMock()
    repository = _RecordingKnowledgeRepository()
    storage_client = _FakeStorageClient()
    index_service = _SuccessfulIndexService(chunk_count=2)
    service = KnowledgeService(
        repository=repository,
        storage_client=storage_client,
        index_service=index_service,
    )

    knowledge_base = await service.create_knowledge_base(
        session=session,
        name="Java 项目知识库",
        category="project_knowledge",
        source_type="manual",
        skill_id="java-backend",
    )
    session.commit.reset_mock()

    result = await service.add_document_to_knowledge_base(
        session=session,
        knowledge_base_id=knowledge_base.id,
        file_name="redis-case.md",
        file_content=b"# Redis\ncache invalidation",
        content_type="text/markdown",
    )

    assert len(repository.added_documents) == 1
    assert result.knowledge_base_id == knowledge_base.id
    assert result.name == "Java 项目知识库"
    assert repository.upserted_knowledge_bases[-1].metadata_json["document_count"] == 1
    assert repository.document_update_snapshots[-1]["metadata_json"]["doc_type"] == "domain_corpus"
    assert repository.document_update_snapshots[-1]["metadata_json"]["chunk_count"] == 2
    assert session.commit.await_count == 3


@pytest.mark.asyncio
async def test_knowledge_service_marks_document_failed_when_indexing_fails() -> None:
    """向量化失败时，知识库文件状态应回写为 failed。"""

    session = AsyncMock()
    repository = _RecordingKnowledgeRepository()
    storage_client = _FakeStorageClient()
    service = KnowledgeService(
        repository=repository,
        storage_client=storage_client,
        index_service=_FailingIndexService(error_message="milvus write failed"),
    )

    with pytest.raises(BusinessException) as exc_info:
        await service.upload_knowledge_document(
            session=session,
            name="评分标准",
            category="rubrics",
            source_type="manual",
            file_name="rubric.md",
            file_content=b"# rubric\nscore by rubric",
            visitor_id="00000000-0000-4000-8000-000000000012",
        )

    assert exc_info.value.code == ErrorCode.KNOWLEDGE_INDEX_FAILED
    assert repository.document_update_snapshots[-1]["index_status"] == (
        KnowledgeDocumentIndexStatus.FAILED.value
    )
    assert repository.document_update_snapshots[-1]["error_message"] == "milvus write failed"


@pytest.mark.asyncio
async def test_rag_service_normalizes_knowledge_base_ids_and_returns_empty_fallback() -> None:
    """检索服务应合并单个和多个知识库 ID，并在空命中时返回稳定兜底。"""

    vector_store = _FakeVectorStore(hits=[])
    service = RagService(vector_store=vector_store)

    result = await service.search(
        query="  Python 闭包  ",
        knowledge_base_id="kb-1",
        knowledge_base_ids=["kb-2", "kb-1", "  "],
        category="rubrics",
        top_k=5,
        score_threshold=0.9,
        rewrite_enabled=False,
    )

    assert vector_store.calls[0]["query"] == "Python 闭包"
    assert vector_store.calls[0]["knowledge_base_ids"] == ["kb-1", "kb-2"]
    assert vector_store.calls[0]["category"] == "rubrics"
    assert result.used_rewrite is False
    assert result.rewritten_query == "Python 闭包"
    assert result.hits == []
    assert result.message == config.rag.empty_result_message


@pytest.mark.asyncio
async def test_rag_service_uses_rewritten_query_when_rewrite_succeeds() -> None:
    """当 query rewrite 成功时，应使用改写后的查询发起检索。"""

    vector_store = _FakeVectorStore(
        hits=[
            VectorSearchHit(
                id="doc-1:0",
                content="闭包是引用自由变量的函数。",
                score=0.42,
                metadata={
                    "knowledge_base_id": "kb-1",
                    "document_id": "doc-1",
                    "category": "reference_knowledge",
                    "source_type": "manual",
                    "skill_id": "python-backend",
                    "_file_name": "python.md",
                    "_source": "/tmp/python.md",
                },
            )
        ]
    )
    service = RagService(
        vector_store=vector_store,
        rewrite_model=_SuccessfulRewriteModel(),
    )

    result = await service.search(
        query="Python 闭包",
        rewrite_enabled=True,
    )

    assert vector_store.calls[0]["query"] == "改写后的检索问题"
    assert result.used_rewrite is True
    assert result.rewritten_query == "改写后的检索问题"
    assert result.hits[0].knowledge_base_id == "kb-1"
    assert result.hits[0].file_name == "python.md"


@pytest.mark.asyncio
async def test_rag_service_falls_back_to_original_query_when_rewrite_fails() -> None:
    """当 query rewrite 失败时，应降级回原始查询而不是中断检索。"""

    vector_store = _FakeVectorStore(hits=[])
    service = RagService(
        vector_store=vector_store,
        rewrite_model=_FailingRewriteModel(),
    )

    result = await service.search(
        query="Python 装饰器",
        rewrite_enabled=True,
    )

    assert vector_store.calls[0]["query"] == "Python 装饰器"
    assert result.used_rewrite is False
    assert result.rewritten_query == "Python 装饰器"


def _build_knowledge_api_client(
    monkeypatch: pytest.MonkeyPatch,
    upload_mock: AsyncMock,
    session: AsyncMock,
) -> TestClient:
    """构建挂载知识库路由的测试客户端。"""

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(VisitorContextMiddleware)

    monkeypatch.setattr(
        knowledge_api,
        "knowledge_service",
        SimpleNamespace(upload_knowledge_document=upload_mock),
    )

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(knowledge_api.router, prefix="/api")
    return TestClient(app, base_url="https://testserver")


def test_knowledge_upload_api_uses_request_visitor_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识库上传接口应从请求上下文读取 visitor_id，而不是暴露 owner_id。"""

    upload_mock = AsyncMock(return_value=_ApiKnowledgeUploadResult())
    client = _build_knowledge_api_client(monkeypatch, upload_mock, AsyncMock())

    response = client.post(
        "/api/knowledge/upload",
        data={
            "name": "Python 面试参考",
            "category": "reference_knowledge",
            "source_type": "manual",
            "skill_id": "python-backend",
        },
        files={"file": ("python.md", b"# Python\nclosures", "text/markdown")},
    )

    assert response.status_code == 200
    visitor_id = client.cookies.get(VISITOR_ID_COOKIE_NAME)
    assert visitor_id is not None
    assert upload_mock.await_args.kwargs["visitor_id"] == visitor_id
    assert "owner_id" not in upload_mock.await_args.kwargs


def test_vector_index_service_writes_minimal_chunk_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """知识库索引应把最小必要字段写入分片 metadata。"""

    document_path = tmp_path / "python.md"
    document_path.write_text("# Python\ninterpreter", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_split_document(content: str, file_path: str, metadata: dict[str, Any] | None = None):
        captured["metadata"] = dict(metadata or {})
        return [Document(page_content=content, metadata={"chunk_id": "doc-1:0"})]

    class _FakeVectorStoreManager:
        def __init__(self) -> None:
            self.deleted_document_ids: list[str] = []
            self.documents: list[Document] = []

        def delete_by_document_id(self, document_id: str) -> int:
            self.deleted_document_ids.append(document_id)
            return 0

        def add_documents(self, documents: list[Document]) -> list[str]:
            self.documents.extend(documents)
            return ["doc-1:0"]

    fake_vector_store_manager = _FakeVectorStoreManager()
    monkeypatch.setattr(
        "app.services.vector_index_service.document_splitter_service.split_document",
        fake_split_document,
    )
    monkeypatch.setattr(
        "app.services.vector_index_service.vector_store_manager",
        fake_vector_store_manager,
    )

    service = VectorIndexService()
    document = KnowledgeDocumentEntity(
        id="doc-1",
        knowledge_base_id="kb-1",
        original_file_name="python.md",
        storage_path=str(document_path),
        file_extension=".md",
        mime_type="text/markdown",
        file_size=32,
        source_type="manual",
        category="reference_knowledge",
        skill_id="python-backend",
        is_enabled=True,
    )

    result = service.index_knowledge_document(document)

    assert result.success is True
    assert captured["metadata"]["knowledge_base_id"] == "kb-1"
    assert captured["metadata"]["document_id"] == "doc-1"
    assert captured["metadata"]["skill_id"] == "python-backend"
    assert captured["metadata"]["category"] == "reference_knowledge"
    assert captured["metadata"]["is_enabled"] is True
