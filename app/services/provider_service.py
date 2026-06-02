"""Provider 管理服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import config
from app.core.llm_factory import llm_factory
from app.core.provider_runtime import ProviderRuntimeRecord, provider_runtime_registry
from app.models.provider import (
    LlmProviderCreateRequest,
    LlmProviderCredentialFieldDTO,
    LlmProviderDTO,
    LlmProviderEntity,
    LlmProviderStatus,
    LlmProviderUpdateRequest,
    ProviderRuntimeDTO,
)
from app.repositories.provider_repository import ProviderRepository
from app.utils.exceptions import BusinessException, ErrorCode


def _utc_now() -> datetime:
    """返回带时区的 UTC 时间。"""

    return datetime.now(timezone.utc)


class ProviderService:
    """Provider 配置与运行时管理服务。"""

    def __init__(
        self,
        *,
        repository_factory: Callable[[AsyncSession], ProviderRepository] = ProviderRepository,
    ) -> None:
        self._repository_factory = repository_factory

    async def initialize_runtime_registry(self) -> None:
        """初始化运行时 Provider 注册表。"""

        if (
            not config.postgres.enabled
            or not config.provider_runtime.prefer_database_default
        ):
            provider_runtime_registry.ensure_env_fallback()
            return

        try:
            from app.core.database import database_manager

            session_factory = database_manager.get_session_factory()
            async with session_factory() as session:
                repository = self._repository_factory(session)
                providers = await repository.list_providers(limit=500)
                default_provider = await repository.get_default_provider()
        except Exception as exc:
            logger.warning("初始化 Provider 运行时失败，回退 env 默认配置: {}", exc)
            provider_runtime_registry.ensure_env_fallback()
            return

        runtime_providers = [
            self._build_runtime_record(provider)
            for provider in providers
            if provider.status != LlmProviderStatus.INACTIVE.value
        ]
        if runtime_providers and default_provider is not None:
            provider_runtime_registry.load_runtime_providers(
                runtime_providers,
                default_provider_code=default_provider.provider_code,
            )
            return

        provider_runtime_registry.ensure_env_fallback()

    async def list_providers(
        self,
        session: AsyncSession,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[LlmProviderDTO]:
        """返回 Provider 列表。"""

        repository = self._repository_factory(session)
        providers = await repository.list_providers(status=status, limit=limit)
        return [self._to_dto(provider) for provider in providers]

    async def get_provider(
        self,
        session: AsyncSession,
        provider_id: str,
    ) -> LlmProviderDTO:
        """读取单个 Provider。"""

        entity = await self._require_provider(session, provider_id)
        return self._to_dto(entity)

    async def create_provider(
        self,
        session: AsyncSession,
        request: LlmProviderCreateRequest,
    ) -> LlmProviderDTO:
        """创建 Provider。"""

        repository = self._repository_factory(session)
        existing = await repository.get_provider_by_code(request.provider_code)
        if existing is not None:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="Provider 编码已存在",
                details={"provider_code": request.provider_code},
            )

        entity = LlmProviderEntity(
            provider_code=request.provider_code,
            provider_name=request.provider_name,
            provider_type=request.provider_type,
            api_base=request.api_base,
            model_name=request.model_name,
            credentials_json=dict(request.credentials_json),
            settings_json=dict(request.settings_json),
            status=request.status,
            is_default=request.is_default,
        )
        await repository.add_provider(entity)
        if request.is_default:
            await repository.set_default_provider(entity.id)
        await session.flush()
        await session.commit()
        await self.refresh_runtime_registry(session)
        return self._to_dto(entity)

    async def update_provider(
        self,
        session: AsyncSession,
        provider_id: str,
        request: LlmProviderUpdateRequest,
    ) -> LlmProviderDTO:
        """更新 Provider。"""

        entity = await self._require_provider(session, provider_id)
        payload = request.model_dump(exclude_unset=True)
        for field_name, field_value in payload.items():
            setattr(entity, field_name, field_value)

        repository = self._repository_factory(session)
        await repository.upsert_provider(entity)
        if request.is_default:
            await repository.set_default_provider(entity.id)
        await session.flush()
        await session.commit()
        await self.refresh_runtime_registry(session)
        return self._to_dto(entity)

    async def delete_provider(
        self,
        session: AsyncSession,
        provider_id: str,
    ) -> None:
        """删除 Provider。"""

        await self._require_provider(session, provider_id)
        repository = self._repository_factory(session)
        await repository.delete_provider(provider_id)
        await session.commit()
        await self.refresh_runtime_registry(session)

    async def set_default_provider(
        self,
        session: AsyncSession,
        provider_id: str,
    ) -> LlmProviderDTO:
        """设置默认 Provider。"""

        entity = await self._require_provider(session, provider_id)
        repository = self._repository_factory(session)
        await repository.set_default_provider(provider_id)
        entity.is_default = True
        await session.commit()
        await self.refresh_runtime_registry(session)
        return self._to_dto(entity)

    async def test_connection(
        self,
        session: AsyncSession,
        provider_id: str,
    ) -> LlmProviderDTO:
        """显式测试 Provider 连通性。"""

        entity = await self._require_provider(session, provider_id)
        entity.last_tested_at = _utc_now()
        try:
            test_llm = llm_factory.create_chat_model(
                provider_code=entity.provider_code,
                model=entity.model_name,
                base_url=entity.api_base,
                api_key=str(entity.credentials_json.get("api_key", "") or ""),
                streaming=False,
            )
            if hasattr(test_llm, "ainvoke"):
                await test_llm.ainvoke("请回复 OK")
            entity.status = LlmProviderStatus.ACTIVE.value
            entity.last_error_message = None
        except Exception as exc:
            entity.status = LlmProviderStatus.ERROR.value
            entity.last_error_message = str(exc)
            await session.flush()
            await session.commit()
            raise BusinessException(
                code=ErrorCode.PROVIDER_TEST_FAILED,
                message="Provider 连通性测试失败",
                http_status=502,
                details={
                    "provider_id": provider_id,
                    "provider_code": entity.provider_code,
                    "error": str(exc),
                },
            ) from exc

        repository = self._repository_factory(session)
        await repository.upsert_provider(entity)
        await session.flush()
        await session.commit()
        await self.refresh_runtime_registry(session)
        return self._to_dto(entity)

    async def get_runtime_snapshot(self) -> ProviderRuntimeDTO:
        """返回当前运行时 Provider。"""

        if not provider_runtime_registry.initialized:
            await self.initialize_runtime_registry()
        return provider_runtime_registry.get_runtime_snapshot()

    async def refresh_runtime_registry(self, session: AsyncSession) -> None:
        """从数据库刷新运行时 Provider 注册表。"""

        repository = self._repository_factory(session)
        providers = await repository.list_providers(limit=500)
        default_provider = await repository.get_default_provider()
        runtime_providers = [
            self._build_runtime_record(provider)
            for provider in providers
            if provider.status != LlmProviderStatus.INACTIVE.value
        ]
        if runtime_providers and default_provider is not None:
            provider_runtime_registry.load_runtime_providers(
                runtime_providers,
                default_provider_code=default_provider.provider_code,
            )
            return
        provider_runtime_registry.ensure_env_fallback()

    async def _require_provider(
        self,
        session: AsyncSession,
        provider_id: str,
    ) -> LlmProviderEntity:
        """确保 Provider 存在。"""

        repository = self._repository_factory(session)
        entity = await repository.get_provider(provider_id)
        if entity is None:
            raise BusinessException(
                code=ErrorCode.PROVIDER_NOT_FOUND,
                message="Provider 不存在",
                http_status=404,
                details={"provider_id": provider_id},
            )
        return entity

    @staticmethod
    def _build_runtime_record(entity: LlmProviderEntity) -> ProviderRuntimeRecord:
        """从实体构建运行时记录。"""

        return ProviderRuntimeRecord(
            provider_code=entity.provider_code,
            provider_name=entity.provider_name,
            provider_type=entity.provider_type,
            api_base=entity.api_base,
            model_name=entity.model_name,
            api_key=str(entity.credentials_json.get("api_key", "") or ""),
            settings_json=dict(entity.settings_json or {}),
            source="database",
            status=entity.status,
            is_default=entity.is_default,
        )

    @staticmethod
    def _to_dto(entity: LlmProviderEntity) -> LlmProviderDTO:
        """实体转 DTO。"""

        return LlmProviderDTO(
            provider_id=entity.id,
            provider_code=entity.provider_code,
            provider_name=entity.provider_name,
            provider_type=entity.provider_type,
            api_base=entity.api_base,
            model_name=entity.model_name,
            credentials=_mask_credentials(entity.credentials_json),
            settings_json=dict(entity.settings_json or {}),
            status=entity.status,
            is_default=entity.is_default,
            last_tested_at=entity.last_tested_at,
            last_error_message=entity.last_error_message,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


def _mask_credentials(credentials_json: dict[str, Any] | None) -> list[LlmProviderCredentialFieldDTO]:
    """将凭据字段脱敏后返回。"""

    items = credentials_json or {}
    results: list[LlmProviderCredentialFieldDTO] = []
    for field_name, field_value in items.items():
        raw_value = str(field_value or "")
        masked_value = ""
        if raw_value:
            if len(raw_value) <= 4:
                masked_value = "*" * len(raw_value)
            else:
                masked_value = f"{raw_value[:2]}***{raw_value[-2:]}"
        results.append(
            LlmProviderCredentialFieldDTO(
                field_name=field_name,
                configured=bool(raw_value),
                masked_value=masked_value,
            )
        )
    return results


provider_service = ProviderService()


__all__ = [
    "ProviderService",
    "provider_service",
]
