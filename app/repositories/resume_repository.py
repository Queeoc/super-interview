"""简历领域数据访问仓储。"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import ResumeAnalysisEntity, ResumeAnalysisStatus, ResumeEntity, ResumeStatus


def _status_value(status: str | Enum) -> str:
    """把枚举或字符串统一转成字符串值。"""

    return status.value if isinstance(status, Enum) else status


class ResumeRepository:
    """简历相关实体的数据库访问封装。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_resume(self, entity: ResumeEntity) -> ResumeEntity:
        """新增简历记录。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_resume(self, entity: ResumeEntity) -> ResumeEntity:
        """创建或更新简历记录。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_resume(self, resume_id: str) -> ResumeEntity | None:
        """按简历 ID 查询简历。"""

        return await self._session.get(ResumeEntity, resume_id)

    async def get_resume_by_content_hash(self, content_hash: str) -> ResumeEntity | None:
        """按内容哈希查询简历。"""

        stmt = select(ResumeEntity).where(ResumeEntity.content_hash == content_hash)
        result = await self._session.scalars(stmt)
        return result.first()

    async def list_resumes_by_status(
        self,
        status: ResumeStatus | str,
        limit: int = 20,
    ) -> list[ResumeEntity]:
        """按状态查询简历列表。"""

        stmt = (
            select(ResumeEntity)
            .where(ResumeEntity.status == _status_value(status))
            .order_by(desc(ResumeEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def upsert_analysis(self, entity: ResumeAnalysisEntity) -> ResumeAnalysisEntity:
        """创建或更新简历分析结果。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_analysis_by_resume_id(
        self,
        resume_id: str,
    ) -> ResumeAnalysisEntity | None:
        """按简历 ID 查询分析结果。"""

        stmt = select(ResumeAnalysisEntity).where(ResumeAnalysisEntity.resume_id == resume_id)
        result = await self._session.scalars(stmt)
        return result.first()

    async def list_analysis_by_status(
        self,
        status: ResumeAnalysisStatus | str,
        limit: int = 20,
    ) -> list[ResumeAnalysisEntity]:
        """按状态查询分析结果。"""

        stmt = (
            select(ResumeAnalysisEntity)
            .where(ResumeAnalysisEntity.status == _status_value(status))
            .order_by(desc(ResumeAnalysisEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def delete_resume(self, resume_id: str) -> None:
        """删除简历记录。"""

        entity = await self.get_resume(resume_id)
        if entity is None:
            return
        await self._session.delete(entity)
        await self._session.flush()


__all__ = ["ResumeRepository"]
