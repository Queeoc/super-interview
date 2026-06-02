"""LLM Provider 数据访问仓储。"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import LlmProviderEntity, LlmProviderStatus


def _status_value(status: str | Enum) -> str:
    """把枚举或字符串统一转成字符串值。"""

    return status.value if isinstance(status, Enum) else status


class ProviderRepository:
    """LLM Provider 配置的数据访问封装。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_provider(self, entity: LlmProviderEntity) -> LlmProviderEntity:
        """新增 Provider 配置。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_provider(self, entity: LlmProviderEntity) -> LlmProviderEntity:
        """创建或更新 Provider 配置。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_provider(self, provider_id: str) -> LlmProviderEntity | None:
        """按 ID 查询 Provider。"""

        return await self._session.get(LlmProviderEntity, provider_id)

    async def get_provider_by_code(self, provider_code: str) -> LlmProviderEntity | None:
        """按 provider_code 查询 Provider。"""

        stmt = select(LlmProviderEntity).where(LlmProviderEntity.provider_code == provider_code)
        result = await self._session.scalars(stmt)
        return result.first()

    async def list_providers(
        self,
        status: LlmProviderStatus | str | None = None,
        limit: int = 50,
    ) -> list[LlmProviderEntity]:
        """查询 Provider 列表。"""

        stmt = select(LlmProviderEntity)
        if status is not None:
            stmt = stmt.where(LlmProviderEntity.status == _status_value(status))
        stmt = stmt.order_by(desc(LlmProviderEntity.is_default), desc(LlmProviderEntity.updated_at))
        stmt = stmt.limit(limit)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_available_providers(
        self,
        *,
        exclude_provider_codes: set[str] | None = None,
        limit: int = 500,
    ) -> list[LlmProviderEntity]:
        """列出当前可用于自动切换的 Provider。"""

        stmt = select(LlmProviderEntity).where(LlmProviderEntity.status == LlmProviderStatus.ACTIVE.value)
        if exclude_provider_codes:
            stmt = stmt.where(LlmProviderEntity.provider_code.notin_(sorted(exclude_provider_codes)))
        stmt = stmt.order_by(desc(LlmProviderEntity.is_default), desc(LlmProviderEntity.updated_at))
        stmt = stmt.limit(limit)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def get_default_provider(self) -> LlmProviderEntity | None:
        """查询默认 Provider。"""

        stmt = select(LlmProviderEntity).where(LlmProviderEntity.is_default.is_(True))
        result = await self._session.scalars(stmt)
        return result.first()

    async def set_default_provider(self, provider_id: str) -> None:
        """设置默认 Provider。"""

        await self._session.execute(
            update(LlmProviderEntity)
            .where(LlmProviderEntity.id != provider_id)
            .values(is_default=False)
        )
        await self._session.execute(
            update(LlmProviderEntity)
            .where(LlmProviderEntity.id == provider_id)
            .values(is_default=True)
        )
        await self._session.flush()

    async def delete_provider(self, provider_id: str) -> None:
        """删除 Provider。"""

        entity = await self.get_provider(provider_id)
        if entity is None:
            return
        await self._session.delete(entity)
        await self._session.flush()


__all__ = ["ProviderRepository"]
