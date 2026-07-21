"""面试领域数据访问仓储。"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import (
    InterviewAnswerEntity,
    InterviewAnswerStatus,
    InterviewReportEntity,
    InterviewSessionEntity,
    InterviewSessionStatus,
)


def _status_value(status: str | Enum) -> str:
    """把枚举或字符串统一转成字符串值。"""

    return status.value if isinstance(status, Enum) else status


class InterviewRepository:
    """面试相关实体的数据库访问封装。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        """新增面试会话。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def upsert_session(self, entity: InterviewSessionEntity) -> InterviewSessionEntity:
        """创建或更新面试会话。"""

        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    async def get_session(self, session_id: str) -> InterviewSessionEntity | None:
        """按会话 ID 查询面试会话。"""

        return await self._session.get(InterviewSessionEntity, session_id)

    async def list_sessions_by_status(
        self,
        status: InterviewSessionStatus | str,
        limit: int = 20,
    ) -> list[InterviewSessionEntity]:
        """按状态查询会话列表。"""

        stmt = (
            select(InterviewSessionEntity)
            .where(InterviewSessionEntity.status == _status_value(status))
            .order_by(desc(InterviewSessionEntity.updated_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_sessions_by_visitor(
        self,
        visitor_id: str,
        limit: int = 20,
    ) -> list[InterviewSessionEntity]:
        """按访客查询其历史面试会话。"""

        stmt = (
            select(InterviewSessionEntity)
            .where(InterviewSessionEntity.visitor_id == visitor_id)
            .order_by(desc(InterviewSessionEntity.updated_at), desc(InterviewSessionEntity.created_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def add_answer(self, entity: InterviewAnswerEntity) -> InterviewAnswerEntity:
        """新增答案记录。"""

        self._session.add(entity)
        await self._session.flush()
        return entity

    async def update_answer_metadata(
        self,
        answer_id: str,
        metadata: dict,
    ) -> InterviewAnswerEntity | None:
        """更新答案元数据。"""

        entity = await self._session.get(InterviewAnswerEntity, answer_id)
        if entity is None:
            return None
        entity.answer_metadata_json = dict(metadata or {})
        await self._session.flush()
        return entity

    async def list_answers_by_session(
        self,
        session_id: str,
    ) -> list[InterviewAnswerEntity]:
        """按会话 ID 查询答案列表。"""

        stmt = (
            select(InterviewAnswerEntity)
            .where(InterviewAnswerEntity.session_id == session_id)
            .order_by(InterviewAnswerEntity.round_index.asc(), InterviewAnswerEntity.created_at.asc())
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_answers_by_round(
        self,
        session_id: str,
        round_index: int,
    ) -> list[InterviewAnswerEntity]:
        """按会话与轮次查询答案列表。"""

        stmt = (
            select(InterviewAnswerEntity)
            .where(InterviewAnswerEntity.session_id == session_id)
            .where(InterviewAnswerEntity.round_index == round_index)
            .order_by(InterviewAnswerEntity.created_at.asc())
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def get_answer_by_question_key(
        self,
        session_id: str,
        question_key: str,
    ) -> InterviewAnswerEntity | None:
        """按题目标识查询单条答案记录。"""

        stmt = (
            select(InterviewAnswerEntity)
            .where(InterviewAnswerEntity.session_id == session_id)
            .where(InterviewAnswerEntity.question_key == question_key)
            .order_by(desc(InterviewAnswerEntity.created_at))
        )
        result = await self._session.scalars(stmt)
        return result.first()

    async def list_answers_by_status(
        self,
        status: InterviewAnswerStatus | str,
        limit: int = 50,
    ) -> list[InterviewAnswerEntity]:
        """按答案状态查询答案列表。"""

        stmt = (
            select(InterviewAnswerEntity)
            .where(InterviewAnswerEntity.answer_status == _status_value(status))
            .order_by(desc(InterviewAnswerEntity.created_at))
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def upsert_report(self, entity: InterviewReportEntity) -> InterviewReportEntity:
        """按 session_id 幂等创建或更新面试报告。"""

        existing = None
        if entity.session_id:
            existing = await self.get_report_by_session(entity.session_id)

        if existing is None:
            self._session.add(entity)
            await self._session.flush()
            return entity

        existing.status = entity.status
        existing.summary_text = entity.summary_text
        existing.report_json = dict(entity.report_json or {})
        existing.score_json = dict(entity.score_json or {})
        existing.error_message = entity.error_message
        existing.generated_at = entity.generated_at
        await self._session.flush()
        return existing

    async def get_report_by_session(self, session_id: str) -> InterviewReportEntity | None:
        """按会话 ID 查询报告。"""

        stmt = select(InterviewReportEntity).where(InterviewReportEntity.session_id == session_id)
        result = await self._session.scalars(stmt)
        return result.first()

    async def get_session_snapshot(
        self,
        session_id: str,
    ) -> tuple[
        InterviewSessionEntity | None,
        list[InterviewAnswerEntity],
        InterviewReportEntity | None,
    ]:
        """读取面试会话、答案与报告快照。"""

        interview_session = await self.get_session(session_id)
        if interview_session is None:
            return None, [], None

        answers = await self.list_answers_by_session(session_id)
        report = await self.get_report_by_session(session_id)
        return interview_session, answers, report

    async def delete_session(self, session_id: str) -> None:
        """删除面试会话。"""

        entity = await self.get_session(session_id)
        if entity is None:
            return
        await self._session.delete(entity)
        await self._session.flush()


__all__ = ["InterviewRepository"]
