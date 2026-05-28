"""匿名访客上下文中间件。"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import config

VISITOR_ID_COOKIE_NAME = "visitor_id"
VISITOR_ID_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 180


def _normalize_visitor_id(raw_visitor_id: str | None) -> str | None:
    """校验并标准化匿名访客标识。"""

    if not raw_visitor_id:
        return None

    try:
        return str(UUID(str(raw_visitor_id)))
    except (TypeError, ValueError):
        return None


def get_request_visitor_id(request: Request) -> str:
    """从请求上下文读取匿名访客标识。"""

    visitor_id = getattr(request.state, "visitor_id", None)
    if not isinstance(visitor_id, str) or not visitor_id:
        raise RuntimeError("request.state.visitor_id 未初始化")
    return visitor_id


class VisitorContextMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入匿名访客标识。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        visitor_id = _normalize_visitor_id(request.cookies.get(VISITOR_ID_COOKIE_NAME)) or str(uuid4())
        request.state.visitor_id = visitor_id

        response = await call_next(request)
        response.set_cookie(
            key=VISITOR_ID_COOKIE_NAME,
            value=visitor_id,
            max_age=VISITOR_ID_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=not config.debug,
            path="/",
        )
        return response


__all__ = [
    "VISITOR_ID_COOKIE_MAX_AGE_SECONDS",
    "VISITOR_ID_COOKIE_NAME",
    "VisitorContextMiddleware",
    "get_request_visitor_id",
]
