"""Provider 运行时注册表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.config import config
from app.models.provider import ProviderRuntimeDTO


@dataclass(slots=True)
class ProviderRuntimeRecord:
    """运行时 Provider 记录。"""

    provider_code: str
    provider_name: str
    provider_type: str
    api_base: str | None
    model_name: str | None
    api_key: str | None
    settings_json: dict[str, Any] = field(default_factory=dict)
    source: str = "env"
    status: str = "active"
    is_default: bool = False

    def to_dto(self, *, initialized: bool) -> ProviderRuntimeDTO:
        """转换为对外暴露的运行时 DTO。"""

        return ProviderRuntimeDTO(
            provider_code=self.provider_code,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            api_base=self.api_base,
            model_name=self.model_name,
            source=self.source,
            status=self.status,
            is_default=self.is_default,
            settings_json=dict(self.settings_json),
            initialized=initialized,
        )


class ProviderRuntimeRegistry:
    """维护内存中的当前运行时 Provider 注册表。"""

    def __init__(self) -> None:
        self._providers_by_code: dict[str, ProviderRuntimeRecord] = {}
        self._default_provider_code: str | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        """是否已经完成初始化。"""

        return self._initialized

    def reset(self) -> None:
        """清空运行时注册表。"""

        self._providers_by_code.clear()
        self._default_provider_code = None
        self._initialized = False

    def load_runtime_providers(
        self,
        providers: list[ProviderRuntimeRecord],
        *,
        default_provider_code: str | None = None,
    ) -> None:
        """加载运行时 Provider 列表。"""

        self._providers_by_code = {provider.provider_code: provider for provider in providers}
        resolved_default = default_provider_code
        if resolved_default is None:
            for provider in providers:
                if provider.is_default:
                    resolved_default = provider.provider_code
                    break
        self._default_provider_code = resolved_default
        self._initialized = True
        logger.info(
            "Provider 运行时注册表已刷新: provider_count={}, default_provider_code={}",
            len(providers),
            self._default_provider_code,
        )

    def ensure_env_fallback(self) -> ProviderRuntimeRecord:
        """确保环境变量回退 Provider 存在。"""

        provider_code = config.provider_runtime.env_fallback_provider_code
        provider = ProviderRuntimeRecord(
            provider_code=provider_code,
            provider_name=config.provider_runtime.env_fallback_provider_name,
            provider_type="chat",
            api_base=config.llm.api_base,
            model_name=config.llm.chat_model,
            api_key=config.llm.api_key,
            settings_json={
                "request_timeout_seconds": config.llm.request_timeout_seconds,
                "embedding_model": config.llm.embedding_model,
            },
            source="env",
            status="active",
            is_default=True,
        )
        self.load_runtime_providers([provider], default_provider_code=provider.provider_code)
        return provider

    def get_runtime_provider(self, provider_code: str | None = None) -> ProviderRuntimeRecord | None:
        """读取运行时 Provider。"""

        resolved_code = provider_code or self._default_provider_code
        if resolved_code is None:
            return None
        return self._providers_by_code.get(resolved_code)

    def list_runtime_providers(self) -> list[ProviderRuntimeRecord]:
        """按当前加载顺序返回运行时 Provider 列表。"""

        return list(self._providers_by_code.values())

    def has_default_provider(self) -> bool:
        """是否存在默认运行时 Provider。"""

        return self.get_runtime_provider() is not None

    def get_runtime_snapshot(self) -> ProviderRuntimeDTO:
        """返回当前运行时 Provider 快照。"""

        provider = self.get_runtime_provider()
        if provider is None:
            return ProviderRuntimeDTO(
                provider_code="",
                provider_name="",
                provider_type="chat",
                api_base=None,
                model_name=None,
                source="uninitialized",
                status="inactive",
                is_default=False,
                settings_json={},
                initialized=self._initialized,
            )
        return provider.to_dto(initialized=self._initialized)


provider_runtime_registry = ProviderRuntimeRegistry()


__all__ = [
    "ProviderRuntimeRecord",
    "ProviderRuntimeRegistry",
    "provider_runtime_registry",
]
