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
from app.agent.interview.plan_observer import InterviewPlanObserver
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
    get_latest_observation,
    hydrate_plan_state,
    list_questions,
    mark_question_answered,
    resolve_main_question_key,
)
from app.config import config
from app.core.stream_producer import StreamProducer, StreamTaskEnvelope, stream_producer
from app.services.evaluation_service import EvaluationService, evaluation_service as preset_evaluation_service
from app.core.redis_client import redis_manager
from app.models.interview import (
    InterviewAnswerHistoryDTO,
    CreateInterviewRequest,
    InterviewAnswerEntity,
    InterviewAnswerStatus,
    InterviewQuestionSnapshot,
    InterviewQuestionEvaluationDTO,
    InterviewReportEntity,
    InterviewReportDTO,
    InterviewReportExportDTO,
    InterviewReportStatus,
    InterviewSessionSummaryDTO,
    InterviewSessionDTO,
    InterviewSessionEntity,
    InterviewSessionStatus,
    InterviewWorkflowAction,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from app.repositories.interview_repository import InterviewRepository
from app.services.interview_persistence_service import InterviewPersistenceService
from app.services.resume_service import ResumeService, resume_service as preset_resume_service
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
        resume_service: ResumeService | None = None,
        repository_factory: Callable[[AsyncSession], InterviewRepository] | None = None,
        prompt_runner: InterviewPromptRunner | None = None,
        planner: InterviewPlanner | None = None,
        plan_observer: InterviewPlanObserver | None = None,
        executor: InterviewExecutor | None = None,
        replanner: InterviewReplanner | None = None,
        cache: InterviewSessionCache | None = None,
        evaluation_service: EvaluationService | None = None,
        stream_producer_backend: StreamProducer | None = None,
    ) -> None:
        self._skill_service = skill_service or preset_skill_service
        self._persistence_service = persistence_service or InterviewPersistenceService()
        self._resume_service = resume_service or preset_resume_service
        self._repository_factory = repository_factory or InterviewRepository
        self._prompt_runner = prompt_runner or InterviewPromptRunner(
            enable_llm=bool(config.dashscope_api_key),
        )
        self._planner = planner or InterviewPlanner(self._prompt_runner)
        self._plan_observer = plan_observer or InterviewPlanObserver(self._prompt_runner)
        self._executor = executor or InterviewExecutor(self._prompt_runner)
        self._replanner = replanner or InterviewReplanner(self._prompt_runner)
        self._cache = cache or InterviewSessionCache()
        self._evaluation_service = evaluation_service or preset_evaluation_service
        self._stream_producer = stream_producer_backend or stream_producer
        self._initial_graph = self._build_initial_graph()
        self._advance_graph = self._build_advance_graph()

    async def ensure_session_available(
        self,
        session: AsyncSession,
        session_id: str,
        visitor_id: str,
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

        self._ensure_session_belongs_to_visitor(interview_session, visitor_id)

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
        visitor_id: str,
    ) -> InterviewSessionDTO:
        """创建面试会话并同步生成首题。"""

        skill_detail = self._skill_service.get_skill_detail(request.skill_id)
        reference_section = self._skill_service.build_reference_section(request.skill_id)
        resume_context = await self._resume_service.resolve_resume_for_interview(
            session,
            visitor_id=visitor_id,
            resume_id=request.resume_id,
        )
        resolved_resume_id = resume_context.resume_id if resume_context else None
        session_id = self._generate_identifier()
        max_rounds = min(
            request.max_rounds or config.interview.max_rounds,
            config.interview.max_rounds,
        )
        language = request.language or config.interview.default_language
        title = request.title or f"{skill_detail.display_name} 模拟面试"
        planning_snapshot_markdown, planning_snapshot_sources = self._build_planning_snapshot(
            skill_detail,
            reference_section.reference_markdown,
            reference_section.resolved_reference_files,
            resume_markdown=resume_context.markdown_content if resume_context else "",
            resume_metadata=resume_context.metadata if resume_context else {},
        )

        initial_state = build_initial_state(
            session_id=session_id,
            skill=self._build_skill_context(
                skill_detail,
                reference_section.reference_markdown,
                reference_section.resolved_reference_files,
                planning_snapshot_markdown=planning_snapshot_markdown,
                planning_snapshot_sources=planning_snapshot_sources,
                resume_markdown=resume_context.markdown_content if resume_context else "",
                resume_metadata=resume_context.metadata if resume_context else {},
            ),
            language=language,
            max_rounds=max_rounds,
            title=title,
            visitor_id=visitor_id,
            resume_id=resolved_resume_id,
            resume_markdown=resume_context.markdown_content if resume_context else "",
            resume_metadata=resume_context.metadata if resume_context else {},
        )
        planned_state = await self._initial_graph.ainvoke(initial_state)

        interview_session = InterviewSessionEntity(
            id=session_id,
            visitor_id=visitor_id,
            skill_id=skill_detail.skill_id,
            resume_id=resolved_resume_id,
            title=title,
            language=language,
            mode=config.interview.default_mode,
            status=InterviewSessionStatus.ACTIVE.value,
            current_round=planned_state.get("current_round", 1),
            max_rounds=max_rounds,
            questions_json=list_questions(planned_state),
            session_context_json=build_session_context_payload(planned_state),
            metadata_json=self._build_session_metadata(
                skill_detail,
                reference_section.reference_markdown,
                reference_section.resolved_reference_files,
                planning_snapshot_markdown=planning_snapshot_markdown,
                planning_snapshot_sources=planning_snapshot_sources,
                resume_markdown=resume_context.markdown_content if resume_context else "",
                resume_metadata=resume_context.metadata if resume_context else {},
            ),
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
        visitor_id: str,
    ) -> InterviewSessionDTO:
        """读取面试会话快照。"""

        interview_session, answers, report, state = await self._load_session_bundle(
            session,
            session_id,
            visitor_id,
        )
        return self._build_session_dto(interview_session, state, answers, report)

    async def list_sessions(
        self,
        session: AsyncSession,
        *,
        visitor_id: str,
        limit: int = 20,
    ) -> list[InterviewSessionSummaryDTO]:
        """列出当前访客的历史面试会话摘要。"""

        repository = self._get_repository(session)
        sessions = await repository.list_sessions_by_visitor(visitor_id, limit=limit)
        summaries: list[InterviewSessionSummaryDTO] = []

        for interview_session in sessions:
            answers = await repository.list_answers_by_session(interview_session.id)
            report = await repository.get_report_by_session(interview_session.id)
            state = await self._load_state_from_cache_or_session(
                interview_session=interview_session,
                answers=answers,
                report=report,
            )
            summaries.append(
                self._build_session_summary_dto(
                    interview_session=interview_session,
                    state=state,
                    answer_count=len(answers),
                    report=report,
                )
            )

        return summaries

    async def save_draft_answer(
        self,
        session: AsyncSession,
        session_id: str,
        request: SubmitAnswerRequest,
        visitor_id: str,
    ) -> SubmitAnswerResponse:
        """暂存当前轮答案。"""

        interview_session, answers, report, state = await self._load_session_bundle(
            session,
            session_id,
            visitor_id,
        )
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
        visitor_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """提交答案并通过 SSE 推进主流程。"""

        interview_session, answers, report, state = await self._load_session_bundle(
            session,
            session_id,
            visitor_id,
        )
        self._ensure_session_is_active(interview_session)
        current_question = self._require_current_question(state, request.question_key)
        answer_text = request.answer_text.strip()
        logger.info(
            "submit_answer_stream started session_id={}, visitor_id={}, question_key={}, round_index={}, answer_length={}",
            session_id,
            visitor_id,
            current_question["question_key"],
            current_question["round_index"],
            len(answer_text),
        )

        process_run = self._build_initial_process_run(
            question_key=current_question["question_key"],
            round_index=int(current_question["round_index"]),
            process_run_id=(
                str(request.answer_metadata.get("process_run_id")).strip()
                if request.answer_metadata.get("process_run_id")
                else None
            ),
        )

        def record_process_event(event: dict[str, Any]) -> dict[str, Any]:
            self._append_process_event(process_run, event)
            return event

        yield record_process_event({
            "type": "status",
            "message": "已加载当前面试会话",
            "session_id": session_id,
            "stage": "session_loaded",
            "label": "加载会话与答案上下文",
            "detail": "已加载当前面试会话，准备分析本轮回答。",
        })

        state["latest_answer_text"] = answer_text
        state["latest_answer_metadata"] = deepcopy(request.answer_metadata)
        state["last_draft_answer"] = {}
        state["feedback"] = self._build_pending_feedback()
        state["assistant_message"] = None
        state["completion_message"] = None
        state["action_reason"] = None
        state["answer_count"] = len(answers) + 1
        mark_question_answered(state, current_question["question_key"])

        logger.info(
            "submit_answer_stream stage=answer_observation status=start session_id={}, question_key={}, round_index={}",
            session_id,
            current_question["question_key"],
            current_question["round_index"],
        )
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="answer_observation_start",
            label="评估回答完整性",
            detail="正在判断当前回答是否覆盖本题关键观察点。",
            message="开始评估回答完整性",
        ))
        observed_state = await self._plan_observer.run(state)
        latest_observation = get_latest_observation(
            observed_state,
            question_key=current_question["question_key"],
            main_question_key=resolve_main_question_key(observed_state, question=current_question)
            or current_question["question_key"],
        ) or {}
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="answer_observation_complete",
            label="评估回答完整性",
            detail=self._build_observation_stage_detail(latest_observation),
            message="回答完整性评估完成",
        ))

        logger.info(
            "submit_answer_stream stage=replan status=start session_id={}, question_key={}",
            session_id,
            current_question["question_key"],
        )
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="replan_start",
            label="判断下一步动作",
            detail="正在判断需要追问、切换主题还是结束面试。",
            message="开始判断下一步动作",
        ))
        replanned_state = await self._replanner.run(observed_state)
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="replan_complete",
            label="判断下一步动作",
            detail=f"下一步动作：{replanned_state.get('next_action', 'unknown')}。",
            message="下一步动作判断完成",
        ))
        yield record_process_event({
            "type": "plan",
            "action": replanned_state.get("next_action"),
            "reason": replanned_state.get("action_reason"),
            "session_id": session_id,
        })

        is_follow_up_action = replanned_state.get("next_action") == InterviewWorkflowAction.FOLLOW_UP.value
        if not replanned_state.get("completed"):
            if is_follow_up_action:
                yield record_process_event(self._build_stream_status_event(
                    session_id=session_id,
                    stage="tool_prepare_start",
                    label="准备工具上下文",
                    detail="正在判断是否需要结合简历或 GitHub 代码证据继续追问。",
                    message="开始准备工具上下文",
                ))
            else:
                yield record_process_event(self._build_stream_status_event(
                    session_id=session_id,
                    stage="llm_generation_start",
                    label="LLM 生成下一题 / 结束语",
                    detail="正在生成面试官下一条提问或收尾说明。",
                    message="开始生成面试官回应",
                ))
        elif replanned_state.get("next_action") == InterviewWorkflowAction.COMPLETE.value:
            yield record_process_event(self._build_stream_status_event(
                session_id=session_id,
                stage="llm_generation_start",
                label="LLM 生成下一题 / 结束语",
                detail="正在生成本场面试的收尾说明。",
                message="开始生成面试收尾说明",
            ))

        transitioned_state = await self._executor.run(replanned_state)
        if is_follow_up_action:
            tool_context = dict(transitioned_state.get("latest_tool_context", {}) or {})
            if tool_context.get("success") and tool_context.get("tool_name"):
                yield record_process_event(self._build_stream_status_event(
                    session_id=session_id,
                    stage="tool_call_complete",
                    label="调用证据工具",
                    detail=self._build_tool_stage_detail(tool_context),
                    message="证据工具调用完成",
                    extra={"tool": self._build_tool_status_summary(tool_context)},
                ))
            else:
                yield record_process_event(self._build_stream_status_event(
                    session_id=session_id,
                    stage="tool_skipped",
                    label="准备工具上下文",
                    detail=self._build_tool_stage_detail(tool_context) or "本轮未调用证据工具。",
                    message="本轮未调用证据工具",
                ))
            yield record_process_event(self._build_stream_status_event(
                session_id=session_id,
                stage="llm_generation_start",
                label="LLM 生成下一题 / 结束语",
                detail="已结合工具结果生成面试官下一条追问。",
                message="开始生成面试官回应",
            ))
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="llm_generation_complete",
            label="LLM 生成下一题 / 结束语",
            detail="面试官回应已生成。",
            message="面试官回应生成完成",
        ))
        logger.info(
            "submit_answer_stream stage=graph status=complete session_id={}, question_key={}, next_action={}, completed={}, assistant_message_present={}",
            session_id,
            current_question["question_key"],
            transitioned_state.get("next_action"),
            bool(transitioned_state.get("completed", False)),
            bool(transitioned_state.get("assistant_message")),
        )
        answer_entity = InterviewAnswerEntity(
            session_id=session_id,
            round_index=current_question["round_index"],
            question_key=current_question["question_key"],
            question_text=current_question["question_text"],
            answer_text=answer_text,
            answer_status=InterviewAnswerStatus.SUBMITTED.value,
            score_json={
                "status": "pending",
                "message": "待统一评估",
            },
            feedback_json=deepcopy(transitioned_state.get("feedback", {})),
            answer_metadata_json={
                **deepcopy(request.answer_metadata),
                "question_source": current_question.get("source"),
                "is_follow_up": bool(current_question.get("is_follow_up")),
                "process_run": deepcopy(process_run),
            },
        )

        self._apply_state_to_session(interview_session, transitioned_state)
        report_entity = None if not transitioned_state.get("completed") else self._build_pending_report_entity(
            interview_session=interview_session,
            state=transitioned_state,
            existing_report=report,
        )

        logger.info(
            "submit_answer_stream stage=persist status=start session_id={}, question_key={}, completed={}",
            session_id,
            current_question["question_key"],
            bool(transitioned_state.get("completed", False)),
        )
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="persist_start",
            label="保存本轮结果",
            detail="正在保存答案、题目状态和流程结果。",
            message="开始保存本轮结果",
        ))
        answer_entity.answer_metadata_json["process_run"] = deepcopy(process_run)
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
        logger.info(
            "submit_answer_stream stage=persist status=complete session_id={}, question_key={}, cache_action={}, report_pending={}",
            session_id,
            current_question["question_key"],
            "delete" if transitioned_state.get("completed") else "set",
            bool(report_entity),
        )
        yield record_process_event(self._build_stream_status_event(
            session_id=session_id,
            stage="persist_complete",
            label="保存本轮结果",
            detail="本轮答案和面试状态已保存。",
            message="本轮结果保存完成",
        ))

        if transitioned_state.get("assistant_message"):
            yield record_process_event({
                "type": "content",
                "session_id": session_id,
                "content": transitioned_state["assistant_message"],
            })

        if transitioned_state.get("completed"):
            logger.info(
                "submit_answer_stream stage=report status=start session_id={}, question_key={}",
                session_id,
                current_question["question_key"],
            )
            yield record_process_event({
                "type": "status",
                "session_id": session_id,
                "message": "面试已结束，开始生成统一评估报告",
                "stage": "report_start",
                "label": "生成统一评估报告",
                "detail": "正在收集本场问答并生成统一评估报告。",
            })
            report_entity = await self._schedule_or_generate_report(
                session,
                session_id=session_id,
                interview_session=interview_session,
                state=transitioned_state,
                existing_report=report_entity or report,
            )
            logger.info(
                "submit_answer_stream stage=report status=complete session_id={}, question_key={}, report_status={}",
                session_id,
                current_question["question_key"],
                report_entity.status,
            )
            yield record_process_event(self._build_stream_status_event(
                session_id=session_id,
                stage="report_complete",
                label="生成统一评估报告",
                detail=f"统一评估报告状态：{report_entity.status}。",
                message="统一评估报告已更新",
            ))
            yield record_process_event({
                "type": "report",
                "session_id": session_id,
                "report": deepcopy(report_entity.report_json),
            })

        logger.info(
            "submit_answer_stream stage=dto status=start session_id={}, question_key={}",
            session_id,
            current_question["question_key"],
        )
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
        yield record_process_event({
            "type": "step_complete",
            "session_id": session_id,
            "response": step_response.model_dump(mode="json"),
        })

        process_run["status"] = "completed"
        self._append_process_event(
            process_run,
            {
                "type": "done",
                "session_id": session_id,
            },
        )
        answer_entity.answer_metadata_json = {
            **deepcopy(answer_entity.answer_metadata_json or {}),
            "process_run": deepcopy(process_run),
        }
        await self._persistence_service.update_answer_metadata(
            session,
            answer_id=answer_entity.id,
            metadata=answer_entity.answer_metadata_json,
        )
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="更新运行过程记录失败",
            details={"session_id": session_id, "answer_id": answer_entity.id},
        )
        final_dto = self._build_session_dto(
            interview_session,
            transitioned_state,
            answers=answers + [answer_entity],
            report=report_entity or report,
        )
        logger.info(
            "submit_answer_stream stage=dto status=complete session_id={}, question_key={}, session_status={}, completed={}",
            session_id,
            current_question["question_key"],
            final_dto.status,
            final_dto.completed,
        )
        logger.info(
            "submit_answer_stream stage=done status=emit session_id={}, question_key={}",
            session_id,
            current_question["question_key"],
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
        visitor_id: str,
    ) -> InterviewSessionDTO:
        """主动结束面试会话。"""

        interview_session, answers, report, state = await self._load_session_bundle(
            session,
            session_id,
            visitor_id,
        )
        if not state.get("completed"):
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "用户主动结束面试"
            state = await self._executor.run(state)

        self._apply_state_to_session(interview_session, state)
        report_entity = self._build_pending_report_entity(
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
        report_entity = await self._schedule_or_generate_report(
            session,
            session_id=session_id,
            interview_session=interview_session,
            state=state,
            existing_report=report_entity,
        )
        await self._cache.delete_state(session_id)
        return self._build_session_dto(interview_session, state, answers, report_entity)

    async def get_report(
        self,
        session: AsyncSession,
        session_id: str,
        visitor_id: str,
    ) -> InterviewReportDTO:
        """获取统一评估报告。"""

        await self.ensure_session_available(session, session_id, visitor_id)
        return await self._evaluation_service.get_report(session, session_id)

    async def export_report(
        self,
        session: AsyncSession,
        session_id: str,
        visitor_id: str,
    ) -> InterviewReportExportDTO:
        """导出统一评估报告。"""

        await self.ensure_session_available(session, session_id, visitor_id)
        return await self._evaluation_service.export_report(session, session_id)

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
        workflow.add_node("plan_observer", self._plan_observer.run)
        workflow.add_node("replanner", self._replanner.run)
        workflow.add_node("executor", self._executor.run)
        workflow.set_entry_point("plan_observer")
        workflow.add_edge("plan_observer", "replanner")

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

    @staticmethod
    def _build_stream_status_event(
        *,
        session_id: str,
        stage: str,
        label: str,
        detail: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建兼容旧客户端的 SSE 状态事件。"""

        event = {
            "type": "status",
            "session_id": session_id,
            "stage": stage,
            "label": label,
            "detail": detail,
            "message": message,
        }
        if extra:
            event.update(extra)
        return event

    @staticmethod
    def _build_initial_process_run(
        *,
        question_key: str | None,
        round_index: int,
        process_run_id: str | None = None,
    ) -> dict[str, Any]:
        """构建用于刷新后回放的本轮运行过程。"""

        return {
            "id": process_run_id or f"{question_key or 'round'}-{round_index}-{uuid4()}",
            "question_key": question_key,
            "round_index": round_index,
            "status": "running",
            "steps": [
                {
                    "id": "submit",
                    "label": "提交答案",
                    "detail": "答案已发送到服务端，正在进入本轮处理流程。",
                    "status": "completed",
                    "timestamp": InterviewService._process_timestamp_ms(),
                }
            ],
        }

    @staticmethod
    def _process_timestamp_ms() -> int:
        """返回前端可直接消费的毫秒时间戳。"""

        return int(_utc_now().timestamp() * 1000)

    @classmethod
    def _append_process_event(cls, process_run: dict[str, Any], event: dict[str, Any]) -> None:
        """把 SSE 事件同步折叠为可持久化的运行过程步骤。"""

        event_type = event.get("type")
        if event_type == "status":
            cls._append_process_status_event(process_run, event)
            return
        if event_type == "plan":
            cls._upsert_process_step(
                process_run,
                step_id="replan",
                label="判断下一步动作",
                detail=f"推进决策：{event.get('action') or 'unknown'}"
                + (f" · {event.get('reason')}" if event.get("reason") else ""),
                status="completed",
            )
            return
        if event_type == "content":
            cls._upsert_process_step(
                process_run,
                step_id="llm",
                label="生成下一题 / 结束语",
                detail="面试官已生成下一步内容，正在保存本轮结果。",
                status="completed",
            )
            return
        if event_type == "step_complete":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            cls._upsert_process_step(
                process_run,
                step_id="persist",
                label="保存本轮结果",
                detail=str(response.get("message") or "答案已提交，流程已推进"),
                status="completed",
            )
            return
        if event_type == "report":
            cls._upsert_process_step(
                process_run,
                step_id="report",
                label="生成统一评估报告",
                detail="统一评估报告已更新。",
                status="completed",
            )
            return
        if event_type == "done":
            cls._upsert_process_step(
                process_run,
                step_id="done",
                label="本轮完成",
                detail="当前轮流式流程已结束，可以继续作答或查看结果。",
                status="completed",
            )
            process_run["status"] = "completed"
            return
        if event_type == "error":
            cls._mark_process_failed(process_run, str(event.get("message") or "流式过程异常"))

    @classmethod
    def _append_process_status_event(cls, process_run: dict[str, Any], event: dict[str, Any]) -> None:
        """把 status SSE 阶段映射为运行过程步骤。"""

        stage = str(event.get("stage") or "")
        label = str(event.get("label") or event.get("message") or "")
        detail = str(event.get("detail") or event.get("message") or "")
        if stage in {"session_loaded", "answer_observation_start"}:
            cls._upsert_process_step(process_run, step_id="observation", label=label or "评估回答完整性", detail=detail, status="active")
        elif stage == "answer_observation_complete":
            cls._upsert_process_step(process_run, step_id="observation", label=label or "评估回答完整性", detail=detail, status="completed")
        elif stage == "replan_start":
            cls._upsert_process_step(process_run, step_id="replan", label=label or "判断下一步动作", detail=detail, status="active")
        elif stage == "replan_complete":
            cls._upsert_process_step(process_run, step_id="replan", label=label or "判断下一步动作", detail=detail, status="completed")
        elif stage == "tool_prepare_start":
            cls._upsert_process_step(process_run, step_id="tool_prepare", label=label or "准备工具上下文", detail=detail, status="active")
        elif stage == "tool_call_complete":
            cls._upsert_process_step(
                process_run,
                step_id="tool_prepare",
                label="准备工具上下文",
                detail="证据工具上下文准备完成。",
                status="completed",
            )
            cls._upsert_process_step(
                process_run,
                step_id="tool_call",
                label=label or "调用证据工具",
                detail=detail,
                status="completed",
                tool_summary=deepcopy(event.get("tool")) if isinstance(event.get("tool"), dict) else None,
            )
        elif stage == "tool_skipped":
            cls._upsert_process_step(process_run, step_id="tool_prepare", label=label or "本轮未调用证据工具", detail=detail, status="skipped")
        elif stage == "llm_generation_start":
            cls._upsert_process_step(process_run, step_id="llm", label=label or "生成下一题 / 结束语", detail=detail, status="active")
        elif stage == "llm_generation_complete":
            cls._upsert_process_step(process_run, step_id="llm", label=label or "生成下一题 / 结束语", detail=detail, status="completed")
        elif stage == "persist_start":
            cls._upsert_process_step(process_run, step_id="persist", label=label or "保存本轮结果", detail=detail, status="active")
        elif stage == "persist_complete":
            cls._upsert_process_step(process_run, step_id="persist", label=label or "保存本轮结果", detail=detail, status="completed")
        elif stage == "report_start":
            cls._upsert_process_step(process_run, step_id="report", label=label or "生成统一评估报告", detail=detail, status="active")
        elif stage == "report_complete":
            cls._upsert_process_step(process_run, step_id="report", label=label or "生成统一评估报告", detail=detail, status="completed")

    @classmethod
    def _upsert_process_step(
        cls,
        process_run: dict[str, Any],
        *,
        step_id: str,
        label: str,
        detail: str,
        status: str,
        tool_summary: dict[str, Any] | None = None,
    ) -> None:
        """插入或更新一个运行过程步骤。"""

        steps = list(process_run.get("steps", []))
        if status == "active":
            for step in steps:
                if step.get("status") == "active" and step.get("id") != step_id:
                    step["status"] = "completed"
        next_step = {
            "id": step_id,
            "label": label,
            "detail": detail,
            "status": status,
            "timestamp": cls._process_timestamp_ms(),
        }
        if tool_summary:
            next_step["tool_summary"] = deepcopy(tool_summary)

        for index, step in enumerate(steps):
            if step.get("id") == step_id:
                next_step["timestamp"] = step.get("timestamp") or next_step["timestamp"]
                if not next_step.get("detail") and step.get("detail"):
                    next_step["detail"] = step["detail"]
                if "tool_summary" not in next_step and step.get("tool_summary"):
                    next_step["tool_summary"] = deepcopy(step["tool_summary"])
                steps[index] = next_step
                process_run["steps"] = steps
                return

        steps.append(next_step)
        process_run["steps"] = steps

    @classmethod
    def _mark_process_failed(cls, process_run: dict[str, Any], message: str) -> None:
        """把当前运行过程标记为失败。"""

        steps = list(process_run.get("steps", []))
        active_step = next((step for step in steps if step.get("status") == "active"), None)
        failed_step_id = str((active_step or steps[-1] if steps else {}).get("id") or "submit")
        cls._upsert_process_step(
            process_run,
            step_id=failed_step_id,
            label=str((active_step or {}).get("label") or "提交答案"),
            detail=message,
            status="failed",
        )
        process_run["status"] = "failed"

    @staticmethod
    def _build_observation_stage_detail(observation: dict[str, Any]) -> str:
        """把回答观察结果转成适合前端展示的阶段说明。"""

        status = str(observation.get("main_question_status") or "partial")
        confidence = observation.get("confidence")
        missing_signals = [
            str(item).strip()
            for item in list(observation.get("missing_signals", []) or [])
            if str(item).strip()
        ]
        parts = [f"完整性状态：{status}"]
        if isinstance(confidence, (int, float)):
            parts.append(f"置信度：{confidence:.0%}")
        if missing_signals:
            parts.append(f"仍需关注：{'、'.join(missing_signals[:2])}")
        return "；".join(parts) + "。"

    @staticmethod
    def _build_tool_stage_detail(tool_context: dict[str, Any]) -> str:
        """把工具上下文转成适合前端展示的阶段说明。"""

        tool_name = str(tool_context.get("tool_name") or "").strip()
        reason = str(tool_context.get("decision_reason") or tool_context.get("error_message") or "").strip()
        if not tool_name:
            return reason
        if tool_context.get("success"):
            return f"{tool_name} 已返回可用于追问的证据。"
        if reason:
            return f"{tool_name} 未调用或未命中：{reason}。"
        return f"{tool_name} 本轮未产生可用证据。"

    @staticmethod
    def _build_tool_status_summary(tool_context: dict[str, Any]) -> dict[str, Any]:
        """生成面向前端展示的工具调用摘要，避免暴露完整工具原始结果。"""

        tool_name = str(tool_context.get("tool_name") or "").strip()
        result = tool_context.get("result", {})
        result_payload = result if isinstance(result, dict) else {}
        if tool_name == "github_repo_evidence_tool":
            repo_name = str(result_payload.get("repo_name") or result_payload.get("repo_url") or "GitHub 仓库").strip()
            matched_files = [
                str(item).strip()
                for item in list(result_payload.get("matched_files", []) or [])
                if str(item).strip()
            ]
            items = list(result_payload.get("items", []) or [])
            if not matched_files:
                matched_files = [
                    str(item.get("source_path", "")).strip()
                    for item in items
                    if isinstance(item, dict) and str(item.get("source_path", "")).strip()
                ]
            file_summary = "、".join(matched_files[:3])
            summary = f"从 {repo_name} 中获取了相关代码片段"
            if file_summary:
                summary += f"，命中文件：{file_summary}"
            return {
                "name": tool_name,
                "display_name": "GitHub 仓库证据工具",
                "source": repo_name,
                "summary": summary + "。",
                "matched_files": matched_files[:5],
                "retrieval_reason": result_payload.get("retrieval_reason") or tool_context.get("decision_reason"),
            }
        if tool_name == "resume_evidence_tool":
            matched_projects = [
                str(item).strip()
                for item in list(result_payload.get("matched_projects", []) or [])
                if str(item).strip()
            ]
            matched_skills = [
                str(item).strip()
                for item in list(result_payload.get("matched_skills", []) or [])
                if str(item).strip()
            ]
            source_parts = matched_projects[:2] or matched_skills[:3]
            source = "、".join(source_parts) if source_parts else "简历内容"
            return {
                "name": tool_name,
                "display_name": "简历证据工具",
                "source": source,
                "summary": f"从{source}中获取了与当前追问相关的项目/技能片段。",
                "matched_projects": matched_projects[:5],
                "matched_skills": matched_skills[:5],
                "retrieval_reason": result_payload.get("retrieval_reason") or tool_context.get("decision_reason"),
            }
        return {
            "name": tool_name,
            "display_name": tool_name or "证据工具",
            "source": "工具结果",
            "summary": InterviewService._build_tool_stage_detail(tool_context),
            "retrieval_reason": tool_context.get("decision_reason"),
        }

    async def _load_session_bundle(
        self,
        session: AsyncSession,
        session_id: str,
        visitor_id: str,
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

        self._ensure_session_belongs_to_visitor(interview_session, visitor_id)

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
                    visitor_id=interview_session.visitor_id,
                    resume_id=interview_session.resume_id,
                    resume_markdown=str((interview_session.metadata_json or {}).get("resume_markdown", "")),
                    resume_metadata=deepcopy((interview_session.metadata_json or {}).get("resume_metadata", {})),
                )

        state["session_id"] = interview_session.id
        state["visitor_id"] = interview_session.visitor_id
        state["resume_id"] = interview_session.resume_id
        state["title"] = interview_session.title
        state["language"] = interview_session.language
        state["max_rounds"] = interview_session.max_rounds
        resume_markdown = interview_session.session_context_json.get(
            "resume_markdown",
            state.get("resume_markdown", (interview_session.metadata_json or {}).get("resume_markdown", "")),
        )
        state["resume_markdown"] = str(resume_markdown or "")
        state["resume_metadata"] = deepcopy(
            interview_session.session_context_json.get(
                "resume_metadata",
                state.get("resume_metadata", (interview_session.metadata_json or {}).get("resume_metadata", {})),
            )
        )
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
        state["interview_plan"] = deepcopy(
            interview_session.session_context_json.get("interview_plan", state.get("interview_plan", {}))
        )
        state["plan_progress"] = deepcopy(
            interview_session.session_context_json.get("plan_progress", state.get("plan_progress", {}))
        )
        state["current_main_question_key"] = interview_session.session_context_json.get(
            "current_main_question_key",
            interview_session.session_context_json.get(
                "current_topic_key",
                state.get("current_main_question_key"),
            ),
        )
        state["remaining_main_question_keys"] = list(
            interview_session.session_context_json.get(
                "remaining_main_question_keys",
                interview_session.session_context_json.get(
                    "remaining_topics",
                    state.get("remaining_main_question_keys", []),
                ),
            )
        )
        state["coverage_status"] = deepcopy(
            interview_session.session_context_json.get("coverage_status", state.get("coverage_status", {}))
        )
        state["answer_observations"] = deepcopy(
            interview_session.session_context_json.get(
                "answer_observations",
                state.get("answer_observations", []),
            )
        )
        state["termination_decision_context"] = deepcopy(
            interview_session.session_context_json.get(
                "termination_decision_context",
                state.get("termination_decision_context", {}),
            )
        )
        state["latest_tool_context"] = deepcopy(
            interview_session.session_context_json.get(
                "latest_tool_context",
                state.get("latest_tool_context", {}),
            )
        )
        state["answer_count"] = len(answers)
        state["completed"] = interview_session.status == InterviewSessionStatus.COMPLETED.value
        state["report_summary"] = deepcopy(report.report_json if report else state.get("report_summary", {}))
        hydrate_plan_state(state, max_follow_up_questions=config.interview.max_follow_up_questions)
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
            "resume_markdown": state.get("resume_markdown", ""),
            "resume_metadata": deepcopy(state.get("resume_metadata", {})),
            "interview_plan": deepcopy(state.get("interview_plan", {})),
            "plan_progress": deepcopy(state.get("plan_progress", {})),
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
        *,
        planning_snapshot_markdown: str,
        planning_snapshot_sources: list[dict[str, Any]],
        resume_markdown: str,
        resume_metadata: dict[str, Any],
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
            "planning_snapshot_markdown": planning_snapshot_markdown,
            "planning_snapshot_sources": deepcopy(planning_snapshot_sources),
            "resume_markdown": resume_markdown,
            "resume_metadata": deepcopy(resume_metadata),
        }

    def _build_skill_context(
        self,
        skill_detail: Any,
        reference_markdown: str,
        reference_files: list[str],
        *,
        planning_snapshot_markdown: str,
        planning_snapshot_sources: list[dict[str, Any]],
        resume_markdown: str,
        resume_metadata: dict[str, Any],
    ) -> InterviewSkillContext:
        """根据 Skill 服务结果构建工作流上下文。"""

        return {
            "skill_id": skill_detail.skill_id,
            "display_name": skill_detail.display_name,
            "description": skill_detail.description,
            "content_markdown": skill_detail.content_markdown,
            "reference_markdown": reference_markdown,
            "planning_snapshot_markdown": planning_snapshot_markdown,
            "planning_snapshot_sources": deepcopy(planning_snapshot_sources),
            "categories": [
                category.model_dump(mode="json") for category in skill_detail.categories
            ],
            "reference_files": reference_files,
            "resume_markdown": resume_markdown,
            "resume_metadata": deepcopy(resume_metadata),
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
            "planning_snapshot_markdown": str(metadata.get("planning_snapshot_markdown", "")),
            "planning_snapshot_sources": list(metadata.get("planning_snapshot_sources", [])),
            "categories": list(metadata.get("skill_categories", [])),
            "reference_files": list(metadata.get("reference_files", [])),
            "resume_markdown": str(metadata.get("resume_markdown", "")),
            "resume_metadata": deepcopy(metadata.get("resume_metadata", {})),
        }

    def _build_planning_snapshot(
        self,
        skill_detail: Any,
        reference_markdown: str,
        reference_files: list[str],
        *,
        resume_markdown: str,
        resume_metadata: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        """组装 planner 使用的冻结快照与来源清单。"""

        planning_snapshot_sources: list[dict[str, Any]] = [
            {
                "source_type": "skill_markdown",
                "name": "SKILL.md",
                "skill_id": skill_detail.skill_id,
                "display_name": skill_detail.display_name,
            },
            {
                "source_type": "reference_files",
                "name": "reference_files",
                "files": list(reference_files),
                "count": len(reference_files),
            },
        ]
        if resume_markdown.strip():
            planning_snapshot_sources.append(
                {
                    "source_type": "resume_markdown",
                    "name": "resume",
                    "has_resume": True,
                    "resume_metadata": deepcopy(resume_metadata),
                }
            )

        sections = [
            "# Planning Snapshot",
            "## Source Manifest",
        ]
        for item in planning_snapshot_sources:
            parts = [f"- {item.get('source_type', 'source')}"]
            name = str(item.get("name", "")).strip()
            if name:
                parts.append(f": {name}")
            if item.get("skill_id"):
                parts.append(f" ({item['skill_id']})")
            if item.get("files"):
                parts.append(f" -> {', '.join(str(value) for value in item['files'])}")
            sections.append("".join(parts))

        sections.extend(
            [
                "",
                "## Skill Persona",
                skill_detail.content_markdown.strip(),
                "",
                "## Reference Material",
                reference_markdown.strip(),
            ]
        )
        if resume_markdown.strip():
            sections.extend(
                [
                    "",
                    "## Resume Context",
                    resume_markdown.strip(),
                ]
            )
        if resume_metadata:
            sections.extend(
                [
                    "",
                    "## Resume Metadata",
                    "```json",
                    json.dumps(resume_metadata, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                ]
            )

        return "\n".join(section for section in sections if section is not None).strip(), planning_snapshot_sources

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
            answers=[
                InterviewAnswerHistoryDTO(
                    answer_id=answer.id,
                    round_index=answer.round_index,
                    question_key=answer.question_key,
                    question_text=answer.question_text,
                    answer_text=answer.answer_text,
                    answer_status=answer.answer_status,
                    submitted_at=answer.submitted_at,
                    answer_metadata=deepcopy(answer.answer_metadata_json),
                    feedback=deepcopy(answer.feedback_json),
                )
                for answer in answers
            ],
            answer_count=len(answers),
            follow_up_count=int(state.get("follow_up_count", 0)),
            completed=bool(state.get("completed", False)),
            report_status=report.status if report else None,
            last_draft_answer=deepcopy(state.get("last_draft_answer", {})),
            started_at=interview_session.started_at,
            completed_at=interview_session.completed_at,
        )

    def _build_session_summary_dto(
        self,
        *,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
        answer_count: int,
        report: InterviewReportEntity | None,
    ) -> InterviewSessionSummaryDTO:
        """将会话实体转换为列表页使用的摘要 DTO。"""

        skill_context = state["skill"]
        return InterviewSessionSummaryDTO(
            session_id=interview_session.id,
            resume_id=interview_session.resume_id,
            skill_id=interview_session.skill_id or "",
            skill_display_name=skill_context["display_name"],
            title=interview_session.title,
            language=interview_session.language,
            status=interview_session.status,
            current_round=interview_session.current_round,
            max_rounds=interview_session.max_rounds,
            answer_count=answer_count,
            completed=bool(state.get("completed", False)),
            report_status=report.status if report else None,
            current_question=self._build_current_question_model(state),
            started_at=interview_session.started_at,
            updated_at=interview_session.updated_at,
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

    def _build_pending_feedback(self) -> dict[str, Any]:
        """生成待统一评估的过程反馈。"""

        return {
            "status": "pending",
            "message": "答案已记录，统一评估将在面试结束后生成。",
            "phase": "phase6-evaluation-pending",
        }

    def _build_pending_report_entity(
        self,
        *,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
        existing_report: InterviewReportEntity | None,
    ) -> InterviewReportEntity:
        """生成或更新等待统一评估的报告实体。"""

        report_entity = existing_report or InterviewReportEntity(session_id=interview_session.id)
        report_entity.status = InterviewReportStatus.PENDING.value
        report_entity.summary_text = state.get("completion_message")
        report_entity.report_json = {
            "session_id": interview_session.id,
            "skill_id": interview_session.skill_id,
            "status": InterviewReportStatus.PENDING.value,
            "message": "统一评估报告待生成",
        }
        report_entity.score_json = {
            "status": InterviewReportStatus.PENDING.value,
            "message": "统一评估报告待生成",
        }
        report_entity.error_message = None
        return report_entity

    async def _schedule_or_generate_report(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        interview_session: InterviewSessionEntity,
        state: InterviewState,
        existing_report: InterviewReportEntity | None,
    ) -> InterviewReportEntity:
        """优先异步发布报告任务，失败时同步回退。"""

        report_entity = self._build_pending_report_entity(
            interview_session=interview_session,
            state=state,
            existing_report=existing_report,
        )
        try:
            if not self._stream_producer.enabled or not config.stream_task.synchronous_fallback_enabled:
                raise RuntimeError("stream task disabled")
            envelope = StreamTaskEnvelope.new(
                task_type="interview_report_generate",
                payload={
                    "session_id": session_id,
                },
                trace_context={
                    "session_id": session_id,
                    "skill_id": interview_session.skill_id,
                },
            )
            await self._stream_producer.publish(envelope)
        except Exception as exc:
            logger.warning("异步报告发布失败，回退同步评估: session_id={}, error={}", session_id, exc)
            report_entity = await self._evaluation_service.generate_report(session, session_id)
            await self._commit_or_raise(
                session,
                code=ErrorCode.INTERVIEW_PERSIST_FAILED,
                message="生成统一评估报告失败",
                details={"session_id": session_id},
            )
            return report_entity

        report_entity.status = InterviewReportStatus.PENDING.value
        report_entity.report_json = {
            "session_id": session_id,
            "skill_id": interview_session.skill_id,
            "status": InterviewReportStatus.PENDING.value,
            "message": "统一评估报告已进入异步队列，请稍后刷新查看结果。",
            "task_id": envelope.task_id,
        }
        report_entity.score_json = {
            "status": InterviewReportStatus.PENDING.value,
            "message": "统一评估报告已进入异步队列，请稍后刷新查看结果。",
            "task_id": envelope.task_id,
        }
        report_entity = await self._persistence_service.save_report(session, report_entity)
        await self._commit_or_raise(
            session,
            code=ErrorCode.INTERVIEW_PERSIST_FAILED,
            message="更新异步报告状态失败",
            details={"session_id": session_id, "task_id": envelope.task_id},
        )
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

    def _ensure_session_belongs_to_visitor(
        self,
        interview_session: InterviewSessionEntity,
        visitor_id: str,
    ) -> None:
        """校验会话归属的匿名访客。"""

        if interview_session.visitor_id != visitor_id:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_NOT_FOUND,
                message="面试会话不存在",
                http_status=404,
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
