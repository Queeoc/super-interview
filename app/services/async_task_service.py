"""异步任务后台消费服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.config import config
from app.core.database import database_manager
from app.core.stream_consumer import StreamConsumer
from app.core.stream_producer import StreamTaskEnvelope
from app.services.evaluation_service import evaluation_service

TaskHandler = Callable[[StreamTaskEnvelope], Awaitable[dict[str, Any] | None]]


class AsyncTaskService:
    """进程内异步任务消费者。"""

    def __init__(
        self,
        *,
        consumer: StreamConsumer | None = None,
    ) -> None:
        self._consumer = consumer or StreamConsumer()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._handlers: dict[str, TaskHandler] = {
            "interview_report_generate": self._handle_interview_report_generate,
        }

    @property
    def enabled(self) -> bool:
        """当前是否启用后台消费。"""

        return self._consumer.enabled

    async def start(self) -> None:
        """启动后台消费循环。"""

        if not self.enabled or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("异步任务后台消费器已启动")

    async def stop(self) -> None:
        """停止后台消费循环。"""

        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("异步任务后台消费器已停止")

    async def _consume_loop(self) -> None:
        """持续消费 Stream 消息。"""

        while self._running:
            try:
                await self._consumer.process_messages(self._dispatch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("异步任务消费循环异常: {}", exc)
            await asyncio.sleep(config.stream_task.worker_poll_interval_seconds)

    async def _dispatch(self, envelope: StreamTaskEnvelope) -> dict[str, Any] | None:
        """按 task_type 分发处理器。"""

        handler = self._handlers.get(envelope.task_type)
        if handler is None:
            raise RuntimeError(f"未注册的 task_type: {envelope.task_type}")
        return await handler(envelope)

    async def _handle_interview_report_generate(
        self,
        envelope: StreamTaskEnvelope,
    ) -> dict[str, Any]:
        """处理面试报告生成任务。"""

        session_id = str(envelope.payload.get("session_id", ""))
        if not session_id:
            raise RuntimeError("session_id 不能为空")

        session_factory = database_manager.get_session_factory()
        async with session_factory() as session:
            report_entity = await evaluation_service.generate_report(session, session_id)
            await session.commit()
            return {
                "session_id": session_id,
                "report_status": report_entity.status,
            }


async_task_service = AsyncTaskService()


__all__ = [
    "AsyncTaskService",
    "async_task_service",
]
