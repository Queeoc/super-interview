"""文字面试主流程 API。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db_session
from app.middleware.visitor_context import get_request_visitor_id
from app.models.interview import CreateInterviewRequest, SubmitAnswerRequest
from app.services.interview_service import interview_service
from app.utils.exceptions import BusinessException, ErrorCode

router = APIRouter()


@router.post("/interview/sessions")
async def create_interview_session(
    request: CreateInterviewRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """创建文字面试会话并同步返回首题。"""

    result = await interview_service.create_session(
        session,
        request,
        get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.get("/interview/sessions")
async def list_interview_sessions(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """列出当前匿名访客的历史面试会话。"""

    result = await interview_service.list_sessions(
        session,
        visitor_id=get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": [item.model_dump(mode="json") for item in result],
    }


@router.get("/interview/sessions/{session_id}")
async def get_interview_session(
    session_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """返回当前面试会话快照。"""

    result = await interview_service.get_session(
        session,
        session_id,
        get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.post("/interview/sessions/{session_id}/answers/draft")
async def save_interview_answer_draft(
    session_id: str,
    request: SubmitAnswerRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """暂存当前轮答案。"""

    visitor_id = get_request_visitor_id(http_request)
    await interview_service.ensure_session_available(
        session,
        session_id,
        visitor_id,
        require_active=True,
    )
    result = await interview_service.save_draft_answer(session, session_id, request, visitor_id)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.post("/interview/sessions/{session_id}/answers")
async def submit_interview_answer(
    session_id: str,
    request: SubmitAnswerRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> EventSourceResponse:
    """提交答案并通过 SSE 推进面试流程。"""

    visitor_id = get_request_visitor_id(http_request)
    await interview_service.ensure_session_available(
        session,
        session_id,
        visitor_id,
        require_active=True,
    )

    async def event_generator():
        try:
            async for event in interview_service.submit_answer_stream(
                session,
                session_id,
                request,
                visitor_id,
            ):
                yield {
                    "event": "message",
                    "data": json.dumps(event, ensure_ascii=False),
                }
                if event.get("type") in {"done", "error"}:
                    break
        except BusinessException as exc:
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "code": int(exc.code),
                        "message": exc.message,
                        "data": exc.details,
                    },
                    ensure_ascii=False,
                ),
            }
        except Exception as exc:
            logger.exception(
                "submit interview answer stream failed session_id={}, visitor_id={}, question_key={}",
                session_id,
                visitor_id,
                request.question_key,
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "code": int(ErrorCode.INTERNAL_SERVER_ERROR),
                        "message": "文字面试流程异常",
                        "data": {"error": str(exc), "session_id": session_id},
                    },
                    ensure_ascii=False,
                ),
            }

    # Some sse-starlette versions treat ping=0 as a tight heartbeat loop instead of disabling it.
    # Use a long interval so short-lived interview streams emit only business JSON frames.
    return EventSourceResponse(event_generator(), ping=60)


@router.post("/interview/sessions/{session_id}/complete")
async def complete_interview_session(
    session_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """主动结束面试会话。"""

    visitor_id = get_request_visitor_id(http_request)
    await interview_service.ensure_session_available(session, session_id, visitor_id)
    result = await interview_service.complete_session(session, session_id, visitor_id)
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.get("/interview/sessions/{session_id}/report")
async def get_interview_report(
    session_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """返回统一评估报告。"""

    result = await interview_service.get_report(
        session,
        session_id,
        get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }


@router.get("/interview/sessions/{session_id}/report/export")
async def export_interview_report(
    session_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """导出统一评估报告占位内容。"""

    result = await interview_service.export_report(
        session,
        session_id,
        get_request_visitor_id(http_request),
    )
    return {
        "code": 200,
        "message": "success",
        "data": result.model_dump(mode="json"),
    }
