"""Async Redis client infrastructure."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import config


class RedisClientManager:
    """Manage the shared async Redis client."""

    def __init__(self) -> None:
        self._client: Redis | None = None

    @property
    def enabled(self) -> bool:
        """Whether Redis integration is enabled."""

        return config.redis.enabled

    def _ensure_client(self) -> None:
        """Create the Redis client lazily."""

        if self._client is not None:
            return

        self._client = Redis.from_url(
            config.redis.url,
            decode_responses=config.redis.decode_responses,
            socket_timeout=config.redis.socket_timeout_seconds,
            socket_connect_timeout=config.redis.socket_timeout_seconds,
        )

    async def connect(self) -> bool:
        """Connect to Redis and verify the connection."""

        if not self.enabled:
            logger.info("Redis 未启用，跳过初始化")
            return False

        self._ensure_client()
        await self.ping()
        logger.info("Redis 连接成功")
        return True

    def get_client(self) -> Redis:
        """Return the shared Redis client."""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("Redis 客户端未初始化")

        return self._client

    async def ping(self) -> bool:
        """Return whether Redis responds to ping."""

        client = self.get_client()
        return bool(await client.ping())

    async def health_check(self) -> bool:
        """Return whether Redis is healthy."""

        if not self.enabled:
            return False

        try:
            return await self.ping()
        except Exception as exc:
            logger.warning("Redis 健康检查失败: {}", exc)
            return False

    async def get_value(self, key: str) -> Any:
        """Fetch a single value from Redis."""

        return await self.get_client().get(self._build_key(key))

    async def set_value(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Set a value in Redis with an optional TTL."""

        ttl = ttl_seconds if ttl_seconds is not None else config.redis.default_ttl_seconds
        result = await self.get_client().set(self._build_key(key), value, ex=ttl)
        return bool(result)

    async def delete_key(self, key: str) -> int:
        """Delete a value from Redis."""

        return int(await self.get_client().delete(self._build_key(key)))

    async def add_stream_entry(
        self,
        stream_name: str,
        data: dict[str, Any],
        maxlen: int | None = None,
    ) -> str:
        """Append an entry to a Redis Stream."""

        return await self.get_client().xadd(
            self._build_key(stream_name),
            fields=data,
            maxlen=maxlen,
            approximate=True if maxlen else False,
        )

    async def create_consumer_group(
        self,
        stream_name: str,
        group_name: str,
        *,
        start_id: str = "$",
        mkstream: bool = True,
    ) -> bool:
        """创建 Redis Stream consumer group。"""

        try:
            await self.get_client().xgroup_create(
                name=self._build_key(stream_name),
                groupname=group_name,
                id=start_id,
                mkstream=mkstream,
            )
            return True
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return False
            raise

    async def read_group_stream(
        self,
        group_name: str,
        consumer_name: str,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block_ms: int | None = None,
    ) -> Any:
        """以 consumer group 方式读取 Stream。"""

        namespaced_streams = {
            self._build_key(name): last_id for name, last_id in streams.items()
        }
        return await self.get_client().xreadgroup(
            groupname=group_name,
            consumername=consumer_name,
            streams=namespaced_streams,
            count=count,
            block=block_ms,
        )

    async def acknowledge_stream_entries(
        self,
        stream_name: str,
        group_name: str,
        entry_ids: Iterable[str],
    ) -> int:
        """确认处理完成的 Stream 消息。"""

        entry_id_list = list(entry_ids)
        if not entry_id_list:
            return 0
        return int(
            await self.get_client().xack(
                self._build_key(stream_name),
                group_name,
                *entry_id_list,
            )
        )

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
        """认领长时间未处理的 pending Stream 消息。"""

        return await self.get_client().xautoclaim(
            name=self._build_key(stream_name),
            groupname=group_name,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id=start_id,
            count=count,
        )

    async def get_group_info(self, stream_name: str) -> list[dict[str, Any]]:
        """查询 Stream group 信息。"""

        return list(await self.get_client().xinfo_groups(self._build_key(stream_name)))

    async def read_stream(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block_ms: int | None = None,
    ) -> Any:
        """Read from one or more Redis Streams."""

        namespaced_streams = {
            self._build_key(name): last_id for name, last_id in streams.items()
        }
        return await self.get_client().xread(
            streams=namespaced_streams,
            count=count,
            block=block_ms,
        )

    async def eval_script(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        """Execute a Lua script in Redis."""

        return await self.get_client().eval(script, numkeys, *keys_and_args)

    async def script_load(self, script: str) -> str:
        """加载 Lua 脚本并返回 SHA。"""

        return await self.get_client().script_load(script)

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: Any) -> Any:
        """按 SHA 执行 Lua 脚本。"""

        return await self.get_client().evalsha(sha, numkeys, *keys_and_args)

    async def close(self) -> None:
        """Close the Redis client."""

        if self._client is None:
            return

        await self._client.aclose()
        self._client = None
        logger.info("Redis 客户端已关闭")

    @staticmethod
    def _build_key(key: str) -> str:
        """Apply the configured key prefix to a logical key."""

        return f"{config.redis.key_prefix}:{key}"

    def build_key(self, key: str) -> str:
        """公开的带前缀 key 构造器。"""

        return self._build_key(key)


redis_manager = RedisClientManager()
