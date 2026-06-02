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

    async def get_resume_by_visitor(
        self,
        session: AsyncSession,
        resume_id: str,
        visitor_id: str,
    ) -> ResumeEntity | None:
        """按访客作用域查询单份简历。"""

        repository = self._resolve_repository(session)
        return await repository.get_resume_by_visitor(resume_id, visitor_id)

    async def get_resume_by_visitor_and_content_hash(
        self,
        session: AsyncSession,
        visitor_id: str,
        content_hash: str,
    ) -> ResumeEntity | None:
        """按访客和内容哈希查询可复用简历。"""

        repository = self._resolve_repository(session)
        return await repository.get_resume_by_visitor_and_content_hash(visitor_id, content_hash)

    async def list_resumes_by_visitor(
        self,
        session: AsyncSession,
        visitor_id: str,
        limit: int = 20,
    ) -> list[ResumeEntity]:
        """查询访客历史简历列表。"""

        repository = self._resolve_repository(session)
        return await repository.list_resumes_by_visitor(visitor_id, limit=limit)

    async def get_latest_available_resume_by_visitor(
        self,
        session: AsyncSession,
        visitor_id: str,
    ) -> ResumeEntity | None:
        """查询访客最近一份可用于面试的简历。"""

        repository = self._resolve_repository(session)
        return await repository.get_latest_available_resume_by_visitor(visitor_id)


__all__ = ["ResumePersistenceService"]
