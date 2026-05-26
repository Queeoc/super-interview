"""Phase 5 文字面试主流程服务。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from langgraph.graph import END, StateGraph
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.interview.executor import InterviewExecutor
from app.agent.interview.planner import InterviewPlanner
from app.agent.interview.prompts import InterviewPromptRunner
from app.agent.interview.replanner import InterviewReplanner
from app.agent.interview.state import (
    InterviewSkillContext,
    InterviewState,
    build_initial_state,
    build_session_context_payload,
    clone_state,
    get_current_question,
    list_questions,
    mark_question_answered,
)
from app.config import config
from app.core.redis_client import redis_manager
from app.models.interview import (
    CreateInterviewRequest,
    InterviewAnswerEntity,
    InterviewAnswerStatus,
    InterviewQuestionSnapshot,
    InterviewReportEntity,
    InterviewReportStatus,
    InterviewSessionDTO,
    InterviewSessionEntity,
    InterviewSessionStatus,
    InterviewWorkflowAction,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from app.repositories.interview_repository import InterviewRepository
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.skill_service import SkillService, skill_service as preset_skill_service
from app.utils.exceptions import BusinessException, ErrorCode


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


class InterviewSessionCache:
    """基于 Redis 的面试会话运行态缓存。"""

    def __init__(
        self,
        redis_backend: Any | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis_backend = redis_backend or redis_manager
        self._ttl_seconds = ttl_seconds or config.interview.session_ttl_minutes * 60

    async def get_state(self, session_id: str) -> InterviewState | None:
        """读取缓存中的工作流状态。"""

        if not self.enabled:
            return None

        try:
            raw_value = await self._redis_backend.get_value(self._build_key(session_id))
            if not raw_value:
                return None
            return json.loads(raw_value)
        except Exception as exc:
            logger.warning("读取面试缓存失败: session_id={}, error={}", session_id, exc)
            return None

    async def set_state(self, session_id: str, state: InterviewState) -> None:
        """写入缓存中的工作流状态。"""

        if not self.enabled:
            return

        try:
            await self._redis_backend.set_value(
                self._build_key(session_id),
                json.dumps(state, ensure_ascii=False),
                ttl_seconds=self._ttl_seconds,
            )
        except Exception as exc:
            logger.warning("写入面试缓存失败: session_id={}, error={}", session_id, exc)

    async def delete_state(self, session_id: str) -> None:
        """删除缓存中的工作流状态。"""

        if not self.enabled:
            return

        try:
            await self._redis_backend.delete_key(self._build_key(session_id))
        except Exception as exc:
            logger.warning("删除面试缓存失败: session_id={}, error={}", session_id, exc)

    @property
    def enabled(self) -> bool:
        """缓存是否启用。"""

        return bool(getattr(self._redis_backend, "enabled", False))

    @staticmethod
    def _build_key(session_id: str) -> str:
        """构建逻辑缓存键。"""

        return f"interview:session:{session_id}:state"


class InterviewService:
    """文字面试主流程编排服务。"""

    def __init__(
        self,
        *,
        skill_service: SkillService | None = None,
        persistence_service: InterviewPersistenceService | None = None,
        repository_factory: Callable[[AsyncSession], InterviewRepository] | None = None,
        prompt_runner: InterviewPromptRunner | None = None,
        planner: InterviewPlanner | None = None,
        executor: InterviewExecutor | None = None,
        replanner: InterviewReplanner | None = None,
        cache: InterviewSessionCache | None = None,
    ) -> None:
        self._skill_service = skill_service or preset_skill_service
        self._persistence_service = persistence_service or InterviewPersistenceService()
        self._repository_factory = repository_factory or InterviewRepository
        self._prompt_runner = prompt_runner or InterviewPromptRunner(
            enable_llm=bool(config.dashscope_api_key),
        )
        self._planner = planner or InterviewPlanner(self._prompt_runner)
        self._executor = executor or InterviewExecutor(self._prompt_runner)
        self._replanner = replanner or InterviewReplanner(self._prompt_runner)
        self._cache = cache or InterviewSessionCache()
        self._initial_graph = self._build_initial_graph()
        self._advance_graph = self._build_advance_graph()

    async def ensure_session_available(
        self,
        session: AsyncSession,
        session_id: str,
        *,
        require_active: bool = False,
    ) -> InterviewSessionEntity:
        """校验会话存在，并按需要求其仍处于可答题状态。"""

        repository = self._get_repository(session)
        interview_session = await repository.get_session(session_id)
        if interview_session is None:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_NOT_FOUND,
                message="面试会话不存在",
                http_status=404,
                details={"session_id": session_id},
            )

        if require_active and interview_session.status == InterviewSessionStatus.COMPLETED.value:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_COMPLETED,
                message="面试会话已结束，不能继续答题",
                http_status=409,
                details={"session_id": session_id},
            )
        return interview_session

    async def create_session(
        self,
        session: AsyncSession,
        request: CreateInterviewRequest,
    ) -> InterviewSessionDTO:
        """创建面试会话并同步生成首题。"""

        skill_detail = self._skill_service.get_skill_detail(request.skill_id)
        reference_section = self._skill_service.build_reference_section(request.skill_id)
        session_id = self._generate_identifier()
        max_rounds = min(
            request.max_rounds or config.interview.max_rounds,
            config.interview.max_rounds,
        )
        language = request.language or config.interview.default_language
        title = request.title or f"{skill_detail.display_name} 模拟面试"

        initial_state = build_initial_state(
            session_id=session_id,
            skill=self._build_skill_context(skill_detail, reference_section.reference_markdown, reference_section.resolved_reference_files),
            language=language,
            max_rounds=max_rounds,
            title=title,
            user_id=request.user_id,
            resume_id=request.resume_id,
        )
        planned_state = await self._initial_graph.ainvoke(initial_state)

        interview_session = InterviewSessionEntity(
            id=session_id,
            user_id=request.user_id,
            skill_id=skill_detail.skill_id,
            resume_id=request.resume_id,
            title=title,
            language=language,
            mode=config.interview.default_mode,
            status=InterviewSessionStatus.ACTIVE.value,
            current_round=planned_state.get("current_round", 1),
            max_rounds=max_rounds,
            questions_json=list_questions(planned_state),
            session_context_json=build_session_context_payload(planned_state),
            metadata_json=self._build_session_metadata(skill_detail, reference_section.reference_markdown, reference_section.resolved_reference_files),
            started_at=_utc_now(),
        )

        await self._persistence_service.save_session_snapshot(session, interview_session)
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="创建面试会话失败",
            details={"session_id": session_id, "skill_id": skill_detail.skill_id},
        )
        await self._cache.set_state(session_id, planned_state)
        return self._build_session_dto(
            interview_session,
            planned_state,
            answers=[],
            report=None,
        )

    async def get_session(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> InterviewSessionDTO:
        """读取面试会话快照。"""

        interview_session, answers, report, state = await self._load_session_bundle(session, session_id)
        return self._build_session_dto(interview_session, state, answers, report)

    async def save_draft_answer(
        self,
        session: AsyncSession,
        session_id: str,
        request: SubmitAnswerRequest,
    ) -> SubmitAnswerResponse:
        """暂存当前轮答案。"""

        interview_session, answers, report, state = await self._load_session_bundle(session, session_id)
        self._ensure_session_is_active(interview_session)
        current_question = self._require_current_question(state, request.question_key)

        state["last_draft_answer"] = {
            "question_key": current_question["question_key"],
            "answer_text": request.answer_text.strip(),
            "answer_metadata": deepcopy(request.answer_metadata),
            "saved_at": _utc_now().isoformat(),
        }
        interview_session.session_context_json = build_session_context_payload(state)
        await self._persistence_service.save_session_snapshot(session, interview_session)
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="暂存答案失败",
            details={"session_id": session_id},
        )
        await self._cache.set_state(session_id, state)
        return SubmitAnswerResponse(
            session_id=session_id,
            action=InterviewWorkflowAction.DRAFT_SAVED.value,
            status=interview_session.status,
            completed=False,
            draft_saved=True,
            message="答案草稿已暂存",
            current_question=InterviewQuestionSnapshot.model_validate(current_question),
            feedback={},
            report_status=report.status if report else None,
        )

    async def submit_answer_stream(
        self,
        session: AsyncSession,
        session_id: str,
        request: SubmitAnswerRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """提交答案并通过 SSE 推进主流程。"""

        interview_session, answers, report, state = await self._load_session_bundle(session, session_id)
        self._ensure_session_is_active(interview_session)
        current_question = self._require_current_question(state, request.question_key)

        yield {
            "type": "status",
            "message": "已加载当前面试会话",
            "session_id": session_id,
        }

        state["latest_answer_text"] = request.answer_text.strip()
        state["latest_answer_metadata"] = deepcopy(request.answer_metadata)
        state["last_draft_answer"] = {}
        state["feedback"] = self._build_placeholder_feedback()
        state["assistant_message"] = None
        state["completion_message"] = None
        state["action_reason"] = None
        state["answer_count"] = len(answers) + 1
        mark_question_answered(state, current_question["question_key"])

        transitioned_state = await self._advance_graph.ainvoke(state)
        yield {
            "type": "plan",
            "action": transitioned_state.get("next_action"),
            "reason": transitioned_state.get("action_reason"),
            "session_id": session_id,
        }

        answer_entity = InterviewAnswerEntity(
            session_id=session_id,
            round_index=current_question["round_index"],
            question_key=current_question["question_key"],
            question_text=current_question["question_text"],
            answer_text=request.answer_text.strip(),
            answer_status=InterviewAnswerStatus.SUBMITTED.value,
            score_json={
                "status": "pending",
                "message": "待 Phase 6 统一评估",
            },
            feedback_json=deepcopy(transitioned_state.get("feedback", {})),
            answer_metadata_json={
                **deepcopy(request.answer_metadata),
                "question_source": current_question.get("source"),
                "is_follow_up": bool(current_question.get("is_follow_up")),
            },
        )

        self._apply_state_to_session(interview_session, transitioned_state)
        report_entity = self._build_report_placeholder(
            interview_session=interview_session,
            state=transitioned_state,
            existing_report=report,
        ) if transitioned_state.get("completed") else None

        await self._persistence_service.save_answer_and_session_snapshot(
            session,
            interview_session=interview_session,
            answer=answer_entity,
            report=report_entity,
        )
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="提交答案失败",
            details={"session_id": session_id},
        )

        if transitioned_state.get("completed"):
            await self._cache.delete_state(session_id)
        else:
            await self._cache.set_state(session_id, transitioned_state)

        if transitioned_state.get("assistant_message"):
            yield {
                "type": "content",
                "session_id": session_id,
                "content": transitioned_state["assistant_message"],
            }

        step_response = SubmitAnswerResponse(
            session_id=session_id,
            action=transitioned_state.get("next_action", InterviewWorkflowAction.NEXT_QUESTION.value),
            status=interview_session.status,
            completed=bool(transitioned_state.get("completed", False)),
            draft_saved=False,
            message="答案已提交，流程已推进",
            current_question=self._build_current_question_model(transitioned_state),
            feedback=deepcopy(transitioned_state.get("feedback", {})),
            report_status=report_entity.status if report_entity else (report.status if report else None),
        )
        yield {
            "type": "step_complete",
            "session_id": session_id,
            "response": step_response.model_dump(mode="json"),
        }

        if report_entity is not None:
            yield {
                "type": "report",
                "session_id": session_id,
                "report": deepcopy(report_entity.report_json),
            }

        final_dto = self._build_session_dto(
            interview_session,
            transitioned_state,
            answers=answers + [answer_entity],
            report=report_entity or report,
        )
        yield {
            "type": "done",
            "session_id": session_id,
            "session": final_dto.model_dump(mode="json"),
        }

    async def complete_session(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> InterviewSessionDTO:
        """主动结束面试会话。"""

        interview_session, answers, report, state = await self._load_session_bundle(session, session_id)
        if not state.get("completed"):
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "用户主动结束面试"
            state = await self._executor.run(state)

        self._apply_state_to_session(interview_session, state)
        report_entity = self._build_report_placeholder(
            interview_session=interview_session,
            state=state,
            existing_report=report,
        )
        await self._persistence_service.save_session_bundle(
            session,
            interview_session=interview_session,
            answers=[],
            report=report_entity,
        )
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="结束面试会话失败",
            details={"session_id": session_id},
        )
        await self._cache.delete_state(session_id)
        return self._build_session_dto(interview_session, state, answers, report_entity)

    def _build_initial_graph(self):
        """构建会话初始化图：planner -> executor。"""

        workflow = StateGraph(InterviewState)
        workflow.add_node("planner", self._planner.run)
        workflow.add_node("executor", self._executor.run)
        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "executor")
        workflow.add_edge("executor", END)
        return workflow.compile()

    def _build_advance_graph(self):
        """构建答题推进图：replanner -> executor/END。"""

        workflow = StateGraph(InterviewState)
        workflow.add_node("replanner", self._replanner.run)
        workflow.add_node("executor", self._executor.run)
        workflow.set_entry_point("replanner")

        def route_after_replanner(state: InterviewState) -> str:
            if state.get("completed"):
                return END
            return "executor"

        workflow.add_conditional_edges(
            "replanner",
            route_after_replanner,
            {
                "executor": "executor",
                END: END,
            },
        )
        workflow.add_edge("executor", END)
        return workflow.compile()

    def _get_repository(self, session: AsyncSession) -> InterviewRepository:
        """解析当前请求要使用的仓储。"""

        return self._repository_factory(session)

    async def _load_session_bundle(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> tuple[
        InterviewSessionEntity,
        list[InterviewAnswerEntity],
        InterviewReportEntity | None,
        InterviewState,
    ]:
        """读取会话、答案、报告与运行态。"""

        repository = self._get_repository(session)
        interview_session, answers, report = await repository.get_session_snapshot(session_id)
        if interview_session is None:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_NOT_FOUND,
                message="面试会话不存在",
                http_status=404,
                details={"session_id": session_id},
            )

        state = await self._load_state_from_cache_or_session(
            interview_session=interview_session,
            answers=answers,
            report=report,
        )
        return interview_session, answers, report, state

    async def _load_state_from_cache_or_session(
        self,
        *,
        interview_session: InterviewSessionEntity,
        answers: list[InterviewAnswerEntity],
        report: InterviewReportEntity | None,
    ) -> InterviewState:
        """优先从 Redis 读取状态，未命中则从数据库快照重建。"""

        cached_state = await self._cache.get_state(interview_session.id)
        if cached_state is not None:
            state = clone_state(cached_state)
        else:
            workflow_state = interview_session.session_context_json.get("workflow_state")
            if isinstance(workflow_state, dict):
                state = clone_state(workflow_state)
            else:
                state = build_initial_state(
                    session_id=interview_session.id,
                    skill=self._build_skill_context_from_entity(interview_session),
                    language=interview_session.language,
                    max_rounds=interview_session.max_rounds,
                    title=interview_session.title,
                    user_id=interview_session.user_id,
                    resume_id=interview_session.resume_id,
                )

        state["session_id"] = interview_session.id
        state["user_id"] = interview_session.user_id
        state["resume_id"] = interview_session.resume_id
        state["title"] = interview_session.title
        state["language"] = interview_session.language
        state["max_rounds"] = interview_session.max_rounds
        state["skill"] = self._build_skill_context_from_entity(interview_session)
        state["questions"] = list(interview_session.questions_json or [])
        state["current_round"] = interview_session.current_round
        state["current_question_key"] = (
            interview_session.session_context_json.get("current_question_key")
            or state.get("current_question_key")
            or self._infer_current_question_key(interview_session.questions_json)
        )
        state["follow_up_count"] = int(
            interview_session.session_context_json.get("follow_up_count", state.get("follow_up_count", 0))
        )
        state["last_draft_answer"] = deepcopy(
            interview_session.session_context_json.get("last_draft_answer", state.get("last_draft_answer", {}))
        )
        state["feedback"] = deepcopy(
            interview_session.session_context_json.get("latest_feedback", state.get("feedback", {}))
        )
        state["next_action"] = str(
            interview_session.session_context_json.get("next_action", state.get("next_action", InterviewWorkflowAction.NEXT_QUESTION.value))
        )
        state["action_reason"] = interview_session.session_context_json.get("action_reason", state.get("action_reason"))
        state["completion_message"] = interview_session.session_context_json.get("completion_message", state.get("completion_message"))
        state["assistant_message"] = interview_session.session_context_json.get("assistant_message", state.get("assistant_message"))
        state["answer_count"] = len(answers)
        state["completed"] = interview_session.status == InterviewSessionStatus.COMPLETED.value
        state["report_summary"] = deepcopy(report.report_json if report else state.get("report_summary", {}))
        return state

    def _apply_state_to_session(
        self,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
    ) -> None:
        """把工作流状态回写到会话实体。"""

        interview_session.questions_json = list_questions(state)
        interview_session.current_round = int(state.get("current_round", interview_session.current_round))
        interview_session.session_context_json = build_session_context_payload(state)
        interview_session.metadata_json = {
            **deepcopy(interview_session.metadata_json),
            "latest_feedback": deepcopy(state.get("feedback", {})),
            "last_action": state.get("next_action"),
        }
        if state.get("completed"):
            interview_session.status = InterviewSessionStatus.COMPLETED.value
            interview_session.completed_at = interview_session.completed_at or _utc_now()
        else:
            interview_session.status = InterviewSessionStatus.ACTIVE.value

    def _build_session_metadata(
        self,
        skill_detail: Any,
        reference_markdown: str,
        reference_files: list[str],
    ) -> dict[str, Any]:
        """构建会话元数据。"""

        return {
            "skill_display_name": skill_detail.display_name,
            "skill_description": skill_detail.description,
            "skill_markdown": skill_detail.content_markdown,
            "skill_categories": [
                category.model_dump(mode="json") for category in skill_detail.categories
            ],
            "reference_markdown": reference_markdown,
            "reference_files": reference_files,
        }

    def _build_skill_context(
        self,
        skill_detail: Any,
        reference_markdown: str,
        reference_files: list[str],
    ) -> InterviewSkillContext:
        """根据 Skill 服务结果构建工作流上下文。"""

        return {
            "skill_id": skill_detail.skill_id,
            "display_name": skill_detail.display_name,
            "description": skill_detail.description,
            "content_markdown": skill_detail.content_markdown,
            "reference_markdown": reference_markdown,
            "categories": [
                category.model_dump(mode="json") for category in skill_detail.categories
            ],
            "reference_files": reference_files,
        }

    def _build_skill_context_from_entity(
        self,
        interview_session: InterviewSessionEntity,
    ) -> InterviewSkillContext:
        """根据已落库的会话元数据恢复 Skill 上下文。"""

        metadata = interview_session.metadata_json or {}
        return {
            "skill_id": interview_session.skill_id or "",
            "display_name": str(metadata.get("skill_display_name", interview_session.skill_id or "")),
            "description": str(metadata.get("skill_description", "")),
            "content_markdown": str(metadata.get("skill_markdown", "")),
            "reference_markdown": str(metadata.get("reference_markdown", "")),
            "categories": list(metadata.get("skill_categories", [])),
            "reference_files": list(metadata.get("reference_files", [])),
        }

    def _build_session_dto(
        self,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
        answers: list[InterviewAnswerEntity],
        report: InterviewReportEntity | None,
    ) -> InterviewSessionDTO:
        """将实体和状态转换成对外 DTO。"""

        skill_context = state["skill"]
        question_models = [
            InterviewQuestionSnapshot.model_validate(question)
            for question in list_questions(state)
        ]
        current_question = self._build_current_question_model(state)
        return InterviewSessionDTO(
            session_id=interview_session.id,
            user_id=interview_session.user_id,
            resume_id=interview_session.resume_id,
            skill_id=interview_session.skill_id or "",
            skill_display_name=skill_context["display_name"],
            title=interview_session.title,
            language=interview_session.language,
            status=interview_session.status,
            current_round=interview_session.current_round,
            max_rounds=interview_session.max_rounds,
            current_question=current_question,
            questions=question_models,
            answer_count=len(answers),
            follow_up_count=int(state.get("follow_up_count", 0)),
            completed=bool(state.get("completed", False)),
            report_status=report.status if report else None,
            last_draft_answer=deepcopy(state.get("last_draft_answer", {})),
            started_at=interview_session.started_at,
            completed_at=interview_session.completed_at,
        )

    def _build_current_question_model(
        self,
        state: InterviewState,
    ) -> InterviewQuestionSnapshot | None:
        """将当前题目转换为 DTO。"""

        current_question = get_current_question(state)
        if current_question is None:
            return None
        return InterviewQuestionSnapshot.model_validate(current_question)

    def _build_placeholder_feedback(self) -> dict[str, Any]:
        """生成 Phase 5 的占位反馈。"""

        return {
            "status": "pending",
            "message": "答案已记录，完整统一评估将在 Phase 6 提供。",
            "phase": "phase5-placeholder",
        }

    def _build_report_placeholder(
        self,
        *,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
        existing_report: InterviewReportEntity | None,
    ) -> InterviewReportEntity:
        """生成或更新 Phase 5 的占位报告。"""

        report_entity = existing_report or InterviewReportEntity(session_id=interview_session.id)
        report_entity.status = InterviewReportStatus.PENDING.value
        report_entity.summary_text = state.get("completion_message")
        report_entity.report_json = {
            **deepcopy(state.get("report_summary", {})),
            "session_id": interview_session.id,
            "skill_id": interview_session.skill_id,
            "ended_reason": state.get("action_reason"),
        }
        report_entity.score_json = {
            "status": "pending",
            "message": "待 Phase 6 统一评估",
        }
        report_entity.error_message = None
        return report_entity

    def _ensure_session_is_active(self, interview_session: InterviewSessionEntity) -> None:
        """校验会话仍可继续答题。"""

        if interview_session.status == InterviewSessionStatus.COMPLETED.value:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_COMPLETED,
                message="面试会话已结束，不能继续答题",
                http_status=409,
                details={"session_id": interview_session.id},
            )

    def _require_current_question(
        self,
        state: InterviewState,
        expected_question_key: str | None,
    ) -> dict[str, Any]:
        """读取并校验当前题目。"""

        current_question = get_current_question(state)
        if current_question is None:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_CURRENT_QUESTION_NOT_FOUND,
                message="当前题目不存在，无法提交答案",
                http_status=409,
                details={"session_id": state.get("session_id")},
            )

        if expected_question_key and expected_question_key != current_question["question_key"]:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_CURRENT_QUESTION_NOT_FOUND,
                message="提交的题目标识与当前题目不匹配",
                http_status=409,
                details={
                    "session_id": state.get("session_id"),
                    "expected_question_key": current_question["question_key"],
                    "received_question_key": expected_question_key,
                },
            )
        return current_question

    def _infer_current_question_key(
        self,
        questions: list[dict[str, Any]],
    ) -> str | None:
        """根据题目列表推断当前待回答题目标识。"""

        for question in reversed(questions):
            if question.get("status") == "asked":
                return str(question.get("question_key"))
        return None

    async def _commit_or_raise(
        self,
        session: AsyncSession,
        *,
        code: ErrorCode,
        message: str,
        details: dict[str, Any],
    ) -> None:
        """统一提交事务，并将失败映射为业务异常。"""

        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("面试事务提交失败: message={}, details={}", message, details)
            raise BusinessException(
                code=code,
                message=message,
                http_status=500,
                details={**details, "error": str(exc)},
            ) from exc

    @staticmethod
    def _generate_identifier() -> str:
        """生成字符串形式的 UUID 主键。"""

        return str(uuid4())


interview_service = InterviewService()


__all__ = [
    "InterviewService",
    "InterviewSessionCache",
    "interview_service",
]

