"""统一 Redis Stream 任务消费者。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any
import uuid

from loguru import logger

from app.config import config
from app.core.redis_client import redis_manager
from app.core.stream_producer import StreamProducer, StreamTaskEnvelope
from app.utils.exceptions import ErrorCode

TaskHandler = Callable[[StreamTaskEnvelope], Awaitable[dict[str, Any] | None]]


def _utc_now_iso() -> str:
    """返回 UTC ISO8601 字符串。"""

    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class StreamMessage:
    """消费者读取到的消息。"""

    stream_name: str
    entry_id: str
    envelope: StreamTaskEnvelope


class StreamConsumer:
    """统一异步任务消费者。"""

    def __init__(
        self,
        *,
        consumer_name: str | None = None,
        redis_backend: Any | None = None,
        producer: StreamProducer | None = None,
    ) -> None:
        self._redis_backend = redis_backend or redis_manager
        self._producer = producer or StreamProducer(redis_backend=self._redis_backend)
        suffix = consumer_name or uuid.uuid4().hex[:8]
        self._consumer_name = f"{config.stream_task.consumer_name_prefix}-{suffix}"

    @property
    def enabled(self) -> bool:
        """当前是否启用 Stream 消费。"""

        return bool(
            config.stream_task.enabled
            and getattr(self._redis_backend, "enabled", False)
        )

    @property
    def consumer_name(self) -> str:
        """当前消费者名称。"""

        return self._consumer_name

    async def ensure_group(self) -> None:
        """确保默认 consumer group 已创建。"""

        if not self.enabled:
            return
        await self._redis_backend.create_consumer_group(
            config.stream_task.stream_name,
            config.stream_task.consumer_group,
            start_id="0",
        )

    async def claim_stale_messages(self) -> list[StreamMessage]:
        """认领超时 pending 消息。"""

        if not self.enabled:
            return []

        await self.ensure_group()
        _, messages, _ = await self._redis_backend.auto_claim_stream_entries(
            config.stream_task.stream_name,
            config.stream_task.consumer_group,
            self._consumer_name,
            min_idle_ms=config.stream_task.claim_idle_ms,
            start_id="0-0",
            count=config.stream_task.read_count,
        )
        return self._deserialize_messages(config.stream_task.stream_name, messages)

    async def read_messages(self) -> list[StreamMessage]:
        """读取新任务消息。"""

        if not self.enabled:
            return []

        await self.ensure_group()
        result = await self._redis_backend.read_group_stream(
            config.stream_task.consumer_group,
            self._consumer_name,
            {config.stream_task.stream_name: ">"},
            count=config.stream_task.read_count,
            block_ms=config.stream_task.block_ms,
        )
        messages: list[StreamMessage] = []
        for raw_stream_name, raw_entries in result or []:
            logical_stream_name = self._strip_prefix(raw_stream_name)
            messages.extend(self._deserialize_messages(logical_stream_name, raw_entries))
        return messages

    async def process_messages(
        self,
        handler: TaskHandler,
    ) -> int:
        """处理一批消息并返回成功 ACK 数。"""

        if not self.enabled:
            return 0

        messages = await self.claim_stale_messages()
        messages.extend(await self.read_messages())
        if not messages:
            return 0

        acknowledged = 0
        for message in messages:
            if await self._handle_single_message(message, handler):
                acknowledged += 1
        return acknowledged

    async def _handle_single_message(
        self,
        message: StreamMessage,
        handler: TaskHandler,
    ) -> bool:
        """处理单条消息。"""

        envelope = message.envelope
        logger.info(
            "开始处理异步任务: task_id={}, task_type={}, consumer_name={}",
            envelope.task_id,
            envelope.task_type,
            self._consumer_name,
        )
        try:
            await self._producer.mark_status(
                envelope.task_id,
                envelope.to_status_payload(status="processing"),
            )
            result_payload = await handler(envelope)
            completed_payload = envelope.to_status_payload(status="completed")
            if result_payload:
                completed_payload["result"] = result_payload
            await self._producer.mark_status(envelope.task_id, completed_payload)
            await self._redis_backend.acknowledge_stream_entries(
                message.stream_name,
                config.stream_task.consumer_group,
                [message.entry_id],
            )
            logger.info(
                "异步任务处理完成: task_id={}, task_type={}",
                envelope.task_id,
                envelope.task_type,
            )
            return True
        except Exception as exc:
            logger.exception(
                "异步任务处理失败: task_id={}, task_type={}, retry_count={}, error={}",
                envelope.task_id,
                envelope.task_type,
                envelope.retry_count,
                exc,
            )
            return await self._handle_failure(message, error_message=str(exc))

    async def _handle_failure(
        self,
        message: StreamMessage,
        *,
        error_message: str,
    ) -> bool:
        """统一处理失败、重试与死信。"""

        envelope = message.envelope
        next_retry_count = envelope.retry_count + 1
        if next_retry_count > envelope.max_retries:
            failed_payload = envelope.to_status_payload(
                status="failed",
                error_message=error_message,
            )
            failed_payload["failure_code"] = int(ErrorCode.ASYNC_TASK_CONSUME_FAILED)
            failed_payload["failed_at"] = _utc_now_iso()
            await self._producer.mark_status(envelope.task_id, failed_payload)
            await self._redis_backend.add_stream_entry(
                config.stream_task.dead_letter_stream_name,
                {
                    **envelope.to_stream_fields(),
                    "retry_count": str(next_retry_count),
                    "error_message": error_message,
                },
                maxlen=config.stream_task.maxlen,
            )
            await self._redis_backend.acknowledge_stream_entries(
                message.stream_name,
                config.stream_task.consumer_group,
                [message.entry_id],
            )
            return True

        retry_envelope = StreamTaskEnvelope(
            task_id=envelope.task_id,
            task_type=envelope.task_type,
            payload=dict(envelope.payload),
            retry_count=next_retry_count,
            max_retries=envelope.max_retries,
            created_at=envelope.created_at,
            trace_context=dict(envelope.trace_context),
        )
        await self._producer.mark_status(
            retry_envelope.task_id,
            retry_envelope.to_status_payload(
                status="retrying",
                error_message=error_message,
            ),
        )
        await self._redis_backend.add_stream_entry(
            config.stream_task.stream_name,
            retry_envelope.to_stream_fields(),
            maxlen=config.stream_task.maxlen,
        )
        await self._redis_backend.acknowledge_stream_entries(
            message.stream_name,
            config.stream_task.consumer_group,
            [message.entry_id],
        )
        return True

    @staticmethod
    def _deserialize_messages(stream_name: str, entries: list[Any]) -> list[StreamMessage]:
        """把 Redis 原始消息转成统一结构。"""

        messages: list[StreamMessage] = []
        for entry_id, fields in entries or []:
            payload = json.loads(str(fields.get("payload", "{}")))
            trace_context = json.loads(str(fields.get("trace_context", "{}")))
            envelope = StreamTaskEnvelope(
                task_id=str(fields.get("task_id", "")),
                task_type=str(fields.get("task_type", "")),
                payload=payload,
                retry_count=int(fields.get("retry_count", 0)),
                max_retries=int(fields.get("max_retries", config.stream_task.max_retries)),
                created_at=str(fields.get("created_at", _utc_now_iso())),
                trace_context=trace_context,
            )
            messages.append(
                StreamMessage(
                    stream_name=stream_name,
                    entry_id=str(entry_id),
                    envelope=envelope,
                )
            )
        return messages

    @staticmethod
    def _strip_prefix(stream_name: str) -> str:
        """去除 Redis key prefix，恢复逻辑 stream 名。"""

        prefix = f"{config.redis.key_prefix}:"
        if stream_name.startswith(prefix):
            return stream_name[len(prefix) :]
        return stream_name


__all__ = [
    "StreamConsumer",
    "StreamMessage",
]
