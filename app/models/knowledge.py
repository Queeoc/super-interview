"""知识库与 RAG 会话持久化实体。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _generate_uuid() -> str:
    """生成字符串形式的 UUID 主键。"""

    return str(uuid4())


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


class KnowledgeBaseStatus(str, Enum):
    """知识库状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class KnowledgeDocumentIndexStatus(str, Enum):
    """知识库文件索引状态。"""

    UPLOADED = "uploaded"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class RagChatSessionStatus(str, Enum):
    """RAG 对话会话状态。"""

    ACTIVE = "active"
    CLOSED = "closed"


class RagMessageRole(str, Enum):
    """RAG 消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class KnowledgeBaseEntity(Base):
    """知识库主表。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index("ix_knowledge_bases_category_status", "category", "status"),
        Index("ix_knowledge_bases_skill_status", "skill_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="reference")
    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeBaseStatus.ACTIVE.value,
        index=True,
    )
    vector_collection_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class KnowledgeDocumentEntity(Base):
    """知识库文件记录表。"""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_kb_status", "knowledge_base_id", "index_status"),
        Index("ix_knowledge_documents_category_status", "category", "index_status"),
        Index("ix_knowledge_documents_skill_status", "skill_id", "index_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    knowledge_base_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="reference")
    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    index_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeDocumentIndexStatus.UPLOADED.value,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class RagChatSessionEntity(Base):
    """RAG 问答会话表。"""

    __tablename__ = "rag_chat_sessions"
    __table_args__ = (Index("ix_rag_chat_sessions_kb_status", "knowledge_base_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    knowledge_base_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RagChatSessionStatus.ACTIVE.value,
        index=True,
    )
    session_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RagChatMessageEntity(Base):
    """RAG 问答消息表。"""

    __tablename__ = "rag_chat_messages"
    __table_args__ = (Index("ix_rag_chat_messages_session_created_at", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rag_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    references_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    message_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    token_count: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )


__all__ = [
    "KnowledgeBaseEntity",
    "KnowledgeBaseStatus",
    "KnowledgeDocumentEntity",
    "KnowledgeDocumentIndexStatus",
    "RagChatMessageEntity",
    "RagChatSessionEntity",
    "RagChatSessionStatus",
    "RagMessageRole",
]
