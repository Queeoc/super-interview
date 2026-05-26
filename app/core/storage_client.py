"""Storage abstraction for local files and future object storage providers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from app.config import config


class StorageClient(ABC):
    """Abstract storage interface used by application services."""

    @abstractmethod
    async def connect(self) -> bool:
        """Prepare the storage backend for use."""

    @abstractmethod
    async def upload_file(self, object_key: str, content: bytes) -> str:
        """Store a file and return its storage URI or path."""

    @abstractmethod
    async def download_file(self, object_key: str) -> bytes:
        """Load a file from storage."""

    @abstractmethod
    async def delete_file(self, object_key: str) -> bool:
        """Delete a file from storage."""

    @abstractmethod
    def get_public_url(self, object_key: str) -> str:
        """Return the public URL or canonical path for an object."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return whether the storage backend is healthy."""


class LocalStorageClient(StorageClient):
    """Local filesystem storage used in the initial refactor phase."""

    def __init__(self, base_dir: Path, temp_dir: Path, public_base_url: str = "") -> None:
        self.base_dir = base_dir
        self.temp_dir = temp_dir
        self.public_base_url = public_base_url.rstrip("/")

    async def connect(self) -> bool:
        """Create required directories if they do not exist."""

        await asyncio.to_thread(self.base_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.temp_dir.mkdir, parents=True, exist_ok=True)
        logger.info("本地存储已就绪: base_dir={}", self.base_dir)
        return True

    async def upload_file(self, object_key: str, content: bytes) -> str:
        """Persist a file under the local base directory."""

        target_path = self.resolve_path(object_key)
        await asyncio.to_thread(target_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target_path.write_bytes, content)
        return str(target_path)

    async def download_file(self, object_key: str) -> bytes:
        """Load a file from the local storage directory."""

        target_path = self.resolve_path(object_key)
        return await asyncio.to_thread(target_path.read_bytes)

    async def delete_file(self, object_key: str) -> bool:
        """Delete a file from the local storage directory."""

        target_path = self.resolve_path(object_key)
        if not target_path.exists():
            return False

        await asyncio.to_thread(target_path.unlink)
        return True

    def get_public_url(self, object_key: str) -> str:
        """Return a public URL or fallback path for the stored file."""

        if self.public_base_url:
            return f"{self.public_base_url}/{object_key.lstrip('/')}"
        return str(self.resolve_path(object_key))

    async def health_check(self) -> bool:
        """Verify that the storage directories are present."""

        return self.base_dir.exists() and self.temp_dir.exists()

    def resolve_path(self, object_key: str) -> Path:
        """Resolve and validate a local object path."""

        normalized = object_key.lstrip("/").replace("\\", "/")
        target_path = (self.base_dir / normalized).resolve()

        try:
            target_path.relative_to(self.base_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"非法存储路径: {object_key}") from exc

        return target_path


class StorageClientManager:
    """Select and expose the configured storage backend."""

    def __init__(self) -> None:
        self._client: StorageClient | None = None

    def _ensure_client(self) -> None:
        """Create the configured storage client lazily."""

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
        """Connect the configured storage backend."""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("存储客户端未初始化")

        return await self._client.connect()

    def get_client(self) -> StorageClient:
        """Return the configured storage client."""

        self._ensure_client()

        if self._client is None:
            raise RuntimeError("存储客户端未初始化")

        return self._client

    async def health_check(self) -> bool:
        """Return whether the configured storage backend is healthy."""

        self._ensure_client()

        if self._client is None:
            return False

        return await self._client.health_check()

    def resolve_local_path(self, object_key: str) -> Path:
        """Resolve the path for the local storage backend."""

        client = self.get_client()

        if not isinstance(client, LocalStorageClient):
            raise RuntimeError("当前存储后端不支持本地路径解析")

        return client.resolve_path(object_key)


storage_manager = StorageClientManager()
