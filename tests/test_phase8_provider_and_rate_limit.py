"""Phase 8 Provider 运行时、限流与健康检查测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import health as health_api
from app.api import provider as provider_api
from app.core.database import get_db_session
from app.core.llm_factory import llm_factory
from app.core.provider_runtime import (
    ProviderRuntimeRecord,
    provider_runtime_registry,
)
from app.middleware.error_handler import register_exception_handlers
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.visitor_context import VisitorContextMiddleware
from app.models.provider import (
    LlmProviderCreateRequest,
    LlmProviderEntity,
    LlmProviderStatus,
)
from app.services.provider_failover_service import ProviderFailoverService, provider_failover_service
from app.services.provider_service import ProviderService, _mask_credentials


@dataclass
class _FakeProviderSession:
    committed: int = 0

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        return None


@dataclass
class _InMemoryProviderRepository:
    providers: dict[str, LlmProviderEntity] = field(default_factory=dict)

    async def add_provider(self, entity: LlmProviderEntity) -> LlmProviderEntity:
        self.providers[entity.id] = entity
        return entity

    async def upsert_provider(self, entity: LlmProviderEntity) -> LlmProviderEntity:
        self.providers[entity.id] = entity
        return entity

    async def get_provider(self, provider_id: str) -> LlmProviderEntity | None:
        return self.providers.get(provider_id)

    async def get_provider_by_code(self, provider_code: str) -> LlmProviderEntity | None:
        for provider in self.providers.values():
            if provider.provider_code == provider_code:
                return provider
        return None

    async def list_providers(self, status: str | None = None, limit: int = 50) -> list[LlmProviderEntity]:
        items = list(self.providers.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.is_default, item.updated_at), reverse=True)
        return items[:limit]

    async def list_available_providers(
        self,
        *,
        exclude_provider_codes: set[str] | None = None,
        limit: int = 500,
    ) -> list[LlmProviderEntity]:
        items = [
            item
            for item in self.providers.values()
            if item.status == LlmProviderStatus.ACTIVE.value
        ]
        if exclude_provider_codes:
            items = [
                item for item in items if item.provider_code not in exclude_provider_codes
            ]
        items.sort(key=lambda item: (item.is_default, item.updated_at), reverse=True)
        return items[:limit]

    async def get_default_provider(self) -> LlmProviderEntity | None:
        for provider in self.providers.values():
            if provider.is_default:
                return provider
        return None

    async def set_default_provider(self, provider_id: str) -> None:
        for provider in self.providers.values():
            provider.is_default = provider.id == provider_id

    async def delete_provider(self, provider_id: str) -> None:
        self.providers.pop(provider_id, None)


class _FakeChatModel:
    async def ainvoke(self, prompt_text: str) -> str:
        return "OK"


class _FakeAsyncSessionContext:
    def __init__(self, session: _FakeProviderSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeProviderSession:
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _QuotaAwareChatModel:
    def __init__(self, provider_code: str, calls: list[str]) -> None:
        self._provider_code = provider_code
        self._calls = calls

    async def ainvoke(self, prompt_text: str) -> str:
        self._calls.append(self._provider_code)
        if self._provider_code == "qwen-default":
            raise RuntimeError("quota exceeded for trial model")
        return f"ok-from-{self._provider_code}"


class _ChatOpenAISpy:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeRedisForRateLimit:
    def __init__(self) -> None:
        self.enabled = True
        self.calls: dict[str, int] = {}

    async def script_load(self, script: str) -> str:
        return "sha-1"

    def build_key(self, key: str) -> str:
        return key

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: Any) -> list[int]:
        key = str(keys_and_args[0])
        limit = int(keys_and_args[2])
        current_count = self.calls.get(key, 0)
        if current_count >= limit:
            return [0, 10]
        self.calls[key] = current_count + 1
        return [1, 0]

    async def eval_script(self, script: str, numkeys: int, *keys_and_args: Any) -> list[int]:
        return await self.evalsha("sha", numkeys, *keys_and_args)

    async def health_check(self) -> bool:
        return True


def test_mask_credentials_hides_sensitive_values() -> None:
    """Provider 凭据返回给接口前必须脱敏。"""

    masked = _mask_credentials({"api_key": "sk-123456", "secret": "abcd"})

    assert masked[0].configured is True
    assert masked[0].masked_value.startswith("sk")
    assert "***" in masked[0].masked_value
    assert masked[1].masked_value == "****"


@pytest.mark.asyncio
async def test_provider_service_can_create_and_set_runtime_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider 服务应能创建配置并刷新运行时默认 Provider。"""

    provider_runtime_registry.reset()
    session = _FakeProviderSession()
    repository = _InMemoryProviderRepository()
    service = ProviderService(repository_factory=lambda _session: repository)

    request = LlmProviderCreateRequest(
        provider_code="openai-main",
        provider_name="OpenAI Main",
        api_base="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        credentials_json={"api_key": "sk-1234567890"},
        is_default=True,
    )

    result = await service.create_provider(session, request)  # type: ignore[arg-type]

    assert result.provider_code == "openai-main"
    runtime = await service.get_runtime_snapshot()
    assert runtime.provider_code == "openai-main"
    assert runtime.source == "database"


