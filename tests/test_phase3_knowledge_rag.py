"""Phase 3 知识库与 RAG 服务重构的最小验证。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config import config
from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
)
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_service import KnowledgeService
from app.services.rag_service import RagService
from app.services.vector_index_service import KnowledgeIndexingResult
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
        self.document_update_snapshots: list[dict[str, Any]] = []

    async def add_knowledge_base(self, entity: KnowledgeBaseEntity) -> KnowledgeBaseEntity:
        self.added_knowledge_bases.append(entity)
        return entity

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
    knowledge_base = KnowledgeBaseEntity(id="kb-1", name="Python 参考知识")
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
    )

    await repository.add_knowledge_base(knowledge_base)
    await repository.add_document(document)
    assert session.added[:2] == [knowledge_base, document]

    session.get_result = document
    assert await repository.get_document(document.id) is document

    session.scalar_result = _FakeScalarResult([document])
    assert await repository.list_documents_by_knowledge_base(knowledge_base.id) == [document]
    assert await repository.list_documents_by_status(document.index_status) == [document]


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
        content_type="text/markdown",
    )

    assert len(repository.added_knowledge_bases) == 1
    assert len(repository.added_documents) == 1
    assert storage_client.calls[0]["object_key"].startswith("knowledge/")
    assert session.commit.await_count == 3
    assert repository.document_update_snapshots[0]["index_status"] == (
        KnowledgeDocumentIndexStatus.INDEXING.value
    )
    assert repository.document_update_snapshots[-1]["index_status"] == (
        KnowledgeDocumentIndexStatus.INDEXED.value
    )
    assert repository.document_update_snapshots[-1]["metadata_json"]["chunk_count"] == 4
    assert result.index_status == KnowledgeDocumentIndexStatus.INDEXED.value
    assert result.chunk_count == 4


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
