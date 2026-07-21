"""管理员知识库管理编排服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage_client import StorageClient, storage_manager
from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeBaseStatus,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
)
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_service import KnowledgeService, KnowledgeUploadResult, knowledge_service
from app.services.vector_index_service import VectorIndexService, vector_index_service
from app.services.vector_store_manager import VectorStoreManager, vector_store_manager
from app.utils.exceptions import BusinessException, ErrorCode


@dataclass(slots=True)
class AdminReindexItemResult:
    """单个文档重建索引结果。"""

    document_id: str
    file_name: str
    success: bool
    chunk_count: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "file_name": self.file_name,
            "success": self.success,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class AdminReindexResult:
    """知识库重建索引结果。"""

    knowledge_base_id: str
    total_documents: int
    success_count: int
    failed_count: int
    items: list[AdminReindexItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_base_id": self.knowledge_base_id,
            "total_documents": self.total_documents,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(slots=True)
class AdminKnowledgeUploadFile:
    """批量上传时传入服务层的文件内容。"""

    file_name: str
    file_content: bytes
    content_type: str | None = None


@dataclass(slots=True)
class AdminKnowledgeBatchUploadItemResult:
    """单个文件批量上传结果。"""

    file_name: str
    success: bool
    document_id: str = ""
    index_status: str = ""
    file_size: int = 0
    chunk_count: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "success": self.success,
            "document_id": self.document_id,
            "index_status": self.index_status,
            "file_size": self.file_size,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class AdminKnowledgeBatchUploadResult:
    """知识库批量上传结果。"""

    knowledge_base_id: str
    total_files: int
    success_count: int
    failed_count: int
    items: list[AdminKnowledgeBatchUploadItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_base_id": self.knowledge_base_id,
            "total_files": self.total_files,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "items": [item.to_dict() for item in self.items],
        }


class AdminKnowledgeService:
    """管理员知识库后台能力编排。"""

    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService | None = None,
        index_service: VectorIndexService | None = None,
        vector_store: VectorStoreManager | None = None,
        storage_client: StorageClient | None = None,
    ) -> None:
        self._knowledge_service = knowledge_service or globals()["knowledge_service"]
        self._index_service = index_service or vector_index_service
        self._vector_store = vector_store or vector_store_manager
        self._storage_client = storage_client or storage_manager.get_client()

    async def list_knowledge_bases(
        self,
        session: AsyncSession,
        *,
        category: str | None = None,
        skill_id: str | None = None,
        enabled_only: bool = False,
        limit: int = 50,
    ) -> list[KnowledgeBaseEntity]:
        """查询管理员知识库列表。"""

        repository = KnowledgeRepository(session)
        return await repository.list_knowledge_bases(
            category=self._strip_optional(category),
            skill_id=self._strip_optional(skill_id),
            enabled_only=enabled_only,
            limit=limit,
        )

    async def create_knowledge_base(
        self,
        session: AsyncSession,
        *,
        name: str,
        category: str,
        description: str | None = None,
        skill_id: str | None = None,
    ) -> KnowledgeBaseEntity:
        """创建管理员逻辑知识库。"""

        return await self._knowledge_service.create_knowledge_base(
            session,
            name=name,
            category=category,
            source_type="admin",
            description=description,
            skill_id=self._strip_optional(skill_id),
            visitor_id=None,
        )

    async def get_knowledge_base_detail(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
    ) -> tuple[KnowledgeBaseEntity, list[KnowledgeDocumentEntity]]:
        """查询知识库详情和文档列表。"""

        repository = KnowledgeRepository(session)
        knowledge_base = await self._get_knowledge_base_or_raise(repository, knowledge_base_id)
        documents = await repository.list_documents_by_knowledge_base(
            knowledge_base.id,
            limit=500,
        )
        return knowledge_base, documents

    async def update_knowledge_base(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
        name: str | None = None,
        description: str | None = None,
        category: str | None = None,
        skill_id: str | None = None,
        is_enabled: bool | None = None,
        status: str | None = None,
    ) -> KnowledgeBaseEntity:
        """更新知识库主记录。"""

        repository = KnowledgeRepository(session)
        knowledge_base = await self._get_knowledge_base_or_raise(repository, knowledge_base_id)

        if name is not None:
            cleaned_name = name.strip()
            if not cleaned_name:
                raise BusinessException(code=ErrorCode.BAD_REQUEST, message="知识库名称不能为空")
            knowledge_base.name = cleaned_name
        if description is not None:
            knowledge_base.description = description
        if category is not None:
            cleaned_category = category.strip()
            if not cleaned_category:
                raise BusinessException(code=ErrorCode.BAD_REQUEST, message="知识库分类不能为空")
            knowledge_base.category = cleaned_category
        if skill_id is not None:
            knowledge_base.skill_id = self._strip_optional(skill_id)
        if is_enabled is not None:
            knowledge_base.is_enabled = is_enabled
        if status is not None:
            normalized_status = status.strip()
            allowed_statuses = {item.value for item in KnowledgeBaseStatus}
            if normalized_status not in allowed_statuses:
                raise BusinessException(
                    code=ErrorCode.BAD_REQUEST,
                    message="知识库状态不合法",
                    details={"status": status, "allowed": sorted(allowed_statuses)},
                )
            knowledge_base.status = normalized_status

        knowledge_base.metadata_json = {
            **dict(knowledge_base.metadata_json or {}),
            "category": knowledge_base.category,
            "source_type": knowledge_base.source_type,
            "skill_id": knowledge_base.skill_id,
        }
        await repository.upsert_knowledge_base(knowledge_base)
        await self._commit_or_raise(
            session,
            message="知识库更新失败",
            details={"knowledge_base_id": knowledge_base.id},
        )
        return knowledge_base

    async def add_document_to_knowledge_base(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
        file_name: str,
        file_content: bytes,
        content_type: str | None = None,
    ) -> KnowledgeUploadResult:
        """向管理员知识库追加文档。"""

        return await self._knowledge_service.add_document_to_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            file_name=file_name,
            file_content=file_content,
            content_type=content_type,
            source_type="admin",
        )

    async def add_documents_to_knowledge_base(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
        files: Sequence[AdminKnowledgeUploadFile],
    ) -> AdminKnowledgeBatchUploadResult:
        """向管理员知识库批量追加文档；单个文件失败不阻断其他文件。"""

        if not files:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="批量上传文件不能为空",
            )

        repository = KnowledgeRepository(session)
        knowledge_base = await self._get_knowledge_base_or_raise(repository, knowledge_base_id)

        items: list[AdminKnowledgeBatchUploadItemResult] = []
        for file_item in files:
            try:
                upload_result = await self.add_document_to_knowledge_base(
                    session,
                    knowledge_base_id=knowledge_base.id,
                    file_name=file_item.file_name,
                    file_content=file_item.file_content,
                    content_type=file_item.content_type,
                )
                items.append(
                    AdminKnowledgeBatchUploadItemResult(
                        file_name=upload_result.file_name,
                        success=True,
                        document_id=upload_result.document_id,
                        index_status=upload_result.index_status,
                        file_size=upload_result.file_size,
                        chunk_count=upload_result.chunk_count,
                    )
                )
            except BusinessException as exc:
                logger.warning(
                    "管理员知识库批量上传单文件失败: knowledge_base_id={}, file_name={}, code={}, error={}",
                    knowledge_base.id,
                    file_item.file_name,
                    exc.code,
                    exc.message,
                )
                items.append(
                    AdminKnowledgeBatchUploadItemResult(
                        file_name=file_item.file_name,
                        success=False,
                        error_message=exc.message,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "管理员知识库批量上传单文件异常: knowledge_base_id={}, file_name={}",
                    knowledge_base.id,
                    file_item.file_name,
                )
                items.append(
                    AdminKnowledgeBatchUploadItemResult(
                        file_name=file_item.file_name,
                        success=False,
                        error_message=str(exc),
                    )
                )

        success_count = sum(1 for item in items if item.success)
        return AdminKnowledgeBatchUploadResult(
            knowledge_base_id=knowledge_base.id,
            total_files=len(items),
            success_count=success_count,
            failed_count=len(items) - success_count,
            items=items,
        )

    async def update_document(
        self,
        session: AsyncSession,
        *,
        document_id: str,
        is_enabled: bool,
    ) -> KnowledgeDocumentEntity:
        """更新文档启用状态；禁用时同步删除向量。"""

        repository = KnowledgeRepository(session)
        document = await self._get_document_or_raise(repository, document_id)
        document.is_enabled = is_enabled
        document.metadata_json = {
            **dict(document.metadata_json or {}),
            "is_enabled": is_enabled,
        }
        if not is_enabled:
            await asyncio.to_thread(self._vector_store.delete_by_document_id, document.id)

        await repository.upsert_document(document)
        await self._commit_or_raise(
            session,
            message="知识库文档更新失败",
            details={"document_id": document.id},
        )
        return document

    async def delete_document(
        self,
        session: AsyncSession,
        *,
        document_id: str,
    ) -> KnowledgeDocumentEntity:
        """物理删除单个文档、原始文件与对应向量。"""

        repository = KnowledgeRepository(session)
        document = await self._get_document_or_raise(repository, document_id)
        knowledge_base = await repository.get_knowledge_base(document.knowledge_base_id)

        await self._delete_document_vectors(document)
        await self._delete_storage_object(document.storage_path)
        await repository.delete_document(document)

        if knowledge_base is not None:
            metadata = dict(knowledge_base.metadata_json or {})
            document_count = int(metadata.get("document_count", 0) or 0)
            knowledge_base.metadata_json = {
                **metadata,
                "document_count": max(0, document_count - 1),
            }
            await repository.upsert_knowledge_base(knowledge_base)

        await self._commit_or_raise(
            session,
            message="知识库文档删除失败",
            details={"document_id": document.id},
        )
        return document

    async def delete_knowledge_base(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeBaseEntity:
        """物理删除知识库、其下文档、原始文件与对应向量。"""

        repository = KnowledgeRepository(session)
        knowledge_base = await self._get_knowledge_base_or_raise(repository, knowledge_base_id)
        documents = await repository.list_documents_by_knowledge_base(
            knowledge_base.id,
            limit=500,
        )

        for document in documents:
            await self._delete_storage_object(document.storage_path)

        await asyncio.to_thread(self._vector_store.delete_by_knowledge_base_id, knowledge_base.id)
        await repository.delete_knowledge_base(knowledge_base)

        await self._commit_or_raise(
            session,
            message="知识库删除失败",
            details={"knowledge_base_id": knowledge_base.id},
        )
        return knowledge_base

    async def reindex_knowledge_base(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
    ) -> AdminReindexResult:
        """同步重建某个知识库下所有已启用文档的向量索引。"""

        repository = KnowledgeRepository(session)
        knowledge_base = await self._get_knowledge_base_or_raise(repository, knowledge_base_id)
        documents = await repository.list_enabled_documents_by_knowledge_base(
            knowledge_base.id,
            limit=500,
        )

        items: list[AdminReindexItemResult] = []
        for document in documents:
            document.index_status = KnowledgeDocumentIndexStatus.INDEXING.value
            document.error_message = None
            await repository.upsert_document(document)
            await self._commit_or_raise(
                session,
                message="知识库文档索引状态更新失败",
                details={"knowledge_base_id": knowledge_base.id, "document_id": document.id},
            )

            indexing_result = await asyncio.to_thread(
                self._index_service.index_knowledge_document,
                document,
            )
            if indexing_result.success:
                indexed_at = datetime.now(timezone.utc)
                document.index_status = KnowledgeDocumentIndexStatus.INDEXED.value
                document.indexed_at = indexed_at
                document.error_message = None
                document.metadata_json = {
                    **dict(document.metadata_json or {}),
                    "chunk_count": indexing_result.chunk_count,
                    "indexed_at": indexed_at.isoformat(),
                }
                items.append(
                    AdminReindexItemResult(
                        document_id=document.id,
                        file_name=document.original_file_name,
                        success=True,
                        chunk_count=indexing_result.chunk_count,
                    )
                )
            else:
                document.index_status = KnowledgeDocumentIndexStatus.FAILED.value
                document.error_message = indexing_result.error_message or "向量化入库失败"
                document.metadata_json = {
                    **dict(document.metadata_json or {}),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
                items.append(
                    AdminReindexItemResult(
                        document_id=document.id,
                        file_name=document.original_file_name,
                        success=False,
                        error_message=document.error_message,
                    )
                )

            await repository.upsert_document(document)
            await self._commit_or_raise(
                session,
                message="知识库文档重建索引结果回写失败",
                details={"knowledge_base_id": knowledge_base.id, "document_id": document.id},
            )

        success_count = sum(1 for item in items if item.success)
        failed_count = len(items) - success_count
        logger.info(
            "知识库重建索引完成: knowledge_base_id={}, total={}, success={}, failed={}",
            knowledge_base.id,
            len(items),
            success_count,
            failed_count,
        )
        return AdminReindexResult(
            knowledge_base_id=knowledge_base.id,
            total_documents=len(items),
            success_count=success_count,
            failed_count=failed_count,
            items=items,
        )

    def _resolve_storage_client(self) -> StorageClient:
        """获取当前使用的存储客户端。"""

        return self._storage_client

    async def _delete_document_vectors(self, document: KnowledgeDocumentEntity) -> None:
        """删除单篇文档对应的向量，失败时仅记录日志。"""

        try:
            await asyncio.to_thread(self._vector_store.delete_by_document_id, document.id)
        except Exception as exc:
            logger.warning(
                "知识库文档向量删除失败，继续执行数据库删除: document_id={}, error={}",
                document.id,
                exc,
            )

    async def _delete_storage_object(self, storage_path: str) -> None:
        """删除原始文件对象，失败时仅记录日志。"""

        if not storage_path.strip():
            return

        try:
            await self._resolve_storage_client().delete_file(storage_path)
        except Exception as exc:
            logger.warning(
                "知识库文件删除失败，继续执行数据库删除: storage_path={}, error={}",
                storage_path,
                exc,
            )

    async def _get_knowledge_base_or_raise(
        self,
        repository: KnowledgeRepository,
        knowledge_base_id: str,
    ) -> KnowledgeBaseEntity:
        knowledge_base = await repository.get_knowledge_base(knowledge_base_id.strip())
        if knowledge_base is None:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                http_status=404,
                details={"knowledge_base_id": knowledge_base_id},
            )
        return knowledge_base

    async def _get_document_or_raise(
        self,
        repository: KnowledgeRepository,
        document_id: str,
    ) -> KnowledgeDocumentEntity:
        document = await repository.get_document(document_id.strip())
        if document is None:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND,
                message="知识库文档不存在",
                http_status=404,
                details={"document_id": document_id},
            )
        return document

    async def _commit_or_raise(
        self,
        session: AsyncSession,
        *,
        message: str,
        details: dict[str, Any],
    ) -> None:
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("管理员知识库事务提交失败: message={}, details={}", message, details)
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_PERSIST_FAILED,
                message=message,
                http_status=500,
                details={**details, "error": str(exc)},
            ) from exc

    def _strip_optional(self, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


admin_knowledge_service = AdminKnowledgeService()


__all__ = [
    "AdminKnowledgeService",
    "AdminKnowledgeBatchUploadItemResult",
    "AdminKnowledgeBatchUploadResult",
    "AdminKnowledgeUploadFile",
    "AdminReindexItemResult",
    "AdminReindexResult",
    "admin_knowledge_service",
]
