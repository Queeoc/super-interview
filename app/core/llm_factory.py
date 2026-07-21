"""LLM 工厂类。

统一从 Provider 运行时注册表解析当前默认模型，
并在额度耗尽类错误时自动切换到下一个可用 Provider。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from langchain_openai import ChatOpenAI

from app.config import config
from app.core.provider_runtime import ProviderRuntimeRecord, provider_runtime_registry
from app.services.provider_failover_service import provider_failover_service
from app.utils.exceptions import BusinessException, ErrorCode


class _FailoverStructuredInvoker:
    """包装 provider-native structured output 的调用器。"""

    def __init__(
        self,
        factory: "LLMFactory",
        *,
        builder: Callable[[str], Any],
        provider_code: str,
    ) -> None:
        self._factory = factory
        self._builder = builder
        self._provider_code = provider_code

    async def ainvoke(self, prompt_text: str) -> Any:
        return await self._factory._invoke_with_failover(
            provider_code=self._provider_code,
            prompt_text=prompt_text,
            invoke_builder=lambda active_provider_code: self._builder(active_provider_code).ainvoke(prompt_text),
        )


class _FailoverChatModel:
    """给 ChatOpenAI 增加最小自动切换能力。"""

    def __init__(
        self,
        factory: "LLMFactory",
        *,
        provider_code: str,
        base_kwargs: dict[str, Any],
    ) -> None:
        self._factory = factory
        self._provider_code = provider_code
        self._base_kwargs = dict(base_kwargs)

    def _build_model(self, provider_code: str) -> ChatOpenAI:
        return self._factory._build_chat_model(provider_code=provider_code, **self._base_kwargs)

    async def ainvoke(self, prompt_text: str) -> Any:
        return await self._factory._invoke_with_failover(
            provider_code=self._provider_code,
            prompt_text=prompt_text,
            invoke_builder=lambda active_provider_code: self._build_model(active_provider_code).ainvoke(prompt_text),
        )

    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> _FailoverStructuredInvoker:
        return _FailoverStructuredInvoker(
            self._factory,
            builder=lambda active_provider_code: self._build_model(active_provider_code).with_structured_output(
                schema,
                **kwargs,
            ),
            provider_code=self._provider_code,
        )


class LLMFactory:
    """LLM 工厂类。"""

    def __init__(self) -> None:
        self._runtime_registry = provider_runtime_registry

    def ensure_runtime_provider(self) -> ProviderRuntimeRecord:
        """确保运行时至少存在一个可用 Provider。"""

        provider = self._runtime_registry.get_runtime_provider()
        if provider is not None:
            return provider
        return self._runtime_registry.ensure_env_fallback()

    def has_available_provider(self) -> bool:
        """当前是否存在可用 Provider。"""

        provider = self._runtime_registry.get_runtime_provider()
        if provider is not None and bool(provider.api_key):
            return True
        return bool(config.llm.api_key)

    def get_runtime_provider(self, provider_code: str | None = None) -> ProviderRuntimeRecord:
        """读取当前运行时 Provider。"""

        provider = self._runtime_registry.get_runtime_provider(provider_code)
        if provider is not None:
            return provider

        if provider_code:
            raise BusinessException(
                code=ErrorCode.PROVIDER_NOT_FOUND,
                message="指定的 Provider 不存在或未加载到运行时",
                http_status=404,
                details={"provider_code": provider_code},
            )

        fallback_provider = self._runtime_registry.ensure_env_fallback()
        if fallback_provider.api_key:
            return fallback_provider

        raise BusinessException(
            code=ErrorCode.PROVIDER_DEFAULT_NOT_CONFIGURED,
            message="当前未配置可用的默认 Provider",
            http_status=503,
            details={},
        )

    def create_chat_model(
        self,
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
        provider_code: str | None = None,
        extra_settings: dict[str, Any] | None = None,
    ) -> _FailoverChatModel:
        runtime_provider = self.get_runtime_provider(provider_code)
        return _FailoverChatModel(
            self,
            provider_code=runtime_provider.provider_code,
            base_kwargs={
                "model": model,
                "temperature": temperature,
                "streaming": streaming,
                "base_url": base_url,
                "api_key": api_key,
                "extra_settings": extra_settings,
            },
        )

    def _build_chat_model(
        self,
        *,
        provider_code: str,
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
        extra_settings: dict[str, Any] | None = None,
    ) -> ChatOpenAI:
        runtime_provider = self.get_runtime_provider(provider_code)
        resolved_model = model or runtime_provider.model_name or config.dashscope_model
        resolved_base_url = base_url or runtime_provider.api_base or config.dashscope_api_base
        resolved_api_key = api_key or runtime_provider.api_key or config.dashscope_api_key
        if not resolved_api_key:
            raise BusinessException(
                code=ErrorCode.PROVIDER_DEFAULT_NOT_CONFIGURED,
                message="当前 Provider 未配置 API Key",
                http_status=503,
                details={"provider_code": runtime_provider.provider_code},
            )

        extra_body = self._resolve_extra_body(
            runtime_provider=runtime_provider,
            resolved_model=resolved_model,
            resolved_base_url=resolved_base_url,
            streaming=streaming,
            extra_settings=extra_settings,
        )

        request_timeout = runtime_provider.settings_json.get(
            "request_timeout_seconds",
            config.llm.request_timeout_seconds,
        )
        logger.info(
            "创建聊天模型: provider_code={}, model={}, streaming={}",
            runtime_provider.provider_code,
            resolved_model,
            streaming,
        )
        return ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            streaming=streaming,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            timeout=request_timeout,
            extra_body=extra_body if extra_body else None,
        )

    def _resolve_extra_body(
        self,
        *,
        runtime_provider: ProviderRuntimeRecord,
        resolved_model: str | None,
        resolved_base_url: str | None,
        streaming: bool,
        extra_settings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """组装 provider 额外参数，并修正已知的不兼容组合。"""

        extra_body = dict(runtime_provider.settings_json.get("extra_body", {}))
        if extra_settings:
            extra_body.update(extra_settings.get("extra_body", {}))
        extra_body["stream"] = streaming

        # DashScope/Qwen 在非流式请求下要求显式关闭 thinking。
        if self._should_force_disable_thinking(
            runtime_provider=runtime_provider,
            resolved_model=resolved_model,
            resolved_base_url=resolved_base_url,
            streaming=streaming,
            extra_body=extra_body,
        ):
            previous_value = extra_body.get("enable_thinking")
            extra_body["enable_thinking"] = False
            if previous_value is not False:
                logger.info(
                    "检测到非流式 Qwen/DashScope 调用，自动关闭 thinking: provider_code={}, model={}",
                    runtime_provider.provider_code,
                    resolved_model,
                )

        return extra_body

    @staticmethod
    def _should_force_disable_thinking(
        *,
        runtime_provider: ProviderRuntimeRecord,
        resolved_model: str | None,
        resolved_base_url: str | None,
        streaming: bool,
        extra_body: dict[str, Any],
    ) -> bool:
        """判断当前请求是否需要强制关闭 thinking。"""

        if streaming:
            return False

        provider_code = runtime_provider.provider_code.lower()
        model_name = (resolved_model or "").lower()
        api_base = (resolved_base_url or "").lower()
        has_thinking_flag = "enable_thinking" in extra_body
        is_dashscope_qwen = (
            "dashscope.aliyuncs.com" in api_base
            and ("qwen" in provider_code or "qwen" in model_name)
        )
        return has_thinking_flag or is_dashscope_qwen

    async def _invoke_with_failover(
        self,
        *,
        provider_code: str,
        prompt_text: str,
        invoke_builder: Callable[[str], Awaitable[Any]],
    ) -> Any:
        active_provider_code = provider_code
        attempted_provider_codes: set[str] = set()

        while True:
            attempted_provider_codes.add(active_provider_code)
            try:
                return await invoke_builder(active_provider_code)
            except Exception as exc:
                if not provider_failover_service.is_quota_exhausted_error(exc):
                    raise
                logger.warning(
                    "检测到 Provider 额度/频控异常，尝试自动切换: provider_code={}, error={}",
                    active_provider_code,
                    exc,
                )
                next_provider_code = await provider_failover_service.disable_and_failover(
                    active_provider_code,
                    error_message=str(exc),
                )
                if not next_provider_code or next_provider_code in attempted_provider_codes:
                    raise
                active_provider_code = next_provider_code


llm_factory = LLMFactory()
