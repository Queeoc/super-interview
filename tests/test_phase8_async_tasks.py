"""Phase 8 Redis Stream 异步任务测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.stream_consumer import StreamConsumer
from app.core.stream_producer import StreamProducer, StreamTaskEnvelope


class _FakeStreamRedisBackend:
    """支持基础 Stream 能力的内存 Redis 假对象。"""

    def __init__(self) -> None:
        self.enabled = True
        self.kv: dict[str, str] = {}
        self.stream_entries: list[tuple[str, dict[str, Any]]] = []
        self.dead_letter_entries: list[tuple[str, dict[str, Any]]] = []
        self.group_created = False
        self.acked: list[str] = []
        self.retry_queue: list[tuple[str, dict[str, Any]]] = []

    async def set_value(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def get_value(self, key: str) -> str | None:
        return self.kv.get(key)

    async def add_stream_entry(self, stream_name: str, data: dict[str, Any], maxlen: int | None = None) -> str:
        entry_id = f"{len(self.stream_entries) + len(self.dead_letter_entries) + 1}-0"
        if "dead_letter" in stream_name:
            self.dead_letter_entries.append((entry_id, data))
        else:
            self.stream_entries.append((entry_id, data))
        return entry_id

    async def create_consumer_group(self, stream_name: str, group_name: str, *, start_id: str = "$", mkstream: bool = True) -> bool:
        self.group_created = True
        return True

    async def auto_claim_stream_entries(
        self,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
        count: int | None = None,
    ) -> tuple[str, list[Any], list[str]]:
        messages = list(self.retry_queue)
        self.retry_queue.clear()
        return "0-0", messages, []

    async def read_group_stream(
        self,
        group_name: str,
        consumer_name: str,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block_ms: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, Any]]]]]:
        if not self.stream_entries:
            return []
        stream_name = next(iter(streams.keys()))
        messages = list(self.stream_entries)
        self.stream_entries.clear()
        return [(stream_name, messages)]

    async def acknowledge_stream_entries(self, stream_name: str, group_name: str, entry_ids: list[str]) -> int:
        self.acked.extend(entry_ids)
        return len(entry_ids)


@pytest.mark.asyncio
async def test_stream_producer_publishes_task_and_initial_status() -> None:
    """Producer 应入队并写入 pending 状态。"""

    redis_backend = _FakeStreamRedisBackend()
    producer = StreamProducer(redis_backend=redis_backend)
    envelope = StreamTaskEnvelope.new(
        task_type="interview_report_generate",
        payload={"session_id": "session-1"},
        trace_context={"session_id": "session-1"},
    )

    entry_id = await producer.publish(envelope)
    status_payload = await producer.get_status(envelope.task_id)

    assert entry_id.endswith("-0")
    assert status_payload is not None
    assert status_payload["status"] == "pending"
    assert redis_backend.stream_entries[0][1]["task_type"] == "interview_report_generate"


@pytest.mark.asyncio
async def test_stream_consumer_acknowledges_successful_message() -> None:
    """Consumer 成功处理后应 ACK 并标记完成。"""

    redis_backend = _FakeStreamRedisBackend()
    producer = StreamProducer(redis_backend=redis_backend)
    consumer = StreamConsumer(redis_backend=redis_backend, producer=producer, consumer_name="test")
    envelope = StreamTaskEnvelope.new(
        task_type="interview_report_generate",
        payload={"session_id": "session-1"},
    )
    await producer.publish(envelope)

    async def handler(task: StreamTaskEnvelope) -> dict[str, Any]:
        return {"session_id": task.payload["session_id"], "report_status": "generated"}

    acknowledged = await consumer.process_messages(handler)
    status_payload = await producer.get_status(envelope.task_id)

    assert acknowledged == 1
    assert redis_backend.acked
    assert status_payload is not None
    assert status_payload["status"] == "completed"
    assert status_payload["result"]["report_status"] == "generated"


@pytest.mark.asyncio
async def test_stream_consumer_retries_and_moves_failed_task_to_dead_letter() -> None:
    """Consumer 多次失败后应写入 dead letter 并标记 failed。"""

    redis_backend = _FakeStreamRedisBackend()
    producer = StreamProducer(redis_backend=redis_backend)
    consumer = StreamConsumer(redis_backend=redis_backend, producer=producer, consumer_name="test")
    envelope = StreamTaskEnvelope.new(
        task_type="interview_report_generate",
        payload={"session_id": "session-1"},
        max_retries=1,
    )
    await producer.publish(envelope)

    async def handler(task: StreamTaskEnvelope) -> dict[str, Any]:
        raise RuntimeError("boom")

    first_ack = await consumer.process_messages(handler)
    assert first_ack == 1
    assert redis_backend.acked

    retry_entry_id, retry_fields = redis_backend.stream_entries[-1]
    redis_backend.retry_queue.append((retry_entry_id, retry_fields))
    redis_backend.stream_entries.clear()

    second_ack = await consumer.process_messages(handler)
    status_payload = await producer.get_status(envelope.task_id)

    assert second_ack == 1
    assert status_payload is not None
    assert status_payload["status"] == "failed"
    assert redis_backend.dead_letter_entries
    dead_letter_payload = redis_backend.dead_letter_entries[0][1]
    assert dead_letter_payload["task_id"] == envelope.task_id