@pytest.mark.asyncio
async def test_provider_service_test_connection_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider 连通性测试成功后应更新状态。"""

    provider_runtime_registry.reset()
    session = _FakeProviderSession()
    repository = _InMemoryProviderRepository()
    entity = LlmProviderEntity(
        provider_code="dashscope-main",
        provider_name="DashScope Main",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-max",
        credentials_json={"api_key": "test-key"},
        status=LlmProviderStatus.ACTIVE.value,
        is_default=True,
    )
    repository.providers[entity.id] = entity
    monkeypatch.setattr(llm_factory, "create_chat_model", lambda **_: _FakeChatModel())
    service = ProviderService(repository_factory=lambda _session: repository)

    result = await service.test_connection(session, entity.id)  # type: ignore[arg-type]

    assert result.status == "active"
    assert result.last_error_message is None


@pytest.mark.asyncio
async def test_provider_failover_service_switches_default_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """额度耗尽后应禁用当前 Provider，并切换到下一个可用默认项。"""

    provider_runtime_registry.reset()
    session = _FakeProviderSession()
    repository = _InMemoryProviderRepository()
    current = LlmProviderEntity(
        provider_code="qwen-default",
        provider_name="Qwen Default",
        credentials_json={"api_key": "sk-default"},
        status=LlmProviderStatus.ACTIVE.value,
        is_default=True,
    )
    backup = LlmProviderEntity(
        provider_code="qwen-backup",
        provider_name="Qwen Backup",
        credentials_json={"api_key": "sk-backup"},
        status=LlmProviderStatus.ACTIVE.value,
        is_default=False,
    )
    repository.providers[current.id] = current
    repository.providers[backup.id] = backup
    service = ProviderService(repository_factory=lambda _session: repository)
    await service.refresh_runtime_registry(session)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "app.services.provider_failover_service.database_manager.enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.provider_failover_service.database_manager.get_session_factory",
        lambda: lambda: _FakeAsyncSessionContext(session),
    )

    failover_service = ProviderFailoverService(repository_factory=lambda _session: repository)
    next_provider_code = await failover_service.disable_and_failover(
        "qwen-default",
        error_message="quota exceeded",
    )

    assert next_provider_code == "qwen-backup"
    assert current.status == LlmProviderStatus.INACTIVE.value
    assert current.is_default is False
    assert backup.is_default is True
    runtime = provider_runtime_registry.get_runtime_provider()
    assert runtime is not None
    assert runtime.provider_code == "qwen-backup"


@pytest.mark.asyncio
async def test_llm_factory_retries_with_next_provider_after_quota_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 工厂应在额度错误后自动切换到下一个 Provider 重试。"""

    provider_runtime_registry.reset()
    provider_runtime_registry.load_runtime_providers(
        [
            ProviderRuntimeRecord(
                provider_code="qwen-default",
                provider_name="Qwen Default",
                provider_type="chat",
                api_base="https://example.com/v1",
                model_name="qwen-plus",
                api_key="sk-default",
                source="database",
                status="active",
                is_default=True,
            ),
            ProviderRuntimeRecord(
                provider_code="qwen-backup",
                provider_name="Qwen Backup",
                provider_type="chat",
                api_base="https://example.com/v1",
                model_name="qwen-max",
                api_key="sk-backup",
                source="database",
                status="active",
                is_default=False,
            ),
        ],
        default_provider_code="qwen-default",
    )
    calls: list[str] = []

    monkeypatch.setattr(
        llm_factory,
        "_build_chat_model",
        lambda *, provider_code, **kwargs: _QuotaAwareChatModel(provider_code, calls),
    )
    monkeypatch.setattr(
        provider_failover_service,
        "is_quota_exhausted_error",
        lambda exc: "quota" in str(exc).lower(),
    )

    async def _fake_disable_and_failover(provider_code: str, *, error_message: str) -> str:
        assert provider_code == "qwen-default"
        assert "quota" in error_message.lower()
        return "qwen-backup"

    monkeypatch.setattr(
        provider_failover_service,
        "disable_and_failover",
        _fake_disable_and_failover,
    )

    llm = llm_factory.create_chat_model(streaming=False)
    result = await llm.ainvoke("hello")

    assert result == "ok-from-qwen-backup"
    assert calls == ["qwen-default", "qwen-backup"]


def test_llm_factory_falls_back_to_env_runtime() -> None:
    """未加载数据库 Provider 时，LLM 工厂应回退 env runtime。"""

    provider_runtime_registry.reset()
    provider = llm_factory.ensure_runtime_provider()

    assert provider.source == "env"
    assert provider.provider_code


