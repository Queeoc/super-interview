"""面试持久化服务。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import InterviewAnswerEntity, InterviewReportEntity, InterviewSessionEntity
from app.repositories.interview_repository import InterviewRepository


class InterviewPersistenceService:
    """把面试会话、答案和报告写入仓储的聚合服务。"""

    def __init__(self, repository: InterviewRepository | None = None) -> None:
        self._repository = repository

    def _resolve_repository(self, session: AsyncSession) -> InterviewRepository:
        """解析当前要使用的仓储实例。"""

        return self._repository or InterviewRepository(session)

    async def save_session_bundle(
        self,
        session: AsyncSession,
        interview_session: InterviewSessionEntity,
        answers: Sequence[InterviewAnswerEntity] | None = None,
        report: InterviewReportEntity | None = None,
    ) -> InterviewSessionEntity:
        """保存一组面试持久化对象。"""

        repository = self._resolve_repository(session)
        saved_session = await repository.upsert_session(interview_session)

        for answer in answers or ():
            if not answer.session_id:
                answer.session_id = saved_session.id
            await repository.add_answer(answer)

        if report is not None:
            if not report.session_id:
                report.session_id = saved_session.id
            await repository.upsert_report(report)

        return saved_session

    async def save_answer(
        self,
        session: AsyncSession,
        answer: InterviewAnswerEntity,
    ) -> InterviewAnswerEntity:
        """保存单条答案记录。"""

        repository = self._resolve_repository(session)
        return await repository.add_answer(answer)

    async def save_report(
        self,
        session: AsyncSession,
        report: InterviewReportEntity,
    ) -> InterviewReportEntity:
        """保存单条报告记录。"""

        repository = self._resolve_repository(session)
        return await repository.upsert_report(report)

    async def save_session_snapshot(
        self,
        session: AsyncSession,
        interview_session: InterviewSessionEntity,
    ) -> InterviewSessionEntity:
        """仅保存会话快照。"""

        repository = self._resolve_repository(session)
        return await repository.upsert_session(interview_session)

    async def save_answer_and_session_snapshot(
        self,
        session: AsyncSession,
        *,
        interview_session: InterviewSessionEntity,
        answer: InterviewAnswerEntity,
        report: InterviewReportEntity | None = None,
    ) -> InterviewSessionEntity:
        """保存答案并同步更新会话快照，可选写入报告占位。"""

        repository = self._resolve_repository(session)
        saved_session = await repository.upsert_session(interview_session)
        if not answer.session_id:
            answer.session_id = saved_session.id
        await repository.add_answer(answer)
        if report is not None:
            if not report.session_id:
                report.session_id = saved_session.id
            await repository.upsert_report(report)
        return saved_session


__all__ = ["InterviewPersistenceService"]
