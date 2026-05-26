"""简历持久化服务。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import ResumeAnalysisEntity, ResumeEntity
from app.repositories.resume_repository import ResumeRepository


class ResumePersistenceService:
    """把简历与简历分析结果写入仓储的聚合服务。"""

    def __init__(self, repository: ResumeRepository | None = None) -> None:
        self._repository = repository

    def _resolve_repository(self, session: AsyncSession) -> ResumeRepository:
        """解析当前要使用的仓储实例。"""

        return self._repository or ResumeRepository(session)

    async def save_resume_bundle(
        self,
        session: AsyncSession,
        resume: ResumeEntity,
        analysis: ResumeAnalysisEntity | None = None,
    ) -> ResumeEntity:
        """保存简历及可选的分析结果。"""

        repository = self._resolve_repository(session)
        saved_resume = await repository.upsert_resume(resume)

        if analysis is not None:
            if not analysis.resume_id:
                analysis.resume_id = saved_resume.id
            await repository.upsert_analysis(analysis)

        return saved_resume

    async def save_analysis(
        self,
        session: AsyncSession,
        analysis: ResumeAnalysisEntity,
    ) -> ResumeAnalysisEntity:
        """保存单条简历分析结果。"""

        repository = self._resolve_repository(session)
        return await repository.upsert_analysis(analysis)

    async def save_resume(
        self,
        session: AsyncSession,
        resume: ResumeEntity,
    ) -> ResumeEntity:
        """保存单条简历记录。"""

        repository = self._resolve_repository(session)
        return await repository.upsert_resume(resume)


__all__ = ["ResumePersistenceService"]