def test_llm_factory_forces_disable_thinking_for_non_streaming_dashscope_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DashScope/Qwen 非流式调用应自动关闭 thinking，避免结构化输出报参数错误。"""

    provider_runtime_registry.reset()
    provider_runtime_registry.load_runtime_providers(
        [
            ProviderRuntimeRecord(
                provider_code="qwen3-32b",
                provider_name="Qwen 3 32B",
                provider_type="chat",
                api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model_name="qwen3-32b",
                api_key="sk-qwen",
                settings_json={"extra_body": {"enable_thinking": True}},
                source="database",
                status="active",
                is_default=True,
            )
        ],
        default_provider_code="qwen3-32b",
    )
    captured: dict[str, Any] = {}

    def _spy_chat_openai(**kwargs: Any) -> _ChatOpenAISpy:
        captured.update(kwargs)
        return _ChatOpenAISpy(**kwargs)

    monkeypatch.setattr("app.core.llm_factory.ChatOpenAI", _spy_chat_openai)

    llm_factory._build_chat_model(provider_code="qwen3-32b", streaming=False)

    assert captured["streaming"] is False
    assert captured["extra_body"]["stream"] is False
    assert captured["extra_body"]["enable_thinking"] is False


def test_llm_factory_keeps_streaming_thinking_for_dashscope_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流式问答链路应保留 provider 的 thinking 配置。"""

    provider_runtime_registry.reset()
    provider_runtime_registry.load_runtime_providers(
        [
            ProviderRuntimeRecord(
                provider_code="qwen3-32b",
                provider_name="Qwen 3 32B",
                provider_type="chat",
                api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model_name="qwen3-32b",
                api_key="sk-qwen",
                settings_json={"extra_body": {"enable_thinking": True}},
                source="database",
                status="active",
                is_default=True,
            )
        ],
        default_provider_code="qwen3-32b",
    )
    captured: dict[str, Any] = {}

    def _spy_chat_openai(**kwargs: Any) -> _ChatOpenAISpy:
        captured.update(kwargs)
        return _ChatOpenAISpy(**kwargs)

    monkeypatch.setattr("app.core.llm_factory.ChatOpenAI", _spy_chat_openai)

    llm_factory._build_chat_model(provider_code="qwen3-32b", streaming=True)

    assert captured["streaming"] is True
    assert captured["extra_body"]["stream"] is True
    assert captured["extra_body"]["enable_thinking"] is True


def test_rate_limit_middleware_blocks_when_limit_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流中间件应在超限时返回 429。"""

    from app.middleware import rate_limit as rate_limit_module

    fake_redis = _FakeRedisForRateLimit()
    monkeypatch.setattr(rate_limit_module, "redis_manager", fake_redis)
    monkeypatch.setattr(rate_limit_module.config.rate_limit, "enabled", True)
    monkeypatch.setattr(rate_limit_module.config.rate_limit, "read_global_limit", 1)
    monkeypatch.setattr(rate_limit_module.config.rate_limit, "read_ip_limit", 1)
    monkeypatch.setattr(rate_limit_module.config.rate_limit, "read_user_limit", 1)

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(VisitorContextMiddleware)
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/interview/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app, base_url="https://testserver")
    first = client.get("/api/interview/ping")
    second = client.get("/api/interview/ping")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == 8001


def test_health_endpoint_includes_phase8_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """健康检查应返回 Provider/异步任务/限流依赖状态。"""

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(health_api.router, prefix="/api")
    client = TestClient(app)

    monkeypatch.setattr(health_api.database_manager, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(health_api.redis_manager, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(health_api.redis_manager, "get_group_info", AsyncMock(return_value=[]))
    monkeypatch.setattr(health_api.milvus_manager, "health_check", lambda: True)
    monkeypatch.setattr(health_api.storage_manager, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(health_api.config.postgres, "enabled", True)
    monkeypatch.setattr(health_api.config.redis, "enabled", True)
    monkeypatch.setattr(health_api.config.stream_task, "enabled", True)
    monkeypatch.setattr(health_api.config.rate_limit, "enabled", True)
    provider_runtime_registry.ensure_env_fallback()

    response = client.get("/api/health")

    assert response.status_code == 200
    dependencies = response.json()["data"]["dependencies"]
    assert "provider_runtime" in dependencies
    assert "async_task_backend" in dependencies
    assert "rate_limit" in dependencies


def test_provider_api_exposes_runtime_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider API 应暴露运行时默认 Provider 快照。"""

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(provider_api.router, prefix="/api")

    async def override_get_db_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = override_get_db_session
    client = TestClient(app)
    provider_runtime_registry.ensure_env_fallback()

    response = client.get("/api/providers/runtime")

    assert response.status_code == 200
    assert response.json()["data"]["provider_code"]
