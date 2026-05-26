"""简历领域持久化实体。"""

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


class ResumeStatus(str, Enum):
    """简历当前状态。"""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ResumeAnalysisStatus(str, Enum):
    """简历分析状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ResumeEntity(Base):
    """简历主表。"""

    __tablename__ = "resumes"
    __table_args__ = (
        Index("ix_resumes_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    file_size: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ResumeStatus.UPLOADED.value,
        index=True,
    )
    source_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
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


class ResumeAnalysisEntity(Base):
    """简历分析结果表。"""

    __tablename__ = "resume_analyses"
    __table_args__ = (Index("ix_resume_analyses_resume_status", "resume_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    resume_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ResumeAnalysisStatus.PENDING.value,
        index=True,
    )
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    skill_tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


__all__ = [
    "ResumeAnalysisEntity",
    "ResumeAnalysisStatus",
    "ResumeEntity",
    "ResumeStatus",
]
