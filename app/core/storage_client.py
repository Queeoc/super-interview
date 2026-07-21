"""存储基础设施组件。

负责为项目提供统一的文件存储抽象，当前默认使用本地文件系统，
后续可以平滑扩展到 MinIO、S3 等对象存储，而不需要让业务层改动调用方式。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from app.config import config


class StorageClient(ABC):
    """存储客户端抽象接口。

    业务层只依赖这组统一能力，而不关心底层到底是本地磁盘、
    MinIO 还是云对象存储。
    """

    @abstractmethod
    async def connect(self) -> bool:
        """初始化当前存储后端，使其进入可用状态。"""

    @abstractmethod
    async def upload_file(self, object_key: str, content: bytes) -> str:
        """保存文件内容，并返回存储路径或存储 URI。"""

    @abstractmethod
    async def download_file(self, object_key: str) -> bytes:
        """按对象键读取文件内容。"""

    @abstractmethod
    async def delete_file(self, object_key: str) -> bool:
        """删除指定对象。"""

    @abstractmethod
    def get_public_url(self, object_key: str) -> str:
        """返回对象的可访问地址或规范路径。"""

    @abstractmethod
    async def health_check(self) -> bool:
        """返回当前存储后端是否健康可用。"""


class LocalStorageClient(StorageClient):
    """本地文件系统存储实现。

    当前重构阶段先使用本地磁盘目录保存上传文件，
    让上层先稳定依赖统一接口，后续再替换为对象存储实现。
    """

    def __init__(self, base_dir: Path, temp_dir: Path, public_base_url: str = "") -> None:
        self.base_dir = base_dir
        self.temp_dir = temp_dir
        self.public_base_url = public_base_url.rstrip("/")

    async def connect(self) -> bool:
        """确保业务目录和临时目录存在。"""

        await asyncio.to_thread(self.base_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.temp_dir.mkdir, parents=True, exist_ok=True)
        logger.info("本地存储已就绪: base_dir={}", self.base_dir)
        return True

    async def upload_file(self, object_key: str, content: bytes) -> str:
        """将文件写入本地存储目录。"""

        target_path = self.resolve_path(object_key)
        await asyncio.to_thread(target_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target_path.write_bytes, content)
        return str(target_path)

    async def download_file(self, object_key: str) -> bytes:
        """从本地存储目录读取文件内容。"""

        target_path = self.resolve_path(object_key)
        return await asyncio.to_thread(target_path.read_bytes)

    async def delete_file(self, object_key: str) -> bool:
        """删除本地存储目录中的文件。"""

        target_path = self.resolve_path(object_key)
        if not target_path.exists():
            return False

        await asyncio.to_thread(target_path.unlink)
        return True

    def get_public_url(self, object_key: str) -> str:
        """返回文件的对外访问地址，若未配置则返回本地路径。"""

        if self.public_base_url:
            return f"{self.public_base_url}/{object_key.lstrip('/')}"
        return str(self.resolve_path(object_key))

    async def health_check(self) -> bool:
        """检查本地存储所需目录是否存在。"""

        return self.base_dir.exists() and self.temp_dir.exists()

    def resolve_path(self, object_key: str) -> Path:
        """将对象键解析为本地路径，并校验路径合法性。

        这里会显式限制目标路径必须位于 `base_dir` 之下，
        防止通过 `../` 等路径穿越方式访问到存储目录外部。
        """

        normalized = object_key.lstrip("/").replace("\\", "/")
        target_path = (self.base_dir / normalized).resolve()

        try:
            target_path.relative_to(self.base_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"非法存储路径: {object_key}") from exc

        return target_path


class StorageClientManager:
    """存储客户端管理器。

    根据配置选择具体的存储实现，并向项目其他模块暴露统一访问入口。
    """

    def __init__(self) -> None:
        self._client: StorageClient | None = None

    def _ensure_client(self) -> None:
        """按需创建配置指定的存储客户端。"""

        if self._client is not None:
            return

        provider = config.storage.provider.lower()

        if provider == "local":
            self._client = LocalStorageClient(
                base_dir=config.storage.resolved_base_dir,
                temp_dir=config.storage.resolved_temp_dir,
                public_base_url=config.storage.public_base_url,
            )
            return

        raise NotImplementedError(f"暂不支持的存储提供方: {provider}")

    async def connect(self) -> bool:
        """初始化当前配置的存储后端。"""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("存储客户端未初始化")

        return await self._client.connect()

    def get_client(self) -> StorageClient:
        """返回当前配置对应的存储客户端。"""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("存储客户端未初始化")

        return self._client

    async def health_check(self) -> bool:
        """检查当前配置的存储后端是否健康。"""

        self._ensure_client()

        if self._client is None:
            return False

        return await self._client.health_check()

    def resolve_local_path(self, object_key: str) -> Path:
        """在本地存储模式下，将对象键解析成真实文件路径。"""

        client = self.get_client()

        if not isinstance(client, LocalStorageClient):
            raise RuntimeError("当前存储后端不支持本地路径解析")

        return client.resolve_path(object_key)


storage_manager = StorageClientManager()
