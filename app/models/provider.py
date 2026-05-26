"""LLM Provider 持久化实体。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _generate_uuid() -> str:
    """生成字符串形式的 UUID 主键。"""

    return str(uuid4())


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


class LlmProviderStatus(str, Enum):
    """Provider 状态。"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class LlmProviderEntity(Base):
    """LLM Provider 配置表。"""

    __tablename__ = "llm_providers"
    __table_args__ = (Index("ix_llm_providers_status_default", "status", "is_default"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False, default="chat")
    api_base: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credentials_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=LlmProviderStatus.ACTIVE.value,
        index=True,
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    "LlmProviderEntity",
    "LlmProviderStatus",
]
