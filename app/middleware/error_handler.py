"""Global exception handlers for FastAPI."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from app.utils.exceptions import BusinessException, ErrorCode


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


async def business_exception_handler(
    request: Request,
    exc: BusinessException,
) -> JSONResponse:
    """Return a structured response for business exceptions."""

    logger.warning(
        "业务异常: method={}, path={}, code={}, message={}",
        request.method,
        request.url.path,
        int(exc.code),
        exc.message,
    )
    return _build_error_response(
        http_status=exc.http_status,
        code=int(exc.code),
        message=exc.message,
        details=exc.details,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return a structured response for HTTP exceptions."""

    logger.warning(
        "HTTP 异常: method={}, path={}, status={}, detail={}",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )
    return _build_error_response(
        http_status=exc.status_code,
        code=int(ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL_SERVER_ERROR),
        message=str(exc.detail),
        details=None,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return a structured response for request validation errors."""

    logger.warning(
        "请求校验失败: method={}, path={}, errors={}",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return _build_error_response(
        http_status=422,
        code=int(ErrorCode.VALIDATION_ERROR),
        message="请求参数校验失败",
        details={"errors": exc.errors()},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a structured response for unexpected exceptions."""

    logger.exception(
        "未处理异常: method={}, path={}, error={}",
        request.method,
        request.url.path,
        exc,
    )
    return _build_error_response(
        http_status=500,
        code=int(ErrorCode.INTERNAL_SERVER_ERROR),
        message="服务器内部错误",
        details=None,
    )


def _build_error_response(
    http_status: int,
    code: int,
    message: str,
    details: dict[str, Any] | None,
) -> JSONResponse:
    """Build a unified error response payload."""

    payload = {
        "code": code,
        "message": message,
        "data": details or {},
    }
    return JSONResponse(status_code=http_status, content=payload)
