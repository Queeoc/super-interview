"""Redis 基础设施组件。

负责统一管理项目中的异步 Redis 客户端，并封装三类常见能力：
- 普通 KV 读写
- Redis Stream 异步任务相关操作
- Lua 脚本加载与执行
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import config


class RedisClientManager:
    """Redis 异步客户端管理器。

    可以把它理解成项目访问 Redis 的统一入口：
    - 启动阶段负责初始化 Redis 客户端
    - 运行期间向各个模块提供共享连接
    - 对上层隐藏 key 前缀、Stream 和脚本执行等底层细节
    """

    def __init__(self) -> None:
        self._client: Redis | None = None

    @property
    def enabled(self) -> bool:
        """返回当前是否启用了 Redis 能力。"""

        return config.redis.enabled

    def _ensure_client(self) -> None:
        """按需延迟创建 Redis 客户端。

        如果当前进程还没有真正用到 Redis，就先不创建底层连接对象；
        第一次访问 Redis 时，再根据配置初始化客户端。
        """

        if self._client is not None:
            return

        self._client = Redis.from_url(
            config.redis.url,
            decode_responses=config.redis.decode_responses,
            socket_timeout=config.redis.socket_timeout_seconds,
            socket_connect_timeout=config.redis.socket_timeout_seconds,
        )

    async def connect(self) -> bool:
        """初始化 Redis 连接并执行一次探活。

        返回值语义：
        - True：Redis 已启用且连接成功
        - False：Redis 未启用，因此跳过初始化
        """

        if not self.enabled:
            logger.info("Redis 未启用，跳过初始化")
            return False

        self._ensure_client()
        await self.ping()
        logger.info("Redis 连接成功")
        return True

    def get_client(self) -> Redis:
        """返回全局共享的 Redis 客户端实例。"""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("Redis 客户端未初始化")

        return self._client

    async def ping(self) -> bool:
        """通过 `PING` 检查 Redis 是否有响应。"""

        client = self.get_client()
        return bool(await client.ping())

    async def health_check(self) -> bool:
        """执行 Redis 健康检查，返回当前是否可达。"""

        if not self.enabled:
            return False

        try:
            return await self.ping()
        except Exception as exc:
            logger.warning("Redis 健康检查失败: {}", exc)
            return False

    async def get_value(self, key: str) -> Any:
        """读取单个 Redis 键值。

        这里传入的是“逻辑 key”，方法内部会自动补上统一前缀。
        """

        return await self.get_client().get(self._build_key(key))

    async def set_value(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        """写入单个 Redis 键值，并支持可选过期时间。

        如果调用方没有显式传入 TTL，就使用项目默认过期时间。
        这适合会话缓存、异步任务状态、短期幂等信息等场景。
        """

        ttl = ttl_seconds if ttl_seconds is not None else config.redis.default_ttl_seconds
        result = await self.get_client().set(self._build_key(key), value, ex=ttl)
        return bool(result)

    async def delete_key(self, key: str) -> int:
        """删除指定 Redis 键。"""

        return int(await self.get_client().delete(self._build_key(key)))

    async def add_stream_entry(
        self,
        stream_name: str,
        data: dict[str, Any],
        maxlen: int | None = None,
    ) -> str:
        """向 Redis Stream 追加一条消息。

        这个方法主要服务于异步任务场景，
        相当于把一条任务投递到 Redis 的消息流水线上。
        """

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
        """创建 Redis Stream 的 consumer group。

        返回值语义：
        - True：本次成功创建
        - False：group 已存在，因此无需重复创建
        """

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
        """以 consumer group 方式读取 Stream 消息。

        适合多个消费者协作处理任务的场景，
        可以避免同一条任务被多个消费者重复消费。
        """

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
        """确认指定 Stream 消息已经处理完成。

        ACK 之后，这些消息就不会继续留在当前消费组的 pending 列表里。
        """

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
        """认领长时间未处理的 pending Stream 消息。

        常用于消费者异常退出后的补偿处理，
        避免任务永远卡在某个失效消费者手里。
        """

        return await self.get_client().xautoclaim(
            name=self._build_key(stream_name),
            groupname=group_name,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id=start_id,
            count=count,
        )

    async def get_group_info(self, stream_name: str) -> list[dict[str, Any]]:
        """查询指定 Stream 的消费组信息。"""

        return list(await self.get_client().xinfo_groups(self._build_key(stream_name)))

    async def read_stream(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block_ms: int | None = None,
    ) -> Any:
        """直接读取一个或多个 Redis Stream。

        这是一种不依赖 consumer group 的读取方式，
        更适合简单监听或调试场景。
        """

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
        """在 Redis 中直接执行 Lua 脚本。"""

        return await self.get_client().eval(script, numkeys, *keys_and_args)

    async def script_load(self, script: str) -> str:
        """加载 Lua 脚本，并返回脚本对应的 SHA。"""

        return await self.get_client().script_load(script)

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: Any) -> Any:
        """根据脚本 SHA 执行已加载的 Lua 脚本。"""

        return await self.get_client().evalsha(sha, numkeys, *keys_and_args)

    async def close(self) -> None:
        """关闭 Redis 客户端并释放连接资源。"""

        if self._client is None:
            return

        await self._client.aclose()
        self._client = None
        logger.info("Redis 客户端已关闭")

    @staticmethod
    def _build_key(key: str) -> str:
        """为逻辑 key 统一补上项目级前缀。

        这样可以避免不同项目或不同环境之间的 Redis key 冲突。
        """

        return f"{config.redis.key_prefix}:{key}"

    def build_key(self, key: str) -> str:
        """公开的 key 前缀拼装方法。"""

        return self._build_key(key)


redis_manager = RedisClientManager()
