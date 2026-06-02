"""Shared application exceptions and error codes."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    """Application error codes aligned with the target architecture."""

    BAD_REQUEST = 1000
    VALIDATION_ERROR = 1001
    NOT_FOUND = 1004
    INTERNAL_SERVER_ERROR = 1005

    POSTGRES_UNAVAILABLE = 1101
    REDIS_UNAVAILABLE = 1102
    MILVUS_UNAVAILABLE = 1103
    STORAGE_UNAVAILABLE = 1104

    RESUME_NOT_FOUND = 2001
    RESUME_FILE_TYPE_NOT_SUPPORTED = 2002
    RESUME_FILE_TOO_LARGE = 2003
    RESUME_PARSE_FAILED = 2004
    RESUME_PERSIST_FAILED = 2005

    STORAGE_UPLOAD_FAILED = 4001

    SKILL_NOT_FOUND = 3001
    SKILL_META_INVALID = 3002
    SKILL_RESOURCE_LOAD_FAILED = 3003
    SKILL_REFERENCE_NOT_FOUND = 3004
    INTERVIEW_SESSION_NOT_FOUND = 3101
    INTERVIEW_SESSION_COMPLETED = 3102
    INTERVIEW_STATE_INVALID = 3103
    INTERVIEW_ANSWER_REQUIRED = 3104
    INTERVIEW_CURRENT_QUESTION_NOT_FOUND = 3105
    INTERVIEW_PERSIST_FAILED = 3106

    PROVIDER_NOT_FOUND = 7101
    PROVIDER_TEST_FAILED = 7102
    PROVIDER_DEFAULT_NOT_CONFIGURED = 7103

    KNOWLEDGE_BASE_NOT_FOUND = 6001
    KNOWLEDGE_DOCUMENT_NOT_FOUND = 6002
    KNOWLEDGE_FILE_TYPE_NOT_SUPPORTED = 6003
    KNOWLEDGE_FILE_TOO_LARGE = 6004
    KNOWLEDGE_INDEX_FAILED = 6005
    KNOWLEDGE_PERSIST_FAILED = 6006
    KNOWLEDGE_SEARCH_FAILED = 6007

    ASYNC_TASK_PUBLISH_FAILED = 7201
    ASYNC_TASK_CONSUME_FAILED = 7202

    RATE_LIMIT_EXCEEDED = 8001


class BusinessException(Exception):
    """Exception raised for expected business or infrastructure failures."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class InfrastructureException(BusinessException):
    """Exception raised when a core dependency is unavailable."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=503,
            details=details,
        )
