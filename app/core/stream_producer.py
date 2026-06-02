"""统一 Redis Stream 任务生产者。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from loguru import logger

from app.config import config
from app.core.redis_client import redis_manager
from app.utils.exceptions import BusinessException, ErrorCode


def _utc_now_iso() -> str:
    """返回 ISO8601 UTC 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class StreamTaskEnvelope:
    """统一异步任务消息体。"""

    task_id: str
    task_type: str
    payload: dict[str, Any]
    retry_count: int = 0
    max_retries: int = field(default_factory=lambda: config.stream_task.max_retries)
    created_at: str = field(default_factory=_utc_now_iso)
    trace_context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        task_type: str,
        payload: dict[str, Any],
        trace_context: dict[str, Any] | None = None,
        max_retries: int | None = None,
    ) -> "StreamTaskEnvelope":
        """创建新的任务消息体。"""

        return cls(
            task_id=str(uuid4()),
            task_type=task_type,
            payload=payload,
            retry_count=0,
            max_retries=max_retries if max_retries is not None else config.stream_task.max_retries,
            trace_context=trace_context or {},
        )

    def to_stream_fields(self) -> dict[str, str]:
        """序列化为 Redis Stream fields。"""

        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "retry_count": str(self.retry_count),
            "max_retries": str(self.max_retries),
            "created_at": self.created_at,
            "trace_context": json.dumps(self.trace_context, ensure_ascii=False),
        }

    def to_status_payload(self, *, status: str, error_message: str | None = None) -> dict[str, Any]:
        """构建任务状态持久化内容。"""

        payload = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": status,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "updated_at": _utc_now_iso(),
            "trace_context": self.trace_context,
            "payload": self.payload,
        }
        if error_message:
            payload["error_message"] = error_message
        return payload


class StreamProducer:
    """异步任务生产者。"""

    def __init__(self, redis_backend: Any | None = None) -> None:
        self._redis_backend = redis_backend or redis_manager

    @property
    def enabled(self) -> bool:
        """当前是否启用异步任务能力。"""

        return bool(
            config.stream_task.enabled
            and getattr(self._redis_backend, "enabled", False)
        )

    async def publish(
        self,
        envelope: StreamTaskEnvelope,
    ) -> str:
        """发布异步任务，并初始化状态。"""

        if not self.enabled:
            raise BusinessException(
                code=ErrorCode.ASYNC_TASK_PUBLISH_FAILED,
                message="异步任务能力未启用",
                http_status=503,
                details={"task_type": envelope.task_type},
            )

        try:
            await self._redis_backend.set_value(
                self._build_status_key(envelope.task_id),
                json.dumps(
                    envelope.to_status_payload(status="pending"),
                    ensure_ascii=False,
                ),
                ttl_seconds=config.redis.default_ttl_seconds,
            )
            entry_id = await self._redis_backend.add_stream_entry(
                config.stream_task.stream_name,
                envelope.to_stream_fields(),
                maxlen=config.stream_task.maxlen,
            )
            logger.info(
                "异步任务已发布: task_id={}, task_type={}",
                envelope.task_id,
                envelope.task_type,
            )
            return entry_id
        except Exception as exc:
            logger.exception(
                "异步任务发布失败: task_id={}, task_type={}, error={}",
                envelope.task_id,
                envelope.task_type,
                exc,
            )
            raise BusinessException(
                code=ErrorCode.ASYNC_TASK_PUBLISH_FAILED,
                message="异步任务发布失败",
                http_status=503,
                details={
                    "task_id": envelope.task_id,
                    "task_type": envelope.task_type,
                    "error": str(exc),
                },
            ) from exc

    async def mark_status(
        self,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """更新任务状态。"""

        await self._redis_backend.set_value(
            self._build_status_key(task_id),
            json.dumps(payload, ensure_ascii=False),
            ttl_seconds=config.redis.default_ttl_seconds,
        )

    async def get_status(self, task_id: str) -> dict[str, Any] | None:
        """读取任务状态。"""

        raw_value = await self._redis_backend.get_value(self._build_status_key(task_id))
        if not raw_value:
            return None
        return json.loads(raw_value)

    @staticmethod
    def _build_status_key(task_id: str) -> str:
        return f"{config.stream_task.status_key_prefix}:{task_id}"


stream_producer = StreamProducer()


__all__ = [
    "StreamProducer",
    "StreamTaskEnvelope",
    "stream_producer",
]
