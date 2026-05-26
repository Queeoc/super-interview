"""知识库上传与入库编排服务。"""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import config
from app.core.storage_client import StorageClient, storage_manager
from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeBaseStatus,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
)
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.vector_index_service import VectorIndexService, vector_index_service
from app.utils.exceptions import BusinessException, ErrorCode

ALLOWED_KNOWLEDGE_FILE_EXTENSIONS = {".txt", ".md"}
MAX_KNOWLEDGE_FILE_SIZE_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class KnowledgeUploadResult:
    """知识库上传与索引结果。"""

    knowledge_base_id: str
    document_id: str
    name: str
    category: str
    source_type: str
    skill_id: str | None
    file_name: str
    file_size: int
    index_status: str
    chunk_count: int

    def to_dict(self) -> dict[str, Any]:
        """转换为接口返回字典。"""

        return {
            "knowledge_base_id": self.knowledge_base_id,
            "document_id": self.document_id,
            "name": self.name,
            "category": self.category,
            "source_type": self.source_type,
            "skill_id": self.skill_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "index_status": self.index_status,
            "chunk_count": self.chunk_count,
        }


class KnowledgeService:
    """知识库结构化上传、存储与向量化编排服务。"""

    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        storage_client: StorageClient | None = None,
        index_service: VectorIndexService | None = None,
    ) -> None:
        self._repository = repository
        self._storage_client = storage_client
        self._index_service = index_service or vector_index_service

    async def upload_knowledge_document(
        self,
        session: AsyncSession,
        *,
        name: str,
        category: str,
        source_type: str,
        file_name: str,
        file_content: bytes,
        description: str | None = None,
        skill_id: str | None = None,
        owner_id: str | None = None,
        content_type: str | None = None,
    ) -> KnowledgeUploadResult:
        """
        上传单个知识库文件并完成结构化入库与向量化。

        Args:
            session: 当前数据库会话
            name: 知识库名称
            category: 知识库分类
            source_type: 来源类型
            file_name: 原始文件名
            file_content: 文件字节内容
            description: 知识库描述
            skill_id: 可选 skill 标识
            owner_id: 可选所有者标识
            content_type: 上传文件 MIME

        Returns:
            KnowledgeUploadResult: 上传与索引结果
        """

        sanitized_name = name.strip()
        sanitized_category = category.strip()
        sanitized_source_type = source_type.strip()

        if not sanitized_name:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="知识库名称不能为空",
            )
        if not sanitized_category:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="知识库分类不能为空",
            )
        if not sanitized_source_type:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="来源类型不能为空",
            )
        if not file_name.strip():
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="文件名不能为空",
            )
        if not file_content:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="上传文件不能为空",
            )

        safe_file_name = self._sanitize_file_name(file_name)
        file_extension = self._get_file_extension(safe_file_name)
        if file_extension not in ALLOWED_KNOWLEDGE_FILE_EXTENSIONS:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_FILE_TYPE_NOT_SUPPORTED,
                message="仅支持上传 .txt 和 .md 文件",
                details={"file_name": safe_file_name, "file_extension": file_extension},
            )
        if len(file_content) > MAX_KNOWLEDGE_FILE_SIZE_BYTES:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_FILE_TOO_LARGE,
                message="知识库文件大小超过限制",
                details={
                    "file_name": safe_file_name,
                    "file_size": len(file_content),
                    "max_size": MAX_KNOWLEDGE_FILE_SIZE_BYTES,
                },
            )

        knowledge_base_id = self._generate_identifier()
        document_id = self._generate_identifier()
        storage_object_key = self._build_storage_object_key(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            file_name=safe_file_name,
        )

        knowledge_base = KnowledgeBaseEntity(
            id=knowledge_base_id,
            owner_id=owner_id,
            name=sanitized_name,
            description=description,
            category=sanitized_category,
            skill_id=skill_id,
            source_type=sanitized_source_type,
            status=KnowledgeBaseStatus.ACTIVE.value,
            vector_collection_name=config.milvus.collection_name,
            metadata_json={
                "document_count": 1,
                "category": sanitized_category,
                "source_type": sanitized_source_type,
                "skill_id": skill_id,
            },
        )
        document = KnowledgeDocumentEntity(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            original_file_name=safe_file_name,
            storage_path=storage_object_key,
            file_extension=file_extension,
            mime_type=self._resolve_mime_type(content_type=content_type, file_extension=file_extension),
            file_size=len(file_content),
            source_type=sanitized_source_type,
            category=sanitized_category,
            skill_id=skill_id,
            index_status=KnowledgeDocumentIndexStatus.UPLOADED.value,
            metadata_json={
                "storage_object_key": storage_object_key,
                "file_name": safe_file_name,
            },
        )

        repository = self._resolve_repository(session)
        await self._create_records_and_commit(
            session=session,
            repository=repository,
            knowledge_base=knowledge_base,
            document=document,
        )

        storage_client = self._resolve_storage_client()
        try:
            stored_path = await storage_client.upload_file(storage_object_key, file_content)
        except Exception as exc:
            await self._mark_document_failed(
                session=session,
                repository=repository,
                document=document,
                error_message=str(exc),
            )
            raise BusinessException(
                code=ErrorCode.STORAGE_UPLOAD_FAILED,
                message="知识库文件上传失败",
                http_status=500,
                details={
                    "knowledge_base_id": knowledge_base_id,
                    "document_id": document_id,
                    "error": str(exc),
                },
            ) from exc

        document.storage_path = stored_path
        document.index_status = KnowledgeDocumentIndexStatus.INDEXING.value
        document.metadata_json = {
            **document.metadata_json,
            "storage_path": stored_path,
        }
        await self._upsert_document_and_commit(
            session=session,
            repository=repository,
            document=document,
            message="知识库文件状态更新失败",
        )

        indexing_result = await asyncio.to_thread(
            self._index_service.index_knowledge_document,
            document,
        )
        if not indexing_result.success:
            await self._mark_document_failed(
                session=session,
                repository=repository,
                document=document,
                error_message=indexing_result.error_message or "向量化入库失败",
            )
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_INDEX_FAILED,
                message="知识库文件向量化失败",
                http_status=500,
                details={
                    "knowledge_base_id": knowledge_base_id,
                    "document_id": document_id,
                    "error": indexing_result.error_message,
                },
            )

        indexed_at = datetime.now(timezone.utc)
        document.index_status = KnowledgeDocumentIndexStatus.INDEXED.value
        document.indexed_at = indexed_at
        document.error_message = None
        document.metadata_json = {
            **document.metadata_json,
            "chunk_count": indexing_result.chunk_count,
            "indexed_at": indexed_at.isoformat(),
        }
        await self._upsert_document_and_commit(
            session=session,
            repository=repository,
            document=document,
            message="知识库索引状态回写失败",
        )

        logger.info(
            "知识库上传完成: knowledge_base_id={}, document_id={}, chunk_count={}",
            knowledge_base_id,
            document_id,
            indexing_result.chunk_count,
        )
        return KnowledgeUploadResult(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            name=sanitized_name,
            category=sanitized_category,
            source_type=sanitized_source_type,
            skill_id=skill_id,
            file_name=safe_file_name,
            file_size=len(file_content),
            index_status=document.index_status,
            chunk_count=indexing_result.chunk_count,
        )

    def _resolve_repository(self, session: AsyncSession) -> KnowledgeRepository:
        """解析当前请求要使用的知识库仓储。"""

        return self._repository or KnowledgeRepository(session)

    def _resolve_storage_client(self) -> StorageClient:
        """解析当前要使用的存储客户端。"""

        return self._storage_client or storage_manager.get_client()

    async def _commit_or_raise(
        self,
        session: AsyncSession,
        *,
        code: ErrorCode,
        message: str,
        details: dict[str, Any],
    ) -> None:
        """统一提交事务并在失败时转为业务异常。"""

        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("知识库事务提交失败: message={}, details={}", message, details)
            raise BusinessException(
                code=code,
                message=message,
                http_status=500,
                details={**details, "error": str(exc)},
            ) from exc

    async def _mark_document_failed(
        self,
        *,
        session: AsyncSession,
        repository: KnowledgeRepository,
        document: KnowledgeDocumentEntity,
        error_message: str,
    ) -> None:
        """把知识库文件状态更新为失败，失败时仅记录日志。"""

        try:
            document.index_status = KnowledgeDocumentIndexStatus.FAILED.value
            document.error_message = error_message
            document.metadata_json = {
                **document.metadata_json,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            await repository.upsert_document(document)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "知识库失败状态回写失败: knowledge_base_id={}, document_id={}",
                document.knowledge_base_id,
                document.id,
            )

    async def _create_records_and_commit(
        self,
        *,
        session: AsyncSession,
        repository: KnowledgeRepository,
        knowledge_base: KnowledgeBaseEntity,
        document: KnowledgeDocumentEntity,
    ) -> None:
        """写入知识库主记录与文件记录并提交。"""

        try:
            await repository.add_knowledge_base(knowledge_base)
            await repository.add_document(document)
        except Exception as exc:
            await session.rollback()
            logger.exception(
                "知识库初始记录写入失败: knowledge_base_id={}, document_id={}",
                knowledge_base.id,
                document.id,
            )
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_PERSIST_FAILED,
                message="知识库记录创建失败",
                http_status=500,
                details={
                    "knowledge_base_id": knowledge_base.id,
                    "document_id": document.id,
                    "error": str(exc),
                },
            ) from exc

        await self._commit_or_raise(
            session,
            code=ErrorCode.KNOWLEDGE_PERSIST_FAILED,
            message="知识库记录创建失败",
            details={
                "knowledge_base_id": knowledge_base.id,
                "document_id": document.id,
            },
        )

    async def _upsert_document_and_commit(
        self,
        *,
        session: AsyncSession,
        repository: KnowledgeRepository,
        document: KnowledgeDocumentEntity,
        message: str,
    ) -> None:
        """更新文件记录并提交。"""

        try:
            await repository.upsert_document(document)
        except Exception as exc:
            await session.rollback()
            logger.exception(
                "知识库文件记录更新失败: knowledge_base_id={}, document_id={}",
                document.knowledge_base_id,
                document.id,
            )
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_PERSIST_FAILED,
                message=message,
                http_status=500,
                details={
                    "knowledge_base_id": document.knowledge_base_id,
                    "document_id": document.id,
                    "error": str(exc),
                },
            ) from exc

        await self._commit_or_raise(
            session,
            code=ErrorCode.KNOWLEDGE_PERSIST_FAILED,
            message=message,
            details={
                "knowledge_base_id": document.knowledge_base_id,
                "document_id": document.id,
            },
        )

    def _generate_identifier(self) -> str:
        """生成字符串形式的 UUID。"""

        return str(uuid4())

    def _sanitize_file_name(self, file_name: str) -> str:
        """清洗上传文件名，避免路径穿越和特殊字符问题。"""

        sanitized = file_name.strip().replace(" ", "_")
        for character in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            sanitized = sanitized.replace(character, "_")
        return sanitized

    def _get_file_extension(self, file_name: str) -> str:
        """获取上传文件的扩展名。"""

        return PurePosixPath(file_name).suffix.lower()

    def _resolve_mime_type(self, *, content_type: str | None, file_extension: str) -> str:
        """优先使用上传头信息，否则按扩展名推断 MIME。"""

        if content_type:
            return content_type
        guessed_content_type, _ = mimetypes.guess_type(f"file{file_extension}")
        return guessed_content_type or "application/octet-stream"

    def _build_storage_object_key(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        file_name: str,
    ) -> str:
        """构建本地/对象存储统一使用的对象键。"""

        return str(PurePosixPath("knowledge") / knowledge_base_id / document_id / file_name)


knowledge_service = KnowledgeService()


__all__ = [
    "KnowledgeService",
    "KnowledgeUploadResult",
    "knowledge_service",
]
