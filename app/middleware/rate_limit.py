"""Redis Lua 滑动窗口限流中间件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import config
from app.core.redis_client import redis_manager
from app.utils.exceptions import BusinessException, ErrorCode

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rate_limit.lua"

_PROTECTED_PREFIXES = (
    "/api/interview",
    "/api/resume",
    "/api/knowledge",
    "/api/providers",
)

_EXCLUDED_PREFIXES = (
    "/health",
    "/static",
    "/api/chat",
    "/api/chat_stream",
    "/api/aiops",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """通用限流中间件。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._script_text = _SCRIPT_PATH.read_text(encoding="utf-8")
        self._script_sha: str | None = None

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not config.rate_limit.enabled or not self._should_protect(request.url.path):
            return await call_next(request)

        if not getattr(redis_manager, "enabled", False):
            logger.warning("限流已启用但 Redis 不可用，当前请求降级放行: path={}", request.url.path)
            return await call_next(request)

        try:
            await self._enforce_limit(request)
        except BusinessException:
            raise
        except Exception as exc:
            logger.warning("限流执行失败，当前请求降级放行: path={}, error={}", request.url.path, exc)

        return await call_next(request)

    async def _enforce_limit(self, request: Request) -> None:
        """执行限流判定。"""

        if self._script_sha is None:
            self._script_sha = await redis_manager.script_load(self._script_text)

        for dimension, key_suffix, limit in self._build_limit_specs(request):
            key = f"rate_limit:{dimension.lower()}:{key_suffix}"
            now_seconds = "0"
            try:
                result = await redis_manager.evalsha(
                    self._script_sha,
                    1,
                    redis_manager.build_key(key),
                    str(config.rate_limit.window_seconds),
                    str(limit),
                    now_seconds,
                )
            except Exception:
                result = await redis_manager.eval_script(
                    self._script_text,
                    1,
                    redis_manager.build_key(key),
                    str(config.rate_limit.window_seconds),
                    str(limit),
                    now_seconds,
                )
            allowed = int(result[0]) if isinstance(result, (list, tuple)) else int(result)
            retry_after_seconds = (
                int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else config.rate_limit.window_seconds
            )
            if allowed == 0:
                logger.warning(
                    "触发限流: path={}, dimension={}, key_suffix={}, limit={}",
                    request.url.path,
                    dimension,
                    key_suffix,
                    limit,
                )
                raise BusinessException(
                    code=ErrorCode.RATE_LIMIT_EXCEEDED,
                    message="请求过于频繁，请稍后再试",
                    http_status=429,
                    details={
                        "dimension": dimension,
                        "limit": limit,
                        "window_seconds": config.rate_limit.window_seconds,
                        "retry_after_seconds": retry_after_seconds,
                        "path": request.url.path,
                    },
                )

    @staticmethod
    def _should_protect(path: str) -> bool:
        if path.startswith(_EXCLUDED_PREFIXES):
            return False
        return path.startswith(_PROTECTED_PREFIXES)

    @staticmethod
    def _is_write_method(method: str) -> bool:
        return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}

    def _build_limit_specs(self, request: Request) -> list[tuple[str, str, int]]:
        """构建当前请求的三维限流规格。"""

        is_write = self._is_write_method(request.method)
        global_limit = (
            config.rate_limit.write_global_limit if is_write else config.rate_limit.read_global_limit
        )
        ip_limit = config.rate_limit.write_ip_limit if is_write else config.rate_limit.read_ip_limit
        user_limit = (
            config.rate_limit.write_user_limit if is_write else config.rate_limit.read_user_limit
        )
        client_ip = request.client.host if request.client else "unknown"
        visitor_id = getattr(request.state, "visitor_id", "") or "anonymous"
        return [
            ("GLOBAL", request.url.path, global_limit),
            ("IP", f"{client_ip}:{request.url.path}", ip_limit),
            ("USER", f"{visitor_id}:{request.url.path}", user_limit),
        ]


__all__ = ["RateLimitMiddleware"]
