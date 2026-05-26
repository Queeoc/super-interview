"""知识库与 RAG 会话数据访问仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeBaseEntity,
    KnowledgeBaseStatus,
    KnowledgeDocumentEntity,
    KnowledgeDocumentIndexStatus,
    RagChatMessageEntity,
    RagChatSessionEntity,
    RagChatSessionStatus,
    RagMessageRole,
)


def _status_value(status: str | Enum) -> str:
    """把枚举或字符串统一转成字符串值。"""

    return status.value if isinstance(status, Enum) else status


class KnowledgeRepository:
    """知识库、RAG 会话与消息的数据访问封装。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_knowledge_base(self, entity: KnowledgeBaseEntity) -> KnowledgeBaseEntity:
        """新增知识库。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_knowledge_base(self, entity: KnowledgeBaseEntity) -> KnowledgeBaseEntity:
        """创建或更新知识库。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseEntity | None:
        """按知识库 ID 查询知识库。"""

        return await self._session.get(KnowledgeBaseEntity, knowledge_base_id)

    async def list_knowledge_bases_by_status(
        self,
        status: KnowledgeBaseStatus | str,
        limit: int = 20,
    ) -> list[KnowledgeBaseEntity]:
        """按状态查询知识库列表。"""

        stmt = (
            select(KnowledgeBaseEntity)
            .where(KnowledgeBaseEntity.status == _status_value(status))
            .order_by(desc(KnowledgeBaseEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_knowledge_bases_by_category(
        self,
        category: str,
        limit: int = 20,
    ) -> list[KnowledgeBaseEntity]:
        """按分类查询知识库列表。"""

        stmt = (
            select(KnowledgeBaseEntity)
            .where(KnowledgeBaseEntity.category == category)
            .order_by(desc(KnowledgeBaseEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def add_document(self, entity: KnowledgeDocumentEntity) -> KnowledgeDocumentEntity:
        """新增知识库文件记录。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_document(self, entity: KnowledgeDocumentEntity) -> KnowledgeDocumentEntity:
        """创建或更新知识库文件记录。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_document(self, document_id: str) -> KnowledgeDocumentEntity | None:
        """按文件记录 ID 查询知识库文件。"""

        return await self._session.get(KnowledgeDocumentEntity, document_id)

    async def list_documents_by_knowledge_base(
        self,
        knowledge_base_id: str,
        index_status: KnowledgeDocumentIndexStatus | str | None = None,
        limit: int = 50,
    ) -> list[KnowledgeDocumentEntity]:
        """按知识库 ID 查询文件记录列表。"""

        stmt = select(KnowledgeDocumentEntity).where(
            KnowledgeDocumentEntity.knowledge_base_id == knowledge_base_id,
        )
        if index_status is not None:
            stmt = stmt.where(KnowledgeDocumentEntity.index_status == _status_value(index_status))
        stmt = stmt.order_by(desc(KnowledgeDocumentEntity.updated_at)).limit(limit)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_documents_by_status(
        self,
        index_status: KnowledgeDocumentIndexStatus | str,
        limit: int = 50,
    ) -> list[KnowledgeDocumentEntity]:
        """按索引状态查询文件记录。"""

        stmt = (
            select(KnowledgeDocumentEntity)
            .where(KnowledgeDocumentEntity.index_status == _status_value(index_status))
            .order_by(desc(KnowledgeDocumentEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def add_chat_session(self, entity: RagChatSessionEntity) -> RagChatSessionEntity:
        """新增 RAG 会话。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_chat_session(self, entity: RagChatSessionEntity) -> RagChatSessionEntity:
        """创建或更新 RAG 会话。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_chat_session(self, session_id: str) -> RagChatSessionEntity | None:
        """按会话 ID 查询 RAG 会话。"""

        return await self._session.get(RagChatSessionEntity, session_id)

    async def list_chat_sessions_by_knowledge_base(
        self,
        knowledge_base_id: str,
        status: RagChatSessionStatus | str | None = None,
        limit: int = 20,
    ) -> list[RagChatSessionEntity]:
        """按知识库查询 RAG 会话列表。"""

        stmt = select(RagChatSessionEntity).where(
            RagChatSessionEntity.knowledge_base_id == knowledge_base_id,
        )
        if status is not None:
            stmt = stmt.where(RagChatSessionEntity.status == _status_value(status))
        stmt = stmt.order_by(desc(RagChatSessionEntity.updated_at)).limit(limit)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def add_message(self, entity: RagChatMessageEntity) -> RagChatMessageEntity:
        """新增 RAG 消息。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def add_messages(
        self,
        entities: Sequence[RagChatMessageEntity],
    ) -> list[RagChatMessageEntity]:
        """批量新增 RAG 消息。"""

        for entity in entities:
            self._session.add(entity)
        await self._session.flush()
        return list(entities)

    async def list_messages_by_session(
        self,
        session_id: str,
        role: RagMessageRole | str | None = None,
        limit: int = 50,
    ) -> list[RagChatMessageEntity]:
        """按会话查询消息列表。"""

        stmt = select(RagChatMessageEntity).where(RagChatMessageEntity.session_id == session_id)
        if role is not None:
            stmt = stmt.where(RagChatMessageEntity.role == _status_value(role))
        stmt = stmt.order_by(RagChatMessageEntity.created_at.asc()).limit(limit)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def delete_chat_session(self, session_id: str) -> None:
        """删除 RAG 会话。"""

        entity = await self.get_chat_session(session_id)
        if entity is None:
            return
        await self._session.delete(entity)
        await self._session.flush()


__all__ = ["KnowledgeRepository"]
