"""Provider 额度耗尽后的最小自动切换服务。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import database_manager
from app.models.provider import LlmProviderEntity, LlmProviderStatus
from app.repositories.provider_repository import ProviderRepository

_QUOTA_ERROR_HINTS = (
    "quota",
    "quota exceeded",
    "exceeded current quota",
    "insufficient_quota",
    "rate limit",
    "rate_limit",
    "token limit",
    "usage limit",
    "余额不足",
    "额度",
    "配额",
    "限额",
    "调用频次",
)


class ProviderFailoverService:
    """负责把当前失效 Provider 标记为不可用，并切换默认 Provider。"""

    def __init__(
        self,
        *,
        repository_factory: Callable[[AsyncSession], ProviderRepository] = ProviderRepository,
    ) -> None:
        self._repository_factory = repository_factory

    def is_quota_exhausted_error(self, error: BaseException) -> bool:
        """判断异常是否像额度耗尽或频控错误。"""

        error_text = str(error).lower()
        return any(hint in error_text for hint in _QUOTA_ERROR_HINTS)

    async def disable_and_failover(
        self,
        provider_code: str,
        *,
        error_message: str,
    ) -> str | None:
        """将当前 Provider 标记为不可用，并切换到下一个 active Provider。"""

        if not database_manager.enabled:
            logger.warning("Provider 自动切换跳过：PostgreSQL 未启用, provider_code={}", provider_code)
            return None

        session_factory = database_manager.get_session_factory()
        async with session_factory() as session:
            repository = self._repository_factory(session)
            current_provider = await repository.get_provider_by_code(provider_code)
            if current_provider is None:
                return None

            current_provider.status = LlmProviderStatus.INACTIVE.value
            current_provider.is_default = False
            current_provider.last_error_message = error_message
            await repository.upsert_provider(current_provider)

            candidates = await repository.list_available_providers(
                exclude_provider_codes={provider_code},
                limit=500,
            )
            next_provider = candidates[0] if candidates else None
            if next_provider is not None:
                await repository.set_default_provider(next_provider.id)
                next_provider.is_default = True
                logger.warning(
                    "Provider 自动切换完成: old_provider_code={}, new_provider_code={}",
                    provider_code,
                    next_provider.provider_code,
                )
            else:
                logger.warning(
                    "Provider 自动切换失败：没有剩余 active Provider, old_provider_code={}",
                    provider_code,
                )

            await session.commit()

            from app.services.provider_service import provider_service

            await provider_service.refresh_runtime_registry(session)
            return next_provider.provider_code if next_provider is not None else None


provider_failover_service = ProviderFailoverService()


__all__ = [
    "ProviderFailoverService",
    "provider_failover_service",
]
