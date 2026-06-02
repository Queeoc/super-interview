"""LLM Provider 持久化实体与接口 DTO。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
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


class LlmProviderCreateRequest(BaseModel):
    """创建 Provider 请求。"""

    model_config = ConfigDict(extra="forbid")

    provider_code: str = Field(..., min_length=1, description="Provider 唯一编码")
    provider_name: str = Field(..., min_length=1, description="Provider 展示名")
    provider_type: str = Field(default="chat", description="Provider 类型")
    api_base: str | None = Field(default=None, description="兼容 OpenAI 的 API Base URL")
    model_name: str | None = Field(default=None, description="默认模型名")
    credentials_json: dict[str, Any] = Field(default_factory=dict, description="凭据配置")
    settings_json: dict[str, Any] = Field(default_factory=dict, description="附加运行时配置")
    status: str = Field(default=LlmProviderStatus.ACTIVE.value, description="Provider 状态")
    is_default: bool = Field(default=False, description="是否设为默认 Provider")

    @field_validator("provider_code", "provider_name", "provider_type", mode="before")
    @classmethod
    def strip_required_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class LlmProviderUpdateRequest(BaseModel):
    """更新 Provider 请求。"""

    model_config = ConfigDict(extra="forbid")

    provider_name: str | None = Field(default=None, min_length=1, description="Provider 展示名")
    provider_type: str | None = Field(default=None, description="Provider 类型")
    api_base: str | None = Field(default=None, description="兼容 OpenAI 的 API Base URL")
    model_name: str | None = Field(default=None, description="默认模型名")
    credentials_json: dict[str, Any] | None = Field(default=None, description="凭据配置")
    settings_json: dict[str, Any] | None = Field(default=None, description="附加运行时配置")
    status: str | None = Field(default=None, description="Provider 状态")
    is_default: bool | None = Field(default=None, description="是否设为默认 Provider")

    @field_validator("provider_name", "provider_type", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class LlmProviderCredentialFieldDTO(BaseModel):
    """脱敏后的凭据字段。"""

    field_name: str = Field(..., description="字段名")
    configured: bool = Field(..., description="是否已配置")
    masked_value: str = Field(..., description="脱敏后的字段值")


class LlmProviderDTO(BaseModel):
    """对外暴露的 Provider DTO。"""

    provider_id: str = Field(..., description="Provider 主键")
    provider_code: str = Field(..., description="Provider 唯一编码")
    provider_name: str = Field(..., description="Provider 展示名")
    provider_type: str = Field(..., description="Provider 类型")
    api_base: str | None = Field(default=None, description="兼容 OpenAI 的 API Base URL")
    model_name: str | None = Field(default=None, description="默认模型名")
    credentials: list[LlmProviderCredentialFieldDTO] = Field(
        default_factory=list,
        description="脱敏后的凭据字段列表",
    )
    settings_json: dict[str, Any] = Field(default_factory=dict, description="附加运行时配置")
    status: str = Field(..., description="Provider 状态")
    is_default: bool = Field(..., description="是否默认")
    last_tested_at: datetime | None = Field(default=None, description="最近连通性测试时间")
    last_error_message: str | None = Field(default=None, description="最近一次测试错误")
    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")


class ProviderRuntimeDTO(BaseModel):
    """当前运行时 Provider 快照。"""

    provider_code: str = Field(..., description="当前运行时 Provider 编码")
    provider_name: str = Field(..., description="当前运行时 Provider 展示名")
    provider_type: str = Field(..., description="Provider 类型")
    api_base: str | None = Field(default=None, description="API Base URL")
    model_name: str | None = Field(default=None, description="默认模型名")
    source: str = Field(..., description="来源，database 或 env")
    status: str = Field(..., description="运行时状态")
    is_default: bool = Field(..., description="是否为默认 Provider")
    settings_json: dict[str, Any] = Field(default_factory=dict, description="运行时附加配置")
    initialized: bool = Field(..., description="是否已初始化")


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
    "LlmProviderCreateRequest",
    "LlmProviderCredentialFieldDTO",
    "LlmProviderDTO",
    "LlmProviderEntity",
    "LlmProviderStatus",
    "LlmProviderUpdateRequest",
    "ProviderRuntimeDTO",
]
